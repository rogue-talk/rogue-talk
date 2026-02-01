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
    LEVEL_MANIFEST_REQUEST = 0x13  # Client -> Server: Request manifest for level
    LEVEL_MANIFEST = 0x14  # Server -> Client: {filename: (hash, size), ...}
    LEVEL_FILES_REQUEST = 0x15  # Client -> Server: List of filenames needed
    LEVEL_FILES_DATA = 0x16  # Server -> Client: Concatenated file contents
    AUTH_CHALLENGE = 0x20  # Server -> Client: 32-byte nonce
    AUTH_RESPONSE = 0x21  # Client -> Server: pubkey + name + signature
    AUTH_RESULT = 0x22  # Server -> Client: success/error code
    PING = 0x30  # Server -> Client: keepalive ping
    PONG = 0x31  # Client -> Server: keepalive pong
    # WebRTC signaling messages (used over initial TCP for handshake)
    WEBRTC_OFFER = 0x40  # Client -> Server: SDP offer
    WEBRTC_ANSWER = 0x41  # Server -> Client: SDP answer
    WEBRTC_ICE = 0x42  # Bidirectional: ICE candidate exchange
    AUDIO_TRACK_MAP = 0x43  # Server -> Client: Maps track MIDs to player IDs


@dataclass
class PlayerInfo:
    player_id: int
    x: int
    y: int
    is_muted: bool
    name: str
    level: str


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


# SERVER_HELLO: player_id, room_width, room_height, spawn_x, spawn_y, level_data, level_name
def serialize_server_hello(
    player_id: int,
    room_width: int,
    room_height: int,
    spawn_x: int,
    spawn_y: int,
    level_data: bytes,
    level_name: str,
) -> bytes:
    level_name_bytes = level_name.encode("utf-8")
    base = struct.pack(">IHHHH", player_id, room_width, room_height, spawn_x, spawn_y)
    level_length = struct.pack(">H", len(level_data))
    name_length = struct.pack(">B", len(level_name_bytes))
    return base + level_length + level_data + name_length + level_name_bytes


def deserialize_server_hello(data: bytes) -> tuple[int, int, int, int, int, bytes, str]:
    player_id, room_width, room_height, spawn_x, spawn_y = struct.unpack(
        ">IHHHH", data[:12]
    )
    level_length = struct.unpack(">H", data[12:14])[0]
    level_data = data[14 : 14 + level_length]
    name_offset = 14 + level_length
    name_length = struct.unpack(">B", data[name_offset : name_offset + 1])[0]
    level_name = data[name_offset + 1 : name_offset + 1 + name_length].decode("utf-8")
    return player_id, room_width, room_height, spawn_x, spawn_y, level_data, level_name


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
        level_bytes = p.level.encode("utf-8")
        result += struct.pack(
            ">IHHBIB",
            p.player_id,
            p.x,
            p.y,
            1 if p.is_muted else 0,
            len(name_bytes),
            len(level_bytes),
        )
        result += name_bytes
        result += level_bytes
    return result


def deserialize_world_state(data: bytes) -> WorldState:
    offset = 0
    num_players = struct.unpack(">I", data[offset : offset + 4])[0]
    offset += 4
    players = []
    for _ in range(num_players):
        player_id, x, y, is_muted, name_len, level_len = struct.unpack(
            ">IHHBIB", data[offset : offset + 14]
        )
        offset += 14
        name = data[offset : offset + name_len].decode("utf-8")
        offset += name_len
        level = data[offset : offset + level_len].decode("utf-8")
        offset += level_len
        players.append(PlayerInfo(player_id, x, y, bool(is_muted), name, level))
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
    return (
        struct.pack(">H", len(level_bytes))
        + level_bytes
        + struct.pack(">HH", spawn_x, spawn_y)
    )


def deserialize_door_transition(data: bytes) -> tuple[str, int, int]:
    level_len = struct.unpack(">H", data[:2])[0]
    target_level = data[2 : 2 + level_len].decode("utf-8")
    spawn_x, spawn_y = struct.unpack(">HH", data[2 + level_len : 2 + level_len + 4])
    return target_level, spawn_x, spawn_y


# LEVEL_MANIFEST_REQUEST: level name (same as LEVEL_PACK_REQUEST)
def serialize_level_manifest_request(name: str) -> bytes:
    name_bytes = name.encode("utf-8")
    return struct.pack(">H", len(name_bytes)) + name_bytes


def deserialize_level_manifest_request(data: bytes) -> str:
    name_len = struct.unpack(">H", data[:2])[0]
    return data[2 : 2 + name_len].decode("utf-8")


# LEVEL_MANIFEST: JSON-encoded {filename: [hash, size], ...}
def serialize_level_manifest(manifest: dict[str, tuple[str, int]]) -> bytes:
    import json

    # Convert tuples to lists for JSON serialization
    json_manifest = {k: [v[0], v[1]] for k, v in manifest.items()}
    json_bytes = json.dumps(json_manifest).encode("utf-8")
    return struct.pack(">I", len(json_bytes)) + json_bytes


def deserialize_level_manifest(data: bytes) -> dict[str, tuple[str, int]]:
    import json

    json_len = struct.unpack(">I", data[:4])[0]
    json_bytes = data[4 : 4 + json_len]
    json_manifest = json.loads(json_bytes.decode("utf-8"))
    # Convert lists back to tuples
    return {k: (v[0], v[1]) for k, v in json_manifest.items()}


# LEVEL_FILES_REQUEST: list of filenames
def serialize_level_files_request(level_name: str, filenames: list[str]) -> bytes:
    import json

    level_bytes = level_name.encode("utf-8")
    json_bytes = json.dumps(filenames).encode("utf-8")
    return (
        struct.pack(">H", len(level_bytes))
        + level_bytes
        + struct.pack(">I", len(json_bytes))
        + json_bytes
    )


def deserialize_level_files_request(data: bytes) -> tuple[str, list[str]]:
    import json

    level_len = struct.unpack(">H", data[:2])[0]
    level_name = data[2 : 2 + level_len].decode("utf-8")
    offset = 2 + level_len
    json_len = struct.unpack(">I", data[offset : offset + 4])[0]
    offset += 4
    json_bytes = data[offset : offset + json_len]
    filenames = json.loads(json_bytes.decode("utf-8"))
    return level_name, filenames


# LEVEL_FILES_DATA: concatenated files with headers [filename_len, filename, content_len, content, ...]
def serialize_level_files_data(files: dict[str, bytes]) -> bytes:
    result = struct.pack(">I", len(files))  # Number of files
    for filename, content in files.items():
        filename_bytes = filename.encode("utf-8")
        result += struct.pack(">H", len(filename_bytes))
        result += filename_bytes
        result += struct.pack(">I", len(content))
        result += content
    return result


def deserialize_level_files_data(data: bytes) -> dict[str, bytes]:
    offset = 0
    num_files = struct.unpack(">I", data[offset : offset + 4])[0]
    offset += 4
    files: dict[str, bytes] = {}
    for _ in range(num_files):
        filename_len = struct.unpack(">H", data[offset : offset + 2])[0]
        offset += 2
        filename = data[offset : offset + filename_len].decode("utf-8")
        offset += filename_len
        content_len = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
        content = data[offset : offset + content_len]
        offset += content_len
        files[filename] = content
    return files


# AUTH_CHALLENGE: 32-byte nonce
def serialize_auth_challenge(nonce: bytes) -> bytes:
    return nonce


def deserialize_auth_challenge(data: bytes) -> bytes:
    return data[:32]


# AUTH_RESPONSE: public_key (32 bytes) + signature (64 bytes) + name
def serialize_auth_response(public_key: bytes, name: str, signature: bytes) -> bytes:
    name_bytes = name.encode("utf-8")
    return public_key + signature + struct.pack(">H", len(name_bytes)) + name_bytes


def deserialize_auth_response(data: bytes) -> tuple[bytes, str, bytes]:
    public_key = data[:32]
    signature = data[32:96]
    name_len = struct.unpack(">H", data[96:98])[0]
    name = data[98 : 98 + name_len].decode("utf-8")
    return public_key, name, signature


class AuthResult(enum.IntEnum):
    SUCCESS = 0
    NAME_TAKEN = 1  # Name taken by different key
    KEY_MISMATCH = 2  # Key registered with different name
    INVALID_SIGNATURE = 3
    INVALID_NAME = 4
    ALREADY_CONNECTED = 5  # Player with this key is already connected


# AUTH_RESULT: result code (1 byte)
def serialize_auth_result(result: AuthResult) -> bytes:
    return struct.pack("B", result)


def deserialize_auth_result(data: bytes) -> AuthResult:
    return AuthResult(struct.unpack("B", data[:1])[0])


# WEBRTC_OFFER: SDP string (UTF-8 encoded, length-prefixed)
def serialize_webrtc_offer(sdp: str) -> bytes:
    sdp_bytes = sdp.encode("utf-8")
    return struct.pack(">I", len(sdp_bytes)) + sdp_bytes


def deserialize_webrtc_offer(data: bytes) -> str:
    sdp_len = struct.unpack(">I", data[:4])[0]
    return data[4 : 4 + sdp_len].decode("utf-8")


# WEBRTC_ANSWER: SDP string (UTF-8 encoded, length-prefixed)
def serialize_webrtc_answer(sdp: str) -> bytes:
    sdp_bytes = sdp.encode("utf-8")
    return struct.pack(">I", len(sdp_bytes)) + sdp_bytes


def deserialize_webrtc_answer(data: bytes) -> str:
    sdp_len = struct.unpack(">I", data[:4])[0]
    return data[4 : 4 + sdp_len].decode("utf-8")


# WEBRTC_ICE: ICE candidate (sdpMid, sdpMLineIndex, candidate string)
def serialize_webrtc_ice(
    sdp_mid: str | None, sdp_mline_index: int | None, candidate: str
) -> bytes:
    # Handle None values
    mid_bytes = (sdp_mid or "").encode("utf-8")
    idx = sdp_mline_index if sdp_mline_index is not None else 0
    cand_bytes = candidate.encode("utf-8")
    return (
        struct.pack(">H", len(mid_bytes))
        + mid_bytes
        + struct.pack(">H", idx)
        + struct.pack(">I", len(cand_bytes))
        + cand_bytes
    )


def deserialize_webrtc_ice(data: bytes) -> tuple[str | None, int | None, str]:
    offset = 0
    mid_len = struct.unpack(">H", data[offset : offset + 2])[0]
    offset += 2
    sdp_mid = data[offset : offset + mid_len].decode("utf-8") if mid_len > 0 else None
    offset += mid_len
    sdp_mline_index = struct.unpack(">H", data[offset : offset + 2])[0]
    offset += 2
    cand_len = struct.unpack(">I", data[offset : offset + 4])[0]
    offset += 4
    candidate = data[offset : offset + cand_len].decode("utf-8")
    return sdp_mid, sdp_mline_index, candidate


# AUDIO_TRACK_MAP: Maps track MIDs to source player IDs
def serialize_audio_track_map(track_map: dict[str, int]) -> bytes:
    """Serialize a mapping of track MID -> source player ID."""
    import json

    json_bytes = json.dumps(track_map).encode("utf-8")
    return struct.pack(">I", len(json_bytes)) + json_bytes


def deserialize_audio_track_map(data: bytes) -> dict[str, int]:
    """Deserialize a mapping of track MID -> source player ID."""
    import json
    from typing import cast

    json_len = struct.unpack(">I", data[:4])[0]
    json_bytes = data[4 : 4 + json_len]
    return cast(dict[str, int], json.loads(json_bytes.decode("utf-8")))
