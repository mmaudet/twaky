"""Integration test for twaky.oauth.config_listener.

Requires a live twaky-pg instance with the ``oauth_credential`` table and
the ``notify_oauth_credential_changed`` trigger.

Set ``TWAKY_TEST_DSN`` env var to override the default DSN from settings,
or set ``TWAKY_PG_HOST`` to point at the dev postgres.

Pattern mirrors tests/sentinels/test_config_listener.py (marker + skipif).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
import pytest_asyncio  # noqa: F401 — ensures asyncio mode is available

from twaky.config import settings
from twaky.oauth import repository
from twaky.oauth.config_listener import run_oauth_config_listener
from twaky.oauth.refresh_manager import _MANAGERS, get_manager

# ---------------------------------------------------------------------------
# DSN helpers (mirrors test_repository.py)
# ---------------------------------------------------------------------------


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable"),
    pytest.mark.asyncio,
]


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_invalidates_cache() -> None:
    """Updating an oauth_credential row must evict its RefreshManager cache via NOTIFY.

    Flow
    ----
    1. Ensure _MANAGERS is clean; prime get_manager("mail") and seed a cached token.
    2. Upsert an oauth_credential row for "mail" (needed for update_after_refresh).
    3. Start the listener task; give it 0.3 s to register LISTEN.
    4. Trigger NOTIFY via repository.update_after_refresh (fires the trigger).
    5. Poll up to 2 s for cache eviction (50 ms ticks).
    6. Assert _MANAGERS["mail"]._cached_token is None.
    7. Cleanup: stop listener, wipe oauth_credential row.
    """
    stop = asyncio.Event()
    listener_task: asyncio.Task | None = None

    # Step 1: ensure _MANAGERS starts clean for "mail".
    _MANAGERS.pop("mail", None)
    m = get_manager("mail")
    m._cached_token = "cached"  # type: ignore[assignment]
    m._cached_at = time.monotonic()
    assert _MANAGERS["mail"]._cached_token == "cached"

    # Step 2: upsert an oauth_credential row for "mail" so the trigger fires on UPDATE.
    repository.upsert(
        sentinel_name="mail",
        provider="google",
        client_id="test-client-id",
        token_endpoint="https://oauth2.googleapis.com/token",
        session_url="https://api.fastmail.com/jmap/session",
        scope="https://mail.google.com/",
        refresh_token_enc="dummy-refresh",
        access_token_enc="dummy-access",
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        account_email="test@example.com",
        account_name="Test User",
    )

    try:
        # Step 3: start the listener and let it register LISTEN.
        listener_task = asyncio.create_task(
            run_oauth_config_listener(_dsn(), stop_event=stop)
        )
        await asyncio.sleep(0.3)

        # Step 4: trigger the NOTIFY by updating the row.
        repository.update_after_refresh(
            sentinel_name="mail",
            access_token_enc="dummy-new",
            access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

        # Step 5: poll for cache eviction.
        evicted = False
        for _ in range(40):  # 40 × 50 ms = 2 s
            await asyncio.sleep(0.05)
            if _MANAGERS["mail"]._cached_token is None:
                evicted = True
                break

        # Step 6: assert.
        assert evicted, "'mail' RefreshManager cache was not invalidated within 2 s"

    finally:
        # Cleanup: stop the listener task.
        stop.set()
        if listener_task is not None:
            listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await listener_task

        # Wipe the oauth_credential row.
        with contextlib.suppress(Exception):
            repository.delete("mail")

        # Clean up _MANAGERS so other tests aren't affected.
        _MANAGERS.pop("mail", None)
