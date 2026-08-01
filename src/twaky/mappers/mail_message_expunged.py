"""Map a `mail:message:expunged` event to a tombstone on the Email node."""

from __future__ import annotations

from twaky.mappers._cypher import cql_literal


def map_event(payload: dict) -> list[str]:
    mid = payload.get("message_id")
    if not mid:
        return []
    return [f"MERGE (e:Email {{message_id: {cql_literal(mid)}}}) SET e.deleted = true"]
