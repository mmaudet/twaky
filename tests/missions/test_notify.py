"""Regression: engine._notify must actually deliver NOTIFY to LISTEN sessions.

Pre-existing silent failure caught 2026-08-03:
    engine._notify used ``cur.execute(f"NOTIFY {channel}, %s", (payload,))``
    which psycopg3 rewrites to ``NOTIFY {channel}, $1`` — a Postgres
    syntax error at parse time. The exception was silently swallowed by
    ``except Exception: pass``, so every NOTIFY emission from the engine
    was a no-op. Mission scheduling stayed working only because the atlas
    daemon's 5s periodic sweep polled for DECLARED missions independently;
    mission_resumed (no sweep fallback) never fired, and the resume flow
    hung until the daemon's 300s wait_for timeout.

Fix: use ``SELECT pg_notify(%s, %s)`` (function form supports params).

This test opens a real LISTEN session, calls engine._notify, and verifies
the notification arrives — pins the delivery contract at the wire level.
"""

from __future__ import annotations

import os
import select
import time

import psycopg
import pytest

from twaky.config import settings
from twaky.missions import engine


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable")


def _drain(listener_conn: psycopg.Connection, timeout_s: float) -> list[str]:
    """Return payloads received on the listener's active LISTEN within timeout."""
    payloads: list[str] = []
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        r, _, _ = select.select([listener_conn], [], [], remaining)
        if not r:
            break
        for notify in listener_conn.notifies(timeout=0):
            payloads.append(notify.payload)
        if payloads:
            break
    return payloads


def test_notify_actually_delivers_payload():
    """The bug: NOTIFY was silently no-op because psycopg param binding
    generated `NOTIFY channel, $1` — a syntax error."""
    channel = "twaky_test_engine_notify"

    listener = psycopg.connect(_dsn(), autocommit=True)
    try:
        with listener.cursor() as cur:
            cur.execute(f"LISTEN {channel}")

        # Emit via the exact engine helper — this is what production uses.
        engine._notify(channel, "hello-payload")

        payloads = _drain(listener, timeout_s=2.0)
        assert payloads == ["hello-payload"], (
            f"expected exactly one delivery of 'hello-payload', got {payloads!r}. "
            "Regression: NOTIFY from engine._notify is not reaching LISTEN sessions."
        )
    finally:
        listener.close()


def test_notify_accepts_uuid_payload():
    """The mission_declared / mission_resumed callers pass mission UUIDs
    as payloads. Verify the wire format handles them cleanly."""
    channel = "twaky_test_engine_notify_uuid"
    from uuid import uuid4

    payload = str(uuid4())

    listener = psycopg.connect(_dsn(), autocommit=True)
    try:
        with listener.cursor() as cur:
            cur.execute(f"LISTEN {channel}")

        engine._notify(channel, payload)

        payloads = _drain(listener, timeout_s=2.0)
        assert payloads == [payload]
    finally:
        listener.close()


def test_notify_accepts_json_payload():
    """_notify_state_change encodes mission state as a JSON string;
    ensure JSON payload survives round-trip."""
    channel = "twaky_test_engine_notify_json"
    import json

    payload = json.dumps({"mission_id": "abc-123", "state": "running"})

    listener = psycopg.connect(_dsn(), autocommit=True)
    try:
        with listener.cursor() as cur:
            cur.execute(f"LISTEN {channel}")

        engine._notify(channel, payload)

        payloads = _drain(listener, timeout_s=2.0)
        assert payloads == [payload]
        # Round-trip parses back to the original dict.
        assert json.loads(payloads[0]) == {"mission_id": "abc-123", "state": "running"}
    finally:
        listener.close()
