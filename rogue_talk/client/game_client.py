"""Main game client handling network and UI."""

from __future__ import annotations

import asyncio
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

        self.player_id, self.room_width, self.room_height, self.x, self.y = (
            deserialize_server_hello(payload)
        )
        return True

    async def run(self) -> None:
        """Main client loop."""
        self.running = True
        self._loop = asyncio.get_running_loop()
        self._audio_queue = asyncio.Queue()

        # Start audio if available
        await self._start_audio()

        # Start network receiver task
        receiver_task = asyncio.create_task(self._receive_messages())
        audio_sender_task = asyncio.create_task(self._send_audio_frames())

        try:
            with self.term.fullscreen(), self.term.cbreak(), self.term.hidden_cursor():
                self._render()
                render_counter = 0
                while self.running:
                    # Non-blocking input check
                    key = self.term.inkey(timeout=0.05)
                    if key:
                        await self._handle_input(key)

                    # Periodic render for mic level
                    render_counter += 1
                    if render_counter >= 4:
                        render_counter = 0
                        self._render()

                    # Let other tasks run
                    await asyncio.sleep(0)
        finally:
            self.running = False
            receiver_task.cancel()
            audio_sender_task.cancel()
            try:
                await receiver_task
            except asyncio.CancelledError:
                pass
            try:
                await audio_sender_task
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
            # Update our local position from server state
            for p in self.players:
                if p.player_id == self.player_id:
                    self.x = p.x
                    self.y = p.y
                    break
            self._render()

        elif msg_type == MessageType.PLAYER_JOINED:
            player_id, name = deserialize_player_joined(payload)
            # Will be updated in next WORLD_STATE
            self._render()

        elif msg_type == MessageType.PLAYER_LEFT:
            player_id = deserialize_player_left(payload)
            self.players = [p for p in self.players if p.player_id != player_id]
            if self.audio_playback:
                self.audio_playback.remove_player(player_id)
            self._render()

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
        if movement and self.writer:
            dx, dy = movement
            new_x = self.x + dx
            new_y = self.y + dy
            # Basic client-side validation
            if 0 < new_x < self.room_width - 1 and 0 < new_y < self.room_height - 1:
                self.x = new_x
                self.y = new_y
                await write_message(
                    self.writer,
                    MessageType.POSITION_UPDATE,
                    serialize_position_update(new_x, new_y),
                )
                self._render()

    async def _toggle_mute(self) -> None:
        """Toggle mute state."""
        self.is_muted = not self.is_muted
        if self.writer:
            await write_message(
                self.writer,
                MessageType.MUTE_STATUS,
                serialize_mute_status(self.is_muted),
            )
        if self.audio_capture:
            self.audio_capture.set_muted(self.is_muted)
        self._render()

    def _render(self) -> None:
        """Render the current game state."""
        mic_level = self.audio_capture.last_level if self.audio_capture else 0.0
        self.ui.render(
            self.room_width,
            self.room_height,
            self.players,
            self.player_id,
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

    def _on_audio_frame(self, opus_data: bytes, timestamp_ms: int) -> None:
        """Callback when audio frame is captured (called from audio thread)."""
        if self.is_muted or not self._loop or not self._audio_queue or not self.running:
            return

        # Thread-safe queue put
        self._loop.call_soon_threadsafe(
            self._audio_queue.put_nowait, (opus_data, timestamp_ms)
        )
