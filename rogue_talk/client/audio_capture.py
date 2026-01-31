"""Microphone capture with Opus encoding."""

import time
from collections.abc import Callable
from typing import Any

import numpy as np
import numpy.typing as npt
import sounddevice as sd

from ..audio.opus_codec import OpusEncoder
from ..common.constants import CHANNELS, FRAME_SIZE, SAMPLE_RATE


class AudioCapture:
    """Captures audio from microphone and encodes to Opus."""

    # VAD settings
    VAD_THRESHOLD = 0.02  # Minimum level to consider as speech
    VAD_HOLDOVER_FRAMES = 25  # Continue sending for 500ms after speech ends (25 * 20ms)

    def __init__(self, on_frame: Callable[[bytes, int], None]) -> None:
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
        self._vad_holdover_count = 0  # Frames remaining in holdover period

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
        self,
        indata: npt.NDArray[np.float32],
        frames: int,
        time_info: Any,
        status: sd.CallbackFlags,
    ) -> None:
        """Sounddevice callback - runs in separate thread."""
        # Flatten to mono if needed
        pcm: npt.NDArray[np.float32] = (
            indata[:, 0] if indata.ndim > 1 else indata.flatten()
        )

        # Track audio level
        self.last_level = float(np.abs(pcm).max())

        if self.is_muted:
            return

        # Simple VAD with holdover to avoid cutting off speech
        if self.last_level >= self.VAD_THRESHOLD:
            # Speech detected - reset holdover
            self._vad_holdover_count = self.VAD_HOLDOVER_FRAMES
        elif self._vad_holdover_count > 0:
            # In holdover period - continue sending
            self._vad_holdover_count -= 1
        else:
            # Silence and holdover expired - skip frame
            return

        # Encode to Opus
        opus_data = self.encoder.encode(pcm)

        # Calculate timestamp
        timestamp_ms = int(time.time() * 1000) - self._start_time_ms

        # Send to callback
        self.on_frame(opus_data, timestamp_ms)
