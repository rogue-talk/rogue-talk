"""Main BotClient class for creating game bots."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import tempfile
import time
from asyncio import StreamReader, StreamWriter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Coroutine

import numpy as np
import numpy.typing as npt
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription

from ..common.constants import AUDIO_MAX_DISTANCE, FRAME_SIZE, SAMPLE_RATE
from ..common.crypto import generate_keypair, sign_challenge
from ..common.protocol import (
    AuthResult,
    MessageType,
    PlayerInfo,
    deserialize_audio_track_map,
    deserialize_auth_challenge,
    deserialize_auth_result,
    deserialize_door_transition,
    deserialize_player_joined,
    deserialize_player_left,
    deserialize_position_ack,
    deserialize_server_hello,
    deserialize_webrtc_answer,
    deserialize_webrtc_offer,
    deserialize_world_state,
    read_message,
    serialize_auth_response,
    serialize_mute_status,
    serialize_position_update,
    serialize_webrtc_answer,
    serialize_webrtc_offer,
    write_message,
)
from .audio import AudioSource, FileAudioSource, PCMAudioSource
from .audio_track import BotAudioCaptureTrack
from .pathfinding import find_path
from .types import BotConfig, Direction, PlayerState, WorldState

if TYPE_CHECKING:
    from aiortc import RTCDataChannel

    from ..client.level import Level

logger = logging.getLogger(__name__)


# Type aliases for event callbacks
WorldStateCallback = Callable[[WorldState], Coroutine[Any, Any, None]]
PlayerCallback = Callable[[PlayerState], Coroutine[Any, Any, None]]
PlayerJoinedCallback = Callable[[int, str], Coroutine[Any, Any, None]]
PlayerLeftCallback = Callable[[int], Coroutine[Any, Any, None]]
AudioCallback = Callable[[int, float, bytes], Coroutine[Any, Any, None]]


class BotIdentity:
    """Bot identity management."""

    def __init__(self, bot_name: str, identity_dir: Path | None = None) -> None:
        self.bot_name = bot_name
        if identity_dir is None:
            identity_dir = Path.home() / ".rogue-talk" / "bots" / bot_name
        self.identity_dir = identity_dir
        self.identity_path = identity_dir / "identity.json"
        self.private_key: bytes = b""
        self.public_key: bytes = b""

    def load_or_create(self) -> None:
        """Load existing identity or create a new one."""
        if self.identity_path.exists():
            try:
                data = json.loads(self.identity_path.read_text())
                self.private_key = bytes.fromhex(data["private_key"])
                self.public_key = bytes.fromhex(data["public_key"])
                return
            except (json.JSONDecodeError, KeyError, ValueError):
                pass

        # Generate new keypair
        self.private_key, self.public_key = generate_keypair()

        # Save to disk
        self.identity_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "private_key": self.private_key.hex(),
            "public_key": self.public_key.hex(),
        }
        self.identity_path.write_text(json.dumps(data, indent=2))


class BotClient:
    """High-level bot client for connecting to rogue-talk servers.

    Example usage:
        async def main():
            bot = BotClient(name="GuardBot")
            await bot.connect("localhost", 7777)

            @bot.on_player_nearby
            async def on_nearby(player):
                await bot.speak_file("sounds/hello.wav")

            await bot.run()
    """

    def __init__(
        self,
        name: str,
        config: BotConfig | None = None,
    ) -> None:
        """Create a new bot client.

        Args:
            name: Display name for the bot.
            config: Optional configuration.
        """
        self.name = name
        self.config = config or BotConfig()

        # Identity
        self._identity = BotIdentity(name, self.config.identity_dir)

        # Connection state
        self.player_id: int = 0
        self.x: int = 0
        self.y: int = 0
        self.room_width: int = 0
        self.room_height: int = 0
        self.current_level: str = "main"
        self.is_muted: bool = False

        # Level data
        self._level: Level | None = None

        # Other players state
        self._world_state = WorldState()
        self._previous_nearby_players: set[int] = set()
        self._speaking_players: dict[int, float] = {}  # player_id -> last_audio_time
        self._speaking_timeout = 0.5  # seconds of silence before "stopped speaking"

        # Network
        self._reader: StreamReader | None = None
        self._writer: StreamWriter | None = None
        self._peer_connection: RTCPeerConnection | None = None
        self._data_channel: RTCDataChannel | None = None
        self._webrtc_connected: bool = False

        # Audio
        self._audio_capture_track: BotAudioCaptureTrack | None = None
        self._audio_playback_track: Any = None  # AudioPlaybackTrack

        # Movement
        self._move_seq: int = 0
        self._pending_moves: dict[int, tuple[int, int, int, int]] = {}
        self._position_queue: asyncio.Queue[tuple[int, int, int]] | None = None
        self._pathfinding_task: asyncio.Task[None] | None = None
        self._path: list[tuple[int, int]] | None = None
        self._path_index: int = 0

        # Running state
        self._running = False
        self._data_channel_ready = asyncio.Event()
        self._connection_closed = asyncio.Event()

        # Event callbacks
        self._on_world_state_callbacks: list[WorldStateCallback] = []
        self._on_player_joined_callbacks: list[PlayerJoinedCallback] = []
        self._on_player_left_callbacks: list[PlayerLeftCallback] = []
        self._on_player_nearby_callbacks: list[PlayerCallback] = []
        self._on_player_left_range_callbacks: list[PlayerCallback] = []
        self._on_player_speaks_callbacks: list[PlayerCallback] = []
        self._on_player_stops_speaking_callbacks: list[PlayerCallback] = []
        self._on_audio_callbacks: list[AudioCallback] = []

    # Event decorator methods

    def on_world_state(self, callback: WorldStateCallback) -> WorldStateCallback:
        """Decorator for world state updates."""
        self._on_world_state_callbacks.append(callback)
        return callback

    def on_player_joined(self, callback: PlayerJoinedCallback) -> PlayerJoinedCallback:
        """Decorator for player join events."""
        self._on_player_joined_callbacks.append(callback)
        return callback

    def on_player_left(self, callback: PlayerLeftCallback) -> PlayerLeftCallback:
        """Decorator for player leave events."""
        self._on_player_left_callbacks.append(callback)
        return callback

    def on_player_nearby(self, callback: PlayerCallback) -> PlayerCallback:
        """Decorator for when a player enters audio range (10 tiles)."""
        self._on_player_nearby_callbacks.append(callback)
        return callback

    def on_player_left_range(self, callback: PlayerCallback) -> PlayerCallback:
        """Decorator for when a player leaves audio range."""
        self._on_player_left_range_callbacks.append(callback)
        return callback

    def on_player_speaks(self, callback: PlayerCallback) -> PlayerCallback:
        """Decorator for when a nearby player starts speaking."""
        self._on_player_speaks_callbacks.append(callback)
        return callback

    def on_player_stops_speaking(self, callback: PlayerCallback) -> PlayerCallback:
        """Decorator for when a nearby player stops speaking."""
        self._on_player_stops_speaking_callbacks.append(callback)
        return callback

    def on_audio(self, callback: AudioCallback) -> AudioCallback:
        """Decorator for raw audio frames from nearby players.

        Callback receives (player_id, volume, samples_bytes).
        """
        self._on_audio_callbacks.append(callback)
        return callback

    # Connection methods

    async def connect(self, host: str, port: int) -> bool:
        """Connect to a server.

        Args:
            host: Server hostname or IP.
            port: Server port.

        Returns:
            True if connection successful, False otherwise.
        """
        # Load or create identity
        self._identity.load_or_create()

        try:
            self._reader, self._writer = await asyncio.open_connection(host, port)
        except (ConnectionRefusedError, OSError) as e:
            logger.error(f"Failed to connect: {e}")
            return False

        # Wait for AUTH_CHALLENGE
        msg_type, payload = await read_message(self._reader)
        if msg_type != MessageType.AUTH_CHALLENGE:
            logger.error("Unexpected response from server (expected AUTH_CHALLENGE)")
            return False

        nonce = deserialize_auth_challenge(payload)

        # Sign the challenge
        signature = sign_challenge(self._identity.private_key, nonce, self.name)

        # Send AUTH_RESPONSE
        await write_message(
            self._writer,
            MessageType.AUTH_RESPONSE,
            serialize_auth_response(self._identity.public_key, self.name, signature),
        )

        # Wait for AUTH_RESULT
        msg_type, payload = await read_message(self._reader)
        if msg_type != MessageType.AUTH_RESULT:
            logger.error("Unexpected response from server (expected AUTH_RESULT)")
            return False

        auth_result = deserialize_auth_result(payload)
        if auth_result != AuthResult.SUCCESS:
            error_messages = {
                AuthResult.NAME_TAKEN: "Name is already taken by another player",
                AuthResult.KEY_MISMATCH: "Your key is registered with a different name",
                AuthResult.INVALID_SIGNATURE: "Authentication failed (invalid signature)",
                AuthResult.INVALID_NAME: "Invalid name",
                AuthResult.ALREADY_CONNECTED: "You are already connected to this server",
            }
            logger.error(
                f"Authentication failed: {error_messages.get(auth_result, 'Unknown error')}"
            )
            return False

        # Wait for SERVER_HELLO
        msg_type, payload = await read_message(self._reader)
        if msg_type != MessageType.SERVER_HELLO:
            logger.error("Unexpected response from server (expected SERVER_HELLO)")
            return False

        (
            self.player_id,
            self.room_width,
            self.room_height,
            self.x,
            self.y,
            level_data,
            level_name,
        ) = deserialize_server_hello(payload)

        # Import Level class here to avoid circular imports
        from ..client.level import Level

        self._level = Level.from_bytes(level_data)
        self.current_level = level_name

        # Set up WebRTC connection
        if not await self._setup_webrtc():
            logger.error("Failed to establish WebRTC connection")
            return False

        logger.info(
            f"Connected as {self.name} (ID: {self.player_id}) at ({self.x}, {self.y})"
        )
        return True

    async def _setup_webrtc(self) -> bool:
        """Set up WebRTC peer connection with the server."""
        if not self._writer or not self._reader:
            return False

        self._peer_connection = RTCPeerConnection()
        pc = self._peer_connection

        # Create audio capture track for sending audio
        if self.config.audio_enabled:
            self._audio_capture_track = BotAudioCaptureTrack()
            pc.addTrack(self._audio_capture_track)

        # Create data channel for game messages
        self._data_channel = pc.createDataChannel("game", ordered=True)

        @self._data_channel.on("open")
        def on_open() -> None:
            logger.info("Data channel opened")
            self._data_channel_ready.set()

        @self._data_channel.on("message")
        def on_message(message: bytes | str) -> None:
            if isinstance(message, str):
                message = message.encode("utf-8")
            asyncio.create_task(self._handle_data_channel_message(message))

        # Handle incoming audio track from server
        @pc.on("track")
        def on_track(track: Any) -> None:
            if track.kind == "audio":
                logger.info("Received audio track from server")
                asyncio.create_task(self._handle_incoming_audio(track))

        # Handle connection state changes
        @pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            state = pc.connectionState
            logger.info(f"WebRTC connection state: {state}")
            if state in ("failed", "closed", "disconnected"):
                self._connection_closed.set()
                self._running = False
            elif state == "connected":
                self._webrtc_connected = True

        # Create and send offer
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)

        offer_sdp = pc.localDescription.sdp if pc.localDescription else ""
        await write_message(
            self._writer,
            MessageType.WEBRTC_OFFER,
            serialize_webrtc_offer(offer_sdp),
        )

        # Wait for answer
        msg_type, payload = await read_message(self._reader)
        if msg_type != MessageType.WEBRTC_ANSWER:
            logger.error(f"Expected WEBRTC_ANSWER, got {msg_type}")
            return False

        answer_sdp = deserialize_webrtc_answer(payload)
        await pc.setRemoteDescription(
            RTCSessionDescription(sdp=answer_sdp, type="answer")
        )

        # Wait for data channel to be ready
        try:
            await asyncio.wait_for(self._data_channel_ready.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for WebRTC data channel")
            return False

        self._webrtc_connected = True

        # Close TCP connection (signaling complete)
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except Exception:
            pass
        self._reader = None
        self._writer = None

        return True

    async def _handle_incoming_audio(self, track: MediaStreamTrack) -> None:
        """Handle incoming audio from other players."""
        while self._running:
            try:
                frame = await track.recv()
                if not hasattr(frame, "to_ndarray"):
                    continue

                pcm_data = frame.to_ndarray()

                # Extract source player ID from first 2 samples (int16)
                if pcm_data.dtype == np.int16 and len(pcm_data.flatten()) >= 2:
                    flat = pcm_data.flatten()
                    source_player_id = int(flat[0] & 0xFFFF) | (
                        int(flat[1] & 0xFFFF) << 16
                    )
                else:
                    source_player_id = 0

                if source_player_id == 0:
                    continue  # Silence frame

                # Convert to float32 and normalize
                if pcm_data.dtype == np.int16:
                    pcm_float = pcm_data.astype(np.float32) / 32768.0
                elif pcm_data.dtype == np.int32:
                    pcm_float = pcm_data.astype(np.float32) / 2147483648.0
                else:
                    pcm_float = pcm_data.astype(np.float32)

                pcm_float = pcm_float.flatten()

                # Handle stereo if needed
                layout_name = getattr(getattr(frame, "layout", None), "name", "mono")
                if layout_name == "stereo" and len(pcm_float) > 0:
                    pcm_float = pcm_float[::2]

                # Skip first 2 samples (player ID)
                if len(pcm_float) > 2:
                    pcm_float = pcm_float[2:]

                # Calculate volume (RMS)
                volume = float(np.sqrt(np.mean(pcm_float**2)))

                # Update speaking state
                now = time.time()
                was_speaking = source_player_id in self._speaking_players
                self._speaking_players[source_player_id] = now

                # Fire on_player_speaks if this is a new speaker
                if not was_speaking:
                    speaking_player = self._world_state.get_player(source_player_id)
                    if speaking_player is not None:
                        for speak_cb in self._on_player_speaks_callbacks:
                            try:
                                await speak_cb(speaking_player)
                            except Exception as e:
                                logger.error(f"Error in on_player_speaks callback: {e}")

                # Fire on_audio callbacks
                audio_bytes = pcm_float.tobytes()
                for audio_cb in self._on_audio_callbacks:
                    try:
                        await audio_cb(source_player_id, volume, audio_bytes)
                    except Exception as e:
                        logger.error(f"Error in on_audio callback: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error receiving audio: {e}")
                break

    async def disconnect(self) -> None:
        """Disconnect from the server."""
        self._running = False

        if self._pathfinding_task:
            self._pathfinding_task.cancel()
            try:
                await self._pathfinding_task
            except asyncio.CancelledError:
                pass

        if self._peer_connection:
            await self._peer_connection.close()

        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass

    async def run(self) -> None:
        """Run the bot's main event loop.

        This method blocks until the bot disconnects.
        """
        self._running = True
        self._position_queue = asyncio.Queue()

        # Start position sender task
        position_sender_task = asyncio.create_task(self._send_position_updates())

        # Start speaking timeout checker
        speaking_checker_task = asyncio.create_task(self._check_speaking_timeouts())

        try:
            while self._running:
                await asyncio.sleep(0.05)
        finally:
            self._running = False
            position_sender_task.cancel()
            speaking_checker_task.cancel()
            try:
                await position_sender_task
            except asyncio.CancelledError:
                pass
            try:
                await speaking_checker_task
            except asyncio.CancelledError:
                pass
            await self.disconnect()

    async def _check_speaking_timeouts(self) -> None:
        """Check for players who stopped speaking."""
        while self._running:
            await asyncio.sleep(0.1)
            now = time.time()

            timed_out = []
            for player_id, last_time in list(self._speaking_players.items()):
                if now - last_time > self._speaking_timeout:
                    timed_out.append(player_id)

            for player_id in timed_out:
                del self._speaking_players[player_id]
                stopped_player = self._world_state.get_player(player_id)
                if stopped_player is not None:
                    for stop_cb in self._on_player_stops_speaking_callbacks:
                        try:
                            await stop_cb(stopped_player)
                        except Exception as e:
                            logger.error(
                                f"Error in on_player_stops_speaking callback: {e}"
                            )

    # Message handling

    async def _handle_data_channel_message(self, data: bytes) -> None:
        """Handle a message received via WebRTC data channel."""
        if len(data) < 1:
            return
        try:
            msg_type = MessageType(data[0])
        except ValueError:
            # Unknown message type - ignore it
            logger.debug(f"Ignoring unknown message type: {data[0]}")
            return
        payload = data[1:]
        await self._handle_server_message(msg_type, payload)

    async def _handle_server_message(
        self, msg_type: MessageType, payload: bytes
    ) -> None:
        """Handle a message from the server."""
        if msg_type == MessageType.WORLD_STATE:
            world_state_proto = deserialize_world_state(payload)

            # Convert to bot SDK types
            players = [
                PlayerState(
                    player_id=p.player_id,
                    x=p.x,
                    y=p.y,
                    is_muted=p.is_muted,
                    name=p.name,
                    level=p.level,
                )
                for p in world_state_proto.players
            ]
            self._world_state = WorldState(players=players)

            # Update our position from server if no pending moves
            if not self._pending_moves:
                for p in players:
                    if p.player_id == self.player_id:
                        self.x = p.x
                        self.y = p.y
                        break

            # Check for proximity changes
            await self._check_proximity_changes()

            # Fire callbacks
            for callback in self._on_world_state_callbacks:
                try:
                    await callback(self._world_state)
                except Exception as e:
                    logger.error(f"Error in on_world_state callback: {e}")

        elif msg_type == MessageType.POSITION_ACK:
            seq, server_x, server_y = deserialize_position_ack(payload)

            # Check if move was rejected
            acked_move = self._pending_moves.get(seq)
            move_rejected = False
            if acked_move:
                _, _, expected_x, expected_y = acked_move
                if server_x != expected_x or server_y != expected_y:
                    move_rejected = True

            # Remove acknowledged moves
            seqs_to_remove = [s for s in self._pending_moves if s <= seq]
            for s in seqs_to_remove:
                del self._pending_moves[s]

            if move_rejected:
                self._pending_moves.clear()

            self.x = server_x
            self.y = server_y

            # Replay remaining pending moves
            if self._pending_moves and self._level and not move_rejected:
                for move_seq in sorted(self._pending_moves.keys()):
                    dx, dy, _, _ = self._pending_moves[move_seq]
                    new_x = self.x + dx
                    new_y = self.y + dy
                    if self._level.is_walkable(new_x, new_y):
                        self.x = new_x
                        self.y = new_y

        elif msg_type == MessageType.PLAYER_JOINED:
            joined_id, joined_name = deserialize_player_joined(payload)
            for join_cb in self._on_player_joined_callbacks:
                try:
                    await join_cb(joined_id, joined_name)
                except Exception as e:
                    logger.error(f"Error in on_player_joined callback: {e}")

        elif msg_type == MessageType.PLAYER_LEFT:
            left_id = deserialize_player_left(payload)
            for leave_cb in self._on_player_left_callbacks:
                try:
                    await leave_cb(left_id)
                except Exception as e:
                    logger.error(f"Error in on_player_left callback: {e}")

        elif msg_type == MessageType.DOOR_TRANSITION:
            target_level, spawn_x, spawn_y = deserialize_door_transition(payload)
            # Update position
            self.x = spawn_x
            self.y = spawn_y
            self.current_level = target_level
            self._pending_moves.clear()
            # Note: Level data would need to be requested separately
            logger.info(f"Door transition to {target_level} at ({spawn_x}, {spawn_y})")

        elif msg_type == MessageType.PING:
            self._send_data_channel_message(MessageType.PONG, b"")

        elif msg_type == MessageType.WEBRTC_OFFER:
            # Renegotiation offer from server (new audio tracks)
            logger.debug("Received WEBRTC_OFFER for renegotiation")
            offer_sdp = deserialize_webrtc_offer(payload)
            await self._handle_renegotiation_offer(offer_sdp)

        elif msg_type == MessageType.AUDIO_TRACK_MAP:
            # Track mapping - bot doesn't need to process this currently
            logger.debug("Received AUDIO_TRACK_MAP")

    async def _handle_renegotiation_offer(self, offer_sdp: str) -> None:
        """Handle a renegotiation offer from the server."""
        if not self._peer_connection:
            return

        try:
            logger.debug("Processing renegotiation offer")
            # Set remote description (the new offer)
            await self._peer_connection.setRemoteDescription(
                RTCSessionDescription(sdp=offer_sdp, type="offer")
            )

            # Create and set answer
            answer = await self._peer_connection.createAnswer()
            await self._peer_connection.setLocalDescription(answer)

            # Send answer back to server via data channel
            answer_sdp = (
                self._peer_connection.localDescription.sdp
                if self._peer_connection.localDescription
                else ""
            )
            self._send_data_channel_message(
                MessageType.WEBRTC_ANSWER,
                serialize_webrtc_answer(answer_sdp),
            )
            logger.debug("Sent renegotiation answer")
        except Exception as e:
            logger.error(f"Renegotiation failed: {e}")

    async def _check_proximity_changes(self) -> None:
        """Check for players entering or leaving audio range."""
        current_nearby: set[int] = set()

        for p in self._world_state.players:
            if p.player_id == self.player_id:
                continue
            if p.level != self.current_level:
                continue

            dx = abs(p.x - self.x)
            dy = abs(p.y - self.y)
            distance = max(dx, dy)  # Chebyshev distance

            if distance <= AUDIO_MAX_DISTANCE:
                current_nearby.add(p.player_id)

        # Players who just entered range
        new_nearby = current_nearby - self._previous_nearby_players
        for nearby_id in new_nearby:
            nearby_player = self._world_state.get_player(nearby_id)
            if nearby_player is not None:
                for nearby_cb in self._on_player_nearby_callbacks:
                    try:
                        await nearby_cb(nearby_player)
                    except Exception as e:
                        logger.error(f"Error in on_player_nearby callback: {e}")

        # Players who just left range
        left_range = self._previous_nearby_players - current_nearby
        for left_id in left_range:
            left_player = self._world_state.get_player(left_id)
            if left_player is not None:
                for left_cb in self._on_player_left_range_callbacks:
                    try:
                        await left_cb(left_player)
                    except Exception as e:
                        logger.error(f"Error in on_player_left_range callback: {e}")

        self._previous_nearby_players = current_nearby

    def _send_data_channel_message(self, msg_type: MessageType, payload: bytes) -> None:
        """Send a message via WebRTC data channel."""
        if not self._webrtc_connected or self._data_channel is None:
            return
        try:
            message = bytes([msg_type]) + payload
            self._data_channel.send(message)
        except Exception:
            pass

    async def _send_position_updates(self) -> None:
        """Send position updates from the queue to the server."""
        while self._running:
            try:
                if self._position_queue is None:
                    await asyncio.sleep(0.1)
                    continue
                seq, x, y = await asyncio.wait_for(
                    self._position_queue.get(), timeout=0.1
                )
                if self._webrtc_connected:
                    payload = serialize_position_update(seq, x, y)
                    self._send_data_channel_message(
                        MessageType.POSITION_UPDATE, payload
                    )
            except asyncio.TimeoutError:
                continue

    # Movement methods

    async def move(self, direction: Direction) -> bool:
        """Move one tile in the given direction.

        Args:
            direction: Direction to move.

        Returns:
            True if move was valid, False if blocked.
        """
        if (
            not self._level
            or not self._webrtc_connected
            or self._position_queue is None
        ):
            return False

        dx, dy = direction.dx, direction.dy
        new_x = self.x + dx
        new_y = self.y + dy

        if not self._level.is_walkable(new_x, new_y):
            return False

        # Client-side prediction
        self._move_seq += 1
        seq = self._move_seq
        self._pending_moves[seq] = (dx, dy, new_x, new_y)
        self.x = new_x
        self.y = new_y

        # Queue position update
        self._position_queue.put_nowait((seq, new_x, new_y))

        return True

    async def move_to(
        self,
        x: int,
        y: int,
        step_delay: float = 0.1,
    ) -> bool:
        """Move to a specific position using pathfinding.

        Args:
            x: Target X coordinate.
            y: Target Y coordinate.
            step_delay: Delay between steps in seconds.

        Returns:
            True if reached target, False if no path found or interrupted.
        """
        if not self._level:
            return False

        path = find_path((self.x, self.y), (x, y), self._level)
        if path is None:
            return False

        # Skip the first position (current position)
        for px, py in path[1:]:
            if not self._running:
                return False

            # Calculate direction
            dx = px - self.x
            dy = py - self.y

            # Find matching direction
            direction = None
            for d in Direction:
                if d.dx == dx and d.dy == dy:
                    direction = d
                    break

            if direction is None:
                return False

            if not await self.move(direction):
                return False

            await asyncio.sleep(step_delay)

        return True

    # Position methods

    def get_position(self) -> tuple[int, int]:
        """Get current position.

        Returns:
            Tuple of (x, y) coordinates.
        """
        return (self.x, self.y)

    def get_world_state(self) -> WorldState:
        """Get current world state.

        Returns:
            Current world state with all visible players.
        """
        return self._world_state

    def get_level(self) -> Level | None:
        """Get current level data.

        Returns:
            Level object or None if not loaded.
        """
        return self._level

    # Audio methods

    async def speak_file(self, path: str | Path) -> None:
        """Play an audio file.

        Args:
            path: Path to WAV file.
        """
        if not self._audio_capture_track or self.is_muted:
            return

        source = FileAudioSource(path)
        self._audio_capture_track.queue_source(source)

    async def speak_pcm(
        self,
        samples: npt.NDArray[np.float32],
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        """Send raw PCM audio.

        Args:
            samples: Float32 mono audio samples.
            sample_rate: Sample rate of the audio.
        """
        if not self._audio_capture_track or self.is_muted:
            return

        source = PCMAudioSource(samples, sample_rate)
        source.finish()
        self._audio_capture_track.queue_source(source)

    def mute(self) -> None:
        """Mute the bot's audio output."""
        self.is_muted = True
        if self._audio_capture_track:
            self._audio_capture_track.set_muted(True)
        self._send_data_channel_message(
            MessageType.MUTE_STATUS, serialize_mute_status(True)
        )

    def unmute(self) -> None:
        """Unmute the bot's audio output."""
        self.is_muted = False
        if self._audio_capture_track:
            self._audio_capture_track.set_muted(False)
        self._send_data_channel_message(
            MessageType.MUTE_STATUS, serialize_mute_status(False)
        )

    def is_playing(self) -> bool:
        """Check if currently playing audio.

        Returns:
            True if audio is currently being played.
        """
        if not self._audio_capture_track:
            return False
        return self._audio_capture_track.is_playing()
