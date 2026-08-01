"""Map a `sabre:contact:created` payload to Cypher.

Payload shape (approx):
    {
        "email": "alice@example.com",
        "fn": "Alice Anderson",
        "tel": "+33...",
        "org": "Linagora"
    }
"""

from __future__ import annotations

from twaky.mappers._cypher import cql_literal


def map_event(payload: dict) -> list[str]:
    email = payload.get("email")
    if not email:
        return []

    # MERGE on email only; other props go in SET so updates don't create dupes.
    settable = {
        "fn": payload.get("fn"),
        "tel": payload.get("tel"),
        "deleted": False,
    }
    set_frag = ", ".join(
        f"p.{k} = {cql_literal(v)}" for k, v in settable.items() if v is not None
    )
    stmt = f"MERGE (p:Person {{email: {cql_literal(email)}}})"
    if set_frag:
        stmt += f" SET {set_frag}"
    stmts: list[str] = [stmt]

    org = payload.get("org")
    if org:
        stmts.append(
            f"MERGE (o:Organization {{name: {cql_literal(org)}}}) "
            f"WITH o MATCH (p:Person {{email: {cql_literal(email)}}}) "
            f"MERGE (p)-[:WORKS_AT]->(o)"
        )

    return stmts
