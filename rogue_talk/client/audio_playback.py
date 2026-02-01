"""Audio playback with per-player streams (mixed by PipeWire/PulseAudio)."""

from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt
import sounddevice as sd

from ..common.constants import CHANNELS, FRAME_SIZE, SAMPLE_RATE

# Set application name for PulseAudio/PipeWire (before any streams are created)
os.environ.setdefault("PULSE_PROP_application.name", "rogue_talk")

if TYPE_CHECKING:
    from ..audio.webrtc_tracks import AudioPlaybackTrack
    from .tile_sound_player import TileSoundPlayer

# Debug logging to file (doesn't interfere with terminal UI)
# Use PID in filename so multiple clients on same machine have separate logs
_logger = logging.getLogger(__name__)
_debug_handler = logging.FileHandler(f"/tmp/rogue_talk_audio_{os.getpid()}.log")
_debug_handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
_logger.addHandler(_debug_handler)
_logger.setLevel(logging.DEBUG)


class PlayerAudioStream:
    """Audio output stream for a single player's voice."""

    # Buffer settings
    MIN_BUFFER = FRAME_SIZE * 5  # 100ms before starting playback
    MAX_BUFFER = FRAME_SIZE * 15  # 300ms max buffer

    def __init__(self, player_id: int, player_name: str = "") -> None:
        self.player_id = player_id
        self.player_name = player_name or f"player_{player_id}"
        self._ring_buffer = np.zeros(self.MAX_BUFFER * 2, dtype=np.float32)
        self._write_pos = 0
        self._read_pos = 0
        self._started = False
        self._stream: sd.OutputStream | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start the audio output stream."""
        try:
            # Set stream name for PulseAudio/PipeWire
            os.environ["PULSE_PROP_media.name"] = f"player:{self.player_name}"
            self._stream = sd.OutputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=np.float32,
                blocksize=FRAME_SIZE,
                callback=self._callback,
            )
            self._stream.start()
        except Exception as e:
            _logger.error(
                f"Failed to start audio stream for player {self.player_id}: {e}"
            )

    def stop(self) -> None:
        """Stop the audio output stream."""
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def feed_audio(self, pcm_data: npt.NDArray[np.float32], volume: float) -> None:
        """Feed audio data into the ring buffer (thread-safe)."""
        samples = pcm_data.flatten() * volume
        sample_len = len(samples)

        with self._lock:
            buf_size = len(self._ring_buffer)

            # Check available space
            used = (self._write_pos - self._read_pos) % buf_size
            available = buf_size - used - 1
            if sample_len > available:
                # Discard oldest data
                discard = sample_len - available
                self._read_pos = (self._read_pos + discard) % buf_size

            # Write to ring buffer
            write_pos = self._write_pos
            end_pos = write_pos + sample_len
            if end_pos <= buf_size:
                self._ring_buffer[write_pos:end_pos] = samples
            else:
                first = buf_size - write_pos
                self._ring_buffer[write_pos:buf_size] = samples[:first]
                self._ring_buffer[: end_pos - buf_size] = samples[first:]
            self._write_pos = end_pos % buf_size

    def _callback(
        self,
        outdata: npt.NDArray[np.float32],
        frames: int,
        time_info: Any,
        status: sd.CallbackFlags,
    ) -> None:
        """Sounddevice callback."""
        with self._lock:
            buf_size = len(self._ring_buffer)
            buffer_len = (self._write_pos - self._read_pos) % buf_size

            if not self._started:
                if buffer_len >= self.MIN_BUFFER:
                    self._started = True
                else:
                    outdata[:] = 0
                    return

            if buffer_len >= FRAME_SIZE:
                read_pos = self._read_pos
                end_pos = read_pos + FRAME_SIZE
                if end_pos <= buf_size:
                    outdata[:, 0] = self._ring_buffer[read_pos:end_pos]
                else:
                    first = buf_size - read_pos
                    outdata[:first, 0] = self._ring_buffer[read_pos:buf_size]
                    outdata[first:, 0] = self._ring_buffer[: end_pos - buf_size]
                self._read_pos = end_pos % buf_size
            else:
                # Underrun
                outdata[:] = 0
                self._started = False


class AudioPlayback:
    """Manages per-player audio streams. PipeWire/PulseAudio handles mixing."""

    def __init__(self) -> None:
        # Per-player audio streams
        self._player_streams: dict[int, PlayerAudioStream] = {}
        self._player_names: dict[int, str] = {}  # player_id -> name lookup
        self._streams_lock = threading.Lock()
        # Multiple WebRTC tracks: source_player_id -> AudioPlaybackTrack
        self._playback_tracks: dict[int, "AudioPlaybackTrack"] = {}
        self._tracks_lock = threading.Lock()
        # Tile sounds (will have own stream)
        self.tile_sound_player: TileSoundPlayer | None = None
        self._running = False
        # Poll thread for WebRTC frames
        self._poll_thread: threading.Thread | None = None

    def update_player_names(self, player_names: dict[int, str]) -> None:
        """Update the player ID to name mapping."""
        with self._streams_lock:
            self._player_names = player_names.copy()

    def add_playback_track(
        self, source_player_id: int, track: "AudioPlaybackTrack"
    ) -> None:
        """Add a playback track for a source player."""
        with self._tracks_lock:
            self._playback_tracks[source_player_id] = track
        _logger.debug(f"Added playback track for player {source_player_id}")

    def start(self) -> None:
        """Start audio playback system."""
        self._running = True
        # Start polling thread for WebRTC frames
        self._poll_thread = threading.Thread(target=self._poll_webrtc, daemon=True)
        self._poll_thread.start()

    def stop(self) -> None:
        """Stop all audio streams."""
        self._running = False
        if self._poll_thread:
            self._poll_thread.join(timeout=1.0)
            self._poll_thread = None

        with self._streams_lock:
            for stream in self._player_streams.values():
                stream.stop()
            self._player_streams.clear()

    def remove_player(self, player_id: int) -> None:
        """Clean up audio stream and track for a player who left."""
        with self._streams_lock:
            stream = self._player_streams.pop(player_id, None)
            if stream:
                stream.stop()
        with self._tracks_lock:
            self._playback_tracks.pop(player_id, None)

    def _get_or_create_stream(self, player_id: int) -> PlayerAudioStream:
        """Get existing stream or create new one for player."""
        with self._streams_lock:
            if player_id not in self._player_streams:
                player_name = self._player_names.get(player_id, "")
                stream = PlayerAudioStream(player_id, player_name)
                stream.start()
                self._player_streams[player_id] = stream
                _logger.debug(
                    f"Created and started PlayerAudioStream for player {player_id}"
                )
            return self._player_streams[player_id]

    def _poll_webrtc(self) -> None:
        """Poll all WebRTC tracks for frames and route to player streams."""
        import time

        frame_count = 0
        while self._running:
            # Get snapshot of tracks to poll
            with self._tracks_lock:
                tracks = list(self._playback_tracks.items())

            for source_player_id, track in tracks:
                # Drain all available frames from this track
                while True:
                    pcm_data = track.get_frame()
                    if pcm_data is None:
                        break
                    frame_count += 1
                    if frame_count % 500 == 1:
                        _logger.debug(
                            f"Received audio frame {frame_count} from player {source_player_id}"
                        )
                    stream = self._get_or_create_stream(source_player_id)
                    # Volume is always 1.0 here - server already applied distance scaling
                    stream.feed_audio(pcm_data, 1.0)

            # Sleep briefly to avoid busy-waiting
            time.sleep(0.005)  # 5ms
