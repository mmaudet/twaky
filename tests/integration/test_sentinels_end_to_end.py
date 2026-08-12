"""Sentinel end-to-end integration tests.

Verifies the RabbitMQ path:
  publish JSON to a temp fanout exchange
  → RabbitMQEventSource delivers it
  → _process_with_bookkeeping runs a no-LLM Sentinel subclass
  → sentinel_run row inserted with correct event_ref + outcome

Skipped when either Postgres or RabbitMQ is unreachable (CI-safe default).

Run with real backends:
    TWAKY_PG_HOST=172.27.0.33 \\
    RABBITMQ_URL='amqp://guest:guest@172.27.0.8:5672/%2F' \\
    uv run pytest tests/integration/test_sentinels_end_to_end.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime

import psycopg
import pytest

from twaky.config import settings

# ---------------------------------------------------------------------------
# Reachability guards
# ---------------------------------------------------------------------------

_PG_HOST = os.environ.get("TWAKY_PG_HOST", settings.twaky_pg_host)
_RABBIT_URL = os.environ.get("RABBITMQ_URL", settings.rabbitmq_url)


def _pg_dsn() -> str:
    return (
        f"host={_PG_HOST} port={settings.twaky_pg_port} "
        f"dbname={settings.twaky_pg_db} "
        f"user={settings.twaky_pg_user} password={settings.twaky_pg_password}"
    )


def _pg_reachable() -> bool:
    try:
        with psycopg.connect(_pg_dsn(), connect_timeout=2):
            return True
    except Exception:  # noqa: BLE001
        return False


def _rabbit_reachable() -> bool:
    import socket

    try:
        url = _RABBIT_URL
        host = url.split("@")[-1].split(":")[0]
        port = (
            int(url.split("@")[-1].split(":")[1].split("/")[0])
            if ":" in url.split("@")[-1]
            else 5672
        )
        with socket.create_connection((host, port), timeout=2):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not (_pg_reachable() and _rabbit_reachable()),
    reason="twaky-pg or RabbitMQ not reachable",
)


# ---------------------------------------------------------------------------
# Fake no-LLM sentinel
#
# Uses sentinel_name="mail" so insert_run passes the FK check — the "mail"
# row is seeded by 008_init_sentinels.sh and always present in the dev DB.
# ---------------------------------------------------------------------------

from twaky.sentinels.base import Context, Event, Outcome, Sentinel

_SENTINEL_NAME = "mail"  # must match an existing sentinel row in the DB


class _EchoSentinel(Sentinel):
    """Minimal sentinel that returns PROCESSED without calling any LLM."""

    name = _SENTINEL_NAME
    version = "0.0.1"
    event_source_kind = "rabbitmq"

    def process(self, event: Event, ctx: Context) -> Outcome:
        return Outcome.PROCESSED


# Use a unique exchange per test session so parallel runs don't collide.
_TEMP_EXCHANGE = f"test.sentinel.e2e.{uuid.uuid4().hex[:8]}"
_QUEUE_NAME = f"sentinel.{_SENTINEL_NAME}"


def _minimal_context() -> Context:
    """Build the narrowest possible Context for _process_with_bookkeeping."""
    from twaky.sentinels.emitter import MissionEmitter
    from twaky.sentinels.models import SentinelConfig

    cfg = SentinelConfig(
        name=_SENTINEL_NAME,
        display_name="Test echo",
        description="Integration-test sentinel",
        version="0.0.1",
        enabled=True,
        config_schema={},
        config_values={"event_source": "rabbitmq", "bindings": []},
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )
    return Context(
        db_pool=None,
        mission_emitter=MissionEmitter(_SENTINEL_NAME),
        delegation=None,
        sentinel_row=cfg,
        logger=__import__("logging").getLogger(f"twaky.sentinels.{_SENTINEL_NAME}"),
    )


def _cleanup_runs_like(pattern: str) -> None:
    """Remove test-inserted sentinel_run rows matching a LIKE pattern."""
    with psycopg.connect(_pg_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM sentinel_run WHERE event_ref LIKE %s",
            (pattern,),
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rabbitmq_event_source_delivers_and_run_inserted(monkeypatch) -> None:
    """Publish one JSON event to a temp exchange → verify sentinel_run row inserted.

    Strategy (no full runtime spin-up):
    1. Instantiate RabbitMQEventSource bound to a temp exchange.  The source
       declares the queue + exchange on connect, so we publish AFTER the source
       has set up its bindings to avoid exchange-declaration conflicts.
    2. Pull one (event, ack) from its stream() generator.
    3. Call _process_with_bookkeeping directly with _EchoSentinel.
    4. Assert the returned Outcome is PROCESSED.
    5. Query Postgres to verify the sentinel_run row was inserted.
    """
    import aio_pika

    from twaky.sentinels.runtime import _process_with_bookkeeping
    from twaky.sentinels.sources.rabbitmq import RabbitMQEventSource

    # Override the DB DSN so twaky uses the test Postgres host.
    monkeypatch.setenv("TWAKY_PG_HOST", _PG_HOST)
    monkeypatch.setenv(
        "TWAKY_OWNER_EMAIL", settings.twaky_owner_email or "test@example.com"
    )

    # Re-initialise the db pool so it picks up the env override.
    import twaky.db as _db

    _db._pool = None  # reset singleton

    payload = {"subject": "Hello world", "from": [{"email": "alice@test.com"}]}
    message_id = f"test-e2e-{uuid.uuid4().hex}"
    stop_event = asyncio.Event()

    # Use a unique sentinel_name suffix so the queue is freshly created each run.
    # This avoids picking up stale messages from a pre-existing durable queue.
    unique_suffix = uuid.uuid4().hex[:8]
    unique_sentinel_name = (
        f"mail_e2e_{unique_suffix}"  # no FK check — only for queue name
    )

    # Override the source's queue name via the sentinel_name param.
    source_unique = RabbitMQEventSource(
        sentinel_name=unique_sentinel_name,
        rabbit_url=_RABBIT_URL,
        bindings=[
            {
                "exchange": _TEMP_EXCHANGE,
                "exchange_type": "fanout",
            }
        ],
    )

    # Results captured inside the stream loop (while the connection is alive).
    stream_outcome: list[Outcome] = []
    stream_event: list[Event] = []

    inst = _EchoSentinel()
    ctx = _minimal_context()

    async def _stream_one() -> None:
        """Pull one event, run bookkeeping, ack — all before closing the connection."""
        async for event, ack in source_unique.stream(stop_event=stop_event):
            stream_event.append(event)
            outcome = await _process_with_bookkeeping(inst, ctx, settings, event)
            stream_outcome.append(outcome)
            # Ack while the channel is still open (connection closes on break/stop).
            await ack()
            stop_event.set()
            break

    # Start the consumer task; give it 0.5 s to connect + declare bindings.
    stream_task = asyncio.create_task(_stream_one())
    await asyncio.sleep(0.5)

    pub_conn = await aio_pika.connect_robust(_RABBIT_URL)
    try:
        pub_channel = await pub_conn.channel()
        # Re-declare with the same parameters the source uses (durable=True, fanout).
        exchange = await pub_channel.declare_exchange(
            _TEMP_EXCHANGE,
            type=aio_pika.ExchangeType.FANOUT,
            durable=True,
        )
        msg = aio_pika.Message(
            body=json.dumps(payload).encode(),
            message_id=message_id,
        )
        await exchange.publish(msg, routing_key="")
    finally:
        await pub_conn.close()

    try:
        await asyncio.wait_for(stream_task, timeout=10.0)
    except TimeoutError:
        stop_event.set()
        stream_task.cancel()
        pytest.fail("Timed out waiting for the RabbitMQ event to be delivered")

    assert stream_event, "No event delivered from RabbitMQ"
    received_event = stream_event[0]
    assert received_event["source_kind"] == "rabbitmq"
    assert received_event["message_id"] == message_id
    assert received_event["payload"] == payload

    assert stream_outcome, "No outcome recorded"
    assert stream_outcome[0] is Outcome.PROCESSED

    # Verify the sentinel_run row was inserted in Postgres.
    event_ref = f"{received_event['source_ref']}:{received_event['message_id']}"
    with psycopg.connect(_pg_dsn()) as db, db.cursor() as cur:
        cur.execute(
            "SELECT outcome FROM sentinel_run WHERE event_ref = %s ORDER BY started_at DESC LIMIT 1",
            (event_ref,),
        )
        row = cur.fetchone()

    assert row is not None, f"No sentinel_run row found for event_ref={event_ref!r}"
    assert row[0] == Outcome.PROCESSED.value

    # Cleanup: delete the run rows and the unique temp queue.
    _cleanup_runs_like(f"{_TEMP_EXCHANGE}%")
    unique_queue = f"sentinel.{unique_sentinel_name}"

    import logging as _logging

    _log = _logging.getLogger(__name__)
    try:
        cleanup_conn = await aio_pika.connect_robust(_RABBIT_URL)
        ch = await cleanup_conn.channel()
        try:
            await ch.queue_delete(unique_queue)
        except Exception:  # noqa: BLE001
            _log.debug("cleanup: queue %s did not exist or delete failed", unique_queue)
        await cleanup_conn.close()
    except Exception:  # noqa: BLE001
        _log.debug("cleanup: could not connect to RabbitMQ for queue deletion")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_idempotency_guard_prevents_duplicate_processing(monkeypatch) -> None:
    """Calling _process_with_bookkeeping twice with the same event yields IGNORED.

    _process_with_bookkeeping checks for an existing sentinel_run row with the
    same event_ref within 24 hours and returns Outcome.IGNORED on the second call.
    """
    monkeypatch.setenv("TWAKY_PG_HOST", _PG_HOST)
    monkeypatch.setenv(
        "TWAKY_OWNER_EMAIL", settings.twaky_owner_email or "test@example.com"
    )

    import twaky.db as _db

    _db._pool = None  # reset singleton

    from twaky.sentinels.runtime import _process_with_bookkeeping

    inst = _EchoSentinel()
    ctx = _minimal_context()
    unique_msg_id = f"idem-test-{uuid.uuid4().hex}"

    event: Event = {
        "source_kind": "rabbitmq",
        "source_ref": "test.idem.exchange:",
        "message_id": unique_msg_id,
        "payload": {"test": True},
    }
    event_ref = f"{event['source_ref']}:{event['message_id']}"

    try:
        # First call → PROCESSED
        outcome1 = await _process_with_bookkeeping(inst, ctx, settings, event)
        assert outcome1 is Outcome.PROCESSED

        # Second call with same event → IGNORED
        outcome2 = await _process_with_bookkeeping(inst, ctx, settings, event)
        assert outcome2 is Outcome.IGNORED

    finally:
        _cleanup_runs_like(f"test.idem.exchange:%{unique_msg_id}%")
        # Belt-and-suspenders: also delete by exact event_ref
        with psycopg.connect(_pg_dsn(), autocommit=True) as db, db.cursor() as cur:
            cur.execute(
                "DELETE FROM sentinel_run WHERE event_ref = %s",
                (event_ref,),
            )
