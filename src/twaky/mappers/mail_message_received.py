"""Map a `mail:message:received` event to an Email node.

Metadata only — no body fetch (JMAP fetch is deferred to sub-project 2).
"""

from __future__ import annotations

from twaky.mappers._cypher import cql_literal


def _flatten_path(mbp: object) -> str | None:
    if isinstance(mbp, dict):
        parts = [
            mbp.get("namespace") or "",
            mbp.get("user") or "",
            mbp.get("name") or "",
        ]
        return "/".join(str(p) for p in parts if p)
    if isinstance(mbp, str):
        return mbp
    return None


def map_event(payload: dict) -> list[str]:
    mid = payload.get("message_id")
    if not mid:
        return []
    settable = {
        "user": payload.get("user"),
        "mailbox_path": _flatten_path(payload.get("mailbox_path")),
        "received_at": payload.get("timestamp"),
        "deleted": False,
    }
    set_frag = ", ".join(
        f"e.{k} = {cql_literal(v)}" for k, v in settable.items() if v is not None
    )
    stmt = f"MERGE (e:Email {{message_id: {cql_literal(mid)}}})"
    if set_frag:
        stmt += f" SET {set_frag}"
    return [stmt]
