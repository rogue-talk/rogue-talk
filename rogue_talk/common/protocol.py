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
    POSITION_ACK = 0x09  # Server acknowledges a position update
    LEVEL_PACK_REQUEST = 0x10  # Client -> Server: Request level by name
    LEVEL_PACK_DATA = 0x11  # Server -> Client: Tarball bytes
    DOOR_TRANSITION = 0x12  # Server -> Client: Player entered door, load new level


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


# SERVER_HELLO: player_id, room_width, room_height, spawn_x, spawn_y, level_data
def serialize_server_hello(
    player_id: int,
    room_width: int,
    room_height: int,
    spawn_x: int,
    spawn_y: int,
    level_data: bytes,
) -> bytes:
    base = struct.pack(">IHHHH", player_id, room_width, room_height, spawn_x, spawn_y)
    level_length = struct.pack(">H", len(level_data))
    return base + level_length + level_data


def deserialize_server_hello(data: bytes) -> tuple[int, int, int, int, int, bytes]:
    player_id, room_width, room_height, spawn_x, spawn_y = struct.unpack(
        ">IHHHH", data[:12]
    )
    level_length = struct.unpack(">H", data[12:14])[0]
    level_data = data[14 : 14 + level_length]
    return player_id, room_width, room_height, spawn_x, spawn_y, level_data


# POSITION_UPDATE: seq, x, y
def serialize_position_update(seq: int, x: int, y: int) -> bytes:
    return struct.pack(">IHH", seq, x, y)


def deserialize_position_update(data: bytes) -> tuple[int, int, int]:
    return struct.unpack(">IHH", data)


# POSITION_ACK: seq, x, y (server's authoritative position after processing move)
def serialize_position_ack(seq: int, x: int, y: int) -> bytes:
    return struct.pack(">IHH", seq, x, y)


def deserialize_position_ack(data: bytes) -> tuple[int, int, int]:
    return struct.unpack(">IHH", data)


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


# LEVEL_PACK_REQUEST: name (UTF-8 string)
def serialize_level_pack_request(name: str) -> bytes:
    name_bytes = name.encode("utf-8")
    return struct.pack(">H", len(name_bytes)) + name_bytes


def deserialize_level_pack_request(data: bytes) -> str:
    name_len = struct.unpack(">H", data[:2])[0]
    return data[2 : 2 + name_len].decode("utf-8")


# LEVEL_PACK_DATA: tarball bytes (length-prefixed)
def serialize_level_pack_data(tarball: bytes) -> bytes:
    return struct.pack(">I", len(tarball)) + tarball


def deserialize_level_pack_data(data: bytes) -> bytes:
    tarball_len = struct.unpack(">I", data[:4])[0]
    return data[4 : 4 + tarball_len]


# DOOR_TRANSITION: target_level, spawn_x, spawn_y
def serialize_door_transition(target_level: str, spawn_x: int, spawn_y: int) -> bytes:
    level_bytes = target_level.encode("utf-8")
    return struct.pack(">H", len(level_bytes)) + level_bytes + struct.pack(">HH", spawn_x, spawn_y)


def deserialize_door_transition(data: bytes) -> tuple[str, int, int]:
    level_len = struct.unpack(">H", data[:2])[0]
    target_level = data[2 : 2 + level_len].decode("utf-8")
    spawn_x, spawn_y = struct.unpack(">HH", data[2 + level_len : 2 + level_len + 4])
    return target_level, spawn_x, spawn_y
