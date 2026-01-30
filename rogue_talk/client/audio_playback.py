"""Audio playback with decoding and mixing."""

from collections import defaultdict
from typing import Any

import numpy as np
import numpy.typing as npt
import sounddevice as sd

from ..audio.mixer import AudioMixer
from ..audio.opus_codec import OpusDecoder
from ..common.constants import CHANNELS, FRAME_SIZE, SAMPLE_RATE
from .jitter_buffer import AudioPacket, JitterBuffer


class AudioPlayback:
    """Manages receiving, decoding, and playing back audio from multiple players."""

    def __init__(self) -> None:
        self.jitter_buffers: dict[int, JitterBuffer] = defaultdict(JitterBuffer)
        self.decoders: dict[int, OpusDecoder] = {}
        self.mixer = AudioMixer()
        self.stream: sd.OutputStream | None = None

    def start(self) -> None:
        """Start audio output stream."""
        self.stream = sd.OutputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=np.float32,
            blocksize=FRAME_SIZE,
            callback=self._audio_callback,
        )
        self.stream.start()

    def stop(self) -> None:
        """Stop audio output stream."""
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

    def receive_audio_frame(
        self, player_id: int, timestamp_ms: int, opus_data: bytes, volume: float
    ) -> None:
        """Process an incoming audio frame from the network."""
        packet = AudioPacket(
            timestamp_ms=timestamp_ms,
            opus_data=opus_data,
            volume=volume,
        )
        self.jitter_buffers[player_id].add_packet(packet)

    def remove_player(self, player_id: int) -> None:
        """Clean up resources for a player who left."""
        self.jitter_buffers.pop(player_id, None)
        self.decoders.pop(player_id, None)
        self.mixer.remove_player(player_id)

    def _get_decoder(self, player_id: int) -> OpusDecoder:
        """Get or create decoder for a player."""
        if player_id not in self.decoders:
            self.decoders[player_id] = OpusDecoder()
        return self.decoders[player_id]

    def _audio_callback(
        self,
        outdata: npt.NDArray[np.float32],
        frames: int,
        time_info: Any,
        status: sd.CallbackFlags,
    ) -> None:
        """Sounddevice callback - runs in separate thread."""
        # Process each player's jitter buffer
        for player_id, jitter_buffer in list(self.jitter_buffers.items()):
            packet = jitter_buffer.get_next_packet()
            if packet is not None:
                # Decode Opus to PCM
                decoder = self._get_decoder(player_id)
                pcm = decoder.decode(packet.opus_data)

                # Add to mixer with volume
                self.mixer.add_frame(player_id, pcm, packet.volume)

        # Mix all streams
        mixed = self.mixer.mix()

        # Write to output (reshape for sounddevice)
        outdata[:] = mixed.reshape(-1, 1)
