"""Map a `sabre:contact:deleted` payload to a Person tombstone.

Same rationale as calendar_event_deleted: we keep the node with
`deleted=true` instead of DETACH DELETE, so the graph remembers who
attended what and answers historical queries.
"""

from __future__ import annotations

from twaky.mappers._cypher import cql_literal


def map_event(payload: dict) -> list[str]:
    email = payload.get("email")
    if not email:
        return []
    return [(f"MERGE (p:Person {{email: {cql_literal(email)}}}) SET p.deleted = true")]
