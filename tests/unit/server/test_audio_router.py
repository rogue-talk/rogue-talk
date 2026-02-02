"""Tests for proximity-based audio routing."""

from __future__ import annotations

import pytest

from rogue_talk.common.constants import AUDIO_FULL_VOLUME_DISTANCE, AUDIO_MAX_DISTANCE
from rogue_talk.server.audio_router import (
    _VOLUME_TABLE,
    clear_recipient_cache,
    get_audio_recipients,
    get_volume,
)

from tests.conftest import MockPlayer


class TestGetVolume:
    """Tests for get_volume function."""

    def test_same_position(self) -> None:
        """Test volume at same position is 1.0."""
        assert get_volume(0, 0) == 1.0

    def test_within_full_volume_distance(self) -> None:
        """Test volume within full volume distance is 1.0."""
        # AUDIO_FULL_VOLUME_DISTANCE is 2.0, so distance of 2 should be full volume
        assert get_volume(2, 0) == 1.0
        assert get_volume(0, 2) == 1.0
        assert get_volume(1, 1) == 1.0  # sqrt(2) ≈ 1.41

    def test_beyond_max_distance(self) -> None:
        """Test volume beyond max distance is 0.0."""
        # AUDIO_MAX_DISTANCE is 10.0, so anything > 10 should be 0
        assert get_volume(11, 0) == 0.0
        assert get_volume(0, 11) == 0.0
        assert get_volume(8, 8) == 0.0  # sqrt(128) ≈ 11.3

    def test_at_max_distance(self) -> None:
        """Test volume at exactly max distance."""
        # At distance 10, volume should be close to 0
        vol = get_volume(10, 0)
        assert vol >= 0.0
        assert vol < 0.01  # Should be very close to 0

    def test_linear_falloff(self) -> None:
        """Test that volume decreases with distance."""
        vol_near = get_volume(3, 0)  # distance 3
        vol_mid = get_volume(5, 0)  # distance 5
        vol_far = get_volume(8, 0)  # distance 8

        assert vol_near > vol_mid > vol_far
        assert vol_near > 0.0
        assert vol_far > 0.0

    def test_symmetric(self) -> None:
        """Test volume is symmetric in x and y."""
        assert get_volume(5, 0) == get_volume(0, 5)
        assert get_volume(3, 4) == get_volume(4, 3)
        assert get_volume(-5, 0) == get_volume(5, 0)

    def test_diagonal_distance(self) -> None:
        """Test diagonal distance calculation."""
        # distance of (3, 4) = 5, which is within falloff range
        vol = get_volume(3, 4)
        assert 0.0 < vol < 1.0


class TestVolumeTable:
    """Tests for the pre-computed volume table."""

    def test_table_exists(self) -> None:
        """Test that volume table is computed."""
        assert len(_VOLUME_TABLE) > 0

    def test_table_starts_at_one(self) -> None:
        """Test that volume at distance 0 is 1.0."""
        assert _VOLUME_TABLE[0] == 1.0

    def test_table_monotonic_decrease(self) -> None:
        """Test that volume decreases monotonically (mostly)."""
        # After full volume distance squared, should decrease
        full_vol_sq = int(AUDIO_FULL_VOLUME_DISTANCE**2)
        for i in range(full_vol_sq + 1, len(_VOLUME_TABLE)):
            assert _VOLUME_TABLE[i] <= _VOLUME_TABLE[i - 1]


class TestGetAudioRecipients:
    """Tests for get_audio_recipients function."""

    def setup_method(self) -> None:
        """Clear cache before each test."""
        clear_recipient_cache()

    def test_muted_source_returns_empty(self) -> None:
        """Test that muted source returns no recipients."""
        source = MockPlayer(id=1, x=0, y=0, is_muted=True)
        players = {
            1: source,  # type: ignore[dict-item]
            2: MockPlayer(id=2, x=1, y=0),  # type: ignore[dict-item]
        }
        recipients = get_audio_recipients(source, players)  # type: ignore[arg-type]
        assert recipients == []

    def test_no_other_players(self) -> None:
        """Test with only the source player."""
        source = MockPlayer(id=1, x=0, y=0)
        players = {1: source}  # type: ignore[dict-item]
        recipients = get_audio_recipients(source, players)  # type: ignore[arg-type]
        assert recipients == []

    def test_nearby_player_receives(self) -> None:
        """Test that nearby player is in recipients."""
        source = MockPlayer(id=1, x=0, y=0)
        nearby = MockPlayer(id=2, x=2, y=0)
        players = {1: source, 2: nearby}  # type: ignore[dict-item]

        recipients = get_audio_recipients(source, players)  # type: ignore[arg-type]
        assert len(recipients) == 1
        assert recipients[0][0] == nearby
        assert recipients[0][1] == 1.0  # Within full volume distance

    def test_far_player_excluded(self) -> None:
        """Test that far player is not in recipients."""
        source = MockPlayer(id=1, x=0, y=0)
        far = MockPlayer(id=2, x=100, y=100)
        players = {1: source, 2: far}  # type: ignore[dict-item]

        recipients = get_audio_recipients(source, players)  # type: ignore[arg-type]
        assert recipients == []

    def test_multiple_recipients_with_volumes(self) -> None:
        """Test multiple recipients at different distances."""
        source = MockPlayer(id=1, x=0, y=0)
        near = MockPlayer(id=2, x=1, y=0)
        mid = MockPlayer(id=3, x=5, y=0)
        far = MockPlayer(id=4, x=20, y=0)  # Beyond max distance
        players = {1: source, 2: near, 3: mid, 4: far}  # type: ignore[dict-item]

        recipients = get_audio_recipients(source, players)  # type: ignore[arg-type]

        # Should have 2 recipients (near and mid, not far)
        assert len(recipients) == 2

        recipient_ids = {r[0].id for r in recipients}
        assert 2 in recipient_ids
        assert 3 in recipient_ids
        assert 4 not in recipient_ids

    def test_source_not_in_recipients(self) -> None:
        """Test that source is not in its own recipients list."""
        source = MockPlayer(id=1, x=0, y=0)
        other = MockPlayer(id=2, x=1, y=0)
        players = {1: source, 2: other}  # type: ignore[dict-item]

        recipients = get_audio_recipients(source, players)  # type: ignore[arg-type]
        recipient_ids = {r[0].id for r in recipients}
        assert 1 not in recipient_ids


class TestRecipientCache:
    """Tests for recipient caching."""

    def setup_method(self) -> None:
        """Clear cache before each test."""
        clear_recipient_cache()

    def test_clear_all(self) -> None:
        """Test clearing all cache."""
        source = MockPlayer(id=1, x=0, y=0)
        other = MockPlayer(id=2, x=1, y=0)
        players = {1: source, 2: other}  # type: ignore[dict-item]

        # Populate cache
        get_audio_recipients(source, players)  # type: ignore[arg-type]

        # Clear and verify no errors
        clear_recipient_cache()

    def test_clear_specific_player(self) -> None:
        """Test clearing cache for specific player."""
        source = MockPlayer(id=1, x=0, y=0)
        other = MockPlayer(id=2, x=1, y=0)
        players = {1: source, 2: other}  # type: ignore[dict-item]

        # Populate cache
        get_audio_recipients(source, players)  # type: ignore[arg-type]

        # Clear specific player
        clear_recipient_cache(1)
