"""Shared constants for audio and game settings."""

# Audio parameters
SAMPLE_RATE = 48000  # Hz (Opus native)
CHANNELS = 1  # Mono for voice
FRAME_SIZE = 960  # 20ms at 48kHz
OPUS_BITRATE = 24000  # bps
JITTER_BUFFER_MS = 60

# Proximity audio settings
AUDIO_MAX_DISTANCE = 10.0  # Beyond this, volume = 0
AUDIO_FULL_VOLUME_DISTANCE = 2.0  # Within this, volume = 1.0

# Room defaults
DEFAULT_ROOM_WIDTH = 20
DEFAULT_ROOM_HEIGHT = 15

# Movement
MOVEMENT_TICK_INTERVAL = 0.1  # Seconds between moves (10 tiles/second max)

# Network
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7777
