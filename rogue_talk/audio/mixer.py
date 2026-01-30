"""Multi-stream audio mixer with volume scaling."""

import numpy as np
import numpy.typing as npt

from ..common.constants import FRAME_SIZE


class AudioMixer:
    """Mixes multiple audio streams with volume scaling."""

    def __init__(self) -> None:
        self.streams: dict[int, tuple[npt.NDArray[np.float32], float]] = {}

    def add_frame(
        self, player_id: int, pcm_data: npt.NDArray[np.float32], volume: float
    ) -> None:
        """Add a decoded frame from a player with their current volume."""
        # Ensure correct length
        if len(pcm_data) < FRAME_SIZE:
            pcm_data = np.pad(pcm_data, (0, FRAME_SIZE - len(pcm_data)))
        elif len(pcm_data) > FRAME_SIZE:
            pcm_data = pcm_data[:FRAME_SIZE]

        self.streams[player_id] = (pcm_data, volume)

    def mix(self) -> npt.NDArray[np.float32]:
        """Mix all streams and return combined output."""
        if not self.streams:
            return np.zeros(FRAME_SIZE, dtype=np.float32)

        mixed = np.zeros(FRAME_SIZE, dtype=np.float32)

        for player_id, (pcm, volume) in self.streams.items():
            mixed += pcm.astype(np.float32) * volume

        # Soft clipping to prevent harsh distortion
        mixed = np.tanh(mixed)

        self.streams.clear()
        return mixed

    def remove_player(self, player_id: int) -> None:
        """Remove a player's stream."""
        self.streams.pop(player_id, None)
