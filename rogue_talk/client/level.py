"""Level representation for the client."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ..common import tiles as tile_defs


@dataclass
class DoorInfo:
    """Information about a door/teleporter in a level."""

    x: int
    y: int
    target_level: str | None  # None = same level teleporter
    target_x: int
    target_y: int
    see_through: bool = False


@dataclass
class Level:
    """Client-side level representation."""

    width: int
    height: int
    tiles: list[list[str]]
    doors: list[DoorInfo] | None = None
    _see_through_door_cache: dict[tuple[int, int], DoorInfo] | None = None

    @classmethod
    def from_bytes(cls, data: bytes) -> Level:
        """Deserialize level data from network."""
        width, height = struct.unpack(">HH", data[:4])
        offset = 4

        tiles: list[list[str]] = []
        for _ in range(height):
            row_bytes = data[offset : offset + width]
            row = list(row_bytes.decode("ascii"))
            tiles.append(row)
            offset += width

        return cls(width=width, height=height, tiles=tiles)

    def get_tile(self, x: int, y: int) -> str:
        """Get the character at a position, or space for out-of-bounds."""
        if x < 0 or x >= self.width or y < 0 or y >= self.height:
            return " "
        return self.tiles[y][x]

    def is_walkable(self, x: int, y: int) -> bool:
        """Check if a tile is walkable."""
        if x < 0 or x >= self.width or y < 0 or y >= self.height:
            return False
        return tile_defs.is_walkable(self.tiles[y][x])

    def get_see_through_door_at(self, x: int, y: int) -> DoorInfo | None:
        """Get a see-through door at the given position, or None (O(1) cached)."""
        if self._see_through_door_cache is None:
            self._see_through_door_cache = {}
            if self.doors:
                for door in self.doors:
                    if door.see_through:
                        self._see_through_door_cache[(door.x, door.y)] = door
        return self._see_through_door_cache.get((x, y))
