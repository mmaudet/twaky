"""Unit tests for the Fernet encryption helper."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from twaky.config import settings
from twaky.crypto.secrets import (
    InvalidToken,
    SecretsUnavailable,
    _fernet,
    decrypt,
    encrypt,
)


@pytest.fixture(autouse=True)
def clear_fernet_cache() -> None:
    """Clear the lru_cache on _fernet before every test so key changes take effect."""
    _fernet.cache_clear()
    yield
    _fernet.cache_clear()


def test_encrypt_decrypt_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "twaky_secret_key", Fernet.generate_key().decode())
    assert decrypt(encrypt("hello")) == "hello"


def test_missing_key_raises_secrets_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "twaky_secret_key", "")
    with pytest.raises(SecretsUnavailable, match="TWAKY_SECRET_KEY"):
        encrypt("anything")


def test_malformed_key_raises_secrets_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "twaky_secret_key", "not-a-fernet-key")
    with pytest.raises(SecretsUnavailable):
        encrypt("anything")


def test_tampered_ciphertext_raises_invalid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "twaky_secret_key", Fernet.generate_key().decode())
    ciphertext = encrypt("secret")
    # Flip one character in the middle of the ciphertext
    mid = len(ciphertext) // 2
    tampered = (
        ciphertext[:mid]
        + ("A" if ciphertext[mid] != "A" else "B")
        + ciphertext[mid + 1 :]
    )
    with pytest.raises(InvalidToken):
        decrypt(tampered)


def test_wrong_key_raises_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    key_a = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "twaky_secret_key", key_a)
    ciphertext = encrypt("secret")

    # Switch to a different key and clear the cache so _fernet() re-reads settings
    key_b = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "twaky_secret_key", key_b)
    _fernet.cache_clear()

    with pytest.raises(InvalidToken):
        decrypt(ciphertext)
