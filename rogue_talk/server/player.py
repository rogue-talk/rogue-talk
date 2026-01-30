"""Player state for the server."""

from asyncio import StreamReader, StreamWriter
from dataclasses import dataclass, field


@dataclass
class Player:
    id: int
    name: str
    x: int
    y: int
    reader: StreamReader
    writer: StreamWriter
    is_muted: bool = False
    current_level: str = "main"  # Name of the level the player is currently on
    public_key: bytes = b""  # Ed25519 public key for authentication
