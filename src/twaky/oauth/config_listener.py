"""Async LISTEN oauth_credential_changed → RefreshManager cache invalidation.

The trigger `oauth_credential_notify` (see sql/010) publishes the sentinel name
as payload. On UPDATE / INSERT / DELETE this listener invalidates the
in-process cache of RefreshManager for that name, so the next
get_access_token() re-reads the DB.

Cancellation via `task.cancel()` is treated as a transient error and
triggers reconnect; set `stop_event` for a clean shutdown.

Payload semantics (same as T3 SP6 sentinel config listener):
  - ``""`` or ``"ALL"`` → invalidate ALL managers
  - anything else       → ``get_manager(payload.strip()).invalidate()``

Reconnect policy
----------------
On ``psycopg.OperationalError`` **or** ``asyncio.CancelledError`` (not caused
by the stop_event), the loop backs off exponentially starting at 1.0 s,
doubling each retry up to a cap of 30.0 s.  The backoff sleep uses
``asyncio.wait_for(stop_event.wait(), …)`` so the loop exits quickly if the
stop fires during a wait.
"""

from __future__ import annotations

import asyncio
import logging

import psycopg
from psycopg import sql

from twaky.oauth.refresh_manager import _MANAGERS, get_manager

log = logging.getLogger("twaky.oauth.config_listener")

_BACKOFF_START = 1.0
_BACKOFF_CAP = 30.0


async def run_oauth_config_listener(
    dsn: str,
    *,
    stop_event: asyncio.Event,
    channel: str = "oauth_credential_changed",
) -> None:
    """Long-running coroutine: LISTEN *channel*, invalidate RefreshManager on NOTIFY.

    Returns when *stop_event* is set.
    Reconnects with exponential backoff on transient ``OperationalError``.

    Note: cancellation via ``task.cancel()`` is treated as a transient error
    and triggers reconnect; set ``stop_event`` for a clean shutdown.
    """
    backoff = _BACKOFF_START

    while not stop_event.is_set():
        try:
            async with await psycopg.AsyncConnection.connect(
                dsn, autocommit=True
            ) as conn:
                # Properly quote the channel name as a SQL identifier.
                quoted = sql.Identifier(channel).as_string(conn)
                await conn.execute(f"LISTEN {quoted}")
                log.info("oauth config listener LISTEN %s", channel)

                # Reset backoff on successful connection.
                backoff = _BACKOFF_START

                async for notify in conn.notifies():
                    if stop_event.is_set():
                        return

                    payload: str = notify.payload or ""
                    log.debug(
                        "oauth_credential_changed notify: channel=%s payload=%r",
                        notify.channel,
                        payload,
                    )

                    if payload == "" or payload == "ALL":
                        for m in _MANAGERS.values():
                            m.invalidate()
                    else:
                        get_manager(payload.strip()).invalidate()

        except psycopg.OperationalError as exc:
            if stop_event.is_set():
                return
            log.warning(
                "oauth config listener lost connection (%s); retrying in %.1f s",
                exc,
                backoff,
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                # stop_event fired during backoff — exit cleanly.
                return
            except TimeoutError:
                pass
            backoff = min(backoff * 2, _BACKOFF_CAP)

        except asyncio.CancelledError:
            if stop_event.is_set():
                return
            log.warning("oauth config listener cancelled; retrying in %.1f s", backoff)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                return
            except TimeoutError:
                pass
            backoff = min(backoff * 2, _BACKOFF_CAP)


__all__ = ["run_oauth_config_listener"]
