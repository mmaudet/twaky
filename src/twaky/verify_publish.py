"""Publish synthetic events on the fanout exchanges for end-to-end testing.

Provides two entry points:

- `publish_synthetic(uid)`   — a minimal `calendar:event:created` event
                                 (2 attendees + organizer), no visio.
- `publish_meeting(uid, …)`  — a rich `calendar:event:created` event with a
                                 Meet URL, description, location, end time.
                                 Used by the `twaky demo` CLI to run a full
                                 visio-flavoured end-to-end.
- `publish_reply(uid, attendee_email, partstat)` — attendee RSVP event on
                                 `calendar:event:reply`.
- `publish_delete(uid)`      — soft-delete event on `calendar:event:deleted`.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

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


def _meeting_event(
    uid: str,
    summary: str,
    meet_url: str,
    organizer_email: str = "alice@twake-dev.maudet.cloud",
    attendees: list[tuple[str, str]] | None = None,
) -> dict:
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=1)
    end = start + timedelta(minutes=45)
    return {
        "uid": uid,
        "summary": summary,
        "description": f"Visio meeting — join via {meet_url}",
        "location": meet_url,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "meetUrl": meet_url,
        "organizer": {
            "email": organizer_email,
            "cn": organizer_email.split("@")[0].title(),
        },
        "attendees": [
            {"email": e, "cn": n}
            for e, n in (
                attendees
                or [
                    ("bob@twake-dev.maudet.cloud", "Bob"),
                    ("carol@twake-dev.maudet.cloud", "Carol"),
                ]
            )
        ],
    }


async def _publish(exchange: str, payload: dict, message_id: str) -> None:
    body = json.dumps(payload).encode()
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        exch = await channel.get_exchange(exchange, ensure=True)
        await exch.publish(
            aio_pika.Message(
                body=body,
                content_type="application/json",
                message_id=message_id,
            ),
            routing_key="",
        )
        log.info("published", exchange=exchange, message_id=message_id, bytes=len(body))


async def publish_synthetic(uid: str, exchange: str = "calendar:event:created") -> None:
    await _publish(exchange, _synthetic_calendar_event(uid), f"verify-{uid}")


async def publish_meeting(uid: str, summary: str, meet_url: str) -> None:
    await _publish(
        "calendar:event:created",
        _meeting_event(uid, summary, meet_url),
        f"meeting-{uid}",
    )


async def publish_reply(
    uid: str, attendee_email: str, partstat: str = "ACCEPTED"
) -> None:
    payload = {
        "uid": uid,
        "attendee": {"email": attendee_email, "partstat": partstat},
    }
    await _publish(
        "calendar:event:reply", payload, f"reply-{uid}-{attendee_email}-{partstat}"
    )


async def publish_delete(uid: str) -> None:
    await _publish("calendar:event:deleted", {"uid": uid}, f"delete-{uid}")


__all__ = [
    "publish_delete",
    "publish_meeting",
    "publish_reply",
    "publish_synthetic",
]
