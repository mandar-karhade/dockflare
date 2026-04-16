"""AES-256-GCM encryption vault for secret storage.

Storage format: nonce (12 bytes) || ciphertext || tag (16 bytes)
Key: 32-byte master key from /run/secrets/master_key or config.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.errors import VaultError

NONCE_SIZE = 12
KEY_SIZE = 32


class VaultService:
    """Encrypts and decrypts secrets with AES-256-GCM."""

    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_SIZE:
            msg = f"Master key must be exactly {KEY_SIZE} bytes, got {len(key)}"
            raise ValueError(msg)
        self._aesgcm = AESGCM(key)

    def encrypt(self, plaintext: str) -> bytes:
        """Encrypt a string, returning nonce || ciphertext || tag."""
        nonce = os.urandom(NONCE_SIZE)
        ct = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        return nonce + ct

    def decrypt(self, data: bytes) -> str:
        """Decrypt nonce || ciphertext || tag back to a string."""
        if len(data) < NONCE_SIZE + 16:
            raise VaultError("Ciphertext too short")
        nonce = data[:NONCE_SIZE]
        ct = data[NONCE_SIZE:]
        try:
            plaintext = self._aesgcm.decrypt(nonce, ct, None)
        except Exception as e:
            raise VaultError(f"Decryption failed: {e}") from e
        return plaintext.decode("utf-8")

    @classmethod
    def from_file(cls, path: str | Path) -> VaultService:
        """Load master key from a file (Docker secret mount)."""
        key = Path(path).read_bytes().strip()
        if len(key) != KEY_SIZE:
            raise VaultError(f"Key file must contain exactly {KEY_SIZE} bytes, got {len(key)}")
        return cls(key=key)
