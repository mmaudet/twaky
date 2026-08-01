"""Confirm the `mission` table + expected indexes exist on the running DB."""

from __future__ import annotations

import os

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


pytestmark = pytest.mark.skipif(
    not _reachable(), reason="twaky-pg not reachable (host must be inside twake-network)"
)


def test_mission_table_exists():
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='mission' ORDER BY ordinal_position"
        )
        cols = {r[0] for r in cur.fetchall()}
    expected = {
        "id", "owner_email", "declared_by", "declared_at", "intent_text",
        "plan", "state", "state_reason", "due_at", "artifacts",
        "langfuse_session_id", "created_at", "updated_at",
    }
    assert expected.issubset(cols), f"missing columns: {expected - cols}"


def test_indexes_exist():
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename='mission'"
        )
        idx = {r[0] for r in cur.fetchall()}
    assert "mission_live_idx" in idx
    assert "mission_owner_state_idx" in idx
