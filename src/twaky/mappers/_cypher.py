"""Cypher helpers — inline JSON encoding for AGE MERGE statements.

We build Cypher server-side from validated payloads. Since AGE's parameter
support is limited (accepts a single agtype map, no positional bindings),
inlining JSON-encoded literals via json.dumps is simpler and equally safe
as long as the caller trusts its payload (source is the platform's own
event bus, not end-user input).
"""

from __future__ import annotations

import json
from typing import Any


def cql_literal(value: Any) -> str:
    """Render a Python value as a Cypher literal (uses JSON encoding).

    JSON's string/number/bool/null form is a strict subset of Cypher
    literal syntax, so json.dumps produces valid Cypher for scalars.
    Lists/dicts are rendered as JSON — Cypher accepts the same syntax
    for lists and property maps.
    """
    return json.dumps(value, ensure_ascii=False)


def props(m: dict[str, Any]) -> str:
    """Render a dict as an inline Cypher property map (skips None values)."""
    parts = [f"{k}: {cql_literal(v)}" for k, v in m.items() if v is not None]
    return "{" + ", ".join(parts) + "}"
