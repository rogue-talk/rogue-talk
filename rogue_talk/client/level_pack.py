"""Level pack extraction and handling."""

import io
import json
import tarfile
from dataclasses import dataclass
from pathlib import Path

from .level import DoorInfo, InteractionInfo, StreamInfo


@dataclass
class LevelPack:
    """Represents an extracted level pack."""

    level_path: Path  # Path to level.txt
    tiles_path: Path | None  # Path to tiles.json (optional)
    assets_dir: Path | None  # Path to assets/ directory (optional)
    level_json_path: Path | None  # Path to level.json (optional)


def extract_level_pack(tarball_data: bytes, extract_dir: Path) -> LevelPack:
    """Extract a level pack tarball and return paths to its contents.

    Args:
        tarball_data: Raw bytes of the .tar file
        extract_dir: Directory to extract to

    Returns:
        LevelPack with paths to extracted contents

    Raises:
        ValueError: If level.txt is not found in the tarball
    """
    extract_dir.mkdir(parents=True, exist_ok=True)

    # Extract the tarball
    with tarfile.open(fileobj=io.BytesIO(tarball_data), mode="r:*") as tar:
        # Security: filter out absolute paths and path traversal
        safe_members = []
        for member in tar.getmembers():
            # Skip absolute paths
            if member.name.startswith("/"):
                continue
            # Skip path traversal attempts
            if ".." in member.name:
                continue
            safe_members.append(member)

        tar.extractall(path=extract_dir, members=safe_members)

    # Find level.txt (required)
    level_path = extract_dir / "level.txt"
    if not level_path.exists():
        raise ValueError("level.txt not found in level pack")

    # Find tiles.json (optional)
    _tiles_path = extract_dir / "tiles.json"
    tiles_path: Path | None = _tiles_path if _tiles_path.exists() else None

    # Find assets directory (optional)
    _assets_dir = extract_dir / "assets"
    assets_dir: Path | None = (
        _assets_dir if _assets_dir.exists() and _assets_dir.is_dir() else None
    )

    # Find level.json (optional)
    _level_json_path = extract_dir / "level.json"
    level_json_path: Path | None = (
        _level_json_path if _level_json_path.exists() else None
    )

    return LevelPack(
        level_path=level_path,
        tiles_path=tiles_path,
        assets_dir=assets_dir,
        level_json_path=level_json_path,
    )


def write_files_to_dir(files: dict[str, bytes], extract_dir: Path) -> None:
    """Write files to a directory, creating subdirectories as needed.

    Args:
        files: Dict mapping relative paths to file contents
        extract_dir: Base directory to write to
    """
    extract_dir.mkdir(parents=True, exist_ok=True)
    for rel_path, content in files.items():
        # Security: skip absolute paths and path traversal
        if rel_path.startswith("/") or ".." in rel_path:
            continue
        file_path = extract_dir / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)


def create_level_pack_from_dir(extract_dir: Path) -> LevelPack:
    """Create a LevelPack from an already-populated directory.

    Args:
        extract_dir: Directory containing level files

    Returns:
        LevelPack with paths to contents

    Raises:
        ValueError: If level.txt is not found
    """
    # Find level.txt (required)
    level_path = extract_dir / "level.txt"
    if not level_path.exists():
        raise ValueError("level.txt not found in level pack")

    # Find tiles.json (optional)
    _tiles_path = extract_dir / "tiles.json"
    tiles_path: Path | None = _tiles_path if _tiles_path.exists() else None

    # Find assets directory (optional)
    _assets_dir = extract_dir / "assets"
    assets_dir: Path | None = (
        _assets_dir if _assets_dir.exists() and _assets_dir.is_dir() else None
    )

    # Find level.json (optional)
    _level_json_path = extract_dir / "level.json"
    level_json_path: Path | None = (
        _level_json_path if _level_json_path.exists() else None
    )

    return LevelPack(
        level_path=level_path,
        tiles_path=tiles_path,
        assets_dir=assets_dir,
        level_json_path=level_json_path,
    )


def parse_doors(level_json_path: Path | None) -> list[DoorInfo]:
    """Parse door definitions from level.json.

    Args:
        level_json_path: Path to level.json file, or None

    Returns:
        List of DoorInfo objects
    """
    if level_json_path is None or not level_json_path.exists():
        return []

    with open(level_json_path, encoding="utf-8") as f:
        data = json.load(f)

    doors: list[DoorInfo] = []
    for door_data in data.get("doors", []):
        door = DoorInfo(
            x=door_data["x"],
            y=door_data["y"],
            target_level=door_data.get("target_level"),
            target_x=door_data["target_x"],
            target_y=door_data["target_y"],
            see_through=door_data.get("see_through", False),
        )
        doors.append(door)

    return doors


def parse_streams(level_json_path: Path | None) -> list[StreamInfo]:
    """Parse stream definitions from level.json.

    Args:
        level_json_path: Path to level.json file, or None

    Returns:
        List of StreamInfo objects
    """
    if level_json_path is None or not level_json_path.exists():
        return []

    with open(level_json_path, encoding="utf-8") as f:
        data = json.load(f)

    streams: list[StreamInfo] = []
    for stream_data in data.get("streams", []):
        stream = StreamInfo(
            x=stream_data["x"],
            y=stream_data["y"],
            url=stream_data["url"],
            radius=stream_data.get("radius", 5),
        )
        streams.append(stream)

    return streams


def parse_interactions(level_json_path: Path | None) -> list[InteractionInfo]:
    """Parse interaction definitions from level.json.

    Args:
        level_json_path: Path to level.json file, or None

    Returns:
        List of InteractionInfo objects
    """
    if level_json_path is None or not level_json_path.exists():
        return []

    with open(level_json_path, encoding="utf-8") as f:
        data = json.load(f)

    interactions: list[InteractionInfo] = []
    for interaction_data in data.get("interactions", []):
        interaction = InteractionInfo(
            x=interaction_data["x"],
            y=interaction_data["y"],
            text=interaction_data["text"],
            hidden=interaction_data.get("hidden", False),
        )
        interactions.append(interaction)

    return interactions
