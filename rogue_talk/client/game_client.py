"""Main game client handling network and UI."""

from __future__ import annotations

import asyncio
import logging
import struct
import tempfile
import time
from asyncio import StreamReader, StreamWriter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiortc import RTCPeerConnection, RTCSessionDescription
from blessed import Terminal
from blessed.keyboard import Keystroke

# Set up logging to file (doesn't interfere with terminal UI)
# Use PID in filename so multiple clients on same machine have separate logs
import os as _os

_logger = logging.getLogger(__name__)
_debug_handler = logging.FileHandler(f"/tmp/rogue_talk_client_{_os.getpid()}.log")
_debug_handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
_logger.addHandler(_debug_handler)
_logger.setLevel(logging.DEBUG)

from ..audio.sound_loader import SoundCache
from ..audio.webrtc_tracks import AudioCaptureTrack, AudioPlaybackTrack
from ..common import tiles as tile_defs
from ..common.constants import MOVEMENT_TICK_INTERVAL
from ..common.crypto import sign_challenge
from ..common.protocol import (
    AuthResult,
    MessageType,
    PlayerInfo,
    deserialize_audio_track_map,
    deserialize_auth_challenge,
    deserialize_auth_result,
    deserialize_door_transition,
    deserialize_level_files_data,
    deserialize_level_manifest,
    deserialize_level_pack_data,
    deserialize_player_joined,
    deserialize_player_left,
    deserialize_position_ack,
    deserialize_server_hello,
    deserialize_webrtc_answer,
    deserialize_webrtc_offer,
    deserialize_world_state,
    read_message,
    serialize_auth_response,
    serialize_level_files_request,
    serialize_level_manifest_request,
    serialize_level_pack_request,
    serialize_mute_status,
    serialize_position_update,
    serialize_webrtc_answer,
    serialize_webrtc_offer,
    write_message,
)
from .identity import Identity, load_or_create_identity
from .input_handler import (
    get_movement,
    is_help_key,
    is_mute_key,
    is_player_table_key,
    is_quit_key,
    is_show_names_key,
)
from .level import Level
from .level_cache import cache_received_files, get_cached_files
from .level_pack import (
    LevelPack,
    create_level_pack_from_dir,
    extract_level_pack,
    parse_doors,
    parse_streams,
    write_files_to_dir,
)
from .stream_player import StreamPlayer
from .terminal_ui import TerminalUI
from .tile_sound_player import TileSoundPlayer

if TYPE_CHECKING:
    from aiortc import RTCDataChannel

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
        self.show_player_names: bool = True
        self.show_player_table: bool = False
        self.show_help: bool = False
        self.players: list[PlayerInfo] = []
        # TCP connection (only used for signaling)
        self.reader: StreamReader | None = None
        self.writer: StreamWriter | None = None
        # WebRTC connection
        self.peer_connection: RTCPeerConnection | None = None
        self.data_channel: RTCDataChannel | None = None
        self.webrtc_connected: bool = False
        # WebRTC audio
        self.audio_capture_track: AudioCaptureTrack | None = None
        # Multiple playback tracks: source_player_id -> AudioPlaybackTrack
        self.audio_playback_tracks: dict[int, AudioPlaybackTrack] = {}
        # Track MID -> source_player_id mapping (from server)
        self._track_map: dict[str, int] = {}
        # Player names cached for when audio_playback is created
        self._pending_player_names: dict[int, str] = {}
        self.running = False
        self._needs_render = True  # Flag to track when re-render is needed
        self._last_render_time = 0.0  # For periodic updates (mic level, animations)
        self.term: Any = Terminal()
        self.ui = TerminalUI(self.term)
        self.audio_capture: AudioCapture | None = None
        self.audio_playback: AudioPlayback | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # Queue for outgoing position updates (non-blocking sends)
        self._position_queue: asyncio.Queue[tuple[int, int, int]] | None = None
        # Client-side prediction: track pending (unacked) moves
        self._move_seq: int = 0
        # seq -> (dx, dy, expected_x, expected_y)
        self._pending_moves: dict[int, tuple[int, int, int, int]] = {}
        # Movement rate limiting (matches server's MOVEMENT_TICK_INTERVAL)
        self._last_move_time: float = 0.0
        # Temporary directory for level pack extraction
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        # Tile sound system
        self._sound_cache: SoundCache = SoundCache()
        self._tile_sound_player: TileSoundPlayer = TileSoundPlayer(self._sound_cache)
        # Audio stream player (for radio streams at map locations)
        self._stream_player: StreamPlayer = StreamPlayer()
        # Other levels loaded for see-through portals
        self.other_levels: dict[str, Level] = {}
        # WebRTC connection events
        self._data_channel_ready = asyncio.Event()
        self._connection_closed = asyncio.Event()
        # Pending futures for level caching protocol
        self._pending_manifest_future: asyncio.Future[bytes] | None = None
        self._pending_files_future: asyncio.Future[bytes] | None = None

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

        # Request level files using content-addressed caching
        level_pack = await self._request_level_cached_tcp(level_name)
        if level_pack is None:
            print("Failed to load level pack")
            return False

        # Load custom tiles if present
        if level_pack.tiles_path:
            tile_defs.reload_tiles(level_pack.tiles_path)

        # Set up sound assets directory
        self._sound_cache.set_assets_dir(level_pack.assets_dir)

        # Parse and set doors from level.json
        doors = parse_doors(level_pack.level_json_path)
        self.level.doors = doors

        # Parse and set streams from level.json
        streams = parse_streams(level_pack.level_json_path)
        self.level.streams = streams

        # Set up WebRTC connection
        if not await self._setup_webrtc():
            print("Failed to establish WebRTC connection")
            return False

        # Load other levels for see-through portals (via data channel now)
        await self._load_see_through_portal_levels()

        return True

    async def _request_level_cached_tcp(self, level_name: str) -> LevelPack | None:
        """Request level files via TCP using content-addressed caching.

        This is used during initial connection before WebRTC is established.
        """
        if not self.writer or not self.reader:
            return None

        # Request manifest
        await write_message(
            self.writer,
            MessageType.LEVEL_MANIFEST_REQUEST,
            serialize_level_manifest_request(level_name),
        )

        # Wait for LEVEL_MANIFEST
        while True:
            msg_type, payload = await read_message(self.reader)
            if msg_type == MessageType.LEVEL_MANIFEST:
                break
            # Ignore other messages during initial connection

        manifest = deserialize_level_manifest(payload)
        if not manifest:
            print("Server returned empty manifest")
            return None

        # Check local cache
        cached_files, missing_files = get_cached_files(level_name, manifest)
        cached_count = len(cached_files)
        total_count = len(manifest)

        if missing_files:
            # Request missing files from server
            await write_message(
                self.writer,
                MessageType.LEVEL_FILES_REQUEST,
                serialize_level_files_request(level_name, missing_files),
            )

            # Wait for LEVEL_FILES_DATA
            while True:
                msg_type, payload = await read_message(self.reader)
                if msg_type == MessageType.LEVEL_FILES_DATA:
                    break
                # Ignore other messages

            new_files = deserialize_level_files_data(payload)

            # Cache the new files
            cache_received_files(level_name, manifest, new_files)

            # Combine with cached files
            all_files = {**cached_files, **new_files}
            print(
                f"Level {level_name}: {cached_count}/{total_count} cached, "
                f"downloaded {len(new_files)} files"
            )
        else:
            all_files = cached_files
            print(f"Level {level_name}: {cached_count}/{total_count} files from cache")

        # Write all files to temp directory
        self._temp_dir = tempfile.TemporaryDirectory(prefix="rogue_talk_")
        extract_dir = Path(self._temp_dir.name)
        write_files_to_dir(all_files, extract_dir)

        try:
            return create_level_pack_from_dir(extract_dir)
        except ValueError as e:
            print(f"Failed to create level pack: {e}")
            return None

    async def _setup_webrtc(self) -> bool:
        """Set up WebRTC peer connection with the server."""
        if not self.writer or not self.reader:
            return False

        # Create peer connection
        self.peer_connection = RTCPeerConnection()
        pc = self.peer_connection

        # Create audio capture track for sending voice
        self.audio_capture_track = AudioCaptureTrack()
        pc.addTrack(self.audio_capture_track)

        # Create data channel for game messages
        self.data_channel = pc.createDataChannel("game", ordered=True)

        @self.data_channel.on("open")
        def on_open() -> None:
            print("Data channel opened")
            self._data_channel_ready.set()

        @self.data_channel.on("message")
        def on_message(message: bytes | str) -> None:
            if isinstance(message, str):
                message = message.encode("utf-8")
            asyncio.create_task(self._handle_data_channel_message(message))

        # Handle incoming audio track from server
        @pc.on("track")
        def on_track(track: Any) -> None:
            if track.kind == "audio":
                _logger.debug(f"on_track fired, track_map={self._track_map}")
                # Find which transceiver this track belongs to
                source_player_id = self._get_source_player_for_track(track)
                if source_player_id is not None:
                    _logger.debug(f"Matched track to player {source_player_id}")
                    self._setup_playback_track(source_player_id, track)
                else:
                    _logger.debug("Could not match track to player")

        # Handle connection state changes
        @pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            state = pc.connectionState
            _logger.info(f"WebRTC connection state: {state}")
            if state in ("failed", "closed", "disconnected"):
                self._connection_closed.set()
                self.running = False
            elif state == "connected":
                self.webrtc_connected = True

        # Create and send offer
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)

        offer_sdp = pc.localDescription.sdp if pc.localDescription else ""
        await write_message(
            self.writer,
            MessageType.WEBRTC_OFFER,
            serialize_webrtc_offer(offer_sdp),
        )

        # Wait for answer
        msg_type, payload = await read_message(self.reader)
        if msg_type != MessageType.WEBRTC_ANSWER:
            print(f"Expected WEBRTC_ANSWER, got {msg_type}")
            return False

        answer_sdp = deserialize_webrtc_answer(payload)
        await pc.setRemoteDescription(
            RTCSessionDescription(sdp=answer_sdp, type="answer")
        )

        # Wait for data channel to be ready with timeout
        try:
            await asyncio.wait_for(self._data_channel_ready.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            print("Timeout waiting for WebRTC data channel")
            return False

        # Mark as WebRTC connected (data channel is ready)
        self.webrtc_connected = True

        print("WebRTC connection established, closing TCP signaling")

        # Close TCP connection (signaling complete)
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except Exception:
            pass
        self.reader = None
        self.writer = None

        return True

    def _get_source_player_for_track(self, track: Any) -> int | None:
        """Find the source player ID for an incoming track using the track map."""
        if not self.peer_connection:
            return None

        # Find the transceiver that has this track as its receiver's track
        for transceiver in self.peer_connection.getTransceivers():
            if transceiver.receiver and transceiver.receiver.track == track:
                mid = transceiver.mid
                if mid and mid in self._track_map:
                    return self._track_map[mid]
        return None

    def _setup_playback_track(self, source_player_id: int, track: Any) -> None:
        """Set up a playback track for a source player."""
        # Create new playback track if needed
        if source_player_id not in self.audio_playback_tracks:
            playback = AudioPlaybackTrack(source_player_id)
            self.audio_playback_tracks[source_player_id] = playback

        # Set the track and start receiving
        playback = self.audio_playback_tracks[source_player_id]
        playback.set_track(track)
        asyncio.create_task(playback.start())

        # Update AudioPlayback to know about this track
        if self.audio_playback:
            self.audio_playback.add_playback_track(source_player_id, playback)

    async def _handle_renegotiation_offer(self, offer_sdp: str) -> None:
        """Handle a renegotiation offer from the server."""
        if not self.peer_connection:
            return

        try:
            _logger.debug("Processing renegotiation offer")
            # Set remote description (the new offer)
            await self.peer_connection.setRemoteDescription(
                RTCSessionDescription(sdp=offer_sdp, type="offer")
            )

            # Create and set answer
            answer = await self.peer_connection.createAnswer()
            await self.peer_connection.setLocalDescription(answer)

            # Send answer back to server via data channel
            answer_sdp = (
                self.peer_connection.localDescription.sdp
                if self.peer_connection.localDescription
                else ""
            )
            self._send_data_channel_message(
                MessageType.WEBRTC_ANSWER,
                serialize_webrtc_answer(answer_sdp),
            )
            _logger.debug("Sent renegotiation answer")
        except Exception as e:
            _logger.error(f"Renegotiation failed: {e}")

    async def run(self) -> None:
        """Main client loop."""
        self.running = True
        self._loop = asyncio.get_running_loop()
        self._position_queue = asyncio.Queue()

        # Start audio capture (feeds into WebRTC audio track)
        await self._start_audio()

        # Initialize audio playback with our known position
        if self.audio_playback:
            self.audio_playback.update_positions(self.x, self.y, {})

        # Start position sender task (uses data channel)
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
            position_sender_task.cancel()
            try:
                await position_sender_task
            except asyncio.CancelledError:
                pass
            await self._stop_audio()

            # Stop all audio playback tracks
            for playback in self.audio_playback_tracks.values():
                await playback.stop()
            self.audio_playback_tracks.clear()

            # Close WebRTC peer connection
            if self.peer_connection:
                await self.peer_connection.close()

            # Close TCP if still open
            if self.writer:
                self.writer.close()
                try:
                    await self.writer.wait_closed()
                except Exception:
                    pass  # Connection may already be gone
            if self._temp_dir:
                self._temp_dir.cleanup()
            self.ui.cleanup()

    async def _handle_data_channel_message(self, data: bytes) -> None:
        """Handle a message received via WebRTC data channel."""
        if len(data) < 1:
            return
        msg_type = MessageType(data[0])
        payload = data[1:]
        await self._handle_server_message(msg_type, payload)

    async def _receive_messages(self) -> None:
        """Receive and handle messages from server (legacy TCP, not used with WebRTC)."""
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
            # Update audio playback with player names and positions
            # Always cache player names (needed before audio_playback exists)
            names = {p.player_id: p.name for p in self.players}
            self._pending_player_names = names

            if self.audio_playback:
                self.audio_playback.update_player_names(names)
                positions = {p.player_id: (p.x, p.y) for p in self.players}
                self.audio_playback.update_positions(self.x, self.y, positions)
                # Add any pending tracks
                for player_id, track in self.audio_playback_tracks.items():
                    self.audio_playback.add_playback_track(player_id, track)
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
            self._needs_render = True

        # AUDIO_FRAME is not used with WebRTC - audio comes via track

        elif msg_type == MessageType.DOOR_TRANSITION:
            await self._handle_door_transition(payload)

        elif msg_type == MessageType.PING:
            # Respond with PONG to keep connection alive
            self._send_data_channel_message(MessageType.PONG, b"")

        elif msg_type == MessageType.LEVEL_PACK_DATA:
            # Handle level pack data (for door transitions)
            if (
                hasattr(self, "_pending_level_pack_future")
                and self._pending_level_pack_future
            ):
                self._pending_level_pack_future.set_result(payload)

        elif msg_type == MessageType.LEVEL_MANIFEST:
            # Handle level manifest data (for cached level loading)
            if self._pending_manifest_future:
                self._pending_manifest_future.set_result(payload)

        elif msg_type == MessageType.LEVEL_FILES_DATA:
            # Handle level files data (for cached level loading)
            if self._pending_files_future:
                self._pending_files_future.set_result(payload)

        elif msg_type == MessageType.WEBRTC_OFFER:
            # Renegotiation offer from server (new audio tracks)
            _logger.debug("Received WEBRTC_OFFER for renegotiation")
            offer_sdp = deserialize_webrtc_offer(payload)
            await self._handle_renegotiation_offer(offer_sdp)

        elif msg_type == MessageType.AUDIO_TRACK_MAP:
            # Update track MID -> source player ID mapping
            self._track_map = deserialize_audio_track_map(payload)
            _logger.debug(f"Received AUDIO_TRACK_MAP: {self._track_map}")

    def _send_data_channel_message(self, msg_type: MessageType, payload: bytes) -> None:
        """Send a message via WebRTC data channel."""
        if not self.webrtc_connected or self.data_channel is None:
            return
        try:
            message = bytes([msg_type]) + payload
            self.data_channel.send(message)
        except Exception:
            pass

    async def _request_level_pack(self, level_name: str) -> bytes | None:
        """Request a level pack via data channel and wait for response."""
        if not self.webrtc_connected:
            return None

        # Create a future to wait for the response
        self._pending_level_pack_future: asyncio.Future[bytes] = asyncio.Future()

        # Send request
        self._send_data_channel_message(
            MessageType.LEVEL_PACK_REQUEST,
            serialize_level_pack_request(level_name),
        )

        # Wait for response with timeout
        try:
            payload = await asyncio.wait_for(
                self._pending_level_pack_future, timeout=10.0
            )
            return deserialize_level_pack_data(payload)
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending_level_pack_future = None  # type: ignore[assignment]

    async def _request_level_cached_dc(
        self, level_name: str, extract_dir: Path
    ) -> LevelPack | None:
        """Request level files via data channel using content-addressed caching.

        This is used during gameplay for door transitions and portal level loading.
        """
        if not self.webrtc_connected:
            return None

        # Request manifest
        self._pending_manifest_future = asyncio.Future()
        self._send_data_channel_message(
            MessageType.LEVEL_MANIFEST_REQUEST,
            serialize_level_manifest_request(level_name),
        )

        # Wait for manifest
        try:
            payload = await asyncio.wait_for(
                self._pending_manifest_future, timeout=10.0
            )
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending_manifest_future = None

        manifest = deserialize_level_manifest(payload)
        if not manifest:
            return None

        # Check local cache
        cached_files, missing_files = get_cached_files(level_name, manifest)
        cached_count = len(cached_files)
        total_count = len(manifest)

        if missing_files:
            # Request missing files from server
            self._pending_files_future = asyncio.Future()
            self._send_data_channel_message(
                MessageType.LEVEL_FILES_REQUEST,
                serialize_level_files_request(level_name, missing_files),
            )

            # Wait for files
            try:
                payload = await asyncio.wait_for(
                    self._pending_files_future, timeout=10.0
                )
            except asyncio.TimeoutError:
                return None
            finally:
                self._pending_files_future = None

            new_files = deserialize_level_files_data(payload)

            # Cache the new files
            cache_received_files(level_name, manifest, new_files)

            # Combine with cached files
            all_files = {**cached_files, **new_files}
            _logger.info(
                f"Level {level_name}: {cached_count}/{total_count} cached, "
                f"downloaded {len(new_files)} files"
            )
        else:
            all_files = cached_files
            _logger.info(
                f"Level {level_name}: {cached_count}/{total_count} files from cache"
            )

        # Write all files to directory
        write_files_to_dir(all_files, extract_dir)

        try:
            return create_level_pack_from_dir(extract_dir)
        except ValueError:
            return None

    async def _handle_door_transition(self, payload: bytes) -> None:
        """Handle a door transition to a new level."""
        target_level, spawn_x, spawn_y = deserialize_door_transition(payload)

        if not self.webrtc_connected:
            return

        # Clear pending moves immediately to prevent rubber banding
        # (POSITION_ACK may arrive while we're loading the new level)
        self._pending_moves.clear()

        # Clean up old temp directory and create new one
        if self._temp_dir:
            self._temp_dir.cleanup()
        self._temp_dir = tempfile.TemporaryDirectory(prefix="rogue_talk_")
        extract_dir = Path(self._temp_dir.name)

        # Request level files using content-addressed caching
        level_pack = await self._request_level_cached_dc(target_level, extract_dir)
        if level_pack is None:
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
        self._stream_player.clear()

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

        # Parse and set streams for new level
        streams = parse_streams(level_pack.level_json_path)
        self.level.streams = streams

        # Load other levels for see-through portals
        await self._load_see_through_portal_levels()

    async def _load_see_through_portal_levels(self) -> None:
        """Load other levels needed for see-through portals."""
        if not self.level or not self.level.doors or not self.webrtc_connected:
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

            if not self._temp_dir:
                continue
            extract_dir = Path(self._temp_dir.name) / f"other_{target_level_name}"

            # Request level files using content-addressed caching
            level_pack = await self._request_level_cached_dc(
                target_level_name, extract_dir
            )
            if level_pack is None:
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

        if is_player_table_key(key):
            self.show_player_table = not self.show_player_table
            self._needs_render = True
            return

        if is_help_key(key):
            self.show_help = not self.show_help
            self._needs_render = True
            return

        movement = get_movement(key)
        if movement and self.webrtc_connected and self.level and self._position_queue:
            # Rate limit movement (max 1 tile per tick interval)
            now = time.monotonic()
            if now - self._last_move_time < MOVEMENT_TICK_INTERVAL:
                return  # Too fast, ignore this input

            dx, dy = movement
            new_x = self.x + dx
            new_y = self.y + dy
            # Client-side prediction: apply locally and track for reconciliation
            if self.level.is_walkable(new_x, new_y):
                self._last_move_time = now
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
        # Send mute status via data channel
        self._send_data_channel_message(
            MessageType.MUTE_STATUS, serialize_mute_status(self.is_muted)
        )
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

        # Update audio streams based on nearby stream sources
        self._stream_player.update_streams(self.x, self.y, self.level)

        # Get mic level from WebRTC audio track or legacy capture
        if self.audio_capture_track:
            mic_level = self.audio_capture_track.last_level
        elif self.audio_capture:
            mic_level = self.audio_capture.last_level
        else:
            mic_level = 0.0

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
            self.show_player_table,
            self.show_help,
        )

    async def _start_audio(self) -> None:
        """Start audio capture and playback if available."""
        try:
            from .audio_capture import AudioCapture
            from .audio_playback import AudioPlayback

            # Start tile sounds (has its own "environment" output stream)
            self._tile_sound_player.start()

            # Start audio stream player (has its own "radio" output stream)
            self._stream_player.start()

            # Start voice playback (per-player streams)
            self.audio_playback = AudioPlayback()
            self.audio_playback.start()

            # Set cached player names before adding tracks (so sinks get correct names)
            if self._pending_player_names:
                self.audio_playback.update_player_names(self._pending_player_names)

            # Add any tracks that arrived before audio_playback was ready
            for player_id, track in self.audio_playback_tracks.items():
                self.audio_playback.add_playback_track(player_id, track)

            # Start capture - feed audio to WebRTC track
            self.audio_capture = AudioCapture(self._on_audio_frame)
            self.audio_capture.start()
        except ImportError:
            # Audio modules not available
            pass
        except Exception as e:
            _logger.error(f"Audio init failed: {e}")

    async def _stop_audio(self) -> None:
        """Stop audio capture and playback."""
        if self.audio_capture:
            self.audio_capture.stop()
        if self.audio_playback:
            self.audio_playback.stop()
        self._tile_sound_player.stop()
        self._stream_player.stop()

    async def _send_position_updates(self) -> None:
        """Send position updates from the queue to the server via data channel."""
        while self.running:
            try:
                if self._position_queue is None:
                    await asyncio.sleep(0.1)
                    continue
                seq, x, y = await asyncio.wait_for(
                    self._position_queue.get(), timeout=0.1
                )
                if self.webrtc_connected:
                    payload = serialize_position_update(seq, x, y)
                    self._send_data_channel_message(
                        MessageType.POSITION_UPDATE, payload
                    )
            except asyncio.TimeoutError:
                continue

    def _on_audio_frame(self, pcm_data: Any, timestamp_ms: int) -> None:
        """Callback when audio frame is captured (called from audio thread).

        With WebRTC, we feed the raw PCM data to the audio capture track
        instead of encoding to Opus (WebRTC handles encoding).
        """
        if self.is_muted or not self._loop or not self.running:
            return

        # Feed audio to WebRTC track (thread-safe)
        if self.audio_capture_track:
            self.audio_capture_track.feed_audio(pcm_data)
