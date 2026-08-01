"""Map a `calendar:event:reply` payload (attendee RSVP) to an ATTENDED
relationship property update.

Expected payload (best-effort — Sabre/Cozy shape varies):
    {
        "uid": "<event uid>",
        "attendee": { "email": "...", "partstat": "ACCEPTED" }
        # OR
        "attendees": [ { "email": "...", "partstat": "..." }, ... ]
    }
"""

from __future__ import annotations

from twaky.mappers._cypher import cql_literal


def _norm_status(partstat: str | None) -> str:
    if not partstat:
        return "unknown"
    p = partstat.strip().lower()
    if p in {"accepted", "confirmed"}:
        return "accepted"
    if p in {"declined"}:
        return "declined"
    if p in {"tentative"}:
        return "tentative"
    return p


def _rsvp_stmt(uid: str, email: str, partstat: str | None) -> str:
    status = _norm_status(partstat)
    # MERGE the Person (may not exist yet if we didn't see :created).
    # Then MERGE the ATTENDED edge to the CalendarEvent and SET the status.
    return (
        f"MERGE (p:Person {{email: {cql_literal(email)}}}) "
        f"WITH p MERGE (e:CalendarEvent {{uid: {cql_literal(uid)}}}) "
        f"WITH p, e MERGE (p)-[r:ATTENDED]->(e) "
        f"SET r.status = {cql_literal(status)}"
    )


def map_event(payload: dict) -> list[str]:
    uid = payload.get("uid")
    if not uid:
        return []

    stmts: list[str] = []

    # Single-attendee case (canonical RSVP).
    att = payload.get("attendee")
    if isinstance(att, dict) and att.get("email"):
        stmts.append(_rsvp_stmt(uid, att["email"], att.get("partstat")))

    # Multi-attendee case (bulk update).
    for a in payload.get("attendees") or []:
        if not isinstance(a, dict) or not a.get("email"):
            continue
        stmts.append(_rsvp_stmt(uid, a["email"], a.get("partstat")))

    return stmts
