"""Main game client handling network and UI."""

from __future__ import annotations

import asyncio
import socket
import struct
import tempfile
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
from .level import Level
from .level_pack import extract_level_pack
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
        self.is_muted: bool = False
        self.show_player_names: bool = False
        self.players: list[PlayerInfo] = []
        self.reader: StreamReader | None = None
        self.writer: StreamWriter | None = None
        self.running = False
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
        self._pending_moves: dict[int, tuple[int, int]] = {}  # seq -> (dx, dy)
        # Temporary directory for level pack extraction
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        # Tile sound system
        self._sound_cache: SoundCache = SoundCache()
        self._tile_sound_player: TileSoundPlayer = TileSoundPlayer(self._sound_cache)

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

        # Request level pack first
        await write_message(
            self.writer,
            MessageType.LEVEL_PACK_REQUEST,
            serialize_level_pack_request("main"),
        )

        # Wait for LEVEL_PACK_DATA
        msg_type, payload = await read_message(self.reader)
        if msg_type != MessageType.LEVEL_PACK_DATA:
            print("Unexpected response from server (expected LEVEL_PACK_DATA)")
            return False

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

        # Wait for SERVER_HELLO
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
        ) = deserialize_server_hello(payload)
        self.level = Level.from_bytes(level_data)
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
                    had_input = False
                    while True:
                        key = self.term.inkey(timeout=0)
                        if not key:
                            break
                        had_input = True
                        await self._handle_input(key)

                    # Render every frame (state may have changed from network)
                    self._render()

                    # Sleep to target ~60fps and let async tasks run
                    await asyncio.sleep(0.016)
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
            if self._temp_dir:
                self._temp_dir.cleanup()
            self.ui.cleanup()

    async def _receive_messages(self) -> None:
        """Receive and handle messages from server."""
        try:
            while self.running and self.reader:
                msg_type, payload = await read_message(self.reader)
                await self._handle_server_message(msg_type, payload)
        except (asyncio.IncompleteReadError, ConnectionResetError):
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
            # Don't render here - let main loop handle it to avoid render storms

        elif msg_type == MessageType.POSITION_ACK:
            seq, server_x, server_y = deserialize_position_ack(payload)
            # Remove this move and all older moves from pending
            seqs_to_remove = [s for s in self._pending_moves if s <= seq]
            for s in seqs_to_remove:
                del self._pending_moves[s]
            # Reconcile: replay pending moves from server position
            self.x = server_x
            self.y = server_y
            if self._pending_moves and self.level:
                for move_seq in sorted(self._pending_moves.keys()):
                    dx, dy = self._pending_moves[move_seq]
                    new_x = self.x + dx
                    new_y = self.y + dy
                    if self.level.is_walkable(new_x, new_y):
                        self.x = new_x
                        self.y = new_y
            # Don't render here - let main loop handle it to avoid render storms

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

    async def _handle_door_transition(self, payload: bytes) -> None:
        """Handle a door transition to a new level."""
        target_level, spawn_x, spawn_y = deserialize_door_transition(payload)

        if not self.writer or not self.reader:
            return

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

        # Update position to spawn point (server will also send POSITION_ACK)
        self.x = spawn_x
        self.y = spawn_y

        # Clear pending moves since we're in a new level
        self._pending_moves.clear()

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
                self._pending_moves[seq] = (dx, dy)
                self.x = new_x
                self.y = new_y
                # Queue position update (non-blocking)
                self._position_queue.put_nowait((seq, new_x, new_y))
                # Play walking sound for the new tile
                self._tile_sound_player.on_player_move(new_x, new_y, self.level)

    async def _toggle_mute(self) -> None:
        """Toggle mute state."""
        self.is_muted = not self.is_muted
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
                    await write_message(
                        self.writer,
                        MessageType.AUDIO_FRAME,
                        serialize_audio_frame(frame),
                    )
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
