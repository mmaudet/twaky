"""OIDC login / callback / logout."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from twaky.api.oidc import oauth_client
from twaky.config import settings

router = APIRouter(prefix="/oauth", tags=["oauth"])

log = structlog.get_logger("twaky.api.oauth")


def _safe_return_to(value: str) -> str:
    """Return *value* only when it is a safe relative URL, else ``"/"``.

    Guards against open-redirect attacks delivered via the ``return_to``
    query parameter.  A value is considered safe when:
    - it starts with ``/``
    - it does *not* start with ``//`` (protocol-relative, treated as absolute by
      browsers)
    - it does *not* contain a backslash (occasionally exploited)
    """
    if not value.startswith("/"):
        return "/"
    if value.startswith("//"):
        return "/"
    if "\\" in value:
        return "/"
    return value


@router.get("/login")
async def login(request: Request, return_to: str = "/") -> RedirectResponse:
    """Kick off the OIDC Authorization Code + PKCE flow."""
    request.session["oauth_return_to"] = _safe_return_to(return_to)
    callback_url = f"{settings.api_base_url.rstrip('/')}/oauth/callback"
    client = oauth_client().twaky_api  # type: ignore[attr-defined]
    return await client.authorize_redirect(request, callback_url)


@router.get("/callback")
async def callback(request: Request) -> RedirectResponse:
    """Handle the OIDC provider's callback, set session, redirect."""
    client = oauth_client().twaky_api  # type: ignore[attr-defined]
    try:
        token = await client.authorize_access_token(request)
    except Exception as exc:
        log.exception("oidc_callback_failed", exc_info=exc)
        raise HTTPException(status_code=400, detail="oidc_callback_failed") from exc

    userinfo = token.get("userinfo") or {}
    email = userinfo.get("email")
    sub = userinfo.get("sub", "")
    if not email:
        raise HTTPException(status_code=400, detail="id_token missing email claim")

    if email != settings.twaky_owner_email:
        raise HTTPException(status_code=403, detail="not the instance owner")

    request.session["email"] = email
    request.session["sub"] = sub

    return_to = _safe_return_to(request.session.pop("oauth_return_to", "/"))
    return RedirectResponse(url=return_to, status_code=302)


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    """Purge session + redirect to LemonLDAP end-session endpoint."""
    request.session.clear()
    end_session = (
        settings.api_oidc_issuer.rstrip("/")
        + "/oauth2/logout?post_logout_redirect_uri="
        + settings.api_base_url.rstrip("/")
        + "/"
    )
    return RedirectResponse(url=end_session, status_code=302)


__all__ = ["router"]
