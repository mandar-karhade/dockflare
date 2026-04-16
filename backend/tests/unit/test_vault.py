"""Unit tests for VaultService."""

from __future__ import annotations

import pytest

from app.core.errors import VaultError
from app.core.vault import VaultService


def test_vault_round_trip(vault: VaultService):
    token = "eyJhbGciOiJIUzI1NiJ9.test.token"
    encrypted = vault.encrypt(token)
    decrypted = vault.decrypt(encrypted)
    assert decrypted == token


def test_vault_nonce_uniqueness(vault: VaultService):
    """Each encryption produces different ciphertext."""
    token = "same-plaintext"
    a = vault.encrypt(token)
    b = vault.encrypt(token)
    assert a != b
    assert vault.decrypt(a) == vault.decrypt(b) == token


def test_vault_tamper_detection(vault: VaultService):
    encrypted = vault.encrypt("secret")
    tampered = encrypted[:-1] + bytes([encrypted[-1] ^ 0x01])
    with pytest.raises(VaultError, match="Decryption failed"):
        vault.decrypt(tampered)


def test_vault_rejects_wrong_key_size():
    with pytest.raises(ValueError, match="32 bytes"):
        VaultService(key=b"tooshort")


def test_vault_rejects_short_ciphertext(vault: VaultService):
    with pytest.raises(VaultError, match="too short"):
        vault.decrypt(b"short")


def test_vault_empty_string(vault: VaultService):
    encrypted = vault.encrypt("")
    assert vault.decrypt(encrypted) == ""


def test_vault_unicode(vault: VaultService):
    token = "🔑 unicode token with émojis"
    encrypted = vault.encrypt(token)
    assert vault.decrypt(encrypted) == token
