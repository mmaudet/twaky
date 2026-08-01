"""LangGraph PostgresSaver — put/get/delete roundtrip on the twaky DB."""

from __future__ import annotations

import os
from uuid import uuid4

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


def test_setup_creates_checkpoint_tables():
    from twaky.missions.checkpointer import setup_checkpointer_tables

    setup_checkpointer_tables()
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name LIKE 'checkpoint%'"
        )
        tables = {r[0] for r in cur.fetchall()}
    # PostgresSaver 2.x creates at minimum: checkpoints, checkpoint_writes, checkpoint_blobs
    assert "checkpoints" in tables
    assert "checkpoint_writes" in tables


def test_put_get_roundtrip():
    from twaky.missions.checkpointer import get_checkpointer

    saver = get_checkpointer()
    thread_id = str(uuid4())
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    checkpoint = {
        "v": 4,
        "id": thread_id,
        "channel_values": {"n": 42},
        "channel_versions": {"n": 1},
        "versions_seen": {},
    }
    metadata = {"source": "test", "step": 0, "writes": {}, "parents": {}}
    saved_cfg = saver.put(config, checkpoint, metadata, {})
    got = saver.get_tuple(saved_cfg)
    assert got is not None
    assert got.checkpoint["channel_values"] == {"n": 42}
