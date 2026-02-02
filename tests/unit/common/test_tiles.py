"""Tests for tile definitions and lookups."""

from __future__ import annotations

import pytest

from rogue_talk.common.tiles import (
    DEFAULT_TILE,
    TILES,
    TileDef,
    get_tile,
    is_walkable,
)


class TestTileDef:
    """Tests for TileDef dataclass."""

    def test_defaults_blocks_sight(self) -> None:
        """Test that blocks_sight defaults based on walkable."""
        walkable_tile = TileDef(char=".", walkable=True, color="white")
        assert walkable_tile.blocks_sight is False

        wall_tile = TileDef(char="#", walkable=False, color="white")
        assert wall_tile.blocks_sight is True

    def test_defaults_blocks_sound(self) -> None:
        """Test that blocks_sound defaults based on walkable."""
        walkable_tile = TileDef(char=".", walkable=True, color="white")
        assert walkable_tile.blocks_sound is False

        wall_tile = TileDef(char="#", walkable=False, color="white")
        assert wall_tile.blocks_sound is True

    def test_explicit_blocks_sight(self) -> None:
        """Test explicit blocks_sight overrides default."""
        tile = TileDef(char="G", walkable=True, color="green", blocks_sight=True)
        assert tile.blocks_sight is True

    def test_explicit_blocks_sound(self) -> None:
        """Test explicit blocks_sound overrides default."""
        tile = TileDef(char="W", walkable=False, color="blue", blocks_sound=False)
        assert tile.blocks_sound is False


class TestTilesLoaded:
    """Tests that tiles are loaded from JSON."""

    def test_tiles_not_empty(self) -> None:
        """Test that TILES dict is loaded."""
        assert len(TILES) > 0

    def test_default_tile_exists(self) -> None:
        """Test that DEFAULT_TILE is set."""
        assert DEFAULT_TILE is not None
        assert isinstance(DEFAULT_TILE, TileDef)

    def test_common_tiles_exist(self) -> None:
        """Test that common tile types exist."""
        # These are commonly used tiles
        assert "." in TILES  # Floor
        assert "#" in TILES  # Wall

    def test_floor_is_walkable(self) -> None:
        """Test that floor tile is walkable."""
        floor = TILES.get(".")
        assert floor is not None
        assert floor.walkable is True

    def test_wall_is_not_walkable(self) -> None:
        """Test that wall tile is not walkable."""
        wall = TILES.get("#")
        assert wall is not None
        assert wall.walkable is False


class TestGetTile:
    """Tests for get_tile function."""

    def test_get_known_tile(self) -> None:
        """Test getting a known tile."""
        tile = get_tile("#")
        assert tile.char == "#"
        assert tile.walkable is False

    def test_get_unknown_tile_returns_default(self) -> None:
        """Test that unknown tile returns DEFAULT_TILE."""
        tile = get_tile("X")  # Assuming X is not defined
        assert tile == DEFAULT_TILE

    def test_get_floor_tile(self) -> None:
        """Test getting floor tile."""
        tile = get_tile(".")
        assert tile.walkable is True


class TestIsWalkable:
    """Tests for is_walkable function."""

    def test_floor_is_walkable(self) -> None:
        """Test floor is walkable."""
        assert is_walkable(".") is True

    def test_wall_is_not_walkable(self) -> None:
        """Test wall is not walkable."""
        assert is_walkable("#") is False

    def test_unknown_uses_default(self) -> None:
        """Test unknown tile uses default walkability."""
        # Unknown should use DEFAULT_TILE's walkability
        result = is_walkable("UNKNOWN_CHAR")
        assert result == DEFAULT_TILE.walkable
