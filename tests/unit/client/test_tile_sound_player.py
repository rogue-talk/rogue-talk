"""Tests for TileSoundPlayer timing accuracy."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from rogue_talk.common.constants import FRAME_SIZE, SAMPLE_RATE


class TestTileSoundPlayerTiming:
    """Tests that TileSoundPlayer maintains accurate timing without drift."""

    def test_playback_timing_does_not_drift_with_sleep_overshoot(self) -> None:
        """Test that playback loop uses absolute timing to prevent drift.

        With relative timing (sleep for frame_duration - elapsed), small
        sleep overshoots accumulate over time, causing audio to fall behind.

        With absolute timing (track target time and sleep until it), drift
        stays bounded regardless of how many frames are played.

        This test simulates realistic sleep overshoot (1ms per sleep) which
        is common on loaded systems or systems with coarse timer resolution.
        """
        from rogue_talk.audio.sound_loader import SoundCache
        from rogue_talk.client.tile_sound_player import TileSoundPlayer

        # Track when each frame is written
        write_times: list[float] = []
        write_event = threading.Event()
        num_frames = 100  # 2 seconds of audio

        # Create mock stream that records write timestamps
        mock_stream = MagicMock()

        def record_write(data: np.ndarray) -> None:
            write_times.append(time.perf_counter())
            if len(write_times) >= num_frames:
                write_event.set()

        mock_stream.write = record_write
        mock_stream.start = MagicMock()
        mock_stream.stop = MagicMock()

        # Patch time.sleep to always overshoot by 1ms
        # This simulates realistic scheduler behavior on loaded systems
        original_sleep = time.sleep
        sleep_overshoot = 0.001  # 1ms overshoot per sleep

        def overshooting_sleep(duration: float) -> None:
            if duration > 0:
                original_sleep(duration + sleep_overshoot)

        # Create player with mock sound cache
        mock_cache = MagicMock(spec=SoundCache)
        player = TileSoundPlayer(mock_cache)

        # Inject mock stream
        player._stream = mock_stream
        player._running = True

        # Start playback thread with patched sleep
        with patch.object(time, "sleep", overshooting_sleep):
            player._thread = threading.Thread(target=player._playback_loop, daemon=True)
            player._thread.start()

            # Wait for frames
            success = write_event.wait(timeout=15.0)
            player._running = False
            player._thread.join(timeout=1.0)

        assert success, (
            f"Playback loop didn't produce {num_frames} frames in 15 seconds"
        )
        assert len(write_times) >= num_frames, f"Only got {len(write_times)} frames"

        # Calculate expected frame duration
        frame_duration = FRAME_SIZE / SAMPLE_RATE  # 0.02 seconds (20ms)

        # Calculate drift: how far is frame N from where it should be?
        start_time = write_times[0]
        drifts = []
        for i, actual_time in enumerate(write_times):
            expected_time = start_time + i * frame_duration
            drift = actual_time - expected_time
            drifts.append(drift)

        # With relative timing and 1ms overshoot per frame:
        # After 100 frames, drift = 100 * 1ms = 100ms
        #
        # With absolute timing:
        # Drift stays bounded because next_frame_time is calculated from
        # initial time, not measured elapsed time. The sleep duration adjusts
        # to compensate for previous overshoots.
        quarter = num_frames // 4
        drift_at_25pct = drifts[quarter]
        final_drift = drifts[-1]

        # Maximum acceptable drift: 20ms (one frame)
        # With 1ms overshoot per frame and relative timing, we'd see ~100ms drift
        max_allowed_drift = 0.020  # 20ms

        assert final_drift < max_allowed_drift, (
            f"Timing drift accumulated to {final_drift * 1000:.1f}ms after {num_frames} frames. "
            f"Drift at 25%: {drift_at_25pct * 1000:.1f}ms. "
            f"With 1ms sleep overshoot per frame, relative timing would cause "
            f"~{num_frames}ms drift. Absolute timing should keep drift bounded."
        )

    def test_playback_recovers_from_large_delay(self) -> None:
        """Test that playback resets timing if it falls way behind.

        If processing takes too long (e.g., system hiccup), the loop should
        reset its timing reference rather than trying to catch up indefinitely.
        """
        from rogue_talk.audio.sound_loader import SoundCache
        from rogue_talk.client.tile_sound_player import TileSoundPlayer

        write_times: list[float] = []
        frame_count = [0]
        write_event = threading.Event()

        mock_stream = MagicMock()

        def record_write_with_delay(data: np.ndarray) -> None:
            write_times.append(time.perf_counter())
            frame_count[0] += 1

            # Simulate a 200ms hiccup on frame 10
            if frame_count[0] == 10:
                time.sleep(0.2)

            if frame_count[0] >= 30:
                write_event.set()

        mock_stream.write = record_write_with_delay
        mock_stream.start = MagicMock()
        mock_stream.stop = MagicMock()

        mock_cache = MagicMock(spec=SoundCache)
        player = TileSoundPlayer(mock_cache)
        player._stream = mock_stream
        player._running = True

        player._thread = threading.Thread(target=player._playback_loop, daemon=True)
        player._thread.start()

        # Should complete despite the hiccup
        success = write_event.wait(timeout=5.0)
        player._running = False
        player._thread.join(timeout=1.0)

        assert success, "Playback loop stalled after simulated delay"

        # After the hiccup, timing should recover (not stay 200ms behind)
        # Check drift of last few frames
        frame_duration = FRAME_SIZE / SAMPLE_RATE
        start_time = write_times[0]

        # Look at the last 5 frames - their inter-frame timing should be ~20ms
        last_intervals = [write_times[i] - write_times[i - 1] for i in range(-5, 0)]
        avg_interval = sum(last_intervals) / len(last_intervals)

        # Average interval should be close to frame_duration (within 5ms)
        assert abs(avg_interval - frame_duration) < 0.005, (
            f"After recovery, average frame interval was {avg_interval * 1000:.1f}ms "
            f"(expected {frame_duration * 1000:.1f}ms)"
        )
