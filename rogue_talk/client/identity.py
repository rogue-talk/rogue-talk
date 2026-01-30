"""Client identity management.

Stores Ed25519 keypair in ~/.rogue-talk/identity.json
"""

import json
from dataclasses import dataclass
from pathlib import Path

from ..common.crypto import (
    generate_keypair,
    serialize_private_key,
    serialize_public_key,
    deserialize_private_key,
    deserialize_public_key,
)


@dataclass
class Identity:
    """Client identity with keypair."""

    private_key: bytes
    public_key: bytes


def get_identity_path() -> Path:
    """Get the path to the identity file."""
    return Path.home() / ".rogue-talk" / "identity.json"


def load_or_create_identity() -> Identity:
    """Load existing identity or create a new one.

    Returns:
        Identity with private and public key bytes.
    """
    identity_path = get_identity_path()

    if identity_path.exists():
        try:
            data = json.loads(identity_path.read_text())
            return Identity(
                private_key=deserialize_private_key(data["private_key"]),
                public_key=deserialize_public_key(data["public_key"]),
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            # Corrupted file, generate new identity
            pass

    # Generate new keypair
    private_key, public_key = generate_keypair()
    identity = Identity(private_key=private_key, public_key=public_key)

    # Save to disk
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "private_key": serialize_private_key(private_key),
        "public_key": serialize_public_key(public_key),
    }
    identity_path.write_text(json.dumps(data, indent=2))

    return identity
