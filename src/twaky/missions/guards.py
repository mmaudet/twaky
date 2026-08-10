"""Pure state-machine for Mission transitions.

No DB, no I/O — just a static table + a check. Kept separate from
engine.py so it can be reused by anything that needs to reason about
transitions statically (validation, UI hints, docs).
"""

from __future__ import annotations

from twaky.missions.models import MissionState as S

_ALLOWED: dict[S, frozenset[S]] = {
    S.DECLARED: frozenset({S.PLANNING, S.AWAITING_USER, S.CANCELLED, S.FAILED}),
    S.PLANNING: frozenset({S.RUNNING, S.CANCELLED, S.FAILED}),
    S.RUNNING: frozenset({S.AWAITING_USER, S.DONE, S.FAILED, S.CANCELLED}),
    S.AWAITING_USER: frozenset({S.RUNNING, S.CANCELLED, S.FAILED}),
    S.DONE: frozenset(),
    S.FAILED: frozenset(),
    S.CANCELLED: frozenset(),
}


class InvalidTransition(Exception):
    """Raised when a caller tries an illegal Mission state transition."""


def check_transition(from_state: S, to_state: S) -> None:
    allowed = _ALLOWED.get(from_state, frozenset())
    if to_state not in allowed:
        raise InvalidTransition(
            f"illegal Mission transition: {from_state.value} → {to_state.value} "
            f"(allowed from {from_state.value}: "
            f"{sorted(s.value for s in allowed) or '∅'})"
        )


__all__ = ["InvalidTransition", "check_transition"]
