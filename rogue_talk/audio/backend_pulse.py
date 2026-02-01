"""PulseAudio/PipeWire backend using PyAV (ffmpeg).

Provides audio I/O with proper stream naming for Linux systems
running PulseAudio or PipeWire (with PulseAudio compatibility).
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any

import av
import numpy as np
import numpy.typing as npt

from .backend import AudioInputStream, AudioOutputStream

_logger = logging.getLogger(__name__)
_debug_handler = logging.FileHandler("/tmp/rogue_talk_backend.log")
_debug_handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
_logger.addHandler(_debug_handler)
_logger.setLevel(logging.DEBUG)

# Application name shown in mixer
APPLICATION_NAME = "rogue_talk"


class PulseOutputStream(AudioOutputStream):
    """Audio output stream using PulseAudio via PyAV/ffmpeg."""

    def __init__(
        self,
        stream_name: str,
        samplerate: int = 48000,
        channels: int = 1,
    ) -> None:
        self.stream_name = stream_name
        self.samplerate = samplerate
        self.channels = channels

        self._container: Any | None = None
        self._stream: Any | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        # Buffer to handle timing jitter (~200ms at 20ms/frame)
        self._queue: queue.Queue[npt.NDArray[np.float32] | None] = queue.Queue(
            maxsize=10
        )
        self._pts = 0
        self._drop_count = 0
        self._frame_count = 0

    def start(self) -> None:
        """Start the output stream."""
        if self._running:
            return

        # Open PulseAudio output via ffmpeg
        self._container = av.open(
            "default",
            mode="w",
            format="pulse",
            options={"name": f"{APPLICATION_NAME}:{self.stream_name}"},
        )

        # Add audio stream with layout parameter (not attribute assignment)
        layout = "mono" if self.channels == 1 else "stereo"
        self._stream = self._container.add_stream(
            "pcm_f32le", rate=self.samplerate, layout=layout
        )

        self._running = True
        self._thread = threading.Thread(target=self._write_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the output stream."""
        if not self._running:
            return

        self._running = False
        # Signal thread to exit
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

        if self._container:
            try:
                self._container.close()
            except Exception:
                pass
            self._container = None
            self._stream = None

    def write(self, data: npt.NDArray[np.float32]) -> None:
        """Write audio data to the stream."""
        if not self._running:
            return
        self._frame_count += 1
        try:
            self._queue.put_nowait(data.copy())
        except queue.Full:
            self._drop_count += 1
        if self._frame_count % 500 == 0:
            _logger.debug(
                f"PulseOutputStream {self.stream_name}: "
                f"frames={self._frame_count}, drops={self._drop_count}"
            )

    def _write_loop(self) -> None:
        """Background thread that writes audio to PulseAudio."""
        while self._running:
            try:
                data = self._queue.get(timeout=0.1)
                if data is None:
                    break
                self._write_frame(data)
            except queue.Empty:
                continue
            except Exception as e:
                if self._running:
                    _logger.debug(f"Error writing audio: {e}")
                break

    def _write_frame(self, data: npt.NDArray[np.float32]) -> None:
        """Write a single audio frame."""
        if self._container is None or self._stream is None:
            return

        # Create audio frame
        frame = av.AudioFrame.from_ndarray(
            data.reshape(1, -1),  # Shape: (channels, samples)
            format="flt",
            layout="mono" if self.channels == 1 else "stereo",
        )
        frame.sample_rate = self.samplerate
        frame.pts = self._pts
        self._pts += len(data)

        # Encode and write
        for packet in self._stream.encode(frame):
            self._container.mux(packet)


class PulseInputStream(AudioInputStream):
    """Audio input stream using PulseAudio via PyAV/ffmpeg."""

    def __init__(
        self,
        stream_name: str,
        samplerate: int = 48000,
        channels: int = 1,
    ) -> None:
        self.stream_name = stream_name
        self.samplerate = samplerate
        self.channels = channels

        self._container: Any | None = None
        self._stream: Any | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        # Small buffer to minimize latency (~100ms at 20ms/frame)
        self._queue: queue.Queue[npt.NDArray[np.float32]] = queue.Queue(maxsize=5)

    def start(self) -> None:
        """Start the input stream."""
        if self._running:
            return

        # Open PulseAudio input via ffmpeg
        # fragment_size controls the buffer size (in bytes)
        # For 20ms at 48kHz mono int16: 960 samples * 2 bytes = 1920 bytes
        fragment_size = 960 * 2 * self.channels
        self._container = av.open(
            "default",
            mode="r",
            format="pulse",
            options={
                "name": f"{APPLICATION_NAME}:{self.stream_name}",
                "sample_rate": str(self.samplerate),
                "channels": str(self.channels),
                "fragment_size": str(fragment_size),
            },
        )

        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the input stream."""
        if not self._running:
            return

        self._running = False

        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

        if self._container:
            try:
                self._container.close()
            except Exception:
                pass
            self._container = None
            self._stream = None

    def read(self, num_samples: int) -> npt.NDArray[np.float32] | None:
        """Read audio data from the stream."""
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def _read_loop(self) -> None:
        """Background thread that reads audio from PulseAudio."""
        if self._container is None:
            return

        frame_count = 0
        try:
            for frame in self._container.decode(audio=0):
                if not self._running:
                    break

                frame_count += 1
                # Convert to numpy array
                data = frame.to_ndarray()

                if frame_count == 1:
                    _logger.debug(
                        f"PulseInputStream first frame: "
                        f"samples={frame.samples}, rate={frame.sample_rate}, "
                        f"ndarray shape={data.shape}, dtype={data.dtype}"
                    )

                # Normalize to float32 range [-1.0, 1.0] based on original dtype
                if data.dtype == np.int16:
                    data = data.astype(np.float32) / 32768.0
                elif data.dtype == np.int32:
                    data = data.astype(np.float32) / 2147483648.0
                elif data.dtype != np.float32:
                    data = data.astype(np.float32)

                # Flatten to mono if needed
                if data.ndim > 1:
                    data = data[0]  # Take first channel

                try:
                    self._queue.put_nowait(data)
                except queue.Full:
                    # Drop frame if queue is full
                    pass

        except Exception as e:
            if self._running:
                _logger.debug(f"Error reading audio: {e}")
