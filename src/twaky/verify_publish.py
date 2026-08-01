"""Publish a synthetic event on a fanout exchange for end-to-end testing.

Emits a well-formed `calendar:event:created` payload (2 attendees + organizer)
so the projector has something meaningful to MERGE into the AGE graph.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import aio_pika
import structlog

from twaky.config import settings

logging.basicConfig(level=logging.INFO)
log = structlog.get_logger("twaky.verify")


def _synthetic_calendar_event(uid: str) -> dict:
    return {
        "uid": uid,
        "summary": f"Twaky verify event — {uid}",
        "start": datetime(2026, 8, 1, 14, 0, tzinfo=UTC).isoformat(),
        "organizer": {"email": "alice@twake-dev.maudet.cloud", "cn": "Alice"},
        "attendees": [
            {"email": "bob@twake-dev.maudet.cloud", "cn": "Bob"},
            {"email": "carol@twake-dev.maudet.cloud", "cn": "Carol"},
        ],
    }


async def publish_synthetic(uid: str, exchange: str = "calendar:event:created") -> None:
    payload = _synthetic_calendar_event(uid)
    body = json.dumps(payload).encode()
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        exch = await channel.get_exchange(exchange, ensure=True)
        await exch.publish(
            aio_pika.Message(
                body=body,
                content_type="application/json",
                message_id=f"verify-{uid}",
            ),
            routing_key="",
        )
        log.info("published", exchange=exchange, uid=uid, bytes=len(body))


__all__ = ["publish_synthetic"]
