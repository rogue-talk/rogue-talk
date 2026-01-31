"""Opus encoder/decoder wrapper."""

from typing import Any

import numpy as np
import numpy.typing as npt
import opuslib_next as opuslib
import opuslib_next.api.ctl as opus_ctl
import opuslib_next.api.encoder as opus_encoder_api

from ..common.constants import CHANNELS, FRAME_SIZE, OPUS_BITRATE, SAMPLE_RATE


class OpusEncoder:
    encoder: Any

    def __init__(self) -> None:
        self.encoder = opuslib.Encoder(SAMPLE_RATE, CHANNELS, opuslib.APPLICATION_VOIP)
        self.encoder.bitrate = OPUS_BITRATE

        # Enable FEC for packet loss resilience (embeds redundant data for previous frame)
        # Note: Using direct CTL API because property setters have a bug
        opus_encoder_api.encoder_ctl(
            self.encoder.encoder_state, opus_ctl.set_inband_fec, 1
        )
        opus_encoder_api.encoder_ctl(
            self.encoder.encoder_state,
            opus_ctl.set_packet_loss_perc,
            10,  # Expect 10%
        )

        # Enable DTX to save bandwidth during silence
        opus_encoder_api.encoder_ctl(self.encoder.encoder_state, opus_ctl.set_dtx, 1)

    def encode(self, pcm_data: npt.NDArray[np.float32]) -> bytes:
        """Encode PCM float32 data to Opus."""
        # Convert float32 [-1.0, 1.0] to int16
        pcm_int16 = (pcm_data * 32767).astype(np.int16)
        result: bytes = self.encoder.encode(pcm_int16.tobytes(), FRAME_SIZE)
        return result


class OpusDecoder:
    decoder: Any

    def __init__(self) -> None:
        self.decoder = opuslib.Decoder(SAMPLE_RATE, CHANNELS)

    def decode(self, opus_data: bytes) -> npt.NDArray[np.float32]:
        """Decode Opus data to PCM float32."""
        pcm_bytes: bytes = self.decoder.decode(opus_data, FRAME_SIZE)
        pcm_int16 = np.frombuffer(pcm_bytes, dtype=np.int16)
        return pcm_int16.astype(np.float32) / 32767.0
