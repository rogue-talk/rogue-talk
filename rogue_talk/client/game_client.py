"""Main game client handling network and UI."""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import tempfile
import time
from asyncio import StreamReader, StreamWriter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from blessed import Terminal
from blessed.keyboard import Keystroke

from ..common.crypto import sign_challenge
from ..common.protocol import (
    AudioFrame,
    AuthResult,
    MessageType,
    PlayerInfo,
    deserialize_audio_frame,
    deserialize_auth_challenge,
    deserialize_auth_result,
    deserialize_door_transition,
    deserialize_level_pack_data,
    deserialize_player_joined,
    deserialize_player_left,
    deserialize_position_ack,
    deserialize_server_hello,
    deserialize_world_state,
    read_message,
    serialize_audio_frame,
    serialize_auth_response,
    serialize_level_pack_request,
    serialize_mute_status,
    serialize_position_update,
    write_message,
)
from ..audio.sound_loader import SoundCache
from ..common import tiles as tile_defs
from .identity import Identity, load_or_create_identity
from .input_handler import get_movement, is_mute_key, is_quit_key, is_show_names_key
from .level import DoorInfo, Level
from .level_pack import extract_level_pack, parse_doors
from .terminal_ui import TerminalUI
from .tile_sound_player import TileSoundPlayer

if TYPE_CHECKING:
    from .audio_capture import AudioCapture
    from .audio_playback import AudioPlayback


class GameClient:
    def __init__(self, host: str, port: int, name: str) -> None:
        self.host = host
        self.port = port
        self.name = name
        self.identity: Identity | None = None
        self.player_id: int = 0
        self.x: int = 0
        self.y: int = 0
        self.room_width: int = 0
        self.room_height: int = 0
        self.level: Level | None = None
        self.current_level: str = "main"
        self.is_muted: bool = False
        self.show_player_names: bool = False
        self.players: list[PlayerInfo] = []
        self.reader: StreamReader | None = None
        self.writer: StreamWriter | None = None
        self.running = False
        self._needs_render = True  # Flag to track when re-render is needed
        self._last_render_time = 0.0  # For periodic updates (mic level, animations)
        self.term: Any = Terminal()
        self.ui = TerminalUI(self.term)
        self.audio_capture: AudioCapture | None = None
        self.audio_playback: AudioPlayback | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._audio_queue: asyncio.Queue[tuple[bytes, int]] | None = None
        # Queue for outgoing position updates (non-blocking sends)
        self._position_queue: asyncio.Queue[tuple[int, int, int]] | None = None
        # Client-side prediction: track pending (unacked) moves
        self._move_seq: int = 0
        # seq -> (dx, dy, expected_x, expected_y)
        self._pending_moves: dict[int, tuple[int, int, int, int]] = {}
        # Temporary directory for level pack extraction
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        # Tile sound system
        self._sound_cache: SoundCache = SoundCache()
        self._tile_sound_player: TileSoundPlayer = TileSoundPlayer(self._sound_cache)
        # Other levels loaded for see-through portals
        self.other_levels: dict[str, Level] = {}

    async def connect(self) -> bool:
        """Connect to the server and complete handshake."""
        # Load or create identity
        self.identity = load_or_create_identity()

        try:
            self.reader, self.writer = await asyncio.open_connection(
                self.host, self.port
            )
        except (ConnectionRefusedError, OSError) as e:
            print(f"Failed to connect: {e}")
            return False

        # Wait for AUTH_CHALLENGE
        msg_type, payload = await read_message(self.reader)
        if msg_type != MessageType.AUTH_CHALLENGE:
            print("Unexpected response from server (expected AUTH_CHALLENGE)")
            return False

        nonce = deserialize_auth_challenge(payload)

        # Sign the challenge
        signature = sign_challenge(self.identity.private_key, nonce, self.name)

        # Send AUTH_RESPONSE
        await write_message(
            self.writer,
            MessageType.AUTH_RESPONSE,
            serialize_auth_response(self.identity.public_key, self.name, signature),
        )

        # Wait for AUTH_RESULT
        msg_type, payload = await read_message(self.reader)
        if msg_type != MessageType.AUTH_RESULT:
            print("Unexpected response from server (expected AUTH_RESULT)")
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
            print(
                f"Authentication failed: {error_messages.get(auth_result, 'Unknown error')}"
            )
            return False

        # Wait for SERVER_HELLO to learn which level we're in
        msg_type, payload = await read_message(self.reader)
        if msg_type != MessageType.SERVER_HELLO:
            print("Unexpected response from server")
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
        self.level = Level.from_bytes(level_data)
        self.current_level = level_name

        # Now request the correct level pack for doors and tiles
        await write_message(
            self.writer,
            MessageType.LEVEL_PACK_REQUEST,
            serialize_level_pack_request(level_name),
        )

        # Wait for LEVEL_PACK_DATA, handling other messages (like WORLD_STATE)
        while True:
            msg_type, payload = await read_message(self.reader)
            if msg_type == MessageType.LEVEL_PACK_DATA:
                break
            # Ignore other messages during initial connection

        tarball_data = deserialize_level_pack_data(payload)
        if not tarball_data:
            print("Server returned empty level pack")
            return False

        # Extract level pack to temp directory
        self._temp_dir = tempfile.TemporaryDirectory(prefix="rogue_talk_")
        extract_dir = Path(self._temp_dir.name)
        try:
            level_pack = extract_level_pack(tarball_data, extract_dir)
        except ValueError as e:
            print(f"Failed to extract level pack: {e}")
            return False

        # Load custom tiles if present
        if level_pack.tiles_path:
            tile_defs.reload_tiles(level_pack.tiles_path)

        # Set up sound assets directory
        self._sound_cache.set_assets_dir(level_pack.assets_dir)

        # Parse and set doors from level.json
        doors = parse_doors(level_pack.level_json_path)
        self.level.doors = doors

        # Load other levels for see-through portals
        await self._load_see_through_portal_levels()

        return True

    async def run(self) -> None:
        """Main client loop."""
        self.running = True
        self._loop = asyncio.get_running_loop()
        self._audio_queue = asyncio.Queue()
        self._position_queue = asyncio.Queue()

        # Start audio if available
        await self._start_audio()

        # Start network sender/receiver tasks
        receiver_task = asyncio.create_task(self._receive_messages())
        audio_sender_task = asyncio.create_task(self._send_audio_frames())
        position_sender_task = asyncio.create_task(self._send_position_updates())

        try:
            with self.term.fullscreen(), self.term.cbreak(), self.term.hidden_cursor():
                self._render()
                while self.running:
                    # Drain all pending input (process buffered keys immediately)
                    while True:
                        key = self.term.inkey(timeout=0)
                        if not key:
                            break
                        await self._handle_input(key)

                    # Render when something changed or periodically for animations/mic
                    now = time.monotonic()
                    if self._needs_render or (now - self._last_render_time) > 0.25:
                        self._render()
                        self._needs_render = False
                        self._last_render_time = now

                    # Sleep longer when idle to save CPU
                    await asyncio.sleep(0.05)
        finally:
            self.running = False
            receiver_task.cancel()
            audio_sender_task.cancel()
            position_sender_task.cancel()
            try:
                await receiver_task
            except asyncio.CancelledError:
                pass
            try:
                await audio_sender_task
            except asyncio.CancelledError:
                pass
            try:
                await position_sender_task
            except asyncio.CancelledError:
                pass
            await self._stop_audio()
            if self.writer:
                self.writer.close()
                try:
                    await self.writer.wait_closed()
                except Exception:
                    pass  # Connection may already be gone
            if self._temp_dir:
                self._temp_dir.cleanup()
            self.ui.cleanup()

    async def _receive_messages(self) -> None:
        """Receive and handle messages from server."""
        try:
            while self.running and self.reader:
                msg_type, payload = await read_message(self.reader)
                await self._handle_server_message(msg_type, payload)
        except (
            asyncio.IncompleteReadError,
            ConnectionResetError,
            BrokenPipeError,
            OSError,
        ):
            self.running = False

    async def _handle_server_message(
        self, msg_type: MessageType, payload: bytes
    ) -> None:
        """Handle a message from the server."""
        if msg_type == MessageType.WORLD_STATE:
            world_state = deserialize_world_state(payload)
            self.players = world_state.players
            # Only update our position from server if no pending moves
            # (otherwise we'd rubber-band while moves are in-flight)
            if not self._pending_moves:
                for p in self.players:
                    if p.player_id == self.player_id:
                        self.x = p.x
                        self.y = p.y
                        break
            self._needs_render = True

        elif msg_type == MessageType.POSITION_ACK:
            seq, server_x, server_y = deserialize_position_ack(payload)
            # Check if this move was rejected (position doesn't match expected)
            acked_move = self._pending_moves.get(seq)
            move_rejected = False
            if acked_move:
                _, _, expected_x, expected_y = acked_move
                if server_x != expected_x or server_y != expected_y:
                    move_rejected = True
            # Remove this move and all older moves from pending
            seqs_to_remove = [s for s in self._pending_moves if s <= seq]
            for s in seqs_to_remove:
                del self._pending_moves[s]
            # If move was rejected, clear all pending moves - they were sent with
            # wrong absolute positions and will also be rejected
            if move_rejected:
                self._pending_moves.clear()
            # Set position from server
            self.x = server_x
            self.y = server_y
            # Replay remaining pending moves (only if not rejected)
            if self._pending_moves and self.level and not move_rejected:
                for move_seq in sorted(self._pending_moves.keys()):
                    dx, dy, _, _ = self._pending_moves[move_seq]
                    new_x = self.x + dx
                    new_y = self.y + dy
                    if self.level.is_walkable(new_x, new_y):
                        self.x = new_x
                        self.y = new_y
            self._needs_render = True

        elif msg_type == MessageType.PLAYER_JOINED:
            player_id, name = deserialize_player_joined(payload)
            # Will be updated in next WORLD_STATE

        elif msg_type == MessageType.PLAYER_LEFT:
            player_id = deserialize_player_left(payload)
            self.players = [p for p in self.players if p.player_id != player_id]
            if self.audio_playback:
                self.audio_playback.remove_player(player_id)

        elif msg_type == MessageType.AUDIO_FRAME:
            frame = deserialize_audio_frame(payload)
            if self.audio_playback:
                self.audio_playback.receive_audio_frame(
                    frame.player_id, frame.timestamp_ms, frame.opus_data, frame.volume
                )

        elif msg_type == MessageType.DOOR_TRANSITION:
            await self._handle_door_transition(payload)

        elif msg_type == MessageType.PING:
            # Respond with PONG to keep connection alive
            if self.writer:
                try:
                    await write_message(self.writer, MessageType.PONG, b"")
                except (ConnectionResetError, BrokenPipeError):
                    pass

    async def _handle_door_transition(self, payload: bytes) -> None:
        """Handle a door transition to a new level."""
        target_level, spawn_x, spawn_y = deserialize_door_transition(payload)

        if not self.writer or not self.reader:
            return

        # Clear pending moves immediately to prevent rubber banding
        # (POSITION_ACK may arrive while we're loading the new level)
        self._pending_moves.clear()

        # Request the new level pack
        await write_message(
            self.writer,
            MessageType.LEVEL_PACK_REQUEST,
            serialize_level_pack_request(target_level),
        )

        # Wait for LEVEL_PACK_DATA, handling other messages that may arrive first
        # (e.g., POSITION_ACK sent by server after DOOR_TRANSITION)
        while True:
            msg_type, pack_payload = await read_message(self.reader)
            if msg_type == MessageType.LEVEL_PACK_DATA:
                break
            # Handle other messages normally while waiting
            await self._handle_server_message(msg_type, pack_payload)

        tarball_data = deserialize_level_pack_data(pack_payload)
        if not tarball_data:
            return

        # Clean up old temp directory and create new one
        if self._temp_dir:
            self._temp_dir.cleanup()
        self._temp_dir = tempfile.TemporaryDirectory(prefix="rogue_talk_")
        extract_dir = Path(self._temp_dir.name)

        try:
            level_pack = extract_level_pack(tarball_data, extract_dir)
        except ValueError:
            return

        # Load custom tiles if present
        if level_pack.tiles_path:
            tile_defs.reload_tiles(level_pack.tiles_path)
        else:
            # Reset to default tiles
            tile_defs.reload_tiles()

        # Update sound assets directory for new level pack
        self._sound_cache.set_assets_dir(level_pack.assets_dir)
        self._tile_sound_player.clear()

        # Load the new level from the pack
        with open(level_pack.level_path, encoding="utf-8") as f:
            level_content = f.read()

        # Parse level dimensions from content
        lines = level_content.rstrip("\n").split("\n")
        height = len(lines)
        width = max(len(line) for line in lines) if lines else 0

        # Create tiles list
        tiles: list[list[str]] = []
        for line in lines:
            row: list[str] = []
            for x in range(width):
                if x < len(line):
                    char = line[x]
                    # Convert spawn markers to floor
                    if char == "S":
                        char = "."
                else:
                    char = " "
                row.append(char)
            tiles.append(row)

        self.level = Level(width=width, height=height, tiles=tiles)
        self.room_width = width
        self.room_height = height
        self.current_level = target_level

        # Update position to spawn point (server will also send POSITION_ACK)
        self.x = spawn_x
        self.y = spawn_y

        # Clear pending moves since we're in a new level
        self._pending_moves.clear()
        self._needs_render = True

        # Parse and set doors for new level
        doors = parse_doors(level_pack.level_json_path)
        self.level.doors = doors

        # Load other levels for see-through portals
        await self._load_see_through_portal_levels()

    async def _load_see_through_portal_levels(self) -> None:
        """Load other levels needed for see-through portals."""
        if not self.level or not self.level.doors or not self.writer or not self.reader:
            return

        # Collect unique cross-level targets from see-through portals
        target_levels: set[str] = set()
        for door in self.level.doors:
            if door.see_through and door.target_level:
                target_levels.add(door.target_level)

        # Load each target level
        for target_level_name in target_levels:
            if target_level_name in self.other_levels:
                continue  # Already loaded

            # Request the level pack
            await write_message(
                self.writer,
                MessageType.LEVEL_PACK_REQUEST,
                serialize_level_pack_request(target_level_name),
            )

            # Wait for LEVEL_PACK_DATA, handling other messages
            while True:
                msg_type, payload = await read_message(self.reader)
                if msg_type == MessageType.LEVEL_PACK_DATA:
                    break
                # Handle other messages (but not door transitions to avoid recursion)
                if msg_type != MessageType.DOOR_TRANSITION:
                    await self._handle_server_message(msg_type, payload)

            tarball_data = deserialize_level_pack_data(payload)
            if not tarball_data:
                continue

            # Extract to a subdirectory for this level
            if not self._temp_dir:
                continue
            extract_dir = Path(self._temp_dir.name) / f"other_{target_level_name}"

            try:
                level_pack = extract_level_pack(tarball_data, extract_dir)
            except ValueError:
                continue

            # Load the level from the pack
            with open(level_pack.level_path, encoding="utf-8") as f:
                level_content = f.read()

            lines = level_content.rstrip("\n").split("\n")
            height = len(lines)
            width = max(len(line) for line in lines) if lines else 0

            tiles: list[list[str]] = []
            for line in lines:
                row: list[str] = []
                for x in range(width):
                    if x < len(line):
                        char = line[x]
                        if char == "S":
                            char = "."
                    else:
                        char = " "
                    row.append(char)
                tiles.append(row)

            other_level = Level(width=width, height=height, tiles=tiles)

            # Parse doors for the other level too (for rendering tile chars)
            other_doors = parse_doors(level_pack.level_json_path)
            other_level.doors = other_doors

            self.other_levels[target_level_name] = other_level

    async def _handle_input(self, key: Keystroke) -> None:
        """Handle keyboard input."""
        if is_quit_key(key):
            self.running = False
            return

        if is_mute_key(key):
            await self._toggle_mute()
            return

        if is_show_names_key(key):
            self.show_player_names = not self.show_player_names
            self._needs_render = True
            return

        movement = get_movement(key)
        if movement and self.writer and self.level and self._position_queue:
            dx, dy = movement
            new_x = self.x + dx
            new_y = self.y + dy
            # Client-side prediction: apply locally and track for reconciliation
            if self.level.is_walkable(new_x, new_y):
                self._move_seq += 1
                seq = self._move_seq
                self._pending_moves[seq] = (dx, dy, new_x, new_y)
                self.x = new_x
                self.y = new_y
                self._needs_render = True
                # Queue position update (non-blocking)
                self._position_queue.put_nowait((seq, new_x, new_y))
                # Play walking sound for the new tile
                self._tile_sound_player.on_player_move(new_x, new_y, self.level)

    async def _toggle_mute(self) -> None:
        """Toggle mute state."""
        self.is_muted = not self.is_muted
        self._needs_render = True
        if self.writer:
            # Send without blocking - write directly
            payload = serialize_mute_status(self.is_muted)
            length = 1 + len(payload)
            self.writer.write(struct.pack(">I", length))
            self.writer.write(struct.pack("B", MessageType.MUTE_STATUS))
            self.writer.write(payload)
        if self.audio_capture:
            self.audio_capture.set_muted(self.is_muted)

    def _render(self) -> None:
        """Render the current game state."""
        if not self.level:
            return

        # Update ambient tile sounds based on nearby tiles
        self._tile_sound_player.update_nearby_sounds(
            self.x, self.y, self.level, self.ui.has_line_of_sound
        )

        mic_level = self.audio_capture.last_level if self.audio_capture else 0.0
        self.ui.render(
            self.level,
            self.players,
            self.player_id,
            self.x,
            self.y,
            self.is_muted,
            mic_level,
            self.show_player_names,
            self.other_levels,
            self.current_level,
        )

    async def _start_audio(self) -> None:
        """Start audio capture and playback if available."""
        try:
            from .audio_capture import AudioCapture
            from .audio_playback import AudioPlayback

            self.audio_playback = AudioPlayback()
            self.audio_playback.tile_sound_player = self._tile_sound_player
            self.audio_playback.start()

            self.audio_capture = AudioCapture(self._on_audio_frame)
            self.audio_capture.start()
        except ImportError:
            # Audio modules not available
            pass
        except Exception as e:
            print(f"Audio init failed: {e}")

    async def _stop_audio(self) -> None:
        """Stop audio capture and playback."""
        if self.audio_capture:
            self.audio_capture.stop()
        if self.audio_playback:
            self.audio_playback.stop()

    async def _send_audio_frames(self) -> None:
        """Send audio frames from the queue to the server."""
        _frame_count = 0
        _bytes_sent = 0
        _last_log_time = 0.0
        _logger = logging.getLogger(__name__)

        while self.running:
            try:
                if self._audio_queue is None:
                    await asyncio.sleep(0.1)
                    continue
                opus_data, timestamp_ms = await asyncio.wait_for(
                    self._audio_queue.get(), timeout=0.1
                )
                if self.writer and not self.is_muted:
                    frame = AudioFrame(
                        player_id=self.player_id,
                        timestamp_ms=timestamp_ms,
                        volume=1.0,
                        opus_data=opus_data,
                    )
                    payload = serialize_audio_frame(frame)
                    await write_message(
                        self.writer,
                        MessageType.AUDIO_FRAME,
                        payload,
                    )
                    # Track bandwidth
                    _frame_count += 1
                    _bytes_sent += len(payload) + 5  # +5 for message header
                    now = time.time()
                    if now - _last_log_time >= 5.0:
                        kbps = (_bytes_sent * 8) / (now - _last_log_time) / 1000
                        _logger.info(
                            f"Audio: {_frame_count} frames, {kbps:.1f} kbps "
                            f"(avg {_bytes_sent // max(_frame_count, 1)} bytes/frame)"
                        )
                        _frame_count = 0
                        _bytes_sent = 0
                        _last_log_time = now
            except asyncio.TimeoutError:
                continue

    async def _send_position_updates(self) -> None:
        """Send position updates from the queue to the server."""
        while self.running:
            try:
                if self._position_queue is None:
                    await asyncio.sleep(0.1)
                    continue
                seq, x, y = await asyncio.wait_for(
                    self._position_queue.get(), timeout=0.1
                )
                if self.writer:
                    # Write without drain() to avoid blocking on slow networks
                    payload = serialize_position_update(seq, x, y)
                    length = 1 + len(payload)
                    self.writer.write(struct.pack(">I", length))
                    self.writer.write(struct.pack("B", MessageType.POSITION_UPDATE))
                    self.writer.write(payload)
                    # Don't await drain - let it buffer
            except asyncio.TimeoutError:
                continue

    def _on_audio_frame(self, opus_data: bytes, timestamp_ms: int) -> None:
        """Callback when audio frame is captured (called from audio thread)."""
        if self.is_muted or not self._loop or not self._audio_queue or not self.running:
            return

        # Thread-safe queue put
        self._loop.call_soon_threadsafe(
            self._audio_queue.put_nowait, (opus_data, timestamp_ms)
        )
