"""Terminal UI rendering with blessed and viewport support."""

import math
import time

from blessed import Terminal

from ..common import tiles
from ..common.protocol import PlayerInfo
from .level import DoorInfo, Level
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


ANIM_INTERVAL = 0.25  # Seconds between animation frames


class TerminalUI:
    def __init__(self, terminal: Terminal):
        self.term = terminal
        self.anim_frame = 0
        self._last_anim_time = time.monotonic()

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
                # See-through portals also block normal line-of-sight
                # (you can see through them via portal view, but not past them in current level)
                if level.get_see_through_door_at(x, y):
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

    def has_line_of_sound(
        self, x1: int, y1: int, x2: int, y2: int, level: Level
    ) -> bool:
        """Check if there's a clear path for sound between two points.

        Uses Bresenham's line algorithm to trace the path.
        Returns True if no sound-blocking tiles are in the way.
        Uses the tile's blocks_sound property instead of hardcoded checks.
        """
        # Same position - always audible
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

            # Check if current tile blocks sound (skip the starting position)
            if x != x1 or y != y1:
                tile_char = level.get_tile(x, y)
                tile_def = tiles.get_tile(tile_char)
                if tile_def.blocks_sound:
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
        show_player_names: bool = False,
        other_levels: dict[str, Level] | None = None,
        current_level: str = "main",
    ) -> None:
        """Render the game state to the terminal."""
        # Advance animation frame based on time (not render rate)
        now = time.monotonic()
        if now - self._last_anim_time >= ANIM_INTERVAL:
            self.anim_frame += 1
            self._last_anim_time = now

        output: list[str] = []

        # Move to top (don't clear - overwrite in place to avoid flicker)
        output.append(str(self.term.home))

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
                    lx,
                    ly,
                    level,
                    players,
                    local_player_id,
                    player_x,
                    player_y,
                    show_player_names,
                    other_levels or {},
                    current_level,
                )
                row += char
            output.append(row + str(self.term.clear_eol))

        # Status bar
        clear_eol = str(self.term.clear_eol)
        output.append(clear_eol)
        local_player = next(
            (p for p in players if p.player_id == local_player_id), None
        )
        mute_status = self.term.red("MUTED") if is_muted else self.term.green("LIVE")
        player_count = len(players)

        status = f"[{mute_status}] Players: {player_count}"
        if local_player:
            status += f" | Position: ({local_player.x}, {local_player.y})"
        output.append(status + clear_eol)

        # Mic level (green 0-50%, yellow 50-90%, red 90-100%)
        level_chars = int(mic_level * 20)
        green_part = self.term.green("#" * min(level_chars, 10))
        yellow_part = self.term.yellow("#" * max(0, min(level_chars - 10, 8)))
        red_part = self.term.red("#" * max(0, level_chars - 18))
        padding = " " * (20 - level_chars)
        output.append(f"Mic: [{green_part}{yellow_part}{red_part}{padding}]{clear_eol}")

        # Player list
        output.append(clear_eol)
        output.append("Players:" + clear_eol)
        for p in players:
            marker = ">" if p.player_id == local_player_id else " "
            muted = " (muted)" if p.is_muted else ""
            output.append(f"  {marker} {p.name} at ({p.x}, {p.y}){muted}{clear_eol}")

        # Controls
        output.append(clear_eol)
        output.append(
            f"Controls: WASD/HJKL/Arrows=Move, M=Mute, Tab=Names, Q=Quit{clear_eol}"
        )

        # Clear any remaining lines from previous frame
        output.append(str(self.term.clear_eos))

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
        show_player_names: bool = False,
        other_levels: dict[str, Level] | None = None,
        current_level: str = "main",
    ) -> str:
        """Get the character to display at a cell with distance-based lighting."""
        # Calculate distance from player
        dx = x - player_x
        dy = y - player_y
        distance = math.sqrt(dx * dx + dy * dy)

        # Beyond visibility range - show empty
        if distance > LIGHT_FADING_RADIUS:
            return " "

        # Check for see-through portal view before normal LOS check
        portal_result = self._check_portal_view(
            x, y, player_x, player_y, level, other_levels or {}
        )
        if portal_result:
            portal_level, portal_x, portal_y, total_distance = portal_result

            # Determine target level name for player filtering
            # Find the door we went through to get the target level name
            target_level_name = current_level  # default to same level
            if level.doors:
                for door in level.doors:
                    if door.target_level and other_levels:
                        if other_levels.get(door.target_level) is portal_level:
                            target_level_name = door.target_level
                            break

            # Check for players at the mapped position (including self)
            if portal_x == player_x and portal_y == player_y:
                # Local player visible through portal
                return str(self.term.bold_magenta("@"))
            for p in players:
                if p.x == portal_x and p.y == portal_y and p.level == target_level_name:
                    # Other player visible through portal (on target level)
                    return str(self.term.magenta("@"))

            return self._render_tile_with_portal_tint(
                portal_level.get_tile(portal_x, portal_y), total_distance, portal_x
            )

        # Check line of sight - walls block light
        # The first wall hit IS visible (ray reaches it), but nothing behind it
        if not self._has_line_of_sight(player_x, player_y, x, y, level):
            return " "

        # Check if we should render a player name above them (other players only)
        if show_player_names:
            for p in players:
                if p.player_id == local_player_id:
                    continue
                if p.level != current_level:
                    continue  # Skip players on other levels
                # Check if player is one row below this position
                if p.y == y + 1:
                    # Calculate name position (centered above player)
                    name_start = p.x - len(p.name) // 2
                    name_end = name_start + len(p.name)
                    if name_start <= x < name_end:
                        char_idx = x - name_start
                        name_char = p.name[char_idx]
                        return str(self.term.bold_yellow(name_char))

        # Draw local player at predicted position (not server state)
        if x == player_x and y == player_y:
            return str(self.term.bold_green("@"))

        # Check for other players at this position (only on current level)
        for p in players:
            if p.x == x and p.y == y and p.player_id != local_player_id:
                if p.level != current_level:
                    continue  # Skip players on other levels
                # Other players affected by distance
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

    def _get_color_fn(self, color_name: str) -> str:
        """Get color escape sequence for a color name or 256-color code."""
        # Check if it's a numeric 256-color code
        if color_name.isdigit():
            return str(self.term.color(int(color_name)))  # type: ignore
        # Named color
        color_fn = getattr(self.term, color_name, None)
        if color_fn:
            return str(color_fn)
        return ""

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
        else:
            color_name = tile_def.color

        # Check if using 256-color code
        is_256_color = color_name.isdigit()

        # Apply lighting based on distance - gradual fade
        if distance <= LIGHT_FULL_RADIUS:
            # Full brightness
            if is_256_color:
                return str(self.term.color(int(color_name))(tile_def.char))  # type: ignore
            if not color_name.startswith("bold_"):
                bold_color = f"bold_{color_name}"
                if hasattr(self.term, bold_color):
                    color_name = bold_color
            color_fn = getattr(self.term, color_name, None)
            if color_fn:
                return str(color_fn(tile_def.char))
            return tile_def.char

        elif distance <= LIGHT_NORMAL_RADIUS:
            # Normal brightness
            if is_256_color:
                return str(self.term.color(int(color_name))(tile_def.char))  # type: ignore
            if color_name.startswith("bold_"):
                color_name = color_name[5:]
            color_fn = getattr(self.term, color_name, None)
            if color_fn:
                return str(color_fn(tile_def.char))
            return tile_def.char

        elif distance <= LIGHT_DIM_RADIUS:
            # Slightly dim
            if is_256_color:
                color_attr = str(self.term.color(int(color_name)))  # type: ignore
            else:
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

    def _check_portal_view(
        self,
        target_x: int,
        target_y: int,
        player_x: int,
        player_y: int,
        level: Level,
        other_levels: dict[str, Level],
        depth: int = 0,
        accumulated_distance: float = 0.0,
    ) -> tuple[Level, int, int, float] | None:
        """Check if viewing through a see-through portal to see a target cell.

        Traces line of sight from player to target. If a see-through portal is
        encountered, returns the mapped position in the target level.
        Recursively checks for chained portals up to a maximum depth.

        Returns:
            Tuple of (target_level, mapped_x, mapped_y, total_distance) if viewing
            through a portal, or None if not.
        """
        # Limit portal chain depth to prevent infinite loops
        if depth > 5:
            return None

        if not level.doors:
            return None

        # Same position - can't be through a portal
        if target_x == player_x and target_y == player_y:
            return None

        # Trace line from player to target using Bresenham
        dx = abs(target_x - player_x)
        dy = abs(target_y - player_y)
        sx = 1 if player_x < target_x else -1
        sy = 1 if player_y < target_y else -1
        err = dx - dy

        x, y = player_x, player_y

        while True:
            # Move to next cell
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x += sx
            if e2 < dx:
                err += dx
                y += sy

            # Check if we've reached the target
            if x == target_x and y == target_y:
                return None  # Reached target without hitting a portal

            # Check if this cell has a see-through portal
            door = level.get_see_through_door_at(x, y)
            if door:
                # Calculate the offset from portal to target
                offset_x = target_x - x
                offset_y = target_y - y

                # Map to target level position
                mapped_x = door.target_x + offset_x
                mapped_y = door.target_y + offset_y

                # Get the target level
                if door.target_level:
                    # Cross-level portal
                    target_level = other_levels.get(door.target_level)
                    if not target_level:
                        return None
                else:
                    # Same-level teleporter
                    target_level = level

                # Check line of sight in target level (from portal exit to mapped position)
                if not self._has_line_of_sight(
                    door.target_x, door.target_y, mapped_x, mapped_y, target_level
                ):
                    return None

                # Calculate distance so far
                dist_to_portal = math.sqrt((x - player_x) ** 2 + (y - player_y) ** 2)
                dist_from_portal = math.sqrt(offset_x**2 + offset_y**2)
                total_distance = (
                    accumulated_distance + dist_to_portal + dist_from_portal
                )

                return (target_level, mapped_x, mapped_y, total_distance)

            # Check if we hit a light-blocking tile (stop tracing)
            tile = level.get_tile(x, y)
            if tile in LIGHT_BLOCKING_TILES:
                return None

        return None

    def _render_tile_with_portal_tint(
        self, tile_char: str, distance: float, tile_x: int = 0
    ) -> str:
        """Render a tile seen through a portal with magenta tint."""
        # Void/space tiles should render as empty
        if tile_char == " ":
            return " "

        tile_def = tiles.get_tile(tile_char)
        # For unknown tiles (from other levels), use the raw character
        if tile_def.char == "?":
            display_char = tile_char
        else:
            display_char = (
                tile_def.render_char if tile_def.render_char else tile_def.char
            )

        # Apply distance-based magenta tinting
        if distance <= LIGHT_FULL_RADIUS:
            return str(self.term.bold_magenta(display_char))
        elif distance <= LIGHT_NORMAL_RADIUS:
            return str(self.term.magenta(display_char))
        elif distance <= LIGHT_DIM_RADIUS:
            # Dim magenta (256-color: 133)
            return str(self.term.color(133)(display_char))  # type: ignore
        elif distance <= LIGHT_DARKER_RADIUS:
            # Darker magenta (256-color: 96)
            return str(self.term.color(96)(display_char))  # type: ignore
        else:
            # Very dim magenta (256-color: 53)
            return str(self.term.color(53)(display_char))  # type: ignore

    def cleanup(self) -> None:
        """Restore terminal state."""
        print(self.term.normal + self.term.clear, end="")
