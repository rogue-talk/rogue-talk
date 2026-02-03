"""Keyboard input handling."""

from blessed.keyboard import Keystroke


# Movement mappings: key -> (dx, dy)
MOVEMENT_KEYS = {
    # WASD
    "w": (0, -1),
    "a": (-1, 0),
    "s": (0, 1),
    "d": (1, 0),
    # HJKL (vim-style)
    "h": (-1, 0),
    "j": (0, 1),
    "k": (0, -1),
    "l": (1, 0),
}


def get_movement(key: Keystroke) -> tuple[int, int] | None:
    """Get movement delta from key press, or None if not a movement key."""
    # Check arrow keys
    if key.name == "KEY_UP":
        return (0, -1)
    elif key.name == "KEY_DOWN":
        return (0, 1)
    elif key.name == "KEY_LEFT":
        return (-1, 0)
    elif key.name == "KEY_RIGHT":
        return (1, 0)

    # Check WASD/HJKL
    return MOVEMENT_KEYS.get(key.lower(), None)


def is_mute_key(key: Keystroke) -> bool:
    """Check if key is the mute toggle."""
    return str(key).lower() == "m"


def is_quit_key(key: Keystroke) -> bool:
    """Check if key is the quit key."""
    return str(key).lower() == "q"


def is_show_names_key(key: Keystroke) -> bool:
    """Check if key is the show names toggle (N)."""
    return str(key).lower() == "n"


def is_player_table_key(key: Keystroke) -> bool:
    """Check if key is the player table toggle (Tab)."""
    return key.name == "KEY_TAB"


def is_help_key(key: Keystroke) -> bool:
    """Check if key is the help toggle (?)."""
    return str(key) == "?"


def is_interact_key(key: Keystroke) -> bool:
    """Check if key is the interact key (Space)."""
    return str(key) == " "
