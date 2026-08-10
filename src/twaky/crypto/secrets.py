"""Symmetric encryption for at-rest secrets (OAuth tokens, future skills API keys).

Uses Fernet (AES-128-CBC + HMAC-SHA256). Key comes from TWAKY_SECRET_KEY env var,
generated once via:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Loss of TWAKY_SECRET_KEY = loss of every encrypted credential. Store in the same
secrets manager as .env (currently just the deploy checkout on twake-dev).
"""

from __future__ import annotations

import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from twaky.config import settings

log = logging.getLogger(__name__)


class SecretsUnavailable(RuntimeError):
    """Raised when TWAKY_SECRET_KEY is unset or malformed."""


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = settings.twaky_secret_key
    if not key:
        raise SecretsUnavailable(
            "TWAKY_SECRET_KEY is not set. Generate with "
            '`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` '
            "and add to .env."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as e:  # ValueError / InvalidToken subclass
        raise SecretsUnavailable(f"TWAKY_SECRET_KEY malformed: {e}") from e


def encrypt(plaintext: str) -> str:
    """Return base64-encoded ciphertext (ASCII-safe for TEXT columns)."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Reverse of encrypt(). Raises InvalidToken on tamper or wrong key."""
    return _fernet().decrypt(ciphertext.encode()).decode()


__all__ = ["InvalidToken", "SecretsUnavailable", "decrypt", "encrypt"]
