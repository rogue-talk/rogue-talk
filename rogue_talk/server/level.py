"""Level loading and representation for the server."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..common import tiles as tile_defs


@dataclass
class DoorInfo:
    """Metadata for a door/teleporter at a specific position."""

    x: int
    y: int
    target_level: str | None  # None means same level (teleporter)
    target_x: int
    target_y: int


@dataclass
class StreamInfo:
    """Metadata for an audio stream at a specific position."""

    x: int
    y: int
    url: str
    radius: int = 5  # How far the stream can be heard (in tiles)


@dataclass
class Level:
    """Represents a game level loaded from an ASCII file."""

    width: int
    height: int
    tiles: list[list[str]]
    spawn_positions: list[tuple[int, int]] = field(default_factory=list)
    doors: dict[tuple[int, int], DoorInfo] = field(default_factory=dict)
    streams: dict[tuple[int, int], StreamInfo] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: str) -> Level:
        """Load a level from an ASCII text file."""
        with open(path, encoding="utf-8") as f:
            content = f.read()
        return cls.from_string(content)

    @classmethod
    def from_string(cls, content: str) -> Level:
        """Load a level from an ASCII string."""
        lines = content.rstrip("\n").split("\n")

        # Determine dimensions
        height = len(lines)
        width = max(len(line) for line in lines) if lines else 0

        # Parse tiles and collect spawn positions
        tiles: list[list[str]] = []
        spawn_positions: list[tuple[int, int]] = []

        for y, line in enumerate(lines):
            row: list[str] = []
            for x in range(width):
                if x < len(line):
                    char = line[x]
                else:
                    char = " "  # Pad with void

                if char == "S":
                    spawn_positions.append((x, y))
                    row.append(".")  # Treat spawn as floor
                else:
                    row.append(char)
            tiles.append(row)

        return cls(
            width=width,
            height=height,
            tiles=tiles,
            spawn_positions=spawn_positions,
        )

    def is_walkable(self, x: int, y: int) -> bool:
        """Check if a tile is walkable."""
        if x < 0 or x >= self.width or y < 0 or y >= self.height:
            return False
        tile = self.tiles[y][x]
        return tile_defs.is_walkable(tile)

    def get_tile(self, x: int, y: int) -> str:
        """Get the character at a position, or space for out-of-bounds."""
        if x < 0 or x >= self.width or y < 0 or y >= self.height:
            return " "
        return self.tiles[y][x]

    def get_spawn_position(self) -> tuple[int, int]:
        """Get a spawn position. Falls back to random walkable tile."""
        if self.spawn_positions:
            return random.choice(self.spawn_positions)
        # Fallback: find any walkable position
        for y in range(self.height):
            for x in range(self.width):
                if self.is_walkable(x, y):
                    return x, y
        # Last resort
        return self.width // 2, self.height // 2

    def get_door_at(self, x: int, y: int) -> DoorInfo | None:
        """Get door info at position, or None if no door defined there."""
        return self.doors.get((x, y))

    def to_bytes(self) -> bytes:
        """Serialize level data for network transmission."""
        import struct

        # Format: [width: H][height: H][tiles row by row as ASCII bytes]
        data = struct.pack(">HH", self.width, self.height)
        for row in self.tiles:
            row_bytes = "".join(row).encode("ascii")
            data += row_bytes
        return data

    @classmethod
    def from_bytes(cls, data: bytes) -> Level:
        """Deserialize level data from network."""
        import struct

        width, height = struct.unpack(">HH", data[:4])
        offset = 4

        tiles: list[list[str]] = []
        for _ in range(height):
            row_bytes = data[offset : offset + width]
            row = list(row_bytes.decode("ascii"))
            tiles.append(row)
            offset += width

        return cls(width=width, height=height, tiles=tiles, spawn_positions=[])
