"""Event-to-Cypher mappers, keyed by RabbitMQ exchange name.

Each mapper module exposes a `map_event(payload: dict) -> list[str]` returning
one or more Cypher `MERGE` statements (already inline-safe — values are
json.dumps'd — no external input beyond the trusted payload).
"""

from __future__ import annotations

from typing import Callable

from twaky.mappers import calendar_event_created, sabre_contact_created

Mapper = Callable[[dict], list[str]]

_REGISTRY: dict[str, Mapper] = {
    "calendar:event:created": calendar_event_created.map_event,
    "sabre:contact:created": sabre_contact_created.map_event,
}


def get_mapper(exchange: str) -> Mapper | None:
    return _REGISTRY.get(exchange)


__all__ = ["get_mapper", "Mapper"]
