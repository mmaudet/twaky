"""Map a `mail:message:moved` event to an update of Email.mailbox_path."""

from __future__ import annotations

from twaky.mappers._cypher import cql_literal
from twaky.mappers.mail_message_received import _flatten_path


def map_event(payload: dict) -> list[str]:
    mid = payload.get("message_id")
    if not mid:
        return []
    new_path = _flatten_path(payload.get("mailbox_path"))
    if new_path is None:
        return []
    return [
        (
            f"MERGE (e:Email {{message_id: {cql_literal(mid)}}}) "
            f"SET e.mailbox_path = {cql_literal(new_path)}"
        )
    ]
