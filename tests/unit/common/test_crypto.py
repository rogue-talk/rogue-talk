"""Tests for Ed25519 cryptographic operations."""

from __future__ import annotations

import os

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rogue_talk.common.crypto import (
    deserialize_private_key,
    deserialize_public_key,
    generate_keypair,
    serialize_private_key,
    serialize_public_key,
    sign_challenge,
    verify_signature,
)


class TestGenerateKeypair:
    """Tests for keypair generation."""

    def test_keypair_sizes(self) -> None:
        """Test that keys are correct size."""
        private_key, public_key = generate_keypair()
        assert len(private_key) == 32
        assert len(public_key) == 32

    def test_keypair_uniqueness(self) -> None:
        """Test that generated keypairs are unique."""
        keypairs = [generate_keypair() for _ in range(10)]
        private_keys = [k[0] for k in keypairs]
        public_keys = [k[1] for k in keypairs]

        # All private keys should be unique
        assert len(set(private_keys)) == 10
        # All public keys should be unique
        assert len(set(public_keys)) == 10

    def test_keypair_different(self) -> None:
        """Test that private and public key are different."""
        private_key, public_key = generate_keypair()
        assert private_key != public_key


class TestSignAndVerify:
    """Tests for signing and verification."""

    def test_sign_verify_success(self, keypair: tuple[bytes, bytes]) -> None:
        """Test that valid signature verifies."""
        private_key, public_key = keypair
        nonce = os.urandom(32)
        name = "testplayer"

        signature = sign_challenge(private_key, nonce, name)
        assert len(signature) == 64

        assert verify_signature(public_key, nonce, name, signature) is True

    def test_verify_wrong_public_key(self, keypair: tuple[bytes, bytes]) -> None:
        """Test that signature fails with wrong public key."""
        private_key, public_key = keypair
        _, wrong_public_key = generate_keypair()
        nonce = os.urandom(32)
        name = "testplayer"

        signature = sign_challenge(private_key, nonce, name)
        assert verify_signature(wrong_public_key, nonce, name, signature) is False

    def test_verify_wrong_nonce(self, keypair: tuple[bytes, bytes]) -> None:
        """Test that signature fails with wrong nonce."""
        private_key, public_key = keypair
        nonce = os.urandom(32)
        wrong_nonce = os.urandom(32)
        name = "testplayer"

        signature = sign_challenge(private_key, nonce, name)
        assert verify_signature(public_key, wrong_nonce, name, signature) is False

    def test_verify_wrong_name(self, keypair: tuple[bytes, bytes]) -> None:
        """Test that signature fails with wrong name."""
        private_key, public_key = keypair
        nonce = os.urandom(32)
        name = "testplayer"
        wrong_name = "wrongplayer"

        signature = sign_challenge(private_key, nonce, name)
        assert verify_signature(public_key, nonce, wrong_name, signature) is False

    def test_verify_corrupted_signature(self, keypair: tuple[bytes, bytes]) -> None:
        """Test that corrupted signature fails."""
        private_key, public_key = keypair
        nonce = os.urandom(32)
        name = "testplayer"

        signature = sign_challenge(private_key, nonce, name)
        corrupted = bytearray(signature)
        corrupted[0] ^= 0xFF  # Flip bits in first byte
        assert verify_signature(public_key, nonce, name, bytes(corrupted)) is False

    def test_sign_empty_name(self, keypair: tuple[bytes, bytes]) -> None:
        """Test signing with empty name."""
        private_key, public_key = keypair
        nonce = os.urandom(32)
        name = ""

        signature = sign_challenge(private_key, nonce, name)
        assert verify_signature(public_key, nonce, name, signature) is True

    @given(st.text(max_size=100))
    @settings(max_examples=20)
    def test_sign_verify_any_name(self, name: str) -> None:
        """Property-based test for any name."""
        private_key, public_key = generate_keypair()
        nonce = os.urandom(32)

        signature = sign_challenge(private_key, nonce, name)
        assert verify_signature(public_key, nonce, name, signature) is True


class TestKeySerialization:
    """Tests for key serialization."""

    def test_private_key_roundtrip(self, keypair: tuple[bytes, bytes]) -> None:
        """Test private key serialization roundtrip."""
        private_key, _ = keypair
        hex_string = serialize_private_key(private_key)
        result = deserialize_private_key(hex_string)
        assert result == private_key

    def test_public_key_roundtrip(self, keypair: tuple[bytes, bytes]) -> None:
        """Test public key serialization roundtrip."""
        _, public_key = keypair
        hex_string = serialize_public_key(public_key)
        result = deserialize_public_key(hex_string)
        assert result == public_key

    def test_serialized_format(self, keypair: tuple[bytes, bytes]) -> None:
        """Test that serialized keys are valid hex strings."""
        private_key, public_key = keypair

        private_hex = serialize_private_key(private_key)
        public_hex = serialize_public_key(public_key)

        # Should be 64 hex characters for 32 bytes
        assert len(private_hex) == 64
        assert len(public_hex) == 64

        # Should be valid hex
        assert all(c in "0123456789abcdef" for c in private_hex)
        assert all(c in "0123456789abcdef" for c in public_hex)

    def test_key_usable_after_roundtrip(self, keypair: tuple[bytes, bytes]) -> None:
        """Test that keys work after serialization roundtrip."""
        private_key, public_key = keypair

        # Roundtrip the keys
        private_key = deserialize_private_key(serialize_private_key(private_key))
        public_key = deserialize_public_key(serialize_public_key(public_key))

        # Should still be able to sign and verify
        nonce = os.urandom(32)
        name = "test"
        signature = sign_challenge(private_key, nonce, name)
        assert verify_signature(public_key, nonce, name, signature) is True
