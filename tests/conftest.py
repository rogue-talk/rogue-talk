"""Shared fixtures for rogue-talk tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


# Mock StreamReader/StreamWriter for protocol tests
class MockStreamReader:
    """Mock asyncio.StreamReader for testing protocol reads."""

    def __init__(self, data: bytes = b"") -> None:
        self._data = data
        self._offset = 0

    def feed_data(self, data: bytes) -> None:
        """Add data to the stream."""
        self._data = self._data[self._offset :] + data
        self._offset = 0

    async def readexactly(self, n: int) -> bytes:
        """Read exactly n bytes."""
        if self._offset + n > len(self._data):
            raise asyncio.IncompleteReadError(
                self._data[self._offset :], n - (len(self._data) - self._offset)
            )
        result = self._data[self._offset : self._offset + n]
        self._offset += n
        return result


class MockStreamWriter:
    """Mock asyncio.StreamWriter for testing protocol writes."""

    def __init__(self) -> None:
        self._data = b""
        self._closed = False

    def write(self, data: bytes) -> None:
        """Write data to the stream."""
        self._data += data

    async def drain(self) -> None:
        """Drain the write buffer (no-op for mock)."""
        pass

    def get_data(self) -> bytes:
        """Get all written data."""
        return self._data

    def clear(self) -> None:
        """Clear written data."""
        self._data = b""

    def close(self) -> None:
        """Close the writer."""
        self._closed = True

    async def wait_closed(self) -> None:
        """Wait for writer to close."""
        pass


@dataclass
class MockPlayer:
    """Minimal player mock for audio routing tests."""

    id: int
    x: int
    y: int
    is_muted: bool = False
    name: str = "test"
    current_level: str = "main"


@pytest.fixture
def mock_reader() -> MockStreamReader:
    """Create a mock stream reader."""
    return MockStreamReader()


@pytest.fixture
def mock_writer() -> MockStreamWriter:
    """Create a mock stream writer."""
    return MockStreamWriter()


@pytest.fixture
def sample_level_string() -> str:
    """A simple level string for testing."""
    return """\
##########
#........#
#...S....#
#........#
##########"""


@pytest.fixture
def keypair() -> tuple[bytes, bytes]:
    """Generate a test Ed25519 keypair."""
    from rogue_talk.common.crypto import generate_keypair

    return generate_keypair()
