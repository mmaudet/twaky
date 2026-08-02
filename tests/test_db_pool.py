"""Pool config assertions — guards against silent recycle-off regressions.

Idle Postgres connections held forever get killed by the server or by
Docker NAT, and the next check-out raises OperationalError. The pool must
recycle proactively.
"""

from __future__ import annotations

import inspect

from psycopg_pool import ConnectionPool

from twaky import db


def test_pool_recycles_idle_connections():
    """max_idle must be set well below the typical server/NAT idle timeout.

    psycopg-pool's default is 600s. That's the ceiling — we want a tighter
    value (≤300s) to survive Docker NAT conntrack timeouts observed at
    ~5-10min in some environments. Bumping this assertion is a conscious
    trade-off with the connection-reset cost.
    """
    pool = db.get_pool()
    assert pool.max_idle <= 300.0, (
        f"pool.max_idle={pool.max_idle} — must be explicitly configured "
        "≤300s to survive Docker NAT idle timeout"
    )


def test_pool_caps_connection_lifetime():
    """max_lifetime must be set as a defence-in-depth against slow leaks."""
    pool = db.get_pool()
    assert 0 < pool.max_lifetime <= 3600.0, (
        f"pool.max_lifetime={pool.max_lifetime} — connections should be "
        "recycled at least hourly"
    )


def test_get_pool_returns_singleton_connection_pool_instance():
    """get_pool must return the same ConnectionPool on repeated calls (module-level cache)."""
    a = db.get_pool()
    b = db.get_pool()
    assert a is b
    assert isinstance(a, ConnectionPool)


def test_get_pool_uses_configure_hook_that_loads_age():
    """The AGE extension must be loaded on every checkout — the configure hook
    is the seam that guarantees it."""
    src = inspect.getsource(db._configure_conn)
    assert "LOAD 'age'" in src
    assert "search_path" in src
