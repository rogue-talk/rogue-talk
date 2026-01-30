"""Terminal UI rendering with blessed and viewport support."""

from blessed import Terminal

from ..common import tiles
from ..common.protocol import PlayerInfo
from .level import Level
from .viewport import Viewport


class TerminalUI:
    def __init__(self, terminal: Terminal):
        self.term = terminal
        self.viewport = Viewport(width=40, height=20)

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
        output = []

        # Clear screen and move to top
        output.append(self.term.home + self.term.clear)

        # Calculate camera position centered on player
        cam_x, cam_y = self.viewport.calculate_camera(
            player_x, player_y, level.width, level.height
        )

        # Draw the visible portion of the level
        for vy in range(self.viewport.height):
            row = ""
            for vx in range(self.viewport.width):
                # Convert viewport coordinates to level coordinates
                lx = cam_x + vx
                ly = cam_y + vy
                char = self._get_cell_char(lx, ly, level, players, local_player_id)
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
    ) -> str:
        """Get the character to display at a cell."""
        # Check for players at this position
        for p in players:
            if p.x == x and p.y == y:
                if p.player_id == local_player_id:
                    return str(self.term.bold_green("@"))
                else:
                    return str(self.term.bold_yellow("@"))

        # Get tile from level and render with color
        tile_char = level.get_tile(x, y)
        return tiles.render_tile(tile_char, self.term)

    def cleanup(self) -> None:
        """Restore terminal state."""
        print(self.term.normal + self.term.clear, end="")
