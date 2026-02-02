"""Tests for jitter buffer audio packet ordering."""

from __future__ import annotations

import pytest

from rogue_talk.client.jitter_buffer import AudioPacket, JitterBuffer


class TestAudioPacket:
    """Tests for AudioPacket dataclass."""

    def test_create_packet(self) -> None:
        """Test creating an audio packet."""
        packet = AudioPacket(timestamp_ms=1000, opus_data=b"\x00\x01", volume=0.5)
        assert packet.timestamp_ms == 1000
        assert packet.opus_data == b"\x00\x01"
        assert packet.volume == 0.5


class TestJitterBuffer:
    """Tests for JitterBuffer class."""

    def test_initial_state(self) -> None:
        """Test buffer initial state."""
        buf = JitterBuffer()
        assert buf.has_started() is False
        assert buf.get_next_packet() is None

    def test_min_packets_before_playback(self) -> None:
        """Test that playback waits for min_packets."""
        buf = JitterBuffer(min_packets=3, max_packets=10)

        # Add 2 packets - should not start playback yet
        buf.add_packet(AudioPacket(0, b"data0", 1.0))
        buf.add_packet(AudioPacket(20, b"data1", 1.0))

        assert buf.has_started() is False
        assert buf.get_next_packet() is None

        # Add third packet - now should start
        buf.add_packet(AudioPacket(40, b"data2", 1.0))

        packet = buf.get_next_packet()
        assert packet is not None
        assert packet.timestamp_ms == 0
        assert buf.has_started() is True

    def test_packets_in_order(self) -> None:
        """Test packets come out in timestamp order."""
        buf = JitterBuffer(min_packets=2, max_packets=10)

        buf.add_packet(AudioPacket(0, b"first", 1.0))
        buf.add_packet(AudioPacket(20, b"second", 1.0))
        buf.add_packet(AudioPacket(40, b"third", 1.0))

        p1 = buf.get_next_packet()
        p2 = buf.get_next_packet()
        p3 = buf.get_next_packet()

        assert p1 is not None and p1.timestamp_ms == 0
        assert p2 is not None and p2.timestamp_ms == 20
        assert p3 is not None and p3.timestamp_ms == 40

    def test_out_of_order_insertion(self) -> None:
        """Test that out-of-order packets are reordered."""
        buf = JitterBuffer(min_packets=3, max_packets=10)

        # Add packets out of order
        buf.add_packet(AudioPacket(40, b"third", 1.0))
        buf.add_packet(AudioPacket(0, b"first", 1.0))
        buf.add_packet(AudioPacket(20, b"second", 1.0))

        # Should come out in order
        p1 = buf.get_next_packet()
        p2 = buf.get_next_packet()
        p3 = buf.get_next_packet()

        assert p1 is not None and p1.opus_data == b"first"
        assert p2 is not None and p2.opus_data == b"second"
        assert p3 is not None and p3.opus_data == b"third"

    def test_max_packets_drops_oldest(self) -> None:
        """Test that buffer drops oldest packets when full."""
        buf = JitterBuffer(min_packets=2, max_packets=3)

        # Add 4 packets - should drop oldest
        buf.add_packet(AudioPacket(0, b"drop_me", 1.0))
        buf.add_packet(AudioPacket(20, b"second", 1.0))
        buf.add_packet(AudioPacket(40, b"third", 1.0))
        buf.add_packet(AudioPacket(60, b"fourth", 1.0))

        # First packet should be dropped
        p1 = buf.get_next_packet()
        assert p1 is not None
        assert p1.opus_data != b"drop_me"
        assert p1.timestamp_ms == 20

    def test_reset(self) -> None:
        """Test resetting buffer."""
        buf = JitterBuffer(min_packets=2, max_packets=10)

        buf.add_packet(AudioPacket(0, b"data", 1.0))
        buf.add_packet(AudioPacket(20, b"data", 1.0))
        buf.get_next_packet()  # Start playback

        assert buf.has_started() is True

        buf.reset()

        assert buf.has_started() is False
        assert buf.get_next_packet() is None

    def test_empty_after_all_consumed(self) -> None:
        """Test buffer returns None when empty."""
        buf = JitterBuffer(min_packets=2, max_packets=10)

        buf.add_packet(AudioPacket(0, b"data", 1.0))
        buf.add_packet(AudioPacket(20, b"data", 1.0))

        buf.get_next_packet()
        buf.get_next_packet()

        assert buf.get_next_packet() is None

    def test_volume_preserved(self) -> None:
        """Test that volume is preserved through buffer."""
        buf = JitterBuffer(min_packets=2, max_packets=10)

        buf.add_packet(AudioPacket(0, b"data", 0.5))
        buf.add_packet(AudioPacket(20, b"data", 0.75))

        p1 = buf.get_next_packet()
        p2 = buf.get_next_packet()

        assert p1 is not None and p1.volume == 0.5
        assert p2 is not None and p2.volume == 0.75

    def test_continues_after_started(self) -> None:
        """Test that playback continues even with low packets after start."""
        buf = JitterBuffer(min_packets=3, max_packets=10)

        # Start with enough packets
        for i in range(3):
            buf.add_packet(AudioPacket(i * 20, b"data", 1.0))

        buf.get_next_packet()  # Start playback
        buf.get_next_packet()
        buf.get_next_packet()

        # Add one more packet - should still work since started
        buf.add_packet(AudioPacket(60, b"new", 1.0))
        packet = buf.get_next_packet()
        assert packet is not None
        assert packet.opus_data == b"new"
