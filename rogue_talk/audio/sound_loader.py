"""Sound file loading and caching for tile sounds."""

from pathlib import Path

import numpy as np
import numpy.typing as npt
import soundfile as sf

from ..common.constants import SAMPLE_RATE


class SoundCache:
    """Load and cache WAV/FLAC/OGG files as numpy float32 arrays at 48kHz mono."""

    def __init__(self) -> None:
        self._sounds: dict[str, npt.NDArray[np.float32]] = {}
        self._assets_dir: Path | None = None

    def set_assets_dir(self, path: Path | None) -> None:
        """Set the assets directory and clear the cache.

        Args:
            path: Path to assets directory, or None to disable sound loading.
        """
        self._sounds.clear()
        self._assets_dir = path

    def get(self, filename: str) -> npt.NDArray[np.float32] | None:
        """Load and return a sound file, caching for future use.

        Args:
            filename: Name of the sound file (e.g., "footstep_stone.wav")

        Returns:
            Sound data as float32 numpy array at 48kHz mono, or None if not found.
        """
        if self._assets_dir is None:
            return None

        # Check cache first
        if filename in self._sounds:
            return self._sounds[filename]

        # Try to load the file
        file_path = self._assets_dir / filename
        if not file_path.exists():
            return None

        try:
            # Load audio file (soundfile handles WAV, FLAC, OGG)
            data, sample_rate = sf.read(file_path, dtype="float32")

            # Convert to numpy array
            audio: npt.NDArray[np.float32] = np.asarray(data, dtype=np.float32)

            # Convert stereo to mono if needed
            if audio.ndim == 2:
                audio = np.mean(audio, axis=1).astype(np.float32)

            # Resample if needed
            if sample_rate != SAMPLE_RATE:
                audio = self._resample(audio, sample_rate, SAMPLE_RATE)

            self._sounds[filename] = audio
            return audio

        except Exception:
            # Any error loading the file - skip silently
            return None

    def _resample(
        self,
        audio: npt.NDArray[np.float32],
        src_rate: int,
        dst_rate: int,
    ) -> npt.NDArray[np.float32]:
        """Simple linear interpolation resampling.

        Args:
            audio: Source audio data
            src_rate: Source sample rate
            dst_rate: Destination sample rate

        Returns:
            Resampled audio data
        """
        if src_rate == dst_rate:
            return audio

        # Calculate new length
        duration = len(audio) / src_rate
        new_length = int(duration * dst_rate)

        # Linear interpolation
        old_indices = np.arange(len(audio))
        new_indices = np.linspace(0, len(audio) - 1, new_length)
        resampled = np.interp(new_indices, old_indices, audio).astype(np.float32)

        return resampled
