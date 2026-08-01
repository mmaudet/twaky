"""Map a `calendar:event:created` payload to idempotent Cypher MERGE statements.

Expected payload shape (best-effort — falls back gracefully on missing fields):
    {
        "uid": "<event uid>",              # required
        "summary": "...",                  # optional
        "start": "2026-08-01T14:00:00Z",   # optional ISO 8601
        "organizer": { "email": "...", "cn": "..." },   # optional
        "attendees": [ { "email": "...", "cn": "..." }, ... ]   # optional
    }
"""

from __future__ import annotations

from twaky.mappers._cypher import cql_literal, props


def _person(email: str, cn: str | None) -> str:
    return (
        f"MERGE (p:Person {{email: {cql_literal(email)}}}) "
        f"SET p.fn = coalesce(p.fn, {cql_literal(cn)})"
    )


def map_event(payload: dict) -> list[str]:
    uid = payload.get("uid")
    if not uid:
        return []  # cannot merge without a natural key

    event_props = {
        "uid": uid,
        "summary": payload.get("summary"),
        "start": payload.get("start"),
    }
    stmts: list[str] = [f"MERGE (e:CalendarEvent {props(event_props)})"]

    organizer = payload.get("organizer") or {}
    org_email = organizer.get("email")
    if org_email:
        stmts.append(
            f"{_person(org_email, organizer.get('cn'))} "
            f"WITH p MATCH (e:CalendarEvent {{uid: {cql_literal(uid)}}}) "
            f"MERGE (p)-[:ORGANIZED]->(e)"
        )

    for att in payload.get("attendees") or []:
        email = (att or {}).get("email")
        if not email:
            continue
        stmts.append(
            f"{_person(email, att.get('cn'))} "
            f"WITH p MATCH (e:CalendarEvent {{uid: {cql_literal(uid)}}}) "
            f"MERGE (p)-[:ATTENDED]->(e)"
        )

    return stmts
