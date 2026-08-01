"""Map a `calendar:event:{deleted,cancel}` payload to a tombstone MERGE.

We do NOT `DETACH DELETE` the CalendarEvent node — keeping the row with
`deleted=true` lets the graph answer historical questions ("who used to
attend event X?") and is idempotent on replay.
"""

from __future__ import annotations

from twaky.mappers._cypher import cql_literal


def map_event(payload: dict) -> list[str]:
    uid = payload.get("uid")
    if not uid:
        return []
    return [
        (f"MERGE (e:CalendarEvent {{uid: {cql_literal(uid)}}}) SET e.deleted = true")
    ]
