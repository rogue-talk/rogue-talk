"""Integration tests for multi-player audio routing."""

from __future__ import annotations

import pytest

from rogue_talk.common.constants import AUDIO_FULL_VOLUME_DISTANCE, AUDIO_MAX_DISTANCE
from rogue_talk.server.audio_router import (
    clear_recipient_cache,
    get_audio_recipients,
    get_volume,
)

from tests.conftest import MockPlayer


@pytest.mark.integration
class TestMultiPlayerAudioRouting:
    """Integration tests for audio routing with multiple players."""

    def setup_method(self) -> None:
        """Clear cache before each test."""
        clear_recipient_cache()

    def test_three_player_triangle(self) -> None:
        """Test audio routing with three players in a triangle."""
        # Player positions form a triangle
        p1 = MockPlayer(id=1, x=0, y=0)
        p2 = MockPlayer(id=2, x=5, y=0)  # 5 units from p1
        p3 = MockPlayer(id=3, x=0, y=5)  # 5 units from p1

        players = {1: p1, 2: p2, 3: p3}  # type: ignore[dict-item]

        # From p1's perspective
        recipients = get_audio_recipients(p1, players)  # type: ignore[arg-type]
        assert len(recipients) == 2

        # Check both p2 and p3 receive audio
        recipient_ids = {r[0].id for r in recipients}
        assert 2 in recipient_ids
        assert 3 in recipient_ids

        # Check volumes are equal (same distance)
        volumes = {r[0].id: r[1] for r in recipients}
        assert abs(volumes[2] - volumes[3]) < 0.01

    def test_line_of_players(self) -> None:
        """Test audio routing with players in a line."""
        # Players at increasing distances
        players_list = [
            MockPlayer(id=i, x=i * 3, y=0) for i in range(5)
        ]  # 0, 3, 6, 9, 12 units away
        players = {p.id: p for p in players_list}  # type: ignore[dict-item]

        source = players_list[0]
        recipients = get_audio_recipients(source, players)  # type: ignore[arg-type]

        # Players at 3, 6, 9 should hear (distance <= 10)
        # Player at 12 should not hear (distance > 10)
        recipient_ids = {r[0].id for r in recipients}
        assert 1 in recipient_ids  # distance 3
        assert 2 in recipient_ids  # distance 6
        assert 3 in recipient_ids  # distance 9
        assert 4 not in recipient_ids  # distance 12, beyond max

        # Check volumes decrease with distance
        volumes = sorted([(r[1], r[0].id) for r in recipients], reverse=True)
        # Volumes should be in decreasing order
        for i in range(len(volumes) - 1):
            assert volumes[i][0] >= volumes[i + 1][0]

    def test_scattered_players(self) -> None:
        """Test audio routing with players scattered around."""
        source = MockPlayer(id=0, x=50, y=50)
        players = {0: source}  # type: ignore[dict-item]

        # Add players at various positions
        positions = [
            (51, 50),  # distance 1 - full volume
            (45, 50),  # distance 5 - partial volume
            (41, 50),  # distance 9 - near zero but audible
            (70, 50),  # distance 20 - out of range
            (50, 70),  # distance 20 - out of range
        ]

        for i, (x, y) in enumerate(positions, start=1):
            players[i] = MockPlayer(id=i, x=x, y=y)  # type: ignore[assignment]

        recipients = get_audio_recipients(source, players)  # type: ignore[arg-type]

        # Should only hear nearby players (within max distance)
        recipient_ids = {r[0].id for r in recipients}
        assert 1 in recipient_ids  # distance 1
        assert 2 in recipient_ids  # distance 5
        assert 3 in recipient_ids  # distance 9 (within range)
        assert 4 not in recipient_ids  # distance 20
        assert 5 not in recipient_ids  # distance 20

    def test_mutual_hearing(self) -> None:
        """Test that two nearby players can hear each other."""
        p1 = MockPlayer(id=1, x=0, y=0)
        p2 = MockPlayer(id=2, x=3, y=0)
        players = {1: p1, 2: p2}  # type: ignore[dict-item]

        # p1 can hear p2
        r1 = get_audio_recipients(p1, players)  # type: ignore[arg-type]
        assert len(r1) == 1 and r1[0][0].id == 2

        # p2 can hear p1
        r2 = get_audio_recipients(p2, players)  # type: ignore[arg-type]
        assert len(r2) == 1 and r2[0][0].id == 1

        # Volumes should be equal
        assert abs(r1[0][1] - r2[0][1]) < 0.001

    def test_muted_players_excluded(self) -> None:
        """Test that muted source produces no recipients."""
        p1 = MockPlayer(id=1, x=0, y=0, is_muted=True)
        p2 = MockPlayer(id=2, x=1, y=0)
        p3 = MockPlayer(id=3, x=2, y=0)
        players = {1: p1, 2: p2, 3: p3}  # type: ignore[dict-item]

        recipients = get_audio_recipients(p1, players)  # type: ignore[arg-type]
        assert recipients == []

    def test_large_player_count(self) -> None:
        """Test audio routing with many players."""
        source = MockPlayer(id=0, x=50, y=50)
        players = {0: source}  # type: ignore[dict-item]

        # Add 100 players in a grid
        for i in range(1, 101):
            x = 40 + (i % 21)  # x from 40 to 60
            y = 40 + (i // 21)  # y from 40 to 44
            players[i] = MockPlayer(id=i, x=x, y=y)  # type: ignore[assignment]

        recipients = get_audio_recipients(source, players)  # type: ignore[arg-type]

        # Should have found many recipients
        assert len(recipients) > 10

        # All recipients should be within range
        for player, volume in recipients:
            dx = player.x - source.x
            dy = player.y - source.y
            assert volume == get_volume(dx, dy)
            assert volume > 0.0
