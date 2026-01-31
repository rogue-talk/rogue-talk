"""Per-player jitter buffer for smooth audio playback."""

import collections
from dataclasses import dataclass


@dataclass
class AudioPacket:
    timestamp_ms: int
    opus_data: bytes
    volume: float


class JitterBuffer:
    """Buffers audio packets to smooth out network jitter."""

    # Gap threshold: if timestamp jumps by more than this, reset buffer
    # This handles VAD silence gaps - treat as new speech burst
    GAP_THRESHOLD_MS = 500

    def __init__(self, min_packets: int = 5, max_packets: int = 15):
        # Increased defaults for WiFi tolerance:
        # min_packets=5 (100ms) gives more buffer for jitter
        # max_packets=15 (300ms) allows more buffering before drops
        self.min_packets = min_packets
        self.max_packets = max_packets
        self.packets: collections.deque[AudioPacket] = collections.deque()
        self.playback_started = False
        self._last_timestamp_ms = 0

    def add_packet(self, packet: AudioPacket) -> None:
        """Add a packet, maintaining timestamp order."""
        # Detect large timestamp gaps (e.g., from VAD silence)
        # Reset buffer to re-sync playback
        if self.playback_started and self.packets:
            gap = packet.timestamp_ms - self._last_timestamp_ms
            if gap > self.GAP_THRESHOLD_MS:
                self.reset()

        self._last_timestamp_ms = packet.timestamp_ms

        if not self.packets or packet.timestamp_ms >= self.packets[-1].timestamp_ms:
            self.packets.append(packet)
        else:
            # Insert in sorted position for out-of-order packets
            for i, p in enumerate(self.packets):
                if packet.timestamp_ms < p.timestamp_ms:
                    self.packets.insert(i, packet)
                    break

        # Drop oldest packets if buffer is too full (prevents latency growth)
        while len(self.packets) > self.max_packets:
            self.packets.popleft()

    def get_next_packet(self) -> AudioPacket | None:
        """Get next packet for playback, or None if not ready."""
        if not self.packets:
            return None

        # Wait for a few packets before starting playback
        if not self.playback_started:
            if len(self.packets) < self.min_packets:
                return None
            self.playback_started = True

        return self.packets.popleft()

    def has_started(self) -> bool:
        """Check if playback has started for this buffer."""
        return self.playback_started

    def reset(self) -> None:
        """Reset buffer state."""
        self.packets.clear()
        self.playback_started = False
