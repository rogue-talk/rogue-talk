"""Main game client handling network and UI."""

from __future__ import annotations

import asyncio
import socket
import struct
from asyncio import StreamReader, StreamWriter
from typing import TYPE_CHECKING, Any

from blessed import Terminal
from blessed.keyboard import Keystroke

from ..common.protocol import (
    AudioFrame,
    MessageType,
    PlayerInfo,
    deserialize_audio_frame,
    deserialize_player_joined,
    deserialize_player_left,
    deserialize_position_ack,
    deserialize_server_hello,
    deserialize_world_state,
    read_message,
    serialize_audio_frame,
    serialize_client_hello,
    serialize_mute_status,
    serialize_position_update,
    write_message,
)
from .input_handler import get_movement, is_mute_key, is_quit_key
from .level import Level
from .terminal_ui import TerminalUI

if TYPE_CHECKING:
    from .audio_capture import AudioCapture
    from .audio_playback import AudioPlayback


class GameClient:
    def __init__(self, host: str, port: int, name: str) -> None:
        self.host = host
        self.port = port
        self.name = name
        self.player_id: int = 0
        self.x: int = 0
        self.y: int = 0
        self.room_width: int = 0
        self.room_height: int = 0
        self.level: Level | None = None
        self.is_muted: bool = False
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

    async def connect(self) -> bool:
        """Connect to the server and complete handshake."""
        try:
            self.reader, self.writer = await asyncio.open_connection(
                self.host, self.port
            )
        except (ConnectionRefusedError, OSError) as e:
            print(f"Failed to connect: {e}")
            return False

        # Send CLIENT_HELLO
        await write_message(
            self.writer, MessageType.CLIENT_HELLO, serialize_client_hello(self.name)
        )

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

    async def _handle_input(self, key: Keystroke) -> None:
        """Handle keyboard input."""
        if is_quit_key(key):
            self.running = False
            return

        if is_mute_key(key):
            await self._toggle_mute()
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
        mic_level = self.audio_capture.last_level if self.audio_capture else 0.0
        self.ui.render(
            self.level,
            self.players,
            self.player_id,
            self.x,
            self.y,
            self.is_muted,
            mic_level,
        )

    async def _start_audio(self) -> None:
        """Start audio capture and playback if available."""
        try:
            from .audio_capture import AudioCapture
            from .audio_playback import AudioPlayback

            self.audio_playback = AudioPlayback()
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
