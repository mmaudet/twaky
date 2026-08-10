"""Integration tests for RabbitMQEventSource.

Requires a live RabbitMQ broker.  Set ``RABBITMQ_URL`` in the environment
to override the default settings URL (useful when the hostname ``rabbitmq``
is not DNS-resolvable from the test host):

    RABBITMQ_URL=amqp://guest:guest@172.27.0.8:5672/%2F \\
        uv run pytest tests/sentinels/sources/test_rabbitmq.py -v

All tests are skipped when the broker is unreachable (checked once at
module import time via ``asyncio.run``).
"""

from __future__ import annotations

import asyncio
import json
import os

import aio_pika
import pytest

from twaky.config import settings
from twaky.sentinels.sources.rabbitmq import RabbitMQEventSource

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rabbit_url() -> str:
    return os.environ.get("RABBITMQ_URL") or settings.rabbitmq_url


def _reachable() -> bool:
    """Return True if the broker is reachable within 1 second."""

    async def _probe() -> bool:
        try:
            conn = await aio_pika.connect_robust(_rabbit_url(), timeout=1)
            await conn.close()
            return True
        except Exception:  # noqa: BLE001
            return False

    return asyncio.run(_probe())


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _reachable(),
        reason="RabbitMQ broker not reachable — set RABBITMQ_URL to run",
    ),
    pytest.mark.asyncio,
]


# ---------------------------------------------------------------------------
# Test 1: declare queue and receive a message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_declare_queue_and_receive_message() -> None:
    """Publish one JSON message; verify the consumer yields it with correct fields."""
    exchange_name = "test-sentinel-fanout-1"
    sentinel_name = "test-recv"
    queue_name = f"sentinel.{sentinel_name}"
    url = _rabbit_url()

    # --- setup: connect and ensure the queue exists before publishing ---
    setup_conn = await aio_pika.connect_robust(url)
    try:
        setup_ch = await setup_conn.channel()
        exchange = await setup_ch.declare_exchange(
            exchange_name,
            type=aio_pika.ExchangeType.FANOUT,
            durable=True,
        )
        # Pre-declare the queue so the message isn't lost before the consumer binds
        setup_q = await setup_ch.declare_queue(
            queue_name, durable=True, auto_delete=False
        )
        await setup_q.bind(exchange, routing_key="")

        body = json.dumps({"email_id": "eml-99"}).encode()
        msg = aio_pika.Message(
            body=body,
            message_id="msg-99",
            content_type="application/json",
        )
        await exchange.publish(msg, routing_key="")
    finally:
        await setup_conn.close()

    # --- consume ---
    source = RabbitMQEventSource(
        sentinel_name=sentinel_name,
        rabbit_url=url,
        bindings=[{"exchange": exchange_name, "exchange_type": "fanout"}],
    )
    stop = asyncio.Event()
    received_event = None

    async def _consume() -> None:
        nonlocal received_event
        async for event, ack in source.stream(stop_event=stop):
            received_event = event
            await ack()
            stop.set()  # stop after first message

    try:
        await asyncio.wait_for(_consume(), timeout=5.0)
    finally:
        # cleanup
        cleanup_conn = await aio_pika.connect_robust(url)
        try:
            ch = await cleanup_conn.channel()
            await ch.queue_delete(queue_name)
        finally:
            await cleanup_conn.close()

    assert received_event is not None
    assert received_event["message_id"] == "msg-99"
    assert received_event["source_kind"] == "rabbitmq"
    assert received_event["payload"]["email_id"] == "eml-99"


# ---------------------------------------------------------------------------
# Test 2: two sentinels don't steal from each other
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_sentinels_dont_steal_from_each_other() -> None:
    """Two RabbitMQEventSources on the same exchange each receive all messages."""
    exchange_name = "test-sentinel-fanout-2"
    url = _rabbit_url()

    sentinel_a_name = "test-no-steal-a"
    sentinel_b_name = "test-no-steal-b"
    queue_a = f"sentinel.{sentinel_a_name}"
    queue_b = f"sentinel.{sentinel_b_name}"

    # --- setup: pre-declare both queues bound to the exchange, then publish ---
    setup_conn = await aio_pika.connect_robust(url)
    try:
        setup_ch = await setup_conn.channel()
        exchange = await setup_ch.declare_exchange(
            exchange_name,
            type=aio_pika.ExchangeType.FANOUT,
            durable=True,
        )
        for qname in (queue_a, queue_b):
            q = await setup_ch.declare_queue(qname, durable=True, auto_delete=False)
            await q.bind(exchange, routing_key="")

        for i in range(2):
            body = json.dumps({"index": i}).encode()
            msg = aio_pika.Message(
                body=body,
                message_id=f"msg-{i}",
                content_type="application/json",
            )
            await exchange.publish(msg, routing_key="")
    finally:
        await setup_conn.close()

    # --- consume from both sentinels independently ---
    source_a = RabbitMQEventSource(
        sentinel_name=sentinel_a_name,
        rabbit_url=url,
        bindings=[{"exchange": exchange_name, "exchange_type": "fanout"}],
    )
    source_b = RabbitMQEventSource(
        sentinel_name=sentinel_b_name,
        rabbit_url=url,
        bindings=[{"exchange": exchange_name, "exchange_type": "fanout"}],
    )

    stop_a = asyncio.Event()
    stop_b = asyncio.Event()
    events_a: list[dict] = []
    events_b: list[dict] = []

    async def _collect(
        source: RabbitMQEventSource,
        stop: asyncio.Event,
        bucket: list,
        target: int,
    ) -> None:
        async for event, ack in source.stream(stop_event=stop):
            bucket.append(event)
            await ack()
            if len(bucket) >= target:
                stop.set()

    try:
        await asyncio.wait_for(
            asyncio.gather(
                _collect(source_a, stop_a, events_a, 2),
                _collect(source_b, stop_b, events_b, 2),
            ),
            timeout=5.0,
        )
    finally:
        cleanup_conn = await aio_pika.connect_robust(url)
        try:
            ch = await cleanup_conn.channel()
            await ch.queue_delete(queue_a)
            await ch.queue_delete(queue_b)
        finally:
            await cleanup_conn.close()

    # Each sentinel must have received both messages independently
    assert len(events_a) == 2, f"sentinel-a got {len(events_a)} messages, expected 2"
    assert len(events_b) == 2, f"sentinel-b got {len(events_b)} messages, expected 2"

    # Verify all events carry the right source_kind
    for ev in events_a + events_b:
        assert ev["source_kind"] == "rabbitmq"
