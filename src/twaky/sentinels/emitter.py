"""MissionEmitter — thin wrapper that lets a sentinel park a mission for owner review.

Usage
-----
::

    from twaky.sentinels.emitter import MissionEmitter

    emitter = MissionEmitter("mail")
    mid = emitter.emit(
        intent_text="Mail: Q3 report from alice@example.com",
        reason="Incoming email needs owner attention",
        artifact={
            "kind": "sentinel_evidence",
            "sentinel": "mail",
            "evidence": {"email_id": "eml-42", "sender": "alice@example.com"},
            "hints": {"suggested_action": "reply"},
        },
    )

Artifact convention
-------------------
Sentinel callers should pass an artifact shaped like::

    {
        "kind": "sentinel_evidence",
        "sentinel": "<sentinel_name>",
        "evidence": {...},   # raw evidence data (email headers, cal event, etc.)
        "hints": {...},      # optional — suggested next actions for the owner
    }

The emitter does NOT enforce this schema; callers are free to extend it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from twaky.config import settings
from twaky.missions import engine


class MissionEmitter:
    """Emit a mission that immediately sits in ``awaiting_user`` state.

    Parameters
    ----------
    sentinel_name:
        Short identifier for the sentinel (e.g. ``"mail"``, ``"calendar"``).
        Becomes the ``declared_by`` prefix: ``"sentinel:<sentinel_name>"``.
    """

    def __init__(self, sentinel_name: str) -> None:
        self.sentinel_name = sentinel_name
        self.declared_by = f"sentinel:{sentinel_name}"

    def emit(
        self,
        *,
        intent_text: str,
        reason: str,
        artifact: dict[str, Any],
    ) -> UUID:
        """Create a mission and park it in AWAITING_USER in one call.

        Parameters
        ----------
        intent_text:
            Human-readable description of what needs to be done
            (e.g. the email subject or rule name).
        reason:
            Why the owner's attention is needed
            (stored as ``mission.state_reason``).
        artifact:
            Evidence dict appended to ``mission.artifacts``.
            Convention: use ``{"kind": "sentinel_evidence", "sentinel": ...,
            "evidence": {...}, "hints": {...}}``.

        Returns
        -------
        UUID
            The id of the newly created mission (state=AWAITING_USER).
        """
        m = engine.park_for_review(
            intent_text=intent_text,
            owner_email=settings.twaky_owner_email,
            declared_by=self.declared_by,
            reason=reason,
            artifact=artifact,
        )
        return m.id


__all__ = ["MissionEmitter"]
