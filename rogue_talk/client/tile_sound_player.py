"""Tile sound effects management with dedicated audio output stream."""

from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

import numpy as np
import numpy.typing as npt
import sounddevice as sd

from ..audio.sound_loader import SoundCache
from ..common import tiles
from ..common.constants import CHANNELS, FRAME_SIZE, SAMPLE_RATE

# Set application name for PulseAudio/PipeWire
os.environ.setdefault("PULSE_PROP_application.name", "rogue_talk")

if TYPE_CHECKING:
    from .level import Level

# Sound constants
WALKING_SOUND_VOLUME = 0.5
NEARBY_SOUND_MAX_DISTANCE = 3
NEARBY_SOUND_BASE_VOLUME = 0.3
FADE_SPEED = 0.1  # Volume change per frame for ambient fade in/out


@dataclass
class OneShotSound:
    """A one-shot sound effect (e.g., footstep)."""

    data: npt.NDArray[np.float32]
    position: int = 0  # Current playback position
    volume: float = 1.0


@dataclass
class LoopingSound:
    """A looping ambient sound effect."""

    data: npt.NDArray[np.float32]
    position: int = 0  # Current playback position
    target_volume: float = 0.0  # Target volume (for fade in/out)
    current_volume: float = 0.0  # Current volume (interpolating to target)


class TileSoundPlayer:
    """Manages tile sound effects with dedicated audio output stream."""

    def __init__(self, sound_cache: SoundCache) -> None:
        self.sound_cache = sound_cache
        self._lock = threading.Lock()
        self._one_shots: list[OneShotSound] = []
        self._ambient_sounds: dict[str, LoopingSound] = {}
        self._stream: sd.OutputStream | None = None

    def start(self) -> None:
        """Start the tile sound output stream."""
        # Set stream name for PulseAudio/PipeWire
        os.environ["PULSE_PROP_media.name"] = "environment"
        self._stream = sd.OutputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=np.float32,
            blocksize=FRAME_SIZE,
            callback=self._audio_callback,
        )
        self._stream.start()

    def stop(self) -> None:
        """Stop the tile sound output stream."""
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _audio_callback(
        self,
        outdata: npt.NDArray[np.float32],
        frames: int,
        time_info: Any,
        status: sd.CallbackFlags,
    ) -> None:
        """Sounddevice callback - get mixed tile sounds."""
        mixed = self._get_mixed_frame()
        outdata[:, 0] = mixed

    def on_player_move(self, x: int, y: int, level: Level) -> None:
        """Called when player moves to a new tile. Plays walking sound.

        Args:
            x: New x position
            y: New y position
            level: Current level
        """
        tile_char = level.get_tile(x, y)
        tile_def = tiles.get_tile(tile_char)

        if tile_def.walking_sound:
            sound_data = self.sound_cache.get(tile_def.walking_sound)
            if sound_data is not None:
                with self._lock:
                    self._one_shots.append(
                        OneShotSound(
                            data=sound_data,
                            position=0,
                            volume=WALKING_SOUND_VOLUME,
                        )
                    )

    def update_nearby_sounds(
        self,
        player_x: int,
        player_y: int,
        level: Level,
        los_fn: Callable[[int, int, int, int, Level], bool],
    ) -> None:
        """Update ambient sounds based on nearby tiles with line-of-sound.

        Args:
            player_x: Player x position
            player_y: Player y position
            level: Current level
            los_fn: Line-of-sound check function (x1, y1, x2, y2, level) -> bool
        """
        # Collect active sounds with their max volumes
        active_sounds: dict[str, float] = {}

        # Scan 7x7 area (±3 tiles)
        for dy in range(-NEARBY_SOUND_MAX_DISTANCE, NEARBY_SOUND_MAX_DISTANCE + 1):
            for dx in range(-NEARBY_SOUND_MAX_DISTANCE, NEARBY_SOUND_MAX_DISTANCE + 1):
                tx = player_x + dx
                ty = player_y + dy

                # Calculate distance
                distance = math.sqrt(dx * dx + dy * dy)
                if distance > NEARBY_SOUND_MAX_DISTANCE:
                    continue

                # Check line of sound
                if not los_fn(player_x, player_y, tx, ty, level):
                    continue

                # Get tile and check for nearby_sound
                tile_char = level.get_tile(tx, ty)
                tile_def = tiles.get_tile(tile_char)

                if tile_def.nearby_sound:
                    # Calculate volume based on distance
                    # At distance 0: full volume, at max distance: 0
                    volume = NEARBY_SOUND_BASE_VOLUME * (
                        1.0 - distance / (NEARBY_SOUND_MAX_DISTANCE + 1)
                    )
                    # Keep the highest volume for this sound
                    current_vol = active_sounds.get(tile_def.nearby_sound, 0.0)
                    active_sounds[tile_def.nearby_sound] = max(current_vol, volume)

        with self._lock:
            # Update target volumes for existing sounds
            for filename, looping in self._ambient_sounds.items():
                if filename in active_sounds:
                    looping.target_volume = active_sounds[filename]
                else:
                    looping.target_volume = 0.0

            # Add new sounds
            for filename, volume in active_sounds.items():
                if filename not in self._ambient_sounds:
                    sound_data = self.sound_cache.get(filename)
                    if sound_data is not None:
                        self._ambient_sounds[filename] = LoopingSound(
                            data=sound_data,
                            position=0,
                            target_volume=volume,
                            current_volume=0.0,
                        )

    def _get_mixed_frame(self) -> npt.NDArray[np.float32]:
        """Get the next frame of mixed tile sounds.

        Returns:
            Mixed audio frame of FRAME_SIZE samples.
        """
        mixed = np.zeros(FRAME_SIZE, dtype=np.float32)

        with self._lock:
            # Process one-shot sounds
            completed_indices: list[int] = []
            for i, one_shot in enumerate(self._one_shots):
                samples_remaining = len(one_shot.data) - one_shot.position
                samples_to_copy = min(FRAME_SIZE, samples_remaining)

                if samples_to_copy > 0:
                    mixed[:samples_to_copy] += (
                        one_shot.data[
                            one_shot.position : one_shot.position + samples_to_copy
                        ]
                        * one_shot.volume
                    )
                    one_shot.position += samples_to_copy

                # Mark as completed if finished
                if one_shot.position >= len(one_shot.data):
                    completed_indices.append(i)

            # Remove completed one-shots (in reverse to preserve indices)
            for i in reversed(completed_indices):
                self._one_shots.pop(i)

            # Process looping ambient sounds
            sounds_to_remove: list[str] = []
            for filename, looping in self._ambient_sounds.items():
                # Fade volume towards target
                if looping.current_volume < looping.target_volume:
                    looping.current_volume = min(
                        looping.current_volume + FADE_SPEED,
                        looping.target_volume,
                    )
                elif looping.current_volume > looping.target_volume:
                    looping.current_volume = max(
                        looping.current_volume - FADE_SPEED,
                        looping.target_volume,
                    )

                # Remove sounds that have faded out
                if looping.current_volume <= 0.0 and looping.target_volume <= 0.0:
                    sounds_to_remove.append(filename)
                    continue

                # Skip if effectively silent
                if looping.current_volume < 0.001:
                    continue

                # Vectorized looping sample copy (replaces per-sample Python loop)
                data = looping.data
                data_len = len(data)
                pos = looping.position

                # Build index array for where to read from (handles wrap-around)
                indices = (np.arange(FRAME_SIZE) + pos) % data_len
                mixed += data[indices] * looping.current_volume

                looping.position = (pos + FRAME_SIZE) % data_len

            # Remove faded out sounds
            for filename in sounds_to_remove:
                del self._ambient_sounds[filename]

        return mixed

    def clear(self) -> None:
        """Clear all sounds (e.g., on level transition)."""
        with self._lock:
            self._one_shots.clear()
            self._ambient_sounds.clear()
