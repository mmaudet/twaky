"""Map a `mail:message:flags:updated` event to Email.read (from `\\Seen`)."""

from __future__ import annotations

from twaky.mappers._cypher import cql_literal


def map_event(payload: dict) -> list[str]:
    mid = payload.get("message_id")
    if not mid:
        return []
    flags = payload.get("flags") or []
    read = "\\Seen" in flags if isinstance(flags, list) else False
    return [
        (
            f"MERGE (e:Email {{message_id: {cql_literal(mid)}}}) "
            f"SET e.read = {'true' if read else 'false'}"
        )
    ]
