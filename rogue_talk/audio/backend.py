"""Abstract audio backend interface.

Provides a platform-agnostic interface for audio I/O, allowing
different implementations for different platforms (PulseAudio on Linux,
CoreAudio on macOS, etc.).
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod

import numpy as np
import numpy.typing as npt


class AudioOutputStream(ABC):
    """Abstract base class for audio output streams."""

    @abstractmethod
    def start(self) -> None:
        """Start the output stream."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop the output stream."""
        ...

    @abstractmethod
    def write(self, data: npt.NDArray[np.float32]) -> None:
        """Write audio data to the stream.

        Args:
            data: Float32 audio samples in range [-1.0, 1.0]
        """
        ...


class AudioInputStream(ABC):
    """Abstract base class for audio input streams."""

    @abstractmethod
    def start(self) -> None:
        """Start the input stream."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop the input stream."""
        ...

    @abstractmethod
    def read(self, num_samples: int) -> npt.NDArray[np.float32] | None:
        """Read audio data from the stream.

        Args:
            num_samples: Number of samples to read

        Returns:
            Float32 audio samples, or None if no data available
        """
        ...


def get_backend() -> str:
    """Determine the appropriate audio backend for the current platform."""
    if sys.platform == "linux":
        return "pulse"
    elif sys.platform == "darwin":
        # Future: return "coreaudio"
        raise NotImplementedError("macOS audio backend not yet implemented")
    elif sys.platform == "win32":
        # Future: return "wasapi"
        raise NotImplementedError("Windows audio backend not yet implemented")
    else:
        raise NotImplementedError(f"No audio backend for platform: {sys.platform}")


def create_output_stream(
    stream_name: str,
    samplerate: int = 48000,
    channels: int = 1,
) -> AudioOutputStream:
    """Create an audio output stream with the appropriate backend.

    Args:
        stream_name: Name for the stream (shown in mixer applications)
        samplerate: Sample rate in Hz
        channels: Number of audio channels

    Returns:
        An AudioOutputStream instance
    """
    backend = get_backend()
    if backend == "pulse":
        from .backend_pulse import PulseOutputStream

        return PulseOutputStream(stream_name, samplerate, channels)
    else:
        raise NotImplementedError(f"Backend '{backend}' not yet implemented")


def create_input_stream(
    stream_name: str,
    samplerate: int = 48000,
    channels: int = 1,
) -> AudioInputStream:
    """Create an audio input stream with the appropriate backend.

    Args:
        stream_name: Name for the stream (shown in mixer applications)
        samplerate: Sample rate in Hz
        channels: Number of audio channels

    Returns:
        An AudioInputStream instance
    """
    backend = get_backend()
    if backend == "pulse":
        from .backend_pulse import PulseInputStream

        return PulseInputStream(stream_name, samplerate, channels)
    else:
        raise NotImplementedError(f"Backend '{backend}' not yet implemented")
