"""Microphone capture with Opus encoding."""

import time
from collections.abc import Callable
from threading import Thread

import numpy as np
import sounddevice as sd

from ..audio.opus_codec import OpusEncoder
from ..common.constants import CHANNELS, FRAME_SIZE, SAMPLE_RATE


class AudioCapture:
    """Captures audio from microphone and encodes to Opus."""

    def __init__(self, on_frame: Callable[[bytes, int], None]):
        """
        Args:
            on_frame: Callback called with (opus_data, timestamp_ms) for each frame
        """
        self.on_frame = on_frame
        self.encoder = OpusEncoder()
        self.stream: sd.InputStream | None = None
        self.is_muted = False
        self._start_time_ms = 0
        self.last_level = 0.0

    def start(self) -> None:
        """Start capturing audio."""
        self._start_time_ms = int(time.time() * 1000)
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=np.float32,
            blocksize=FRAME_SIZE,
            callback=self._audio_callback,
        )
        self.stream.start()

    def stop(self) -> None:
        """Stop capturing audio."""
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

    def set_muted(self, muted: bool) -> None:
        """Set mute state."""
        self.is_muted = muted

    def _audio_callback(
        self, indata: np.ndarray, frames: int, time_info, status: sd.CallbackFlags
    ) -> None:
        """Sounddevice callback - runs in separate thread."""
        # Flatten to mono if needed
        pcm = indata[:, 0] if indata.ndim > 1 else indata.flatten()

        # Track audio level
        self.last_level = float(np.abs(pcm).max())

        if self.is_muted:
            return

        # Encode to Opus
        opus_data = self.encoder.encode(pcm)

        # Calculate timestamp
        timestamp_ms = int(time.time() * 1000) - self._start_time_ms

        # Send to callback
        self.on_frame(opus_data, timestamp_ms)
