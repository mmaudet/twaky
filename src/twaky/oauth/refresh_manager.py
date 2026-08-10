"""In-process access-token cache + single-flight refresh loop for JMAP OAuth.

``RefreshManager`` is instantiated once per sentinel (via ``get_manager()``)
so the in-process cache is shared across all callers within one worker process.

Threading contract
------------------
``sync_get_access_token()`` and ``sync_force_refresh()`` use ``asyncio.run()``
internally, which creates a fresh event loop.  They MUST be called from a
synchronous thread (not from inside a running event loop).  T8 executes
``sentinel.process()`` via ``asyncio.to_thread()``, so calling the sync
wrappers from within ``process()`` is always safe.  Calling them from inside
a running event loop will raise ``RuntimeError``.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

import httpx

from twaky.config import settings
from twaky.crypto.secrets import decrypt, encrypt
from twaky.oauth import repository

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CACHE_TTL_S = 30.0
_EXPIRY_SKEW_S = 60.0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RefreshFailed(Exception):
    """Raised when the token endpoint refuses to refresh.

    The string arg is the OAuth error code (e.g. ``invalid_grant``,
    ``invalid_client``, ``network``, ``no_credential``, ``http_500``).
    """


# ---------------------------------------------------------------------------
# RefreshManager
# ---------------------------------------------------------------------------


class RefreshManager:
    """Per-sentinel access-token cache with single-flight refresh.

    Instantiate via ``get_manager(sentinel_name)`` to get a process-level
    singleton — never construct directly in production code.
    """

    def __init__(self, sentinel_name: str) -> None:
        self.sentinel_name = sentinel_name
        self._cached_token: str | None = None
        self._cached_at: float = 0.0
        self._lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def get_access_token(self) -> str:
        """Return a valid access token, refreshing if necessary.

        Decision order:
        1. In-process cache hit (< ``_CACHE_TTL_S`` seconds old) → return cached.
        2. Re-read DB; if ``access_token_expires_at > now() + _EXPIRY_SKEW_S``
           → decrypt, update cache, return.
        3. Otherwise → call ``_refresh()`` and return new token.
        """
        # 1. Fast path: in-process cache
        if (
            self._cached_token is not None
            and time.monotonic() - self._cached_at < _CACHE_TTL_S
        ):
            return self._cached_token

        # 2. Re-read DB; check server-side expiry
        cred = await asyncio.to_thread(repository.get, self.sentinel_name)
        if (
            cred is not None
            and cred.access_token_enc is not None
            and cred.access_token_expires_at is not None
            and cred.access_token_expires_at
            > datetime.now(UTC) + timedelta(seconds=_EXPIRY_SKEW_S)
        ):
            token = decrypt(cred.access_token_enc)
            self._cached_token = token
            self._cached_at = time.monotonic()
            return token

        # 3. Need a refresh
        return await self._refresh()

    async def force_refresh(self) -> str:
        """Bypass cache and expiry check; always call the token endpoint."""
        return await self._refresh()

    def invalidate(self) -> None:
        """Clear in-process cache.  Called by NOTIFY listener (T6) when another
        process has already refreshed the token and written it to the DB.
        """
        self._cached_token = None
        self._cached_at = 0.0

    # ------------------------------------------------------------------
    # Synchronous wrappers (for the sync JMAP adapter, called from a thread)
    # ------------------------------------------------------------------

    def sync_get_access_token(self) -> str:
        """Synchronous wrapper around ``get_access_token()``.

        Uses ``asyncio.run()`` which creates a fresh event loop.
        MUST be called from a synchronous thread — see module docstring.
        """
        return asyncio.run(self.get_access_token())

    def sync_force_refresh(self) -> str:
        """Synchronous wrapper around ``force_refresh()``.

        Uses ``asyncio.run()`` which creates a fresh event loop.
        MUST be called from a synchronous thread — see module docstring.
        """
        return asyncio.run(self.force_refresh())

    # ------------------------------------------------------------------
    # Internal: single-flight refresh
    # ------------------------------------------------------------------

    async def _refresh(self) -> str:
        """POST to the token endpoint and update the DB.

        Serialised by ``self._lock`` so that multiple concurrent callers
        only issue a single HTTP request (single-flight pattern).

        Double-checked locking: after acquiring the lock, if the cache is
        now warm (a sibling coroutine refreshed while we waited), return the
        cached token immediately without hitting the network again.
        """
        async with self._lock:
            # Double-check: another waiter may have refreshed while we queued.
            if (
                self._cached_token is not None
                and time.monotonic() - self._cached_at < _CACHE_TTL_S
            ):
                return self._cached_token

            cred = await asyncio.to_thread(repository.get, self.sentinel_name)
            if cred is None or cred.refresh_token_enc is None:
                raise RefreshFailed("no_credential")

            refresh_token = decrypt(cred.refresh_token_enc)

            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    r = await client.post(
                        cred.token_endpoint,
                        data={
                            "grant_type": "refresh_token",
                            "refresh_token": refresh_token,
                            "client_id": cred.client_id,
                            "client_secret": settings.jmap_oauth_client_secret,
                        },
                    )
                except httpx.HTTPError as e:
                    await asyncio.to_thread(
                        repository.set_error,
                        self.sentinel_name,
                        f"network:{e.__class__.__name__}",
                    )
                    raise RefreshFailed("network") from e

            if r.status_code >= 400:
                body = (
                    r.json()
                    if r.headers.get("content-type", "").startswith("application/json")
                    else {}
                )
                err = body.get("error", f"http_{r.status_code}")
                await asyncio.to_thread(repository.set_error, self.sentinel_name, err)
                raise RefreshFailed(err)

            data = r.json()
            new_access: str = data["access_token"]
            new_refresh: str | None = data.get("refresh_token")  # None if not rotated
            expires_in: int = int(data.get("expires_in", 3600))
            expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

            await asyncio.to_thread(
                repository.update_after_refresh,
                sentinel_name=self.sentinel_name,
                access_token_enc=encrypt(new_access),
                access_token_expires_at=expires_at,
                refresh_token_enc=encrypt(new_refresh) if new_refresh else None,
            )

            self._cached_token = new_access
            self._cached_at = time.monotonic()
            return new_access


# ---------------------------------------------------------------------------
# Module-level singleton cache
# ---------------------------------------------------------------------------

_MANAGERS: dict[str, RefreshManager] = {}


def get_manager(sentinel_name: str) -> RefreshManager:
    """Return the process-level ``RefreshManager`` for *sentinel_name*.

    Creates a new instance on first call; returns the cached instance on
    subsequent calls.  Not thread-safe for initial creation, but ``process()``
    is always called from the same asyncio event loop so there is no race.
    """
    if sentinel_name not in _MANAGERS:
        _MANAGERS[sentinel_name] = RefreshManager(sentinel_name)
    return _MANAGERS[sentinel_name]


__all__ = [
    "_CACHE_TTL_S",
    "_EXPIRY_SKEW_S",
    "RefreshFailed",
    "RefreshManager",
    "get_manager",
]
