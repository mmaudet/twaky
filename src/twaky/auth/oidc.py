"""OIDC client-credentials + RFC 8693 token exchange.

Used by Plume to obtain a JMAP-callable bearer token that impersonates the
mission's owner. Same shape the Twake Visio ↔ Calendar path uses. If the
token payload the platform expects differs (grant_type, requested_token_type,
subject_token_type), consult meet_app / calendar_app in the deploy repo and
mirror it here.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx


@dataclass(slots=True)
class _CacheEntry:
    token: str
    expires_at: float


_CACHE: dict[str, _CacheEntry] = {}
_CACHE_REFRESH_SECONDS = 60  # refresh 60s before expiry


async def _client_credentials_token(
    *, client_id: str, client_secret: str, issuer: str, scope: str = "openid email"
) -> str:
    url = f"{issuer.rstrip('/')}/oauth2/token"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": scope,
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    return data["access_token"]


async def _exchange_token(
    *,
    subject_email: str,
    actor_token: str,
    issuer: str,
    client_id: str,
    client_secret: str,
    audience: str | None = None,
) -> str:
    """RFC 8693 token exchange to impersonate the owner user."""
    url = f"{issuer.rstrip('/')}/oauth2/token"
    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": actor_token,
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "client_id": client_id,
        "client_secret": client_secret,
        # LemonLDAP-NG uses `sub` on the mapped identity — pass the subject email
        # explicitly so the exchange resolves it. Adjust once the exact payload
        # the platform expects is confirmed against meet_app / calendar_app (spec §13).
        "subject": subject_email,
    }
    if audience:
        data["audience"] = audience
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, data=data, headers={"Accept": "application/json"})
        resp.raise_for_status()
        payload = resp.json()
    return payload["access_token"]


def get_impersonated_token(
    subject_email: str,
    *,
    issuer: str,
    client_id: str,
    client_secret: str,
    audience: str | None = None,
) -> str:
    """Return a cached impersonated token for `subject_email`, refreshing as needed."""
    now = time.time()
    entry = _CACHE.get(subject_email)
    if entry is not None and entry.expires_at - _CACHE_REFRESH_SECONDS > now:
        return entry.token

    async def _refresh() -> str:
        svc = await _client_credentials_token(
            client_id=client_id,
            client_secret=client_secret,
            issuer=issuer,
        )
        return await _exchange_token(
            subject_email=subject_email,
            actor_token=svc,
            issuer=issuer,
            client_id=client_id,
            client_secret=client_secret,
            audience=audience,
        )

    token = asyncio.run(_refresh())
    _CACHE[subject_email] = _CacheEntry(token=token, expires_at=now + 3600)
    return token


def _clear_cache_for_tests() -> None:
    _CACHE.clear()


__all__ = ["get_impersonated_token"]
