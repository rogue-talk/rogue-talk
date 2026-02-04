"""Microphone capture for WebRTC audio track."""

import threading
import time
from collections.abc import Callable

import numpy as np
import numpy.typing as npt

from ..audio.backend import AudioInputStream, create_input_stream
from ..common.constants import CHANNELS, FRAME_SIZE, SAMPLE_RATE


class AudioCapture:
    """Captures audio from microphone and sends raw PCM to WebRTC track.

    With WebRTC, the audio encoding (Opus) is handled by aiortc, so we just
    pass raw PCM data to the callback.
    """

    # VAD settings (set threshold to 0 to disable VAD)
    VAD_THRESHOLD = 0.0  # Disabled - WebRTC handles this
    VAD_HOLDOVER_FRAMES = 25  # Continue sending for 500ms after speech ends (25 * 20ms)

    def __init__(
        self, on_frame: Callable[[npt.NDArray[np.float32], int], None]
    ) -> None:
        """
        Args:
            on_frame: Callback called with (pcm_data, timestamp_ms) for each frame
        """
        self.on_frame = on_frame
        self._stream: AudioInputStream | None = None
        self.is_muted = False
        self._start_time_ms = 0
        self.last_level = 0.0
        self._vad_holdover_count = 0  # Frames remaining in holdover period
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start capturing audio."""
        self._start_time_ms = int(time.time() * 1000)
        self._stream = create_input_stream(
            stream_name="microphone",
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
        )
        self._stream.start()
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop capturing audio."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._stream:
            self._stream.stop()
            self._stream = None

    def set_muted(self, muted: bool) -> None:
        """Set mute state."""
        self.is_muted = muted

    def _capture_loop(self) -> None:
        """Background thread that reads audio and processes it."""
        while self._running and self._stream is not None:
            # Read audio from the stream
            pcm = self._stream.read(FRAME_SIZE)
            if pcm is None:
                time.sleep(0.001)  # Brief sleep if no data
                continue

            # Flatten to mono if needed
            if pcm.ndim > 1:
                pcm = pcm[:, 0]
            pcm = pcm.flatten()

            # Track audio level
            self.last_level = float(np.abs(pcm).max())

            if self.is_muted:
                # Send silence instead of nothing to avoid abrupt cutoff
                pcm = np.zeros(FRAME_SIZE, dtype=np.float32)

            # Simple VAD with holdover to avoid cutting off speech
            if self.last_level >= self.VAD_THRESHOLD:
                # Speech detected - reset holdover
                self._vad_holdover_count = self.VAD_HOLDOVER_FRAMES
            elif self._vad_holdover_count > 0:
                # In holdover period - continue sending
                self._vad_holdover_count -= 1
            else:
                # Silence and holdover expired - skip frame
                continue

            # Calculate timestamp
            timestamp_ms = int(time.time() * 1000) - self._start_time_ms

            # Send raw PCM to callback (WebRTC track handles encoding)
            self.on_frame(pcm, timestamp_ms)
