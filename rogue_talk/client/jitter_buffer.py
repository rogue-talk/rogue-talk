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

    def __init__(self, min_packets: int = 3, max_packets: int = 10):
        self.min_packets = min_packets
        self.max_packets = max_packets  # ~200ms at 20ms per frame
        self.packets: collections.deque[AudioPacket] = collections.deque()
        self.playback_started = False

    def add_packet(self, packet: AudioPacket) -> None:
        """Add a packet, maintaining timestamp order."""
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

    def reset(self) -> None:
        """Reset buffer state."""
        self.packets.clear()
        self.playback_started = False
