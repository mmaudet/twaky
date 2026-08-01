"""Shared FastAPI dependencies (auth, broker access)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

from twaky.config import settings

if TYPE_CHECKING:
    from twaky.api.sse.broker import SSEBroker  # type: ignore[import-untyped]


def require_owner(request: Request) -> str:
    """Ensure the request has a valid session belonging to the instance owner.

    Raises 401 if no session, 403 if the session's email is not the owner.
    Returns the owner's email on success.
    """
    session = request.session
    if not session or "email" not in session:
        raise HTTPException(status_code=401, detail="unauthenticated")
    if session["email"] != settings.twaky_owner_email:
        raise HTTPException(status_code=403, detail="not the instance owner")
    return session["email"]


def get_broker(request: Request) -> SSEBroker:
    """Return the SSE broker singleton stored on app.state.

    Raises AttributeError if the broker wasn't wired at startup.
    """
    return request.app.state.broker


__all__ = ["get_broker", "require_owner"]
