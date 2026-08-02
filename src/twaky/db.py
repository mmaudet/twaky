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
            # Idle connections held forever get silently killed by Postgres
            # (or by NAT in Docker networks) — the next check-out then raises
            # psycopg.OperationalError "server closed the connection
            # unexpectedly". Cycle connections proactively:
            #  - max_idle=300s: recycle after 5 min of inactivity (below any
            #    reasonable server/network idle timeout).
            #  - max_lifetime=3600s: hard-cap connection lifetime at 1h to
            #    catch slower leaks (server-side timeouts, DNS rebalancing).
            max_idle=300.0,
            max_lifetime=3600.0,
            open=True,
        )
    return _pool


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with get_pool().connection() as conn:
        yield conn


def get_langgraph_dsn() -> str:
    """DSN used by the langgraph PostgresSaver. Same DB as the twaky graph."""
    return settings.pg_dsn
