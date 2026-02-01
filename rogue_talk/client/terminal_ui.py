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
        # Map caching - only recalculate when player moves
        self._cached_map: list[list[str]] | None = None
        self._cached_rows: list[str] | None = None  # Pre-joined row strings
        self._cache_player_pos: tuple[int, int] | None = None
        self._cache_level_id: int | None = None
        self._cache_viewport_size: tuple[int, int] | None = None
        self._cache_anim_frame: int | None = None
        # Visibility bitmap - computed once per cache rebuild, reused for player overlays
        # Maps (level_x, level_y) -> True if visible from player position
        self._cached_visibility: dict[tuple[int, int], bool] | None = None

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
        anim_changed = False
        if now - self._last_anim_time >= ANIM_INTERVAL:
            self.anim_frame += 1
            self._last_anim_time = now
            anim_changed = True

        output: list[str] = []

        # Move to top (don't clear - overwrite in place to avoid flicker)
        output.append(str(self.term.home))

        # Get viewport sized to terminal
        viewport = self._get_viewport()

        # Calculate camera position centered on player
        cam_x, cam_y = viewport.calculate_camera(
            player_x, player_y, level.width, level.height
        )

        # Check if cached map is still valid
        viewport_size = (viewport.width, viewport.height)
        cache_valid = (
            self._cached_map is not None
            and self._cache_player_pos == (player_x, player_y)
            and self._cache_level_id == id(level)
            and self._cache_viewport_size == viewport_size
            and self._cache_anim_frame == self.anim_frame
        )

        # Rebuild cache if invalid
        if not cache_valid:
            self._cached_map = []
            self._cached_rows = []
            self._cached_visibility = {}
            for vy in range(viewport.height):
                row: list[str] = []
                for vx in range(viewport.width):
                    lx = cam_x + vx
                    ly = cam_y + vy
                    char, is_visible = self._get_map_cell_char_with_visibility(
                        lx,
                        ly,
                        level,
                        player_x,
                        player_y,
                        other_levels or {},
                    )
                    row.append(char)
                    # Store visibility for player overlay lookups
                    self._cached_visibility[(lx, ly)] = is_visible
                self._cached_map.append(row)
                self._cached_rows.append("".join(row))
            self._cache_player_pos = (player_x, player_y)
            self._cache_level_id = id(level)
            self._cache_viewport_size = viewport_size
            self._cache_anim_frame = self.anim_frame

        # Build display with players overlaid on cached map
        cached_map = self._cached_map  # Local reference for mypy
        cached_rows = self._cached_rows
        assert cached_map is not None
        assert cached_rows is not None

        # Pre-compute player overlay positions (only check actual player locations)
        player_overlays: dict[tuple[int, int], str] = {}
        self._compute_player_overlays(
            player_overlays,
            cam_x,
            cam_y,
            viewport,
            level,
            players,
            local_player_id,
            player_x,
            player_y,
            show_player_names,
            other_levels or {},
            current_level,
        )

        # Group overlays by row for efficient application
        overlays_by_row: dict[int, list[tuple[int, str]]] = {}
        for (vx, vy), char in player_overlays.items():
            if vy not in overlays_by_row:
                overlays_by_row[vy] = []
            overlays_by_row[vy].append((vx, char))

        clear_eol = str(self.term.clear_eol)
        for vy in range(viewport.height):
            if vy in overlays_by_row:
                # This row has overlays - need to modify
                row_chars = list(cached_map[vy])
                for vx, char in overlays_by_row[vy]:
                    row_chars[vx] = char
                output.append("".join(row_chars) + clear_eol)
            else:
                # No overlays - use pre-joined cached row directly
                output.append(cached_rows[vy] + clear_eol)

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

    def _get_map_cell_char_with_visibility(
        self,
        x: int,
        y: int,
        level: Level,
        player_x: int,
        player_y: int,
        other_levels: dict[str, Level],
    ) -> tuple[str, bool]:
        """Get the map character at a cell and whether it's visible.

        Returns:
            Tuple of (rendered_char, is_visible) where is_visible indicates
            direct line of sight (not portal view) for player overlay lookups.
        """
        # Calculate distance from player
        dx = x - player_x
        dy = y - player_y
        distance = math.sqrt(dx * dx + dy * dy)

        # Beyond visibility range - show empty
        if distance > LIGHT_FADING_RADIUS:
            return (" ", False)

        # Check direct line of sight first (most cells have this - fast path)
        if self._has_line_of_sight(player_x, player_y, x, y, level):
            # Direct visibility - render normally
            tile_char = level.get_tile(x, y)
            return (self._render_tile_with_lighting(tile_char, distance, x), True)

        # No direct LOS - check if viewing through a see-through portal
        portal_result = self._check_portal_view(
            x, y, player_x, player_y, level, other_levels
        )
        if portal_result:
            portal_level, portal_x, portal_y, total_distance = portal_result
            char = self._render_tile_with_portal_tint(
                portal_level.get_tile(portal_x, portal_y), total_distance, portal_x
            )
            # Portal view doesn't count as direct visibility for players
            return (char, False)

        # Blocked by wall or other obstacle
        return (" ", False)

    def _compute_player_overlays(
        self,
        overlays: dict[tuple[int, int], str],
        cam_x: int,
        cam_y: int,
        viewport: Viewport,
        level: Level,
        players: list[PlayerInfo],
        local_player_id: int,
        player_x: int,
        player_y: int,
        show_player_names: bool,
        other_levels: dict[str, Level],
        current_level: str,
    ) -> None:
        """Compute player overlay characters at viewport positions (O(num_players))."""
        # Add local player
        vx = player_x - cam_x
        vy = player_y - cam_y
        if 0 <= vx < viewport.width and 0 <= vy < viewport.height:
            overlays[(vx, vy)] = str(self.term.bold_green("@"))

        # Add other players
        for p in players:
            if p.player_id == local_player_id:
                continue
            if p.level != current_level:
                continue

            # Check if in viewport
            vx = p.x - cam_x
            vy = p.y - cam_y
            if not (0 <= vx < viewport.width and 0 <= vy < viewport.height):
                continue

            # Check distance and visibility from cached bitmap
            dx = p.x - player_x
            dy = p.y - player_y
            distance = math.sqrt(dx * dx + dy * dy)

            if distance > LIGHT_FADING_RADIUS:
                continue

            # Use cached visibility bitmap instead of recalculating LOS
            if self._cached_visibility is not None:
                if not self._cached_visibility.get((p.x, p.y), False):
                    continue
            elif not self._has_line_of_sight(player_x, player_y, p.x, p.y, level):
                # Fallback if cache not available (shouldn't happen normally)
                continue

            # Render with distance-based lighting
            if distance <= LIGHT_FULL_RADIUS:
                overlays[(vx, vy)] = str(self.term.bold_yellow("@"))
            elif distance <= LIGHT_NORMAL_RADIUS:
                overlays[(vx, vy)] = str(self.term.yellow("@"))
            elif distance <= LIGHT_DIM_RADIUS:
                overlays[(vx, vy)] = str(self.term.color(229)("@"))  # type: ignore
            elif distance <= LIGHT_DARKER_RADIUS:
                overlays[(vx, vy)] = str(self.term.color(245)("@"))  # type: ignore
            else:
                overlays[(vx, vy)] = str(self.term.color(240)("@"))  # type: ignore

            # Add player name above if enabled
            if show_player_names:
                name_start = p.x - len(p.name) // 2
                name_vy = vy - 1  # Row above player
                if 0 <= name_vy < viewport.height:
                    for i, char in enumerate(p.name):
                        name_vx = (name_start + i) - cam_x
                        if 0 <= name_vx < viewport.width:
                            overlays[(name_vx, name_vy)] = str(
                                self.term.bold_yellow(char)
                            )

        # Add players visible through portals (including self through same-level teleporters)
        if level.doors:
            for door in level.doors:
                if not door.see_through:
                    continue

                # Determine target level (same level if target_level is None)
                if door.target_level:
                    target_level = other_levels.get(door.target_level)
                    if not target_level:
                        continue
                    target_level_name = door.target_level
                else:
                    # Same-level teleporter
                    target_level = level
                    target_level_name = current_level

                # Check if player can see the portal using cached visibility
                door_dx = door.x - player_x
                door_dy = door.y - player_y
                door_dist = math.sqrt(door_dx * door_dx + door_dy * door_dy)
                if door_dist > LIGHT_FADING_RADIUS:
                    continue
                # Use cached visibility bitmap for door visibility check
                if self._cached_visibility is not None:
                    if not self._cached_visibility.get((door.x, door.y), False):
                        continue
                elif not self._has_line_of_sight(
                    player_x, player_y, door.x, door.y, level
                ):
                    continue

                # Check if local player visible through this portal (same-level teleporter)
                if not door.target_level:
                    offset_x = player_x - door.target_x
                    offset_y = player_y - door.target_y
                    apparent_x = door.x + offset_x
                    apparent_y = door.y + offset_y

                    # Verify ray from player to apparent position goes through portal
                    # and maps back to player's actual position
                    portal_check = self._check_portal_view(
                        apparent_x, apparent_y, player_x, player_y, level, {}
                    )
                    if portal_check:
                        _, mapped_x, mapped_y, total_dist = portal_check
                        if mapped_x == player_x and mapped_y == player_y:
                            vx = apparent_x - cam_x
                            vy = apparent_y - cam_y
                            if 0 <= vx < viewport.width and 0 <= vy < viewport.height:
                                if total_dist <= LIGHT_FADING_RADIUS:
                                    overlays[(vx, vy)] = str(
                                        self.term.bold_magenta("@")
                                    )

                # Check other players on target level
                for p in players:
                    if p.level != target_level_name:
                        continue
                    # Skip self (handled above for same-level)
                    if p.player_id == local_player_id:
                        continue

                    # Calculate where this player appears through the portal
                    offset_x = p.x - door.target_x
                    offset_y = p.y - door.target_y
                    apparent_x = door.x + offset_x
                    apparent_y = door.y + offset_y

                    # Verify ray from player to apparent position goes through portal
                    portal_check = self._check_portal_view(
                        apparent_x, apparent_y, player_x, player_y, level, other_levels
                    )
                    if not portal_check:
                        continue
                    _, mapped_x, mapped_y, total_dist = portal_check
                    if mapped_x != p.x or mapped_y != p.y:
                        continue

                    # Check viewport bounds
                    vx = apparent_x - cam_x
                    vy = apparent_y - cam_y
                    if not (0 <= vx < viewport.width and 0 <= vy < viewport.height):
                        continue

                    if total_dist > LIGHT_FADING_RADIUS:
                        continue

                    # Render with magenta tint (portal view)
                    overlays[(vx, vy)] = str(self.term.magenta("@"))

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
    ) -> tuple[Level, int, int, float] | None:
        """Check if viewing through a see-through portal to see a target cell.

        Traces line of sight from player to target. If a see-through portal is
        encountered, returns the mapped position in the target level.
        Only checks one portal deep (no recursive chaining).

        Returns:
            Tuple of (target_level, mapped_x, mapped_y, total_distance) if viewing
            through a portal, or None if not.
        """
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

                # Calculate total distance through portal
                dist_to_portal = math.sqrt((x - player_x) ** 2 + (y - player_y) ** 2)
                dist_from_portal = math.sqrt(offset_x**2 + offset_y**2)
                total_distance = dist_to_portal + dist_from_portal

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
