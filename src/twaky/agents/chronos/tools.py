"""Chronos calendar tools — read from the AGE graph."""

from __future__ import annotations

from langchain_core.tools import tool

from twaky.db import get_pool
from twaky.mappers._cypher import cql_literal

_GRAPH = "twake"
_TAG = "$CQR$"


def _cypher(cur, body: str, alias: str = "v ag_catalog.agtype") -> list:
    if _TAG in body:
        raise ValueError("cypher body contains reserved tag")
    cur.execute(
        f"LOAD 'age'; SET search_path = ag_catalog, \"$user\", public; "
        f"SELECT * FROM cypher('{_GRAPH}', {_TAG}{body}{_TAG}) AS ({alias});"
    )
    return cur.fetchall()


@tool
def list_events(from_iso: str, to_iso: str) -> list[dict]:
    """List calendar events between from_iso and to_iso (ISO 8601 timestamps).

    Returns a list of dicts with uid, summary, start_at, end_at, deleted.
    """
    body = (
        f"MATCH (e:CalendarEvent) "
        f"WHERE e.start_at >= {cql_literal(from_iso)} AND e.start_at <= {cql_literal(to_iso)} "
        f"AND (e.deleted = false OR e.deleted IS NULL) "
        f"RETURN e.uid AS uid, e.summary AS summary, e.start_at AS start_at, "
        f"e.end_at AS end_at ORDER BY e.start_at"
    )
    with get_pool().connection() as conn, conn.cursor() as cur:
        rows = _cypher(
            cur,
            body,
            alias="uid agtype, summary agtype, start_at agtype, end_at agtype",
        )
    return [
        {
            "uid": str(r[0]).strip('"'),
            "summary": str(r[1]).strip('"'),
            "start_at": str(r[2]).strip('"'),
            "end_at": str(r[3]).strip('"'),
        }
        for r in rows
    ]


@tool
def get_event(uid: str) -> dict | None:
    """Fetch a single calendar event by uid. Returns None when not found."""
    body = (
        f"MATCH (e:CalendarEvent {{uid: {cql_literal(uid)}}}) "
        f"RETURN e.uid AS uid, e.summary AS summary, e.start_at AS start_at, "
        f"e.end_at AS end_at, e.meet_url AS meet_url, e.deleted AS deleted"
    )
    with get_pool().connection() as conn, conn.cursor() as cur:
        rows = _cypher(
            cur,
            body,
            alias="uid agtype, summary agtype, start_at agtype, end_at agtype, "
            "meet_url agtype, deleted agtype",
        )
    if not rows:
        return None
    r = rows[0]
    return {
        "uid": str(r[0]).strip('"'),
        "summary": str(r[1]).strip('"'),
        "start_at": str(r[2]).strip('"'),
        "end_at": str(r[3]).strip('"'),
        "meet_url": str(r[4]).strip('"') if r[4] is not None else None,
        "deleted": str(r[5]).lower() == "true",
    }


@tool
def find_conflicts(person_email: str, from_iso: str, to_iso: str) -> list[dict]:
    """Find events between from_iso and to_iso where person_email attends.

    Same shape as list_events output — the caller decides what qualifies
    as a conflict.
    """
    body = (
        f"MATCH (p:Person {{email: {cql_literal(person_email)}}}) "
        f"-[:ATTENDED|:ORGANIZED]->(e:CalendarEvent) "
        f"WHERE e.start_at >= {cql_literal(from_iso)} AND e.start_at <= {cql_literal(to_iso)} "
        f"AND (e.deleted = false OR e.deleted IS NULL) "
        f"RETURN e.uid AS uid, e.summary AS summary, e.start_at AS start_at, e.end_at AS end_at "
        f"ORDER BY e.start_at"
    )
    with get_pool().connection() as conn, conn.cursor() as cur:
        rows = _cypher(
            cur,
            body,
            alias="uid agtype, summary agtype, start_at agtype, end_at agtype",
        )
    return [
        {
            "uid": str(r[0]).strip('"'),
            "summary": str(r[1]).strip('"'),
            "start_at": str(r[2]).strip('"'),
            "end_at": str(r[3]).strip('"'),
        }
        for r in rows
    ]


@tool
def next_free_slot(
    participant_emails: list[str],
    duration_min: int,
    window_from_iso: str,
    window_to_iso: str,
) -> dict | None:
    """Naive first-free slot in [window_from_iso, window_to_iso] where none
    of the participants have an event overlap. Returns {"from": iso, "to": iso}
    or None if none found.
    """
    conflicts: list[tuple[str, str]] = []
    for p in participant_emails:
        for c in find_conflicts.invoke(
            {"person_email": p, "from_iso": window_from_iso, "to_iso": window_to_iso}
        ):
            conflicts.append((c["start_at"], c["end_at"]))
    conflicts.sort()
    # Simple sweep: start at window_from_iso, advance past each conflict,
    # accept the first gap ≥ duration_min minutes.
    from datetime import datetime, timedelta

    def _parse(s: str) -> datetime:
        return datetime.fromisoformat(s.replace("Z", "+00:00", 1))

    cursor = _parse(window_from_iso)
    end = _parse(window_to_iso)
    need = timedelta(minutes=duration_min)
    for s_iso, e_iso in conflicts:
        s = _parse(s_iso)
        if s - cursor >= need:
            return {"from": cursor.isoformat(), "to": (cursor + need).isoformat()}
        e_dt = _parse(e_iso)
        cursor = max(cursor, e_dt)
    if end - cursor >= need:
        return {"from": cursor.isoformat(), "to": (cursor + need).isoformat()}
    return None


__all__ = ["find_conflicts", "get_event", "list_events", "next_free_slot"]
