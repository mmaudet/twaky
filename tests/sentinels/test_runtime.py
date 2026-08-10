"""Integration tests for twaky.sentinels.runtime._process_with_bookkeeping.

Requires a live twaky-pg instance. Set TWAKY_PG_HOST=172.27.0.33 before
running.

Fake sentinels
--------------
_CountingSentinel — records calls; returns Outcome.PROCESSED.
_SlowSentinel     — sleeps 5 s; used with sentinel_timeout_s=2.
_RaisingSentinel  — raises RuntimeError("kaboom").

Only ``_process_with_bookkeeping`` is tested here — the full ``run()``
method (which needs real RabbitMQ + sentinel packages) is covered in T29.
"""

from __future__ import annotations

import os
import time
from typing import Literal

import psycopg
import pytest

from twaky.config import Settings
from twaky.sentinels import repository as repo
from twaky.sentinels.base import Context, Event, Outcome, Sentinel
from twaky.sentinels.registry import get_registry
from twaky.sentinels.runtime import _process_with_bookkeeping

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or _base_settings().pg_dsn


def _base_settings():  # type: ignore[return]
    """Return the module-level settings singleton (picks up .env file)."""
    from twaky.config import settings as _s

    return _s


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable"),
    pytest.mark.asyncio,
]


# ---------------------------------------------------------------------------
# Settings factory
# ---------------------------------------------------------------------------


def _test_settings() -> Settings:
    """Build a Settings instance for runtime tests.

    Inherits all values from the .env file (via the module singleton) so
    the real PG password is used, then overrides sentinel concurrency knobs
    to values suitable for integration tests (e.g. short timeout_s=2 so
    the _SlowSentinel test finishes quickly).
    """
    base = _base_settings()
    # Build from the same .env values but override sentinel knobs.
    return Settings(  # type: ignore[call-arg]
        twaky_owner_email=base.twaky_owner_email or "test@example.com",
        twaky_pg_host=base.twaky_pg_host,
        twaky_pg_port=base.twaky_pg_port,
        twaky_pg_db=base.twaky_pg_db,
        twaky_pg_user=base.twaky_pg_user,
        twaky_pg_password=base.twaky_pg_password,
        rabbitmq_url=base.rabbitmq_url,
        sentinel_timeout_s=2,
        sentinel_max_concurrent_events=4,
        sentinel_run_retention_days=30,
        jmap_session_url=base.jmap_session_url or "",
        jmap_bearer_token=base.jmap_bearer_token or "",
        jmap_account_email=base.jmap_account_email or "",
        jmap_poll_interval_s=base.jmap_poll_interval_s,
    )


# ---------------------------------------------------------------------------
# Fake Sentinel subclasses
# ---------------------------------------------------------------------------


class _CountingSentinel(Sentinel):
    """Records message_ids of processed events; returns PROCESSED."""

    name: str = "mail"  # type: ignore[assignment]
    version: str = "0.0.1"  # type: ignore[assignment]
    event_source_kind: Literal["rabbitmq", "jmap_poll"] = "jmap_poll"  # type: ignore[assignment]

    def __init__(self) -> None:
        self.calls: list[str] = []

    def process(self, event: Event, ctx: Context) -> Outcome:
        self.calls.append(event["message_id"])
        return Outcome.PROCESSED


class _SlowSentinel(Sentinel):
    """Sleeps 5 s — used with sentinel_timeout_s=2 to trigger TimeoutError."""

    name: str = "mail"  # type: ignore[assignment]
    version: str = "0.0.1"  # type: ignore[assignment]
    event_source_kind: Literal["rabbitmq", "jmap_poll"] = "jmap_poll"  # type: ignore[assignment]

    def process(self, event: Event, ctx: Context) -> Outcome:
        time.sleep(5)
        return Outcome.PROCESSED  # never reached


class _RaisingSentinel(Sentinel):
    """Raises RuntimeError("kaboom") — maps to Outcome.ERROR."""

    name: str = "mail"  # type: ignore[assignment]
    version: str = "0.0.1"  # type: ignore[assignment]
    event_source_kind: Literal["rabbitmq", "jmap_poll"] = "jmap_poll"  # type: ignore[assignment]

    def process(self, event: Event, ctx: Context) -> Outcome:
        raise RuntimeError("kaboom")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mk_ev(mid: str) -> Event:
    """Build a minimal test Event."""
    return {
        "source_kind": "rabbitmq",
        "source_ref": "test:x",
        "message_id": mid,
        "payload": {},
    }


def _mk_ctx(inst: Sentinel) -> Context:
    """Build a minimal Context using the seed mail row from the registry."""
    row = get_registry().get(inst.name)  # type: ignore[arg-type]
    return Context(
        db_pool=None,
        mission_emitter=None,  # type: ignore[arg-type]
        delegation=None,  # type: ignore[arg-type]
        sentinel_row=row,
        logger=__import__("logging").getLogger("test.sentinel"),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_runs():
    """Wipe sentinel_run before and after each test."""
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM sentinel_run")
    yield
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM sentinel_run")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_dispatch_records_processed_run():
    """A single event produces one sentinel_run row with outcome=processed."""
    settings = _test_settings()
    inst = _CountingSentinel()
    ctx = _mk_ctx(inst)
    event = _mk_ev("m1")

    outcome = await _process_with_bookkeeping(inst, ctx, settings, event)

    assert outcome is Outcome.PROCESSED

    # Verify DB row
    runs = repo.list_runs("mail", limit=100)
    assert len(runs) == 1
    run = runs[0]
    assert run.outcome == "processed"
    assert run.event_ref == "test:x:m1"
    assert run.completed_at is not None
    assert run.duration_ms is not None
    assert run.duration_ms >= 0

    # Verify process() was called
    assert inst.calls == ["m1"]


async def test_idempotency_skips_duplicate_event():
    """Sending the same event twice produces 2 rows (processed + ignored),
    but process() is called only once."""
    settings = _test_settings()
    inst = _CountingSentinel()
    ctx = _mk_ctx(inst)
    event = _mk_ev("dup")

    outcome1 = await _process_with_bookkeeping(inst, ctx, settings, event)
    outcome2 = await _process_with_bookkeeping(inst, ctx, settings, event)

    assert outcome1 is Outcome.PROCESSED
    assert outcome2 is Outcome.IGNORED

    runs = repo.list_runs("mail", limit=100)
    assert len(runs) == 2

    outcomes = {r.outcome for r in runs}
    assert outcomes == {"processed", "ignored"}

    # process() called only once
    assert inst.calls == ["dup"]

    # The ignored row should have error_repr="already_processed"
    ignored_run = next(r for r in runs if r.outcome == "ignored")
    assert ignored_run.error_repr == "already_processed"
    assert ignored_run.duration_ms == 0


async def test_timeout_marks_error():
    """SlowSentinel (5 s sleep) with timeout=2 s → outcome=error, TimeoutError in error_repr."""
    settings = _test_settings()
    inst = _SlowSentinel()
    ctx = _mk_ctx(inst)
    event = _mk_ev("slow1")

    outcome = await _process_with_bookkeeping(inst, ctx, settings, event)

    assert outcome is Outcome.ERROR

    runs = repo.list_runs("mail", limit=100)
    assert len(runs) == 1
    run = runs[0]
    assert run.outcome == "error"
    assert run.error_repr is not None
    assert "TimeoutError" in run.error_repr


async def test_exception_marks_error_with_traceback():
    """RaisingSentinel raises RuntimeError("kaboom") → outcome=error, traceback in error_repr."""
    settings = _test_settings()
    inst = _RaisingSentinel()
    ctx = _mk_ctx(inst)
    event = _mk_ev("raise1")

    outcome = await _process_with_bookkeeping(inst, ctx, settings, event)

    assert outcome is Outcome.ERROR

    runs = repo.list_runs("mail", limit=100)
    assert len(runs) == 1
    run = runs[0]
    assert run.outcome == "error"
    assert run.error_repr is not None
    assert "kaboom" in run.error_repr
