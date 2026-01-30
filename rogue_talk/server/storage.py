"""File-based player persistence storage.

Directory structure:
    users/
      alice/
        pub          # 32-byte raw Ed25519 public key
        state.json   # {"x": 10, "y": 5, "level": "main"}
      bob/
        pub
        state.json
"""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PlayerState:
    """Saved player state."""

    x: int
    y: int
    level: str


class PlayerStorage:
    """File-based storage for player data."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.users_dir = data_dir / "users"
        self.users_dir.mkdir(parents=True, exist_ok=True)

    def _user_dir(self, name: str) -> Path:
        """Get the directory for a user."""
        return self.users_dir / name

    def get_public_key(self, name: str) -> bytes | None:
        """Get the public key for a registered user.

        Returns None if user doesn't exist.
        """
        pub_path = self._user_dir(name) / "pub"
        if pub_path.exists():
            return pub_path.read_bytes()
        return None

    def get_name_by_key(self, public_key: bytes) -> str | None:
        """Find a user name by their public key.

        Returns None if no user has this key.
        """
        for user_dir in self.users_dir.iterdir():
            if user_dir.is_dir():
                pub_path = user_dir / "pub"
                if pub_path.exists() and pub_path.read_bytes() == public_key:
                    return user_dir.name
        return None

    def register_player(self, name: str, public_key: bytes) -> bool:
        """Register a new player with their public key.

        Returns False if name is already taken.
        """
        user_dir = self._user_dir(name)
        if user_dir.exists():
            return False

        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "pub").write_bytes(public_key)
        return True

    def get_player_state(self, name: str) -> PlayerState | None:
        """Get saved state for a player.

        Returns None if no state exists.
        """
        state_path = self._user_dir(name) / "state.json"
        if not state_path.exists():
            return None

        try:
            data = json.loads(state_path.read_text())
            return PlayerState(
                x=int(data["x"]),
                y=int(data["y"]),
                level=str(data["level"]),
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def save_player_state(self, name: str, x: int, y: int, level: str) -> None:
        """Save player state to disk."""
        user_dir = self._user_dir(name)
        if not user_dir.exists():
            return

        state = {"x": x, "y": y, "level": level}
        (user_dir / "state.json").write_text(json.dumps(state))
