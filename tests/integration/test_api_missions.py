"""Full-stack integration: declare → list → detail → cancel with real PG."""

from __future__ import annotations

import os

import httpx
import psycopg
import pytest

from twaky.api.session import SESSION_COOKIE_NAME, sign_session
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
async def test_declare_list_detail_cancel_cycle(monkeypatch):
    # monkeypatch.setenv doesn't touch the already-instantiated pydantic-
    # settings singleton — patch the object directly so require_owner()'s
    # session["email"] check compares against "alice@x" (not the real
    # owner email). api_session_secret stays live because sign_session()
    # and the SessionMiddleware both read the same settings object.
    monkeypatch.setattr(settings, "twaky_owner_email", "alice@x")

    from twaky.api.main import app

    cookie = sign_session("alice@x")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        client.cookies.set(SESSION_COOKIE_NAME, cookie)

        # 1. Declare
        r = await client.post("/missions", json={"intent_text": "int-test"})
        assert r.status_code == 201
        mid = r.json()["id"]

        # 2. List — find it
        r = await client.get("/missions")
        assert r.status_code == 200
        assert any(row["id"] == mid for row in r.json())

        # 3. Detail
        r = await client.get(f"/missions/{mid}")
        assert r.status_code == 200
        assert r.json()["intent_text"] == "int-test"

        # 4. Cancel
        r = await client.post(f"/missions/{mid}/cancel", json={"reason": "test"})
        assert r.status_code == 200
        assert r.json()["state"] == "cancelled"

    # Cleanup
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mission WHERE id = %s", (mid,))
