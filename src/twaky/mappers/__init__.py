"""Event-to-Cypher mappers, keyed by RabbitMQ exchange name.

Each mapper module exposes a `map_event(payload: dict) -> list[str]` returning
one or more Cypher statements (already inline-safe — values are json.dumps'd —
no external input beyond the trusted payload).

The registry maps multiple exchanges to the same handler when semantics match:
- created / updated / request → same upsert (MERGE + SET current state)
- deleted / cancel → same tombstone (SET deleted = true)
- sabre `update` (no 'd') is Sabre's misspelled sibling of `updated`
"""

from __future__ import annotations

from collections.abc import Callable

from twaky.mappers import (
    calendar_event_created,
    calendar_event_deleted,
    calendar_event_reply,
    sabre_contact_created,
    sabre_contact_deleted,
)

Mapper = Callable[[dict], list[str]]

_REGISTRY: dict[str, Mapper] = {
    # --- CalendarEvent lifecycle: upsert (current-state MERGE) ---
    "calendar:event:created": calendar_event_created.map_event,
    "calendar:event:updated": calendar_event_created.map_event,
    "calendar:event:request": calendar_event_created.map_event,
    # --- CalendarEvent removal: tombstone (deleted = true) ---
    "calendar:event:deleted": calendar_event_deleted.map_event,
    "calendar:event:cancel": calendar_event_deleted.map_event,
    # --- Attendee RSVP → ATTENDED.status ---
    "calendar:event:reply": calendar_event_reply.map_event,
    # --- Contact lifecycle: upsert (Sabre publishes 'update' AND 'updated') ---
    "sabre:contact:created": sabre_contact_created.map_event,
    "sabre:contact:updated": sabre_contact_created.map_event,
    "sabre:contact:update": sabre_contact_created.map_event,
    # --- Contact removal: tombstone ---
    "sabre:contact:deleted": sabre_contact_deleted.map_event,
}


def get_mapper(exchange: str) -> Mapper | None:
    return _REGISTRY.get(exchange)


__all__ = ["Mapper", "get_mapper"]
