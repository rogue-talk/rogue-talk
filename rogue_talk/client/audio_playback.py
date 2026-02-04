"""Audio playback with per-player streams (mixed by PipeWire/PulseAudio)."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import TYPE_CHECKING, Callable

import numpy as np
import numpy.typing as npt

from ..audio.backend import AudioOutputStream, create_output_stream
from ..common.audio import get_volume
from ..common.constants import AUDIO_MAX_DISTANCE, FRAME_SIZE, SAMPLE_RATE

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

    # Buffer settings - minimize latency
    MIN_BUFFER = FRAME_SIZE * 1  # 20ms before starting playback
    MAX_BUFFER = FRAME_SIZE * 5  # 100ms max buffer
    TARGET_BUFFER = FRAME_SIZE * 2  # 40ms target

    def __init__(
        self,
        player_id: int,
        player_name: str = "",
        get_volume: "Callable[[], float] | None" = None,
    ) -> None:
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
        # Volume callback - called at playback time, not poll time
        self._get_volume = get_volume
        # Counters for debugging audio issues
        self._overflow_count = 0
        self._underrun_count = 0
        self._frame_count = 0

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

    def feed_audio(self, pcm_data: npt.NDArray[np.float32]) -> None:
        """Feed audio data into the ring buffer (thread-safe).

        Volume is applied at playback time, not here, to keep this fast.
        """
        samples = pcm_data.flatten()
        sample_len = len(samples)

        with self._lock:
            buf_size = len(self._ring_buffer)

            # Check available space
            used = (self._write_pos - self._read_pos) % buf_size
            available = buf_size - used - 1
            if sample_len > available:
                # Discard oldest data (overflow)
                discard = sample_len - available
                self._read_pos = (self._read_pos + discard) % buf_size
                self._overflow_count += 1

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
        next_frame_time = time.perf_counter()

        while self._running and self._stream is not None:
            # Get current buffer level
            with self._lock:
                buf_size = len(self._ring_buffer)
                buffer_samples = (self._write_pos - self._read_pos) % buf_size

            # Get frame (silence if buffer empty)
            frame = self._get_frame()
            self._frame_count += 1

            # Log stats periodically (every ~10 seconds)
            if self._frame_count % 500 == 1:
                _logger.debug(
                    f"PlayerAudioStream {self.player_id}: "
                    f"frames={self._frame_count}, underruns={self._underrun_count}, "
                    f"overflows={self._overflow_count}, buffer={buffer_samples}"
                )

            # Apply volume at playback time (not in poll thread)
            if self._get_volume is not None:
                volume = self._get_volume()
                if volume != 1.0:
                    frame = frame * volume

            # Write to output stream
            self._stream.write(frame)

            # Maintain timing
            next_frame_time += frame_duration
            sleep_time = next_frame_time - time.perf_counter()
            if sleep_time > 0:
                time.sleep(sleep_time)
            elif sleep_time < -0.1:
                next_frame_time = time.perf_counter()

    def _get_frame(self) -> npt.NDArray[np.float32]:
        """Get the next audio frame from ring buffer, or silence if empty."""
        with self._lock:
            buf_size = len(self._ring_buffer)
            buffer_len = (self._write_pos - self._read_pos) % buf_size

            # Need MIN_BUFFER before starting
            if not self._started:
                if buffer_len >= self.MIN_BUFFER:
                    self._started = True
                else:
                    self._was_silent = True
                    return np.zeros(FRAME_SIZE, dtype=np.float32)

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
                return frame
            else:
                # Buffer empty - return silence
                self._underrun_count += 1
                return np.zeros(FRAME_SIZE, dtype=np.float32)


class AudioPlayback:
    """Manages per-player audio streams. PipeWire/PulseAudio handles mixing."""

    def __init__(self) -> None:
        # Per-player audio streams (keyed by player name)
        self._player_streams: dict[str, PlayerAudioStream] = {}
        self._player_positions: dict[str, tuple[int, int]] = {}  # name -> (x, y)
        self._my_position: tuple[int, int] = (0, 0)
        self._streams_lock = threading.Lock()
        # Multiple WebRTC tracks (keyed by player name)
        self._playback_tracks: dict[str, "AudioPlaybackTrack"] = {}
        self._tracks_lock = threading.Lock()
        self._running = False
        # Poll thread for WebRTC frames
        self._poll_thread: threading.Thread | None = None

    def update_positions(
        self, my_x: int, my_y: int, player_positions: dict[str, tuple[int, int]]
    ) -> None:
        """Update position data and clean up out-of-range streams."""
        with self._streams_lock:
            self._my_position = (my_x, my_y)
            self._player_positions = player_positions.copy()

        # Clean up streams for players that are now out of range
        max_dist_sq = AUDIO_MAX_DISTANCE * AUDIO_MAX_DISTANCE
        to_stop: list[str] = []

        with self._streams_lock:
            for player_name in self._player_streams:
                pos = player_positions.get(player_name)
                if pos is None:
                    continue
                dx = pos[0] - my_x
                dy = pos[1] - my_y
                dist_sq = dx * dx + dy * dy
                if dist_sq > max_dist_sq:
                    to_stop.append(player_name)

        for player_name in to_stop:
            with self._streams_lock:
                stream = self._player_streams.pop(player_name, None)
                if stream:
                    stream.stop()
                    _logger.debug(
                        f"Stopped out-of-range audio output for {player_name}"
                    )

    def _is_in_range(self, player_name: str) -> bool:
        """Check if a player is within audio range."""
        pos = self._player_positions.get(player_name)
        if pos is None:
            _logger.debug(
                f"_is_in_range({player_name}): no position, assuming in range"
            )
            return True  # Unknown position, assume in range
        dx = pos[0] - self._my_position[0]
        dy = pos[1] - self._my_position[1]
        dist_sq = dx * dx + dy * dy
        in_range = dist_sq <= AUDIO_MAX_DISTANCE * AUDIO_MAX_DISTANCE
        if not in_range:
            _logger.debug(
                f"_is_in_range({player_name}): out of range, "
                f"my={self._my_position} them={pos} dist_sq={dist_sq}"
            )
        return in_range

    def add_playback_track(self, player_name: str, track: "AudioPlaybackTrack") -> None:
        """Add a playback track for a player."""
        _logger.debug(f"add_playback_track called for {player_name}")
        with self._tracks_lock:
            if player_name in self._playback_tracks:
                _logger.debug(f"Track for {player_name} already exists")
                return  # Already added
            self._playback_tracks[player_name] = track
        _logger.debug(f"Added playback track for {player_name}")

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

    def remove_player(self, player_name: str) -> None:
        """Clean up audio stream and track for a player who left."""
        with self._streams_lock:
            stream = self._player_streams.pop(player_name, None)
            if stream:
                stream.stop()
        with self._tracks_lock:
            self._playback_tracks.pop(player_name, None)

    def _get_proximity_volume(self, player_name: str) -> float:
        """Calculate proximity volume for a player based on positions.

        Returns 1.0 if either position is unknown (before first WORLD_STATE).
        """
        with self._streams_lock:
            pos = self._player_positions.get(player_name)
            if pos is None:
                return 1.0
            dx = pos[0] - self._my_position[0]
            dy = pos[1] - self._my_position[1]
        return get_volume(dx, dy)

    def _get_or_create_stream(self, player_name: str) -> PlayerAudioStream | None:
        """Get existing stream or create new one for player if in range."""
        with self._streams_lock:
            if player_name in self._player_streams:
                return self._player_streams[player_name]

            # Only create stream if player is in range
            if not self._is_in_range(player_name):
                return None

            # PlayerAudioStream needs an ID for PulseAudio sink naming - use hash of name
            player_id = hash(player_name) & 0x7FFFFFFF  # Positive int

            # Pass volume callback so volume is calculated at playback time, not poll time
            # Capture player_name in default arg to avoid late binding issues
            def make_volume_getter(pn: str) -> Callable[[], float]:
                return lambda: self._get_proximity_volume(pn)

            stream = PlayerAudioStream(
                player_id,
                player_name,
                get_volume=make_volume_getter(player_name),
            )
            stream.start()
            self._player_streams[player_name] = stream
            _logger.debug(f"Created and started PlayerAudioStream for {player_name}")
            return stream

    def _poll_webrtc(self) -> None:
        """Poll all WebRTC tracks for frames and route to player streams.

        This loop should be fast - just move data from WebRTC to ring buffers.
        Volume and other processing happens in the playback threads.
        """
        frame_count = 0
        while self._running:
            # Get snapshot of tracks to poll
            with self._tracks_lock:
                tracks = list(self._playback_tracks.items())

            for player_name, track in tracks:
                # Drain all available frames from this track
                while True:
                    pcm_data = track.get_frame()
                    if pcm_data is None:
                        break
                    frame_count += 1

                    # Get or create stream (volume callback is set at creation time)
                    stream = self._get_or_create_stream(player_name)
                    if stream is not None:
                        stream.feed_audio(pcm_data)

                    # Log periodically
                    if frame_count % 500 == 1:
                        _logger.debug(
                            f"Received audio frame {frame_count} from {player_name}, "
                            f"samples={len(pcm_data)}"
                        )
                        # Log audio level
                        level = float(np.abs(pcm_data).max())
                        _logger.debug(f"Audio level from {player_name}: {level:.4f}")

            # Sleep briefly to avoid busy-waiting
            time.sleep(0.005)  # 5ms
