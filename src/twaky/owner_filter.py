"""Dispatch that decides whether an event on a given exchange concerns
the owner of this twaky instance.

Applied at ingest time, BEFORE inserting into event_log — events that
don't concern the owner are ack'd and dropped silently (no DLQ, no
log noise, no storage cost).
"""

from __future__ import annotations

from collections.abc import Callable

_Matcher = Callable[[dict, str], bool]


def _match_calendar_event(payload: dict, owner: str) -> bool:
    org = (payload.get("organizer") or {}).get("email")
    if org == owner:
        return True
    for att in payload.get("attendees") or []:
        if isinstance(att, dict) and att.get("email") == owner:
            return True
    return False


def _match_sabre_contact(payload: dict, owner: str) -> bool:
    # Assumption to validate with a real payload: the contact's own
    # address book is the owner's, so `payload.email == owner`.
    return payload.get("email") == owner


def _match_mail(payload: dict, owner: str) -> bool:
    return payload.get("user") == owner


_RULES: dict[str, _Matcher] = {
    "calendar:event:": _match_calendar_event,
    "sabre:contact:": _match_sabre_contact,
    "mail:message:": _match_mail,
}


def matches_owner(exchange: str, payload: dict, owner_email: str) -> bool:
    """Return True iff the event concerns the owner. Unknown families → False."""
    for prefix, rule in _RULES.items():
        if exchange.startswith(prefix):
            return rule(payload, owner_email)
    return False


__all__ = ["matches_owner"]
