"""Publish a synthetic mail:message:received → verify Email node in graph.

Requires the live twaky stack (twaky-pg + rabbitmq + twaky-ingest + twaky-projector).
Test is skipped if any component is unreachable. Run inside twake-network via
`docker compose run --rm --no-deps twaky-agent pytest tests/integration/...`.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from urllib.parse import urlparse
from uuid import uuid4

import aio_pika
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


def _rabbitmq_reachable() -> bool:
    """Skip cleanly outside Docker — ``rabbitmq`` is a compose-internal name.

    Attempts a 1-second DNS lookup on the AMQP host from ``settings.rabbitmq_url``.
    Without this guard the test fails with a confusing AMQPConnectionError:
    Temporary failure in name resolution when developers run pytest from
    the host (RabbitMQ is only reachable from within twake-network).
    """
    try:
        parsed = urlparse(settings.rabbitmq_url)
        host = parsed.hostname or "rabbitmq"
        port = parsed.port or 5672
        socket.setdefaulttimeout(1)
        socket.getaddrinfo(host, port)
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _reachable() or not _rabbitmq_reachable(),
    reason="twaky-pg or rabbitmq not reachable",
)


async def _publish_mail_received(mid: str, owner: str):
    conn = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with conn:
        ch = await conn.channel()
        exch = await ch.get_exchange("mail:message:received", ensure=True)
        body = {
            "message_id": mid,
            "user": owner,
            "mailbox_path": {"namespace": "#private", "user": owner, "name": "INBOX"},
            "timestamp": "2026-08-01T12:00:00Z",
        }
        await exch.publish(
            aio_pika.Message(
                body=json.dumps(body).encode(),
                content_type="application/json",
                message_id=f"test-{mid}",
            ),
            routing_key="",
        )


def _read_email_node(mid: str):
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("LOAD 'age';")
        cur.execute('SET search_path = ag_catalog, "$user", public;')
        cur.execute(
            f"SELECT * FROM cypher('twake', $CQR$ MATCH (e:Email {{message_id: '{mid}'}}) "
            f"RETURN e.user AS user, e.deleted AS deleted, e.mailbox_path AS mp $CQR$) "
            f"AS (u ag_catalog.agtype, d ag_catalog.agtype, mp ag_catalog.agtype);"
        )
        rows = cur.fetchall()
    return rows


def test_mail_received_lands_in_graph():
    mid = f"pytest-mail-{uuid4().hex[:8]}"
    owner = settings.twaky_owner_email
    asyncio.run(_publish_mail_received(mid, owner))

    # Wait up to 15s for ingest + projector to catch up.
    for _ in range(15):
        rows = _read_email_node(mid)
        if rows:
            break
        time.sleep(1)
    assert rows, f"Email node {mid!r} did not appear in graph"
    user_val = str(rows[0][0]).strip('"')
    deleted_val = str(rows[0][1]).lower()
    assert user_val == owner
    assert deleted_val == "false"

    # Cleanup graph.
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("LOAD 'age';")
        cur.execute('SET search_path = ag_catalog, "$user", public;')
        cur.execute(
            f"SELECT * FROM cypher('twake', $CQR$ MATCH (e:Email {{message_id: '{mid}'}}) "
            f"DETACH DELETE e $CQR$) AS (v ag_catalog.agtype);"
        )
        cur.execute("DELETE FROM event_log WHERE message_id = %s", (f"test-{mid}",))
        conn.commit()
