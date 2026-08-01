"""Session cookie helpers matching starlette SessionMiddleware wire format.

Wire format: base64(json(payload)) signed by itsdangerous.TimestampSigner
using settings.api_session_secret as the key. This is the exact serialization
starlette.middleware.sessions.SessionMiddleware uses, so a cookie forged here
is accepted by the middleware without any special handling.
"""

from __future__ import annotations

import base64
import json

import itsdangerous

from twaky.config import settings

SESSION_COOKIE_NAME = "twaky_session"
_SESSION_MAX_AGE = 28800  # 8 hours, matches spec §5.4


def _signer() -> itsdangerous.TimestampSigner:
    return itsdangerous.TimestampSigner(str(settings.api_session_secret))


def sign_session(email: str, sub: str = "test-sub") -> str:
    """Forge a signed session cookie value.

    Public seam for sub-project 3b's Playwright tests (re-exported via
    twaky.api.testing.sign_session). Do NOT use in production — real
    sessions come from the OIDC callback flow.
    """
    payload = {"email": email, "sub": sub}
    data = base64.b64encode(json.dumps(payload).encode("utf-8"))
    signed = _signer().sign(data)
    return signed.decode("utf-8")


def unsign_session(cookie_value: str, max_age: int = _SESSION_MAX_AGE) -> dict | None:
    """Recover the payload from a signed cookie value; return None on failure."""
    if not cookie_value:
        return None
    try:
        data = _signer().unsign(cookie_value, max_age=max_age)
    except itsdangerous.BadSignature:
        return None
    try:
        return json.loads(base64.b64decode(data).decode("utf-8"))
    except (json.JSONDecodeError, ValueError):
        return None


__all__ = ["SESSION_COOKIE_NAME", "sign_session", "unsign_session"]
