"""Tests for the wire protocol serialization/deserialization."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rogue_talk.common.protocol import (
    AudioFrame,
    AuthResult,
    PlayerInfo,
    WorldState,
    deserialize_audio_frame,
    deserialize_audio_track_map,
    deserialize_auth_challenge,
    deserialize_auth_response,
    deserialize_auth_result,
    deserialize_client_hello,
    deserialize_door_transition,
    deserialize_level_files_data,
    deserialize_level_files_request,
    deserialize_level_manifest,
    deserialize_level_manifest_request,
    deserialize_level_pack_data,
    deserialize_level_pack_request,
    deserialize_mute_status,
    deserialize_player_joined,
    deserialize_player_left,
    deserialize_position_ack,
    deserialize_position_update,
    deserialize_server_hello,
    deserialize_webrtc_answer,
    deserialize_webrtc_ice,
    deserialize_webrtc_offer,
    deserialize_world_state,
    serialize_audio_frame,
    serialize_audio_track_map,
    serialize_auth_challenge,
    serialize_auth_response,
    serialize_auth_result,
    serialize_client_hello,
    serialize_door_transition,
    serialize_level_files_data,
    serialize_level_files_request,
    serialize_level_manifest,
    serialize_level_manifest_request,
    serialize_level_pack_data,
    serialize_level_pack_request,
    serialize_mute_status,
    serialize_player_joined,
    serialize_player_left,
    serialize_position_ack,
    serialize_position_update,
    serialize_server_hello,
    serialize_webrtc_answer,
    serialize_webrtc_ice,
    serialize_webrtc_offer,
    serialize_world_state,
)


class TestClientHello:
    """Tests for CLIENT_HELLO message type."""

    def test_roundtrip_simple(self) -> None:
        """Test basic roundtrip with simple name."""
        name = "player1"
        data = serialize_client_hello(name)
        result = deserialize_client_hello(data)
        assert result == name

    def test_roundtrip_unicode(self) -> None:
        """Test roundtrip with unicode characters."""
        name = "プレイヤー"
        data = serialize_client_hello(name)
        result = deserialize_client_hello(data)
        assert result == name

    def test_roundtrip_empty(self) -> None:
        """Test roundtrip with empty name."""
        name = ""
        data = serialize_client_hello(name)
        result = deserialize_client_hello(data)
        assert result == name

    @given(st.text(max_size=100))
    @settings(max_examples=50)
    def test_roundtrip_hypothesis(self, name: str) -> None:
        """Property-based test for CLIENT_HELLO roundtrip."""
        data = serialize_client_hello(name)
        result = deserialize_client_hello(data)
        assert result == name


class TestServerHello:
    """Tests for SERVER_HELLO message type."""

    def test_roundtrip_simple(self) -> None:
        """Test basic roundtrip."""
        player_id = 42
        room_width = 80
        room_height = 24
        spawn_x = 10
        spawn_y = 5
        level_data = b"##########\n#........#\n##########"
        level_name = "main"

        data = serialize_server_hello(
            player_id, room_width, room_height, spawn_x, spawn_y, level_data, level_name
        )
        result = deserialize_server_hello(data)

        assert result == (
            player_id,
            room_width,
            room_height,
            spawn_x,
            spawn_y,
            level_data,
            level_name,
        )

    def test_roundtrip_empty_level(self) -> None:
        """Test with empty level data."""
        data = serialize_server_hello(1, 20, 15, 0, 0, b"", "empty")
        result = deserialize_server_hello(data)
        assert result == (1, 20, 15, 0, 0, b"", "empty")


class TestPositionUpdate:
    """Tests for POSITION_UPDATE message type."""

    def test_roundtrip(self) -> None:
        """Test basic roundtrip."""
        seq, x, y = 12345, 50, 30
        data = serialize_position_update(seq, x, y)
        result = deserialize_position_update(data)
        assert result == (seq, x, y)

    @given(
        st.integers(min_value=0, max_value=2**32 - 1),
        st.integers(min_value=0, max_value=65535),
        st.integers(min_value=0, max_value=65535),
    )
    @settings(max_examples=50)
    def test_roundtrip_hypothesis(self, seq: int, x: int, y: int) -> None:
        """Property-based test for position updates."""
        data = serialize_position_update(seq, x, y)
        result = deserialize_position_update(data)
        assert result == (seq, x, y)


class TestPositionAck:
    """Tests for POSITION_ACK message type."""

    def test_roundtrip(self) -> None:
        """Test basic roundtrip."""
        seq, x, y = 12345, 50, 30
        data = serialize_position_ack(seq, x, y)
        result = deserialize_position_ack(data)
        assert result == (seq, x, y)


class TestWorldState:
    """Tests for WORLD_STATE message type."""

    def test_roundtrip_empty(self) -> None:
        """Test with no players."""
        players: list[PlayerInfo] = []
        data = serialize_world_state(players)
        result = deserialize_world_state(data)
        assert result.players == []

    def test_roundtrip_single_player(self) -> None:
        """Test with a single player."""
        players = [PlayerInfo(1, 10, 20, False, "alice", "main")]
        data = serialize_world_state(players)
        result = deserialize_world_state(data)
        assert len(result.players) == 1
        assert result.players[0].player_id == 1
        assert result.players[0].x == 10
        assert result.players[0].y == 20
        assert result.players[0].is_muted is False
        assert result.players[0].name == "alice"
        assert result.players[0].level == "main"

    def test_roundtrip_multiple_players(self) -> None:
        """Test with multiple players."""
        players = [
            PlayerInfo(1, 10, 20, False, "alice", "main"),
            PlayerInfo(2, 30, 40, True, "bob", "dungeon"),
            PlayerInfo(3, 0, 0, False, "carol", "main"),
        ]
        data = serialize_world_state(players)
        result = deserialize_world_state(data)
        assert len(result.players) == 3
        for i, p in enumerate(result.players):
            assert p.player_id == players[i].player_id
            assert p.x == players[i].x
            assert p.y == players[i].y
            assert p.is_muted == players[i].is_muted
            assert p.name == players[i].name
            assert p.level == players[i].level


class TestAudioFrame:
    """Tests for AUDIO_FRAME message type."""

    def test_roundtrip(self) -> None:
        """Test basic roundtrip."""
        frame = AudioFrame(
            player_id=42,
            timestamp_ms=1234567890,
            volume=0.75,
            opus_data=b"\x00\x01\x02",
        )
        data = serialize_audio_frame(frame)
        result = deserialize_audio_frame(data)

        assert result.player_id == frame.player_id
        assert result.timestamp_ms == frame.timestamp_ms
        assert abs(result.volume - frame.volume) < 0.001  # Float precision
        assert result.opus_data == frame.opus_data

    def test_volume_boundaries(self) -> None:
        """Test volume at boundaries."""
        for vol in [0.0, 0.5, 1.0]:
            frame = AudioFrame(1, 0, vol, b"test")
            data = serialize_audio_frame(frame)
            result = deserialize_audio_frame(data)
            assert abs(result.volume - vol) < 0.001

    @given(st.binary(min_size=1, max_size=1000))
    @settings(max_examples=20)
    def test_roundtrip_opus_data(self, opus_data: bytes) -> None:
        """Property-based test for opus data."""
        frame = AudioFrame(1, 0, 0.5, opus_data)
        data = serialize_audio_frame(frame)
        result = deserialize_audio_frame(data)
        assert result.opus_data == opus_data


class TestPlayerJoined:
    """Tests for PLAYER_JOINED message type."""

    def test_roundtrip(self) -> None:
        """Test basic roundtrip."""
        player_id, name = 42, "newplayer"
        data = serialize_player_joined(player_id, name)
        result = deserialize_player_joined(data)
        assert result == (player_id, name)

    @given(st.integers(min_value=0, max_value=2**32 - 1), st.text(max_size=50))
    @settings(max_examples=50)
    def test_roundtrip_hypothesis(self, player_id: int, name: str) -> None:
        """Property-based test."""
        data = serialize_player_joined(player_id, name)
        result = deserialize_player_joined(data)
        assert result == (player_id, name)


class TestPlayerLeft:
    """Tests for PLAYER_LEFT message type."""

    def test_roundtrip(self) -> None:
        """Test basic roundtrip."""
        player_id = 42
        data = serialize_player_left(player_id)
        result = deserialize_player_left(data)
        assert result == player_id


class TestMuteStatus:
    """Tests for MUTE_STATUS message type."""

    def test_roundtrip_true(self) -> None:
        """Test muted state."""
        data = serialize_mute_status(True)
        result = deserialize_mute_status(data)
        assert result is True

    def test_roundtrip_false(self) -> None:
        """Test unmuted state."""
        data = serialize_mute_status(False)
        result = deserialize_mute_status(data)
        assert result is False


class TestLevelPackRequest:
    """Tests for LEVEL_PACK_REQUEST message type."""

    def test_roundtrip(self) -> None:
        """Test basic roundtrip."""
        name = "dungeon_level"
        data = serialize_level_pack_request(name)
        result = deserialize_level_pack_request(data)
        assert result == name


class TestLevelPackData:
    """Tests for LEVEL_PACK_DATA message type."""

    def test_roundtrip(self) -> None:
        """Test basic roundtrip."""
        tarball = b"fake tarball data here"
        data = serialize_level_pack_data(tarball)
        result = deserialize_level_pack_data(data)
        assert result == tarball

    @given(st.binary(max_size=10000))
    @settings(max_examples=20)
    def test_roundtrip_hypothesis(self, tarball: bytes) -> None:
        """Property-based test."""
        data = serialize_level_pack_data(tarball)
        result = deserialize_level_pack_data(data)
        assert result == tarball


class TestDoorTransition:
    """Tests for DOOR_TRANSITION message type."""

    def test_roundtrip(self) -> None:
        """Test basic roundtrip."""
        target_level = "dungeon"
        spawn_x, spawn_y = 5, 10
        data = serialize_door_transition(target_level, spawn_x, spawn_y)
        result = deserialize_door_transition(data)
        assert result == (target_level, spawn_x, spawn_y)


class TestLevelManifestRequest:
    """Tests for LEVEL_MANIFEST_REQUEST message type."""

    def test_roundtrip(self) -> None:
        """Test basic roundtrip."""
        name = "main"
        data = serialize_level_manifest_request(name)
        result = deserialize_level_manifest_request(data)
        assert result == name


class TestLevelManifest:
    """Tests for LEVEL_MANIFEST message type."""

    def test_roundtrip(self) -> None:
        """Test basic roundtrip."""
        manifest = {
            "level.txt": ("abc123hash", 1024),
            "tiles.json": ("def456hash", 512),
        }
        data = serialize_level_manifest(manifest)
        result = deserialize_level_manifest(data)
        assert result == manifest

    def test_roundtrip_empty(self) -> None:
        """Test empty manifest."""
        manifest: dict[str, tuple[str, int]] = {}
        data = serialize_level_manifest(manifest)
        result = deserialize_level_manifest(data)
        assert result == manifest


class TestLevelFilesRequest:
    """Tests for LEVEL_FILES_REQUEST message type."""

    def test_roundtrip(self) -> None:
        """Test basic roundtrip."""
        level_name = "dungeon"
        filenames = ["level.txt", "tiles.json", "config.json"]
        data = serialize_level_files_request(level_name, filenames)
        result = deserialize_level_files_request(data)
        assert result == (level_name, filenames)

    def test_roundtrip_empty(self) -> None:
        """Test empty filenames list."""
        data = serialize_level_files_request("main", [])
        result = deserialize_level_files_request(data)
        assert result == ("main", [])


class TestLevelFilesData:
    """Tests for LEVEL_FILES_DATA message type."""

    def test_roundtrip(self) -> None:
        """Test basic roundtrip."""
        files = {
            "level.txt": b"##########\n#........#",
            "config.json": b'{"name": "test"}',
        }
        data = serialize_level_files_data(files)
        result = deserialize_level_files_data(data)
        assert result == files

    def test_roundtrip_empty(self) -> None:
        """Test empty files dict."""
        files: dict[str, bytes] = {}
        data = serialize_level_files_data(files)
        result = deserialize_level_files_data(data)
        assert result == files


class TestAuthChallenge:
    """Tests for AUTH_CHALLENGE message type."""

    def test_roundtrip(self) -> None:
        """Test basic roundtrip."""
        nonce = b"\x00" * 16 + b"\xff" * 16
        data = serialize_auth_challenge(nonce)
        result = deserialize_auth_challenge(data)
        assert result == nonce


class TestAuthResponse:
    """Tests for AUTH_RESPONSE message type."""

    def test_roundtrip(self) -> None:
        """Test basic roundtrip."""
        public_key = b"\x01" * 32
        name = "testuser"
        signature = b"\x02" * 64
        data = serialize_auth_response(public_key, name, signature)
        result = deserialize_auth_response(data)
        assert result == (public_key, name, signature)


class TestAuthResult:
    """Tests for AUTH_RESULT message type."""

    def test_roundtrip_success(self) -> None:
        """Test success result."""
        data = serialize_auth_result(AuthResult.SUCCESS)
        result = deserialize_auth_result(data)
        assert result == AuthResult.SUCCESS

    def test_roundtrip_all_results(self) -> None:
        """Test all result types."""
        for auth_result in AuthResult:
            data = serialize_auth_result(auth_result)
            result = deserialize_auth_result(data)
            assert result == auth_result


class TestWebRTCOffer:
    """Tests for WEBRTC_OFFER message type."""

    def test_roundtrip(self) -> None:
        """Test basic roundtrip."""
        sdp = "v=0\r\no=- 123 456 IN IP4 127.0.0.1\r\n"
        data = serialize_webrtc_offer(sdp)
        result = deserialize_webrtc_offer(data)
        assert result == sdp

    @given(st.text(max_size=5000))
    @settings(max_examples=20)
    def test_roundtrip_hypothesis(self, sdp: str) -> None:
        """Property-based test."""
        data = serialize_webrtc_offer(sdp)
        result = deserialize_webrtc_offer(data)
        assert result == sdp


class TestWebRTCAnswer:
    """Tests for WEBRTC_ANSWER message type."""

    def test_roundtrip(self) -> None:
        """Test basic roundtrip."""
        sdp = "v=0\r\no=- 789 012 IN IP4 127.0.0.1\r\n"
        data = serialize_webrtc_answer(sdp)
        result = deserialize_webrtc_answer(data)
        assert result == sdp


class TestWebRTCICE:
    """Tests for WEBRTC_ICE message type."""

    def test_roundtrip_with_values(self) -> None:
        """Test with all values present."""
        sdp_mid = "audio"
        sdp_mline_index = 0
        candidate = "candidate:1 1 UDP 2122252543 192.168.1.1 12345 typ host"
        data = serialize_webrtc_ice(sdp_mid, sdp_mline_index, candidate)
        result = deserialize_webrtc_ice(data)
        assert result == (sdp_mid, sdp_mline_index, candidate)

    def test_roundtrip_with_none(self) -> None:
        """Test with None values."""
        data = serialize_webrtc_ice(None, None, "candidate:test")
        result = deserialize_webrtc_ice(data)
        assert result == (None, 0, "candidate:test")


class TestAudioTrackMap:
    """Tests for AUDIO_TRACK_MAP message type."""

    def test_roundtrip(self) -> None:
        """Test basic roundtrip."""
        track_map = {"0": 1, "1": 2, "2": 3}
        data = serialize_audio_track_map(track_map)
        result = deserialize_audio_track_map(data)
        assert result == track_map

    def test_roundtrip_empty(self) -> None:
        """Test empty map."""
        track_map: dict[str, int] = {}
        data = serialize_audio_track_map(track_map)
        result = deserialize_audio_track_map(data)
        assert result == track_map
