"""Thin factory + setup for the langgraph PostgresSaver.

The saver holds the fine-grained per-mission execution state, keyed on
thread_id = str(mission.id). It shares the twaky Postgres instance and
lives in its own tables (checkpoints, checkpoint_writes, checkpoint_blobs)
created by setup_checkpointer_tables() at boot.
"""

from __future__ import annotations

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg_pool import ConnectionPool

from twaky.db import get_langgraph_dsn

_pool: ConnectionPool | None = None
_saver: PostgresSaver | None = None


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=get_langgraph_dsn(),
            min_size=1, max_size=4,
            kwargs={"autocommit": True, "prepare_threshold": 0},
            open=True,
        )
    return _pool


def get_checkpointer() -> PostgresSaver:
    """Return the process-wide PostgresSaver instance."""
    global _saver
    if _saver is None:
        _saver = PostgresSaver(_get_pool())  # type: ignore[arg-type]
    return _saver


def setup_checkpointer_tables() -> None:
    """Create the checkpoint_* tables if missing. Idempotent. Call once at boot."""
    get_checkpointer().setup()


__all__ = ["get_checkpointer", "setup_checkpointer_tables"]
