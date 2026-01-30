"""Wire protocol for client-server communication."""

import enum
import struct
from asyncio import StreamReader, StreamWriter
from dataclasses import dataclass


class MessageType(enum.IntEnum):
    CLIENT_HELLO = 0x01
    SERVER_HELLO = 0x02
    POSITION_UPDATE = 0x03
    WORLD_STATE = 0x04
    AUDIO_FRAME = 0x05
    PLAYER_JOINED = 0x06
    PLAYER_LEFT = 0x07
    MUTE_STATUS = 0x08


@dataclass
class PlayerInfo:
    player_id: int
    x: int
    y: int
    is_muted: bool
    name: str


@dataclass
class WorldState:
    players: list[PlayerInfo]


@dataclass
class AudioFrame:
    player_id: int
    timestamp_ms: int
    volume: float  # 0.0 to 1.0, set by server based on distance
    opus_data: bytes


async def read_message(reader: StreamReader) -> tuple[MessageType, bytes]:
    """Read a length-prefixed message from the stream."""
    length_data = await reader.readexactly(4)
    length = struct.unpack(">I", length_data)[0]
    if length < 1:
        raise ValueError("Invalid message length")
    msg_type = struct.unpack("B", await reader.readexactly(1))[0]
    payload = await reader.readexactly(length - 1) if length > 1 else b""
    return MessageType(msg_type), payload


async def write_message(
    writer: StreamWriter, msg_type: MessageType, payload: bytes = b""
) -> None:
    """Write a length-prefixed message to the stream."""
    length = 1 + len(payload)
    writer.write(struct.pack(">I", length))
    writer.write(struct.pack("B", msg_type))
    writer.write(payload)
    await writer.drain()


# CLIENT_HELLO: name
def serialize_client_hello(name: str) -> bytes:
    name_bytes = name.encode("utf-8")
    return struct.pack(">I", len(name_bytes)) + name_bytes


def deserialize_client_hello(data: bytes) -> str:
    name_len = struct.unpack(">I", data[:4])[0]
    return data[4 : 4 + name_len].decode("utf-8")


# SERVER_HELLO: player_id, room_width, room_height, spawn_x, spawn_y
def serialize_server_hello(
    player_id: int, room_width: int, room_height: int, spawn_x: int, spawn_y: int
) -> bytes:
    return struct.pack(">IHHHH", player_id, room_width, room_height, spawn_x, spawn_y)


def deserialize_server_hello(data: bytes) -> tuple[int, int, int, int, int]:
    return struct.unpack(">IHHHH", data)


# POSITION_UPDATE: x, y
def serialize_position_update(x: int, y: int) -> bytes:
    return struct.pack(">HH", x, y)


def deserialize_position_update(data: bytes) -> tuple[int, int]:
    return struct.unpack(">HH", data)


# WORLD_STATE: list of players
def serialize_world_state(players: list[PlayerInfo]) -> bytes:
    result = struct.pack(">I", len(players))
    for p in players:
        name_bytes = p.name.encode("utf-8")
        result += struct.pack(
            ">IHHBI", p.player_id, p.x, p.y, 1 if p.is_muted else 0, len(name_bytes)
        )
        result += name_bytes
    return result


def deserialize_world_state(data: bytes) -> WorldState:
    offset = 0
    num_players = struct.unpack(">I", data[offset : offset + 4])[0]
    offset += 4
    players = []
    for _ in range(num_players):
        player_id, x, y, is_muted, name_len = struct.unpack(
            ">IHHBI", data[offset : offset + 13]
        )
        offset += 13
        name = data[offset : offset + name_len].decode("utf-8")
        offset += name_len
        players.append(PlayerInfo(player_id, x, y, bool(is_muted), name))
    return WorldState(players)


# AUDIO_FRAME: player_id, timestamp_ms, volume (as uint16 0-65535), opus_data
def serialize_audio_frame(frame: AudioFrame) -> bytes:
    volume_int = int(frame.volume * 65535)
    return (
        struct.pack(">IIH", frame.player_id, frame.timestamp_ms, volume_int)
        + struct.pack(">H", len(frame.opus_data))
        + frame.opus_data
    )


def deserialize_audio_frame(data: bytes) -> AudioFrame:
    player_id, timestamp_ms, volume_int = struct.unpack(">IIH", data[:10])
    opus_len = struct.unpack(">H", data[10:12])[0]
    opus_data = data[12 : 12 + opus_len]
    return AudioFrame(player_id, timestamp_ms, volume_int / 65535.0, opus_data)


# PLAYER_JOINED: player_id, name
def serialize_player_joined(player_id: int, name: str) -> bytes:
    name_bytes = name.encode("utf-8")
    return struct.pack(">II", player_id, len(name_bytes)) + name_bytes


def deserialize_player_joined(data: bytes) -> tuple[int, str]:
    player_id, name_len = struct.unpack(">II", data[:8])
    name = data[8 : 8 + name_len].decode("utf-8")
    return player_id, name


# PLAYER_LEFT: player_id
def serialize_player_left(player_id: int) -> bytes:
    return struct.pack(">I", player_id)


def deserialize_player_left(data: bytes) -> int:
    result: int = struct.unpack(">I", data)[0]
    return result


# MUTE_STATUS: is_muted
def serialize_mute_status(is_muted: bool) -> bytes:
    return struct.pack("B", 1 if is_muted else 0)


def deserialize_mute_status(data: bytes) -> bool:
    return bool(struct.unpack("B", data)[0])
