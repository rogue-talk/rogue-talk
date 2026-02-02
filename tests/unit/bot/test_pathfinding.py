"""Tests for A* pathfinding algorithm."""

from __future__ import annotations

import pytest

from rogue_talk.bot.pathfinding import (
    _get_neighbors,
    _heuristic,
    find_path_with_custom_walkable,
)


class TestHeuristic:
    """Tests for the heuristic function."""

    def test_same_point(self) -> None:
        """Test heuristic for same point is 0."""
        assert _heuristic((5, 5), (5, 5)) == 0

    def test_horizontal_distance(self) -> None:
        """Test horizontal distance."""
        assert _heuristic((0, 0), (10, 0)) == 10

    def test_vertical_distance(self) -> None:
        """Test vertical distance."""
        assert _heuristic((0, 0), (0, 10)) == 10

    def test_diagonal_uses_chebyshev(self) -> None:
        """Test diagonal distance uses Chebyshev (max of dx, dy)."""
        # Chebyshev: max(3, 4) = 4
        assert _heuristic((0, 0), (3, 4)) == 4
        assert _heuristic((0, 0), (5, 5)) == 5


class TestGetNeighbors:
    """Tests for neighbor generation."""

    def test_returns_8_neighbors(self) -> None:
        """Test that all 8 neighbors are returned."""
        neighbors = _get_neighbors(5, 5)
        assert len(neighbors) == 8

    def test_neighbor_positions(self) -> None:
        """Test neighbor positions are correct."""
        neighbors = set(_get_neighbors(5, 5))
        expected = {
            (6, 5),
            (4, 5),
            (5, 6),
            (5, 4),  # Cardinal
            (6, 6),
            (6, 4),
            (4, 6),
            (4, 4),  # Diagonal
        }
        assert neighbors == expected


class TestFindPathWithCustomWalkable:
    """Tests for pathfinding with custom walkable function."""

    def test_start_equals_goal(self) -> None:
        """Test path when start equals goal."""

        def is_walkable(x: int, y: int) -> bool:
            return True

        path = find_path_with_custom_walkable((5, 5), (5, 5), is_walkable)
        assert path == [(5, 5)]

    def test_unreachable_goal(self) -> None:
        """Test path to unwalkable goal."""

        def is_walkable(x: int, y: int) -> bool:
            return (x, y) != (10, 10)

        path = find_path_with_custom_walkable((0, 0), (10, 10), is_walkable)
        assert path is None

    def test_simple_straight_path(self) -> None:
        """Test simple straight path."""

        def is_walkable(x: int, y: int) -> bool:
            return True

        path = find_path_with_custom_walkable((0, 0), (3, 0), is_walkable)
        assert path is not None
        assert path[0] == (0, 0)
        assert path[-1] == (3, 0)
        # Path should be reasonably short
        assert len(path) <= 4

    def test_path_around_obstacle(self) -> None:
        """Test path goes around obstacle."""
        # Simple wall blocking direct path
        wall = {(1, 0), (1, 1), (1, 2)}

        def is_walkable(x: int, y: int) -> bool:
            return (x, y) not in wall and -10 <= x <= 10 and -10 <= y <= 10

        path = find_path_with_custom_walkable((0, 1), (2, 1), is_walkable)
        assert path is not None
        assert path[0] == (0, 1)
        assert path[-1] == (2, 1)
        # Should not go through wall
        for pos in path:
            assert pos not in wall

    def test_diagonal_movement(self) -> None:
        """Test diagonal movement is used."""

        def is_walkable(x: int, y: int) -> bool:
            return True

        path = find_path_with_custom_walkable((0, 0), (3, 3), is_walkable)
        assert path is not None
        # Diagonal should take 4 steps (including start): (0,0), (1,1), (2,2), (3,3)
        assert len(path) == 4

    def test_diagonal_blocked_by_adjacent(self) -> None:
        """Test diagonal is blocked when adjacent tiles are walls."""
        # Wall configuration that blocks diagonal movement through (2,2):
        # . . . . .
        # . . # . .  <- wall at (2,1)
        # . . . . .
        # . # . . .  <- wall at (1,3)
        # . . . . .
        # Moving from (1,1) to (3,3) would prefer diagonal through (2,2)
        # but the wall at (2,1) blocks the diagonal from (1,1) to (2,2)
        walls = {(2, 1)}

        def is_walkable(x: int, y: int) -> bool:
            return (x, y) not in walls and 0 <= x <= 5 and 0 <= y <= 5

        path = find_path_with_custom_walkable((1, 1), (3, 3), is_walkable)
        assert path is not None
        # Direct diagonal would be (1,1)->(2,2)->(3,3) = 3 steps
        # But (2,1) wall blocks (1,1)->(2,2) diagonal, so must go around
        # Verify the path doesn't use the blocked diagonal
        for i, pos in enumerate(path[:-1]):
            next_pos = path[i + 1]
            dx = next_pos[0] - pos[0]
            dy = next_pos[1] - pos[1]
            # If diagonal, check both adjacent tiles are walkable
            if dx != 0 and dy != 0:
                # This should not violate the rule
                assert is_walkable(pos[0] + dx, pos[1])
                assert is_walkable(pos[0], pos[1] + dy)

    def test_max_iterations_limit(self) -> None:
        """Test that max_iterations prevents infinite loop."""

        # Unreachable due to surrounding walls
        def is_walkable(x: int, y: int) -> bool:
            # Only a small area is walkable, goal is unreachable
            return 0 <= x <= 2 and 0 <= y <= 2

        # Goal outside walkable area
        path = find_path_with_custom_walkable(
            (1, 1), (100, 100), is_walkable, max_iterations=100
        )
        assert path is None

    def test_path_includes_start_and_goal(self) -> None:
        """Test path includes both start and goal."""

        def is_walkable(x: int, y: int) -> bool:
            return True

        path = find_path_with_custom_walkable((0, 0), (5, 5), is_walkable)
        assert path is not None
        assert path[0] == (0, 0)
        assert path[-1] == (5, 5)

    def test_path_is_connected(self) -> None:
        """Test that path is a connected sequence."""

        def is_walkable(x: int, y: int) -> bool:
            return True

        path = find_path_with_custom_walkable((0, 0), (10, 10), is_walkable)
        assert path is not None

        # Each step should be adjacent to the next
        for i in range(len(path) - 1):
            dx = abs(path[i + 1][0] - path[i][0])
            dy = abs(path[i + 1][1] - path[i][1])
            # Max 1 step in each direction (8-directional)
            assert dx <= 1 and dy <= 1
            # Must move at least in one direction
            assert dx > 0 or dy > 0

    def test_enclosed_area_unreachable(self) -> None:
        """Test goal inside enclosed area is unreachable."""
        # Goal at (5,5) is surrounded by walls
        walls = {(4, 4), (5, 4), (6, 4), (4, 5), (6, 5), (4, 6), (5, 6), (6, 6)}

        def is_walkable(x: int, y: int) -> bool:
            return (x, y) not in walls and 0 <= x <= 10 and 0 <= y <= 10

        path = find_path_with_custom_walkable((0, 0), (5, 5), is_walkable)
        assert path is None

    def test_narrow_corridor(self) -> None:
        """Test pathfinding through narrow corridor."""

        # Corridor: only y=5 is walkable from x=0 to x=10
        def is_walkable(x: int, y: int) -> bool:
            if 0 <= x <= 10:
                return y == 5 or (x == 0 and 0 <= y <= 5) or (x == 10 and 5 <= y <= 10)
            return False

        path = find_path_with_custom_walkable((0, 0), (10, 10), is_walkable)
        assert path is not None
        # Should go down to corridor, across, then up
        assert path[0] == (0, 0)
        assert path[-1] == (10, 10)
