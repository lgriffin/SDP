"""Throwaway Ed25519 signing keys in OpenSSH format, as a person's git signing key would be."""

from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sdp.adapters.ledger.git import public_key_line


def write_signing_key(directory: Path, principal: str) -> tuple[Path, Ed25519PrivateKey]:
    key = Ed25519PrivateKey.generate()
    path = directory / f"{principal}_ed25519"
    path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption())
    )
    path.chmod(0o600)
    with (directory / "allowed_signers").open("a") as handle:
        handle.write(f"{principal} {public_key_line(key)}\n")
    return path, key
