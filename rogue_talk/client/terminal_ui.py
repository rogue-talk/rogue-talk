"""Terminal UI rendering with blessed and viewport support."""

import math

from blessed import Terminal

from ..common import tiles
from ..common.protocol import PlayerInfo
from .level import Level
from .viewport import Viewport

# Lighting constants - gradual fade zones
LIGHT_FULL_RADIUS = 8  # Full brightness (bold)
LIGHT_NORMAL_RADIUS = 14  # Normal brightness
LIGHT_DIM_RADIUS = 20  # Slightly dim
LIGHT_DARKER_RADIUS = 26  # Darker (gray tones)
LIGHT_FADING_RADIUS = 32  # Very dark, almost invisible
# Beyond LIGHT_FADING_RADIUS: invisible

# Tiles that block light
LIGHT_BLOCKING_TILES = {"#", "O"}  # Walls and pillars


class TerminalUI:
    def __init__(self, terminal: Terminal):
        self.term = terminal
        self.anim_frame = 0

    def _get_viewport(self) -> Viewport:
        """Get viewport sized to current terminal dimensions."""
        # Reserve lines for status bar, mic level, player list, controls
        reserved_lines = 10
        height = max(10, self.term.height - reserved_lines)
        width = max(20, self.term.width)
        return Viewport(width=width, height=height)

    def _has_line_of_sight(
        self, x1: int, y1: int, x2: int, y2: int, level: Level
    ) -> bool:
        """Check if there's a clear line of sight between two points.

        Uses Bresenham's line algorithm to trace the path.
        Returns True if no light-blocking tiles are in the way.
        """
        # Same position - always visible
        if x1 == x2 and y1 == y2:
            return True

        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        sx = 1 if x1 < x2 else -1
        sy = 1 if y1 < y2 else -1
        err = dx - dy

        x, y = x1, y1

        while True:
            # Check if we've reached the target (don't check the target itself)
            if x == x2 and y == y2:
                return True

            # Check if current tile blocks light (skip the starting position)
            if x != x1 or y != y1:
                tile = level.get_tile(x, y)
                if tile in LIGHT_BLOCKING_TILES:
                    return False

            # Move to next cell
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x += sx
            if e2 < dx:
                err += dx
                y += sy

        return True

    def render(
        self,
        level: Level,
        players: list[PlayerInfo],
        local_player_id: int,
        player_x: int,
        player_y: int,
        is_muted: bool,
        mic_level: float = 0.0,
    ) -> None:
        """Render the game state to the terminal."""
        # Advance animation frame
        self.anim_frame += 1

        output = []

        # Clear screen and move to top
        output.append(self.term.home + self.term.clear)

        # Get viewport sized to terminal
        viewport = self._get_viewport()

        # Calculate camera position centered on player
        cam_x, cam_y = viewport.calculate_camera(
            player_x, player_y, level.width, level.height
        )

        # Draw the visible portion of the level
        for vy in range(viewport.height):
            row = ""
            for vx in range(viewport.width):
                # Convert viewport coordinates to level coordinates
                lx = cam_x + vx
                ly = cam_y + vy
                char = self._get_cell_char(
                    lx, ly, level, players, local_player_id, player_x, player_y
                )
                row += char
            output.append(row)

        # Status bar
        output.append("")
        local_player = next(
            (p for p in players if p.player_id == local_player_id), None
        )
        mute_status = self.term.red("MUTED") if is_muted else self.term.green("LIVE")
        player_count = len(players)

        status = f"[{mute_status}] Players: {player_count}"
        if local_player:
            status += f" | Position: ({local_player.x}, {local_player.y})"
        output.append(status)

        # Mic level (green 0-50%, yellow 50-90%, red 90-100%)
        level_chars = int(mic_level * 20)
        green_part = self.term.green("#" * min(level_chars, 10))
        yellow_part = self.term.yellow("#" * max(0, min(level_chars - 10, 8)))
        red_part = self.term.red("#" * max(0, level_chars - 18))
        padding = " " * (20 - level_chars)
        output.append(f"Mic: [{green_part}{yellow_part}{red_part}{padding}]")

        # Player list
        output.append("")
        output.append("Players:")
        for p in players:
            marker = ">" if p.player_id == local_player_id else " "
            muted = " (muted)" if p.is_muted else ""
            output.append(f"  {marker} {p.name} at ({p.x}, {p.y}){muted}")

        # Controls
        output.append("")
        output.append("Controls: WASD/HJKL/Arrows=Move, M=Mute, Q=Quit")

        print("\n".join(output), end="", flush=True)

    def _get_cell_char(
        self,
        x: int,
        y: int,
        level: Level,
        players: list[PlayerInfo],
        local_player_id: int,
        player_x: int,
        player_y: int,
    ) -> str:
        """Get the character to display at a cell with distance-based lighting."""
        # Calculate distance from player
        dx = x - player_x
        dy = y - player_y
        distance = math.sqrt(dx * dx + dy * dy)

        # Beyond visibility range - show empty
        if distance > LIGHT_FADING_RADIUS:
            return " "

        # Check line of sight - walls block light
        # The first wall hit IS visible (ray reaches it), but nothing behind it
        if not self._has_line_of_sight(player_x, player_y, x, y, level):
            return " "

        # Check for players at this position
        for p in players:
            if p.x == x and p.y == y:
                if p.player_id == local_player_id:
                    return str(self.term.bold_green("@"))
                else:
                    # Other players also affected by distance
                    if distance <= LIGHT_FULL_RADIUS:
                        return str(self.term.bold_yellow("@"))
                    elif distance <= LIGHT_NORMAL_RADIUS:
                        return str(self.term.yellow("@"))
                    elif distance <= LIGHT_DIM_RADIUS:
                        return str(self.term.color(229)("@"))  # type: ignore
                    elif distance <= LIGHT_DARKER_RADIUS:
                        return str(self.term.color(245)("@"))  # type: ignore
                    else:
                        return str(self.term.color(240)("@"))  # type: ignore

        # Get tile from level and render with lighting
        tile_char = level.get_tile(x, y)
        return self._render_tile_with_lighting(tile_char, distance, x)

    def _render_tile_with_lighting(
        self, tile_char: str, distance: float, tile_x: int = 0
    ) -> str:
        """Render a tile with distance-based lighting effects."""
        tile_def = tiles.get_tile(tile_char)

        # Determine color based on animation for animated tiles
        # Offset by tile_x so animation flows left to right
        if tile_def.animation_colors:
            anim_index = (self.anim_frame + tile_x) % len(tile_def.animation_colors)
            color_name = tile_def.animation_colors[anim_index]
        elif tile_def.bold:
            color_name = f"bold_{tile_def.color}"
        else:
            color_name = tile_def.color

        # Apply lighting based on distance - gradual fade
        if distance <= LIGHT_FULL_RADIUS:
            # Full brightness - use bold variant if available
            if not color_name.startswith("bold_"):
                bold_color = f"bold_{color_name}"
                if hasattr(self.term, bold_color):
                    color_name = bold_color
            color_fn = getattr(self.term, color_name, None)
            if color_fn:
                return str(color_fn(tile_def.char))
            return tile_def.char

        elif distance <= LIGHT_NORMAL_RADIUS:
            # Normal brightness - strip bold if present
            if color_name.startswith("bold_"):
                color_name = color_name[5:]
            color_fn = getattr(self.term, color_name, None)
            if color_fn:
                return str(color_fn(tile_def.char))
            return tile_def.char

        elif distance <= LIGHT_DIM_RADIUS:
            # Slightly dim - use dim + color
            if color_name.startswith("bold_"):
                color_name = color_name[5:]
            color_attr = getattr(self.term, color_name, "")
            return f"{self.term.dim}{color_attr}{tile_def.char}{self.term.normal}"

        elif distance <= LIGHT_DARKER_RADIUS:
            # Darker - use medium gray (256-color: 245)
            return str(self.term.color(245)(tile_def.char))  # type: ignore

        else:
            # Fading - use dark gray (256-color: 239)
            return str(self.term.color(239)(tile_def.char))  # type: ignore

    def cleanup(self) -> None:
        """Restore terminal state."""
        print(self.term.normal + self.term.clear, end="")
