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
_MIN_SESSION_SECRET_BYTES = 32


class WeakSessionSecret(RuntimeError):
    """Raised when API_SESSION_SECRET is unset or too short to sign sessions."""


def check_session_secret(secret: str) -> None:
    """Fail fast when the session signing key cannot protect a session cookie.

    ``require_owner`` trusts ``session["email"]`` outright, so a forgeable
    cookie is a full authentication bypass — not a degraded mode. The setting
    defaults to ``""`` on purpose (the ingest / projector / sentinel workers
    import ``settings`` and never serve HTTP), which means nothing would stop
    the API from booting with an empty signing key. This is the check that
    does. Called from the app lifespan, i.e. on every real uvicorn startup.
    """
    if not secret:
        raise WeakSessionSecret(
            "API_SESSION_SECRET is not set — refusing to start the API. "
            "Session cookies would be signed with an empty key, making owner "
            "impersonation trivial. Generate one with `openssl rand -hex 32` "
            "and add it to .env."
        )
    if len(secret.encode()) < _MIN_SESSION_SECRET_BYTES:
        raise WeakSessionSecret(
            f"API_SESSION_SECRET is {len(secret.encode())} bytes, "
            f"below the {_MIN_SESSION_SECRET_BYTES}-byte minimum — refusing to "
            "start the API. Generate one with `openssl rand -hex 32`."
        )


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


__all__ = [
    "SESSION_COOKIE_NAME",
    "WeakSessionSecret",
    "check_session_secret",
    "sign_session",
    "unsign_session",
]
