"""Tile definitions with visual and gameplay properties."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blessed import Terminal


@dataclass
class TileDef:
    """Definition for a tile type."""

    char: str
    walkable: bool
    color: str  # blessed color name like "green", "blue", "white"
    bold: bool = False
    # For animated tiles: list of colors to cycle through
    animation_colors: list[str] = field(default_factory=list)


# Tile definitions by character
TILES: dict[str, TileDef] = {
    # Walls and structures
    "#": TileDef("#", walkable=False, color="white"),
    "O": TileDef("O", walkable=False, color="white"),  # Pillar
    "+": TileDef("+", walkable=True, color="yellow"),  # Door
    # Floor types
    ".": TileDef(".", walkable=True, color="white"),  # Stone floor
    ",": TileDef(",", walkable=True, color="green"),  # Grass
    ":": TileDef(":", walkable=True, color="white"),  # Gravel
    "_": TileDef("_", walkable=True, color="yellow"),  # Sand
    # Liquids (animated)
    "~": TileDef(
        "~",
        walkable=False,
        color="blue",
        bold=True,
        animation_colors=["blue", "cyan", "bold_blue", "bold_cyan"],
    ),  # Water
    "^": TileDef(
        "^",
        walkable=False,
        color="red",
        bold=True,
        animation_colors=["red", "yellow", "bold_red", "bold_yellow"],
    ),  # Lava
    # Special
    "=": TileDef("=", walkable=True, color="yellow"),  # Bridge
    "*": TileDef("*", walkable=False, color="yellow", bold=True),  # Crystal/treasure
    "%": TileDef("%", walkable=False, color="green"),  # Bush/foliage
    # Void/empty
    " ": TileDef(" ", walkable=False, color="black"),
}

# Default tile for unknown characters
DEFAULT_TILE = TileDef("?", walkable=False, color="magenta")


def get_tile(char: str) -> TileDef:
    """Get the tile definition for a character."""
    return TILES.get(char, DEFAULT_TILE)


def is_walkable(char: str) -> bool:
    """Check if a tile character is walkable."""
    return get_tile(char).walkable


def render_tile(char: str, term: "Terminal", anim_frame: int = 0) -> str:
    """Render a tile with its color using blessed Terminal."""
    tile = get_tile(char)

    # Determine color - use animation if available
    if tile.animation_colors:
        color_name = tile.animation_colors[anim_frame % len(tile.animation_colors)]
    elif tile.bold:
        color_name = f"bold_{tile.color}"
    else:
        color_name = tile.color

    # Get the color function from terminal
    color_fn = getattr(term, color_name, None)

    if color_fn:
        return str(color_fn(tile.char))
    return tile.char
