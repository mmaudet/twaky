"""Map a `calendar:event:{created,updated,request}` payload to idempotent
Cypher MERGE statements.

Expected payload shape (best-effort — falls back gracefully on missing fields):
    {
        "uid": "<event uid>",              # required
        "summary": "...",                  # optional
        "description": "...",              # optional (may embed a visio URL)
        "location": "...",                 # optional (may be a visio URL too)
        "start": "2026-08-01T14:00:00Z",   # optional ISO 8601
        "end": "2026-08-01T15:00:00Z",     # optional ISO 8601
        "meetUrl": "https://meet.…/room/x",# optional (native Twake visio field)
        "conference": {"uri": "…"},        # optional (alt shape)
        "organizer": {"email": "...", "cn": "..."},        # optional
        "attendees": [{"email": "...", "cn": "..."}, ...]  # optional
    }

Same mapper handles created/updated/request — MERGE + SET makes it
idempotent whichever event triggered it. On re-observation, deleted=false
is re-asserted (an updated/created event is a resurrection).
"""

from __future__ import annotations

from twaky.mappers._cypher import cql_literal, props


def _person(email: str, cn: str | None) -> str:
    return (
        f"MERGE (p:Person {{email: {cql_literal(email)}}}) "
        f"SET p.fn = coalesce(p.fn, {cql_literal(cn)})"
    )


def _extract_meet_url(payload: dict) -> str | None:
    """Pull the visio URL out of the several shapes we've seen in the wild."""
    if payload.get("meetUrl"):
        return payload["meetUrl"]
    if isinstance(payload.get("conference"), dict):
        uri = payload["conference"].get("uri")
        if uri:
            return uri
    # Some Cozy/Sabre setups drop the URL in `location` when it looks like a URL.
    loc = payload.get("location")
    if isinstance(loc, str) and loc.startswith(("http://", "https://")):
        return loc
    return None


def map_event(payload: dict) -> list[str]:
    uid = payload.get("uid")
    if not uid:
        return []  # cannot merge without a natural key

    event_props = {
        "uid": uid,
        "summary": payload.get("summary"),
        "description": payload.get("description"),
        "location": payload.get("location"),
        "start": payload.get("start"),
        "end": payload.get("end"),
        "meet_url": _extract_meet_url(payload),
        # Re-assert not-deleted on any create/update — supports "undelete" scenarios.
        "deleted": False,
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
