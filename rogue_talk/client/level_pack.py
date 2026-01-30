"""Level pack extraction and handling."""

import io
import tarfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class LevelPack:
    """Represents an extracted level pack."""

    level_path: Path  # Path to level.txt
    tiles_path: Path | None  # Path to tiles.json (optional)
    assets_dir: Path | None  # Path to assets/ directory (optional)


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

    return LevelPack(
        level_path=level_path,
        tiles_path=tiles_path,
        assets_dir=assets_dir,
    )
