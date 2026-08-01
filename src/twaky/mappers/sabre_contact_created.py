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

from twaky.mappers._cypher import cql_literal, props


def map_event(payload: dict) -> list[str]:
    email = payload.get("email")
    if not email:
        return []

    person_props = {
        "email": email,
        "fn": payload.get("fn"),
        "tel": payload.get("tel"),
    }
    stmts: list[str] = [f"MERGE (p:Person {props(person_props)})"]

    org = payload.get("org")
    if org:
        stmts.append(
            f"MERGE (o:Organization {{name: {cql_literal(org)}}}) "
            f"WITH o MATCH (p:Person {{email: {cql_literal(email)}}}) "
            f"MERGE (p)-[:WORKS_AT]->(o)"
        )

    return stmts
