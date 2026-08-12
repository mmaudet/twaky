"""Rule condition matcher for the mail sentinel propose endpoint.

Evaluates a structured condition dict against a synthetic email envelope.
This module is the single source of truth for condition evaluation in the
propose simulation.  The production pipeline uses a different (older) schema
``{field, operator, value}``; this module implements the JSON-editor condition
schema introduced in SP6d.

Condition schema
----------------
Leaf predicates:

    {"from_contains": "acme.com"}
    {"subject_contains": "invoice"}
    {"list_id_contains": "news.lists.example.com"}
    {"header_matches": {"name": "List-Unsubscribe", "regex": "https://"}}

Combinators (recursive):

    {"all": [<condition>, ...]}
    {"any": [<condition>, ...]}

Envelope dict (built by the propose endpoint from each SpamDecision):

    {
        "from": "<sender_email>",
        "subject": "<subject or ''>",
        "headers": <envelope_headers dict or {}>,
    }

The ``headers`` value is the dict stored in the ``envelope_headers`` JSONB
column — a mapping of lower-cased header names to raw string values.
"""

from __future__ import annotations

import re
from typing import Any

from twaky.sentinels.mail.store.rules import RuleValidationError

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_LEAF_KEYS = frozenset(
    {"from_contains", "subject_contains", "list_id_contains", "header_matches"}
)
_COMBINATOR_KEYS = frozenset({"all", "any"})


def validate_condition(condition: Any, *, _depth: int = 0) -> None:
    """Validate a condition dict (recursive).

    Raises
    ------
    RuleValidationError
        If the condition is structurally invalid or uses unknown predicates.
    """
    if _depth > 10:
        raise RuleValidationError("condition nesting depth exceeds 10")

    if not isinstance(condition, dict):
        raise RuleValidationError(
            f"condition must be a dict, got {type(condition).__name__!r}"
        )

    keys = set(condition.keys())
    if not keys:
        raise RuleValidationError("condition must have at least one key")

    # A condition node must be exactly ONE leaf or ONE combinator.
    found = keys & (_LEAF_KEYS | _COMBINATOR_KEYS)
    unknown = keys - (_LEAF_KEYS | _COMBINATOR_KEYS)
    if unknown:
        raise RuleValidationError(
            f"unknown condition key(s): {sorted(unknown)}; "
            f"must be one of {sorted(_LEAF_KEYS | _COMBINATOR_KEYS)}"
        )
    if len(found) != 1:
        raise RuleValidationError(
            f"each condition node must have exactly one key, got {sorted(found)}"
        )

    key = next(iter(found))

    if key in {"from_contains", "subject_contains", "list_id_contains"}:
        val = condition[key]
        if not isinstance(val, str) or not val:
            raise RuleValidationError(f"{key!r}: value must be a non-empty string")
        return

    if key == "header_matches":
        hm = condition[key]
        if not isinstance(hm, dict):
            raise RuleValidationError(
                "header_matches: value must be a dict with 'name' and 'regex' keys"
            )
        name = hm.get("name")
        regex = hm.get("regex")
        if not isinstance(name, str) or not name:
            raise RuleValidationError("header_matches.name must be a non-empty string")
        if not isinstance(regex, str) or not regex:
            raise RuleValidationError("header_matches.regex must be a non-empty string")
        try:
            re.compile(regex)
        except re.error as exc:
            raise RuleValidationError(
                f"header_matches.regex {regex!r} does not compile: {exc}"
            ) from exc
        return

    if key in {"all", "any"}:
        children = condition[key]
        if not isinstance(children, list) or not children:
            raise RuleValidationError(
                f"{key!r}: value must be a non-empty list of conditions"
            )
        for i, child in enumerate(children):
            try:
                validate_condition(child, _depth=_depth + 1)
            except RuleValidationError as exc:
                raise RuleValidationError(f"{key}[{i}]: {exc}") from exc
        return


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _uses_header_matches(condition: dict[str, Any]) -> bool:
    """Return True if *condition* (or any descendant) uses header_matches."""
    if "header_matches" in condition:
        return True
    for key in ("all", "any"):
        if key in condition:
            return any(_uses_header_matches(child) for child in condition[key])
    return False


def matches(condition: dict[str, Any], envelope: dict[str, Any]) -> bool:
    """Evaluate *condition* against *envelope*.

    Parameters
    ----------
    condition:
        A validated condition dict (leaf or combinator).
    envelope:
        ``{"from": str, "subject": str, "headers": dict[str, str]}``.

    Returns
    -------
    bool
        True if the condition matches the envelope.
    """
    from_val: str = str(envelope.get("from") or "").lower()
    subject_val: str = str(envelope.get("subject") or "").lower()
    headers: dict[str, str] = envelope.get("headers") or {}

    if "from_contains" in condition:
        needle: str = str(condition["from_contains"]).lower()
        return needle in from_val

    if "subject_contains" in condition:
        needle = str(condition["subject_contains"]).lower()
        return needle in subject_val

    if "list_id_contains" in condition:
        needle = str(condition["list_id_contains"]).lower()
        # headers keys are lower-cased by the capture pipeline.
        list_id_val = str(headers.get("list-id") or "").lower()
        return needle in list_id_val

    if "header_matches" in condition:
        hm = condition["header_matches"]
        header_name: str = str(hm.get("name") or "").lower()
        regex_pat: str = str(hm.get("regex") or "")
        header_val: str = str(headers.get(header_name) or "")
        return bool(re.search(regex_pat, header_val, re.IGNORECASE))

    if "all" in condition:
        return all(matches(child, envelope) for child in condition["all"])

    if "any" in condition:
        return any(matches(child, envelope) for child in condition["any"])

    # Unknown key — should not reach here after validation.
    return False


def uses_header_matches(condition: dict[str, Any]) -> bool:
    """Return True if *condition* (or any descendant) uses header_matches."""
    return _uses_header_matches(condition)


__all__ = [
    "matches",
    "uses_header_matches",
    "validate_condition",
]
