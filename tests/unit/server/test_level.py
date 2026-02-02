"""Tests for level loading and representation."""

from __future__ import annotations

import pytest

from rogue_talk.server.level import Level


class TestLevelFromString:
    """Tests for Level.from_string()."""

    def test_simple_level(self, sample_level_string: str) -> None:
        """Test parsing a simple level."""
        level = Level.from_string(sample_level_string)

        assert level.width == 10
        assert level.height == 5

    def test_spawn_positions_extracted(self, sample_level_string: str) -> None:
        """Test that spawn positions are found."""
        level = Level.from_string(sample_level_string)

        # 'S' is at position (4, 2) in the sample
        assert len(level.spawn_positions) == 1
        assert (4, 2) in level.spawn_positions

    def test_spawn_replaced_with_floor(self, sample_level_string: str) -> None:
        """Test that spawn marker is replaced with floor."""
        level = Level.from_string(sample_level_string)

        # Where 'S' was, should now be '.'
        assert level.tiles[2][4] == "."

    def test_empty_level(self) -> None:
        """Test parsing empty level."""
        level = Level.from_string("")
        # Empty string splits to [""], so height=1, width=0
        assert level.width == 0
        assert level.height == 1
        assert level.tiles == [[]]

    def test_single_line_level(self) -> None:
        """Test parsing single line level."""
        level = Level.from_string("#####")
        assert level.width == 5
        assert level.height == 1

    def test_irregular_width(self) -> None:
        """Test level with varying line lengths."""
        content = """###
#.
#####"""
        level = Level.from_string(content)

        # Width should be max line length
        assert level.width == 5
        assert level.height == 3

        # Short lines should be padded with space
        assert level.tiles[1][2] == " "
        assert level.tiles[1][3] == " "

    def test_multiple_spawns(self) -> None:
        """Test level with multiple spawn points."""
        content = """#####
#S.S#
#####"""
        level = Level.from_string(content)

        assert len(level.spawn_positions) == 2
        assert (1, 1) in level.spawn_positions
        assert (3, 1) in level.spawn_positions


class TestLevelIsWalkable:
    """Tests for Level.is_walkable()."""

    def test_floor_is_walkable(self, sample_level_string: str) -> None:
        """Test floor tiles are walkable."""
        level = Level.from_string(sample_level_string)
        assert level.is_walkable(1, 1) is True

    def test_wall_is_not_walkable(self, sample_level_string: str) -> None:
        """Test wall tiles are not walkable."""
        level = Level.from_string(sample_level_string)
        assert level.is_walkable(0, 0) is False

    def test_out_of_bounds_not_walkable(self, sample_level_string: str) -> None:
        """Test out-of-bounds is not walkable."""
        level = Level.from_string(sample_level_string)
        assert level.is_walkable(-1, 0) is False
        assert level.is_walkable(0, -1) is False
        assert level.is_walkable(100, 0) is False
        assert level.is_walkable(0, 100) is False


class TestLevelGetTile:
    """Tests for Level.get_tile()."""

    def test_get_valid_tile(self, sample_level_string: str) -> None:
        """Test getting a valid tile."""
        level = Level.from_string(sample_level_string)
        assert level.get_tile(0, 0) == "#"
        assert level.get_tile(1, 1) == "."

    def test_get_out_of_bounds(self, sample_level_string: str) -> None:
        """Test getting out-of-bounds returns space."""
        level = Level.from_string(sample_level_string)
        assert level.get_tile(-1, 0) == " "
        assert level.get_tile(100, 100) == " "


class TestLevelGetSpawnPosition:
    """Tests for Level.get_spawn_position()."""

    def test_returns_spawn(self, sample_level_string: str) -> None:
        """Test getting spawn position."""
        level = Level.from_string(sample_level_string)
        x, y = level.get_spawn_position()
        assert (x, y) == (4, 2)

    def test_fallback_to_walkable(self) -> None:
        """Test fallback when no spawn markers."""
        content = """###
#.#
###"""
        level = Level.from_string(content)

        x, y = level.get_spawn_position()
        # Should find the only walkable tile at (1, 1)
        assert level.is_walkable(x, y)


class TestLevelSerialization:
    """Tests for Level byte serialization."""

    def test_roundtrip(self, sample_level_string: str) -> None:
        """Test serialization roundtrip."""
        original = Level.from_string(sample_level_string)
        data = original.to_bytes()
        restored = Level.from_bytes(data)

        assert restored.width == original.width
        assert restored.height == original.height
        assert restored.tiles == original.tiles

    def test_roundtrip_empty(self) -> None:
        """Test roundtrip of minimal level."""
        content = "#"
        original = Level.from_string(content)
        data = original.to_bytes()
        restored = Level.from_bytes(data)

        assert restored.width == 1
        assert restored.height == 1
        assert restored.tiles == [["#"]]

    def test_bytes_format(self, sample_level_string: str) -> None:
        """Test that bytes start with dimensions."""
        level = Level.from_string(sample_level_string)
        data = level.to_bytes()

        # First 4 bytes should be width (2) and height (2)
        import struct

        width, height = struct.unpack(">HH", data[:4])
        assert width == level.width
        assert height == level.height
