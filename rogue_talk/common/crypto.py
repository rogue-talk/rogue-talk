"""Ed25519 cryptographic operations for authentication."""

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization


def generate_keypair() -> tuple[bytes, bytes]:
    """Generate a new Ed25519 keypair.

    Returns:
        Tuple of (private_key_bytes, public_key_bytes), both 32 bytes raw.
    """
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_bytes = private_key.private_bytes_raw()
    public_bytes = public_key.public_bytes_raw()

    return private_bytes, public_bytes


def sign_challenge(private_key_bytes: bytes, nonce: bytes, name: str) -> bytes:
    """Sign a challenge (nonce + name) with the private key.

    Args:
        private_key_bytes: 32-byte raw Ed25519 private key
        nonce: 32-byte random nonce from server
        name: Player name to include in signature

    Returns:
        64-byte Ed25519 signature
    """
    private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    message = nonce + name.encode("utf-8")
    result: bytes = private_key.sign(message)
    return result


def verify_signature(
    public_key_bytes: bytes, nonce: bytes, name: str, signature: bytes
) -> bool:
    """Verify a signature against the challenge.

    Args:
        public_key_bytes: 32-byte raw Ed25519 public key
        nonce: 32-byte random nonce that was sent
        name: Player name that was signed
        signature: 64-byte signature to verify

    Returns:
        True if signature is valid, False otherwise
    """
    try:
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        message = nonce + name.encode("utf-8")
        public_key.verify(signature, message)
        return True
    except Exception:
        return False


def serialize_private_key(private_key_bytes: bytes) -> str:
    """Serialize private key bytes to hex string for JSON storage."""
    return private_key_bytes.hex()


def deserialize_private_key(hex_string: str) -> bytes:
    """Deserialize hex string back to private key bytes."""
    return bytes.fromhex(hex_string)


def serialize_public_key(public_key_bytes: bytes) -> str:
    """Serialize public key bytes to hex string for JSON storage."""
    return public_key_bytes.hex()


def deserialize_public_key(hex_string: str) -> bytes:
    """Deserialize hex string back to public key bytes."""
    return bytes.fromhex(hex_string)
