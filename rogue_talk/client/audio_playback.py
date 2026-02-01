"""Audio playback with per-player streams (mixed by PipeWire/PulseAudio)."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt

from ..audio.backend import AudioOutputStream, create_output_stream
from ..common.constants import FRAME_SIZE, SAMPLE_RATE

if TYPE_CHECKING:
    from ..audio.webrtc_tracks import AudioPlaybackTrack

# Debug logging to file (doesn't interfere with terminal UI)
# Use PID in filename so multiple clients on same machine have separate logs
_logger = logging.getLogger(__name__)
_debug_handler = logging.FileHandler(f"/tmp/rogue_talk_audio_{os.getpid()}.log")
_debug_handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
_logger.addHandler(_debug_handler)
_logger.setLevel(logging.DEBUG)


class PlayerAudioStream:
    """Audio output stream for a single player's voice using PyAV backend."""

    # Buffer settings - balance between latency and jitter handling
    MIN_BUFFER = FRAME_SIZE * 3  # 60ms before starting playback
    MAX_BUFFER = FRAME_SIZE * 10  # 200ms max buffer

    def __init__(self, player_id: int, player_name: str = "") -> None:
        self.player_id = player_id
        self.player_name = player_name or f"player_{player_id}"
        self._ring_buffer = np.zeros(self.MAX_BUFFER * 2, dtype=np.float32)
        self._write_pos = 0
        self._read_pos = 0
        self._started = False
        self._stream: AudioOutputStream | None = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the audio output stream."""
        if self._running:
            return
        try:
            # Create named stream for this player (shows up in pavucontrol)
            self._stream = create_output_stream(
                stream_name=f"player:{self.player_name}",
                samplerate=SAMPLE_RATE,
                channels=1,
            )
            self._stream.start()
            self._running = True
            self._thread = threading.Thread(target=self._playback_loop, daemon=True)
            self._thread.start()
        except Exception as e:
            _logger.error(
                f"Failed to start audio stream for player {self.player_id}: {e}"
            )

    def stop(self) -> None:
        """Stop the audio output stream."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._stream:
            self._stream.stop()
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

    def _playback_loop(self) -> None:
        """Background thread that reads from ring buffer and writes to audio output."""
        frame_duration = FRAME_SIZE / SAMPLE_RATE
        frame_count = 0
        underrun_count = 0

        while self._running and self._stream is not None:
            start_time = time.perf_counter()

            # Generate the next frame from ring buffer
            frame, is_underrun = self._get_frame_with_status()
            frame_count += 1

            if is_underrun:
                underrun_count += 1

            if frame_count % 500 == 1:
                with self._lock:
                    buf_size = len(self._ring_buffer)
                    buffer_samples = (self._write_pos - self._read_pos) % buf_size
                _logger.debug(
                    f"PlayerAudioStream {self.player_id} playback: "
                    f"frame={frame_count}, underruns={underrun_count}, "
                    f"buffer={buffer_samples}/{self.MIN_BUFFER}"
                )

            # Write to output stream
            self._stream.write(frame)

            # Sleep to maintain timing
            elapsed = time.perf_counter() - start_time
            sleep_time = frame_duration - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _get_frame_with_status(self) -> tuple[npt.NDArray[np.float32], bool]:
        """Get the next audio frame from ring buffer with underrun status."""
        with self._lock:
            buf_size = len(self._ring_buffer)
            buffer_len = (self._write_pos - self._read_pos) % buf_size

            if not self._started:
                if buffer_len >= self.MIN_BUFFER:
                    self._started = True
                else:
                    # Still buffering - not a real underrun
                    return np.zeros(FRAME_SIZE, dtype=np.float32), False

            if buffer_len >= FRAME_SIZE:
                read_pos = self._read_pos
                end_pos = read_pos + FRAME_SIZE
                if end_pos <= buf_size:
                    frame = self._ring_buffer[read_pos:end_pos].copy()
                else:
                    first = buf_size - read_pos
                    frame = np.concatenate(
                        [
                            self._ring_buffer[read_pos:buf_size],
                            self._ring_buffer[: end_pos - buf_size],
                        ]
                    )
                self._read_pos = end_pos % buf_size
                return frame, False
            else:
                # Underrun - buffer emptied during playback
                self._started = False
                return np.zeros(FRAME_SIZE, dtype=np.float32), True


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
                            f"Received audio frame {frame_count} from player {source_player_id}, "
                            f"samples={len(pcm_data)}"
                        )
                    stream = self._get_or_create_stream(source_player_id)
                    # Volume is always 1.0 here - server already applied distance scaling
                    stream.feed_audio(pcm_data, 1.0)

            # Sleep briefly to avoid busy-waiting
            time.sleep(0.005)  # 5ms
