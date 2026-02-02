"""Integration tests for full protocol message write/read cycles."""

from __future__ import annotations

import pytest

from rogue_talk.common.protocol import (
    MessageType,
    PlayerInfo,
    read_message,
    serialize_client_hello,
    serialize_mute_status,
    serialize_player_joined,
    serialize_position_update,
    serialize_world_state,
    write_message,
)

from tests.conftest import MockStreamReader, MockStreamWriter


@pytest.mark.integration
class TestProtocolRoundtrip:
    """Integration tests for protocol message roundtrips using mock streams."""

    @pytest.mark.asyncio
    async def test_write_read_client_hello(
        self, mock_writer: MockStreamWriter, mock_reader: MockStreamReader
    ) -> None:
        """Test writing and reading a CLIENT_HELLO message."""
        name = "testplayer"
        payload = serialize_client_hello(name)

        await write_message(mock_writer, MessageType.CLIENT_HELLO, payload)

        # Feed written data to reader
        mock_reader.feed_data(mock_writer.get_data())

        msg_type, received_payload = await read_message(mock_reader)

        assert msg_type == MessageType.CLIENT_HELLO
        assert received_payload == payload

    @pytest.mark.asyncio
    async def test_write_read_position_update(
        self, mock_writer: MockStreamWriter, mock_reader: MockStreamReader
    ) -> None:
        """Test writing and reading a POSITION_UPDATE message."""
        seq, x, y = 12345, 50, 30
        payload = serialize_position_update(seq, x, y)

        await write_message(mock_writer, MessageType.POSITION_UPDATE, payload)
        mock_reader.feed_data(mock_writer.get_data())

        msg_type, received_payload = await read_message(mock_reader)

        assert msg_type == MessageType.POSITION_UPDATE
        assert received_payload == payload

    @pytest.mark.asyncio
    async def test_write_read_world_state(
        self, mock_writer: MockStreamWriter, mock_reader: MockStreamReader
    ) -> None:
        """Test writing and reading a WORLD_STATE message."""
        players = [
            PlayerInfo(1, 10, 20, False, "alice", "main"),
            PlayerInfo(2, 30, 40, True, "bob", "dungeon"),
        ]
        payload = serialize_world_state(players)

        await write_message(mock_writer, MessageType.WORLD_STATE, payload)
        mock_reader.feed_data(mock_writer.get_data())

        msg_type, received_payload = await read_message(mock_reader)

        assert msg_type == MessageType.WORLD_STATE
        assert received_payload == payload

    @pytest.mark.asyncio
    async def test_write_read_empty_payload(
        self, mock_writer: MockStreamWriter, mock_reader: MockStreamReader
    ) -> None:
        """Test writing and reading a message with empty payload."""
        await write_message(mock_writer, MessageType.PING, b"")
        mock_reader.feed_data(mock_writer.get_data())

        msg_type, received_payload = await read_message(mock_reader)

        assert msg_type == MessageType.PING
        assert received_payload == b""

    @pytest.mark.asyncio
    async def test_multiple_messages(
        self, mock_writer: MockStreamWriter, mock_reader: MockStreamReader
    ) -> None:
        """Test writing and reading multiple messages in sequence."""
        # Write multiple messages
        await write_message(
            mock_writer, MessageType.CLIENT_HELLO, serialize_client_hello("player1")
        )
        await write_message(
            mock_writer,
            MessageType.POSITION_UPDATE,
            serialize_position_update(1, 10, 20),
        )
        await write_message(
            mock_writer, MessageType.MUTE_STATUS, serialize_mute_status(True)
        )

        mock_reader.feed_data(mock_writer.get_data())

        # Read all messages
        msg1_type, _ = await read_message(mock_reader)
        msg2_type, _ = await read_message(mock_reader)
        msg3_type, _ = await read_message(mock_reader)

        assert msg1_type == MessageType.CLIENT_HELLO
        assert msg2_type == MessageType.POSITION_UPDATE
        assert msg3_type == MessageType.MUTE_STATUS


@pytest.mark.integration
class TestMessageFormat:
    """Tests for the wire format of messages."""

    @pytest.mark.asyncio
    async def test_message_length_prefix(self, mock_writer: MockStreamWriter) -> None:
        """Test that messages are properly length-prefixed."""
        import struct

        payload = b"test payload"
        await write_message(mock_writer, MessageType.CLIENT_HELLO, payload)

        data = mock_writer.get_data()

        # First 4 bytes should be length (big-endian)
        length = struct.unpack(">I", data[:4])[0]
        assert length == 1 + len(payload)  # 1 for message type + payload

    @pytest.mark.asyncio
    async def test_message_type_byte(self, mock_writer: MockStreamWriter) -> None:
        """Test that message type is correctly encoded."""
        await write_message(mock_writer, MessageType.PLAYER_JOINED, b"")

        data = mock_writer.get_data()

        # 5th byte should be message type
        msg_type = data[4]
        assert msg_type == MessageType.PLAYER_JOINED
