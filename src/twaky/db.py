"""Postgres connection helpers.

AGE requires `LOAD 'age'` and `SET search_path` on every session — we wrap
the psycopg pool to run these on connection checkout.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg_pool import ConnectionPool

from twaky.config import settings


def _configure_conn(conn: psycopg.Connection) -> None:
    """Install AGE on the session (safe to call repeatedly)."""
    with conn.cursor() as cur:
        cur.execute("LOAD 'age';")
        cur.execute('SET search_path = ag_catalog, "$user", public;')
    conn.commit()


_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=settings.pg_dsn,
            min_size=1,
            max_size=4,
            configure=_configure_conn,
            open=True,
        )
    return _pool


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with get_pool().connection() as conn:
        yield conn
