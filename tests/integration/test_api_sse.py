"""End-to-end SSE: real Postgres NOTIFY reaches an /events client."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import threading

import httpx
import psycopg
import pytest
import uvicorn

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


def _free_port() -> int:
    """Return an ephemeral port that is free on localhost."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_sse_delivers_mission_changed_end_to_end(monkeypatch):
    """POST /missions in one task; consume /events in another; assert event received.

    Note: monkeypatch.setenv does NOT update the already-instantiated
    pydantic-settings singleton — patch the ``settings`` object directly
    for twaky_owner_email so ``require_owner()`` matches "alice@x". The
    API_SESSION_SECRET stays live because sign_session() reads the same
    settings object at call time. See flake #4 in
    docs/superpowers/investigations/2026-08-10-nine-flakes.md.

    Note: httpx.ASGITransport buffers the entire response body before
    returning it to the caller — it is incompatible with infinite SSE
    streams.  We therefore spin up a real uvicorn server in a background
    thread so that httpx can connect over a real TCP socket and read SSE
    chunks incrementally.
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

    # Spin up a real uvicorn server on a free port so SSE chunks arrive
    # incrementally (httpx.ASGITransport buffers the full response body).
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            lifespan="off",  # broker lifecycle managed by the test
        )
    )
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    # Wait until the server is ready.
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            async with httpx.AsyncClient() as probe:
                await probe.get(f"{base_url}/health", timeout=0.2)
            break
        except Exception:  # noqa: BLE001
            await asyncio.sleep(0.1)

    try:
        seen: list[dict] = []

        async def _consume():
            async with httpx.AsyncClient() as client:
                client.cookies.set(SESSION_COOKIE_NAME, cookie)
                async with client.stream(
                    "GET", f"{base_url}/events", timeout=None
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data:"):
                            payload = json.loads(line[len("data:") :].strip())
                            seen.append(payload)
                            if len(seen) >= 1:
                                return

        async def _declare():
            await asyncio.sleep(0.5)
            async with httpx.AsyncClient() as client:
                client.cookies.set(SESSION_COOKIE_NAME, cookie)
                r = await client.post(
                    f"{base_url}/missions",
                    json={"intent_text": "sse-e2e"},
                )
                assert r.status_code == 201
                return r.json()["id"]

        results = await asyncio.wait_for(
            asyncio.gather(_consume(), _declare()),
            timeout=10,
        )
        mid = results[1]
    finally:
        server.should_exit = True
        server_thread.join(timeout=5)
        await broker.stop()

    # Cleanup
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mission WHERE id = %s", (mid,))

    assert any(ev.get("mission_id") == mid for ev in seen)
