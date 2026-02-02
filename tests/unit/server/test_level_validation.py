"""Tests for level validation logic in GameServer."""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest

from rogue_talk.common.tiles import TileDef
from rogue_talk.server.level import Level, DoorInfo


class TestLevelValidation:
    """Tests that the server validation correctly detects level issues."""

    def make_tiles(self, chars: dict[str, bool]) -> dict[str, TileDef]:
        """Create a minimal tiles dict for testing."""
        return {
            char: TileDef(
                char=char,
                walkable=walkable,
                color="white",
                name=char,
            )
            for char, walkable in chars.items()
        }

    def test_warns_on_undefined_tile(self) -> None:
        """Test that undefined tiles trigger a warning."""
        from rogue_talk.server.game_server import GameServer

        # Create a level with an undefined tile 'X'
        level = Level(
            width=3,
            height=1,
            tiles=[[".", "X", "."]],
        )
        tiles = self.make_tiles({".": True})

        # Capture printed output
        with patch("builtins.print") as mock_print:
            # Call the validation method directly
            server = object.__new__(GameServer)
            server._validate_level("test", level, tiles)

        # Check that a warning was printed about 'X'
        calls = [str(call) for call in mock_print.call_args_list]
        assert any("'X'" in call and "not defined" in call for call in calls), (
            f"Expected warning about undefined tile 'X', got: {calls}"
        )

    def test_no_warning_when_all_tiles_defined(self) -> None:
        """Test that no warning is emitted when all tiles are defined."""
        from rogue_talk.server.game_server import GameServer

        level = Level(
            width=3,
            height=1,
            tiles=[[".", "#", "."]],
        )
        tiles = self.make_tiles({".": True, "#": False})

        with patch("builtins.print") as mock_print:
            server = object.__new__(GameServer)
            server._validate_level("test", level, tiles)

        calls = [str(call) for call in mock_print.call_args_list]
        assert not any("not defined" in call for call in calls), (
            f"Unexpected warning about undefined tiles: {calls}"
        )

    def test_warns_on_door_without_is_door_tile(self) -> None:
        """Test that doors at non-is_door tiles trigger a warning."""
        from rogue_talk.server.game_server import GameServer

        level = Level(
            width=3,
            height=1,
            tiles=[[".", ".", "."]],
            doors={
                (1, 0): DoorInfo(x=1, y=0, target_level="other", target_x=5, target_y=5)
            },
        )
        # "." is walkable but not a door tile
        tiles = self.make_tiles({".": True})

        with patch("builtins.print") as mock_print:
            server = object.__new__(GameServer)
            server._validate_level("test", level, tiles)

        calls = [str(call) for call in mock_print.call_args_list]
        assert any("without is_door=true" in call for call in calls), (
            f"Expected warning about door without is_door tile, got: {calls}"
        )

    def test_no_warning_when_door_has_is_door_tile(self) -> None:
        """Test that doors at is_door tiles don't trigger a warning."""
        from rogue_talk.server.game_server import GameServer

        level = Level(
            width=3,
            height=1,
            tiles=[[".", ">", "."]],
            doors={
                (1, 0): DoorInfo(x=1, y=0, target_level="other", target_x=5, target_y=5)
            },
        )
        door_tile = TileDef(
            char=">", walkable=True, color="cyan", name="door", is_door=True
        )
        tiles = {".": self.make_tiles({".": True})["."], ">": door_tile}

        with patch("builtins.print") as mock_print:
            server = object.__new__(GameServer)
            server._validate_level("test", level, tiles)

        calls = [str(call) for call in mock_print.call_args_list]
        assert not any("without is_door=true" in call for call in calls), (
            f"Unexpected warning about door tile: {calls}"
        )

    def test_warns_on_orphaned_door_tile(self) -> None:
        """Test that is_door tiles without level.json entries trigger a warning."""
        from rogue_talk.server.game_server import GameServer

        level = Level(
            width=3,
            height=1,
            tiles=[[".", ">", "."]],
            doors={},  # No door entries
        )
        door_tile = TileDef(
            char=">", walkable=True, color="cyan", name="door", is_door=True
        )
        tiles = {".": self.make_tiles({".": True})["."], ">": door_tile}

        with patch("builtins.print") as mock_print:
            server = object.__new__(GameServer)
            server._validate_level("test", level, tiles)

        calls = [str(call) for call in mock_print.call_args_list]
        assert any("no entry in level.json" in call for call in calls), (
            f"Expected warning about orphaned door tile, got: {calls}"
        )

    def test_warns_on_teleporter_with_invalid_target(self) -> None:
        """Test that same-level teleporters with out-of-bounds targets trigger a warning."""
        from rogue_talk.server.game_server import GameServer

        level = Level(
            width=3,
            height=1,
            tiles=[[".", "T", "."]],
            doors={
                (1, 0): DoorInfo(
                    x=1, y=0, target_level=None, target_x=100, target_y=100
                )
            },
        )
        teleporter_tile = TileDef(
            char="T", walkable=True, color="magenta", name="teleporter", is_door=True
        )
        tiles = {".": self.make_tiles({".": True})["."], "T": teleporter_tile}

        with patch("builtins.print") as mock_print:
            server = object.__new__(GameServer)
            server._validate_level("test", level, tiles)

        calls = [str(call) for call in mock_print.call_args_list]
        assert any("outside level bounds" in call for call in calls), (
            f"Expected warning about out-of-bounds teleporter target, got: {calls}"
        )

    def test_warns_on_teleporter_with_non_walkable_target(self) -> None:
        """Test that same-level teleporters targeting non-walkable tiles trigger a warning."""
        from rogue_talk.server.game_server import GameServer

        level = Level(
            width=3,
            height=1,
            tiles=[[".", "T", "#"]],  # "#" is a wall
            doors={
                (1, 0): DoorInfo(x=1, y=0, target_level=None, target_x=2, target_y=0)
            },
        )
        teleporter_tile = TileDef(
            char="T", walkable=True, color="magenta", name="teleporter", is_door=True
        )
        tiles = {
            ".": self.make_tiles({".": True})["."],
            "T": teleporter_tile,
            "#": self.make_tiles({"#": False})["#"],
        }

        with patch("builtins.print") as mock_print:
            server = object.__new__(GameServer)
            server._validate_level("test", level, tiles)

        calls = [str(call) for call in mock_print.call_args_list]
        assert any("non-walkable target" in call for call in calls), (
            f"Expected warning about non-walkable teleporter target, got: {calls}"
        )

    def test_no_warning_for_valid_teleporter(self) -> None:
        """Test that valid same-level teleporters don't trigger warnings."""
        from rogue_talk.server.game_server import GameServer

        level = Level(
            width=3,
            height=1,
            tiles=[[".", "T", "."]],
            doors={
                (1, 0): DoorInfo(x=1, y=0, target_level=None, target_x=2, target_y=0)
            },
        )
        teleporter_tile = TileDef(
            char="T", walkable=True, color="magenta", name="teleporter", is_door=True
        )
        tiles = {".": self.make_tiles({".": True})["."], "T": teleporter_tile}

        with patch("builtins.print") as mock_print:
            server = object.__new__(GameServer)
            server._validate_level("test", level, tiles)

        calls = [str(call) for call in mock_print.call_args_list]
        # Should have no warnings at all
        assert not any("WARNING" in call for call in calls), (
            f"Unexpected warnings for valid teleporter: {calls}"
        )
