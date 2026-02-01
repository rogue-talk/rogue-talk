"""Player state for the server."""

from __future__ import annotations

import time
from asyncio import StreamReader, StreamWriter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiortc import RTCDataChannel, RTCPeerConnection

    from ..audio.webrtc_tracks import ServerAudioRelay, ServerOutboundTrack


@dataclass
class Player:
    id: int
    name: str
    x: int
    y: int
    # Legacy TCP connection (used only for signaling now)
    reader: StreamReader | None = None
    writer: StreamWriter | None = None
    # WebRTC connection
    peer_connection: RTCPeerConnection | None = None
    data_channel: RTCDataChannel | None = None
    # Audio tracks
    audio_relay: ServerAudioRelay | None = None
    # Outbound tracks: source_player_id -> track (one per nearby speaker)
    outbound_tracks: dict[int, "ServerOutboundTrack"] = field(default_factory=dict)
    # State
    is_muted: bool = False
    current_level: str = "main"  # Name of the level the player is currently on
    public_key: bytes = b""  # Ed25519 public key for authentication
    last_pong_time: float = field(default_factory=time.monotonic)
    webrtc_connected: bool = False
    needs_renegotiation: bool = False
