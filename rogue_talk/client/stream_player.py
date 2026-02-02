"""Audio stream player for HTTP audio streams at specific map locations."""

from __future__ import annotations

import logging
import math
import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt

from ..audio.backend import AudioOutputStream, create_output_stream
from ..common.constants import FRAME_SIZE, SAMPLE_RATE

_logger = logging.getLogger(__name__)
_debug_handler = logging.FileHandler(f"/tmp/rogue_talk_stream_{os.getpid()}.log")
_debug_handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
_logger.addHandler(_debug_handler)
_logger.setLevel(logging.DEBUG)

if TYPE_CHECKING:
    from .level import Level, StreamInfo

# Stream audio constants
STREAM_BASE_VOLUME = 0.4
FADE_SPEED = 0.05  # Volume change per frame for smooth fade


@dataclass
class ActiveStream:
    """An active audio stream being played."""

    url: str
    x: int
    y: int
    radius: int
    target_volume: float = 0.0
    current_volume: float = 0.0
    # Threading for stream reading
    thread: threading.Thread | None = None
    running: bool = False
    # Audio data queue
    audio_queue: queue.Queue[npt.NDArray[np.float32]] | None = None
    # Current position in buffered chunk
    buffer: npt.NDArray[np.float32] | None = None
    buffer_pos: int = 0
    # Stats
    drop_count: int = 0
    frame_count: int = 0


class StreamPlayer:
    """Manages audio streams from HTTP URLs with distance-based volume."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._streams: dict[str, ActiveStream] = {}  # url -> ActiveStream
        self._stream: AudioOutputStream | None = None
        self._running = False
        self._output_active = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the stream player (output is created lazily when needed)."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._playback_loop, daemon=True)
        self._thread.start()

    def _ensure_output_started(self) -> None:
        """Start the audio output if not already running."""
        if self._output_active:
            return
        self._stream = create_output_stream(
            stream_name="radio",
            samplerate=SAMPLE_RATE,
            channels=1,
        )
        self._stream.start()
        self._output_active = True
        _logger.debug("Started radio output stream")

    def _stop_output(self) -> None:
        """Stop the audio output."""
        if not self._output_active:
            return
        if self._stream:
            self._stream.stop()
            self._stream = None
        self._output_active = False
        _logger.debug("Stopped radio output stream")

    def stop(self) -> None:
        """Stop the stream player and all streams."""
        self._running = False
        # Stop all stream reader threads
        with self._lock:
            for stream in self._streams.values():
                stream.running = False
            # Wait for threads to finish
            for stream in self._streams.values():
                if stream.thread is not None:
                    stream.thread.join(timeout=1.0)
            self._streams.clear()

        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._stop_output()

    def _playback_loop(self) -> None:
        """Background thread that generates and writes mixed audio."""
        frame_duration = FRAME_SIZE / SAMPLE_RATE
        # Use absolute timing to prevent drift
        next_frame_time = time.perf_counter()

        while self._running:
            # Check if we have any active streams
            with self._lock:
                has_active = any(
                    s.current_volume > 0 or s.target_volume > 0
                    for s in self._streams.values()
                )

            if has_active:
                self._ensure_output_started()
                # Generate the next frame
                mixed = self._get_mixed_frame()
                # Write to output stream
                if self._stream:
                    self._stream.write(mixed)
                # Sleep until next frame time (absolute timing prevents drift)
                next_frame_time += frame_duration
                sleep_time = next_frame_time - time.perf_counter()
                if sleep_time > 0:
                    time.sleep(sleep_time)
                elif sleep_time < -0.1:
                    # We're way behind - reset timing to catch up
                    next_frame_time = time.perf_counter()
            else:
                # No active streams - stop output and sleep longer
                self._stop_output()
                time.sleep(0.1)
                next_frame_time = time.perf_counter()

    def update_streams(
        self,
        player_x: int,
        player_y: int,
        level: Level,
    ) -> None:
        """Update active streams based on player position and level streams.

        Args:
            player_x: Player x position
            player_y: Player y position
            level: Current level containing stream definitions
        """
        if not level.streams:
            # No streams in level, fade out all
            with self._lock:
                for stream in self._streams.values():
                    stream.target_volume = 0.0
            return

        # Calculate volumes for each stream based on distance
        active_urls: set[str] = set()

        for stream_info in level.streams:
            dx = player_x - stream_info.x
            dy = player_y - stream_info.y
            distance = math.sqrt(dx * dx + dy * dy)

            if distance <= stream_info.radius:
                # Player is in range - calculate volume
                # At distance 0: full volume, at radius: 0
                volume = STREAM_BASE_VOLUME * (
                    1.0 - distance / (stream_info.radius + 1)
                )
                active_urls.add(stream_info.url)

                with self._lock:
                    if stream_info.url not in self._streams:
                        # Start new stream
                        self._start_stream(stream_info)
                    # Update target volume
                    self._streams[stream_info.url].target_volume = volume

        # Fade out streams that are out of range
        with self._lock:
            for url, stream in self._streams.items():
                if url not in active_urls:
                    stream.target_volume = 0.0

    def _start_stream(self, stream_info: StreamInfo) -> None:
        """Start a new stream reader thread. Must be called with lock held."""
        stream = ActiveStream(
            url=stream_info.url,
            x=stream_info.x,
            y=stream_info.y,
            radius=stream_info.radius,
            # Larger buffer (100 chunks) to handle HTTP stream bursts
            audio_queue=queue.Queue(maxsize=100),
            running=True,
        )
        self._streams[stream_info.url] = stream

        # Start reader thread
        stream.thread = threading.Thread(
            target=self._stream_reader_thread,
            args=(stream,),
            daemon=True,
        )
        stream.thread.start()

    def _stream_reader_thread(self, stream: ActiveStream) -> None:
        """Background thread that reads audio from HTTP stream."""
        try:
            import av
            from av.audio.frame import AudioFrame
            from av.audio.stream import AudioStream
        except ImportError:
            return

        container = None
        try:
            # Open the HTTP stream
            container = av.open(
                stream.url,
                timeout=10.0,
                options={
                    "reconnect": "1",
                    "reconnect_streamed": "1",
                    "reconnect_delay_max": "5",
                },
            )

            # Find audio stream
            audio_stream: AudioStream | None = None
            for s in container.streams:
                if isinstance(s, AudioStream):
                    audio_stream = s
                    break

            if audio_stream is None:
                return

            # Get sample rate before decoding
            framerate = audio_stream.sample_rate

            # Read and decode audio
            for packet in container.demux(audio_stream):
                if not stream.running:
                    break

                for frame in packet.decode():
                    if not stream.running:
                        break

                    # Only process audio frames
                    if not isinstance(frame, AudioFrame):
                        continue

                    # Convert to numpy array
                    arr = frame.to_ndarray()

                    # Handle different layouts
                    if arr.ndim > 1:
                        # Average channels to mono
                        arr = arr.mean(axis=0)

                    # Normalize based on dtype
                    if arr.dtype == np.int16:
                        arr = arr.astype(np.float32) / 32768.0
                    elif arr.dtype == np.int32:
                        arr = arr.astype(np.float32) / 2147483648.0
                    elif arr.dtype == np.float64:
                        arr = arr.astype(np.float32)
                    elif arr.dtype != np.float32:
                        arr = arr.astype(np.float32)

                    # Resample if needed
                    if framerate != SAMPLE_RATE:
                        arr = self._resample(arr, framerate, SAMPLE_RATE)

                    # Put in queue with blocking (backpressure to HTTP reader)
                    stream.frame_count += 1
                    if stream.audio_queue is not None:
                        try:
                            # Block up to 1 second - provides backpressure
                            stream.audio_queue.put(arr.astype(np.float32), timeout=1.0)
                        except queue.Full:
                            # Timeout - stream is stalled, drop frame
                            stream.drop_count += 1
                    if stream.frame_count % 500 == 0:
                        _logger.debug(
                            f"Stream {stream.url}: frames={stream.frame_count}, "
                            f"drops={stream.drop_count}"
                        )

        except Exception:
            # Stream error - will be cleaned up when faded out
            pass
        finally:
            if container is not None:
                container.close()

    def _resample(
        self,
        audio: npt.NDArray[np.float32],
        src_rate: int,
        dst_rate: int,
    ) -> npt.NDArray[np.float32]:
        """Simple linear resampling."""
        if src_rate == dst_rate:
            return audio

        ratio = dst_rate / src_rate
        new_length = int(len(audio) * ratio)
        old_indices = np.arange(len(audio))
        new_indices = np.linspace(0, len(audio) - 1, new_length)
        resampled = np.interp(new_indices, old_indices, audio)
        return resampled.astype(np.float32)

    def _get_mixed_frame(self) -> npt.NDArray[np.float32]:
        """Get the next frame of mixed stream audio.

        Returns:
            Mixed audio frame of FRAME_SIZE samples.
        """
        mixed = np.zeros(FRAME_SIZE, dtype=np.float32)

        with self._lock:
            streams_to_remove: list[str] = []

            for url, stream in self._streams.items():
                # Fade volume towards target
                if stream.current_volume < stream.target_volume:
                    stream.current_volume = min(
                        stream.current_volume + FADE_SPEED,
                        stream.target_volume,
                    )
                elif stream.current_volume > stream.target_volume:
                    stream.current_volume = max(
                        stream.current_volume - FADE_SPEED,
                        stream.target_volume,
                    )

                # Remove streams that have faded out
                if stream.current_volume <= 0.0 and stream.target_volume <= 0.0:
                    stream.running = False
                    streams_to_remove.append(url)
                    continue

                # Skip if effectively silent
                if stream.current_volume < 0.001:
                    continue

                # Get audio from this stream
                samples_needed = FRAME_SIZE
                output_pos = 0

                while samples_needed > 0:
                    # Use buffered data first
                    if stream.buffer is not None and stream.buffer_pos < len(
                        stream.buffer
                    ):
                        available = len(stream.buffer) - stream.buffer_pos
                        to_copy = min(available, samples_needed)
                        mixed[output_pos : output_pos + to_copy] += (
                            stream.buffer[
                                stream.buffer_pos : stream.buffer_pos + to_copy
                            ]
                            * stream.current_volume
                        )
                        stream.buffer_pos += to_copy
                        output_pos += to_copy
                        samples_needed -= to_copy
                    else:
                        # Need more data from queue
                        try:
                            if stream.audio_queue is not None:
                                stream.buffer = stream.audio_queue.get_nowait()
                                stream.buffer_pos = 0
                            else:
                                break
                        except queue.Empty:
                            # No data available - output silence for remaining
                            break

            # Remove faded out streams
            for url in streams_to_remove:
                stream = self._streams.pop(url)
                if stream.thread is not None:
                    stream.thread.join(timeout=0.1)

        return mixed

    def clear(self) -> None:
        """Clear all streams (e.g., on level transition)."""
        with self._lock:
            for stream in self._streams.values():
                stream.running = False
            for stream in self._streams.values():
                if stream.thread is not None:
                    stream.thread.join(timeout=0.5)
            self._streams.clear()
