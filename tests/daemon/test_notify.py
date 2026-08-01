"""LISTEN helper — integration test using a real Postgres."""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import psycopg
import pytest

from twaky.config import settings
from twaky.daemon.notify import listen


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable")


@pytest.mark.asyncio
async def test_listen_receives_notify():
    ch = f"twaky_test_{uuid4().hex[:8]}"
    received = []

    async def _consume():
        async for channel, payload in listen([ch], _dsn(), poll_interval_s=0.1):
            received.append((channel, payload))
            if len(received) >= 1:
                break

    async def _notify():
        await asyncio.sleep(0.5)
        with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(f"NOTIFY {ch}, 'hello'")

    await asyncio.wait_for(asyncio.gather(_consume(), _notify()), timeout=5)
    assert received == [(ch, "hello")]
