"""End-to-end SSE: real Postgres NOTIFY reaches an /events client."""

from __future__ import annotations

import asyncio
import json
import os

import httpx
import psycopg
import pytest

from twaky.config import settings


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
async def test_sse_delivers_mission_changed_end_to_end(monkeypatch):
    """POST /missions in one task; consume /events in another; assert event received.

    Note: monkeypatch.setenv does NOT update the already-instantiated
    pydantic-settings singleton — patch the ``settings`` object directly
    for twaky_owner_email so ``require_owner()`` matches "alice@x". The
    API_SESSION_SECRET stays live because sign_session() reads the same
    settings object at call time. See flake #4 in
    docs/superpowers/investigations/2026-08-10-nine-flakes.md.
    """
    from twaky.api.main import app
    from twaky.api.session import SESSION_COOKIE_NAME, sign_session
    from twaky.api.sse.broker import SSEBroker

    monkeypatch.setattr(settings, "twaky_owner_email", "alice@x")

    cookie = sign_session("alice@x")

    # Start the broker and wire it to app state for the duration of the test.
    broker = SSEBroker()
    await broker.start()
    app.state.broker = broker

    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            client.cookies.set(SESSION_COOKIE_NAME, cookie)

            seen: list[dict] = []

            async def _consume():
                async with client.stream("GET", "/events") as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data:"):
                            payload = json.loads(line[len("data:") :].strip())
                            seen.append(payload)
                            if len(seen) >= 1:
                                return

            async def _declare():
                await asyncio.sleep(0.5)
                r = await client.post("/missions", json={"intent_text": "sse-e2e"})
                assert r.status_code == 201
                return r.json()["id"]

            results = await asyncio.wait_for(
                asyncio.gather(_consume(), _declare()),
                timeout=10,
            )
            mid = results[1]
    finally:
        await broker.stop()

    # Cleanup
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mission WHERE id = %s", (mid,))

    assert any(ev.get("mission_id") == mid for ev in seen)
