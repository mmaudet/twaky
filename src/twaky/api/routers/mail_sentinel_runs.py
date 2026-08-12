"""``GET /mail-sentinel/runs`` — recent mail-pipeline runs enriched with
spam-decision info.

Sits alongside the existing generic ``GET /sentinels/{name}/runs`` (which
returns the base ``sentinel_run`` row) and adds the fields most useful
when triaging what the mail sentinel did on a specific email:

- The linked spam decision (bucket + signal_source) when the email was
  classified by ``spam_triage``, joined on ``event_ref = email_id`` and
  ``decided_at ≈ started_at``.

Rules matching (``rule_name`` / ``matched_by``) and action list live in
the pipeline state, which is not persisted in ``sentinel_run.trace`` at
this time (the runtime defaults trace to ``[]``). Once the runtime
writes a per-node trace summary, this endpoint can surface it without
changing the API shape — trace fields default to null today.

Requires ``require_owner`` — same auth model as every other
``/mail-sentinel/*`` route.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from twaky.api.deps import require_owner
from twaky.sentinels import repository as sentinel_repo
from twaky.sentinels.mail.store import spam_decisions

router = APIRouter(prefix="/mail-sentinel/runs", tags=["mail-sentinel-runs"])


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class MailSentinelRun(BaseModel):
    """One mail_sentinel run row + resolved spam decision (if any)."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    email_id: str
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    outcome: str
    mission_id: UUID | None
    llm_calls: int
    error_repr: str | None
    # Enriched fields — null when the mail did not trip spam_triage.
    spam_bucket: str | None
    spam_signal_source: str | None
    spam_decision_id: UUID | None


# ---------------------------------------------------------------------------
# GET /mail-sentinel/runs
# ---------------------------------------------------------------------------


@router.get("", response_model=list[MailSentinelRun])
def list_mail_runs(
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    before: Annotated[datetime | None, Query()] = None,
    _email: str = Depends(require_owner),
) -> list[MailSentinelRun]:
    """List the N most recent mail-pipeline runs, newest first.

    Each row includes the base ``sentinel_run`` fields plus the linked
    ``mail_sentinel_spam_decision`` (bucket + signal_source) when the
    ``spam_triage`` node classified this email. Uses the ``event_ref``
    on the run (which is the JMAP email id) as the join key.
    """
    runs = sentinel_repo.list_runs(sentinel_name="mail", limit=limit, before=before)
    if not runs:
        return []

    # Batch-fetch the spam decisions for the email ids we just listed. Most
    # runs won't have one, but the lookup is a single query keyed by
    # email_id — cheaper than N per-run round-trips.
    email_ids = [r.event_ref for r in runs]
    decisions_by_email = _fetch_decisions_by_email(email_ids)

    out: list[MailSentinelRun] = []
    for r in runs:
        d = decisions_by_email.get(r.event_ref)
        out.append(
            MailSentinelRun(
                id=r.id,
                email_id=r.event_ref,
                started_at=r.started_at,
                completed_at=r.completed_at,
                duration_ms=r.duration_ms,
                outcome=r.outcome,
                mission_id=r.mission_id,
                llm_calls=r.llm_calls,
                error_repr=r.error_repr,
                spam_bucket=d.bucket if d else None,
                spam_signal_source=d.signal_source if d else None,
                spam_decision_id=d.id if d else None,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _fetch_decisions_by_email(
    email_ids: list[str],
) -> dict[str, spam_decisions.SpamDecision]:
    """Return ``email_id → most-recent SpamDecision`` for the given ids.

    Uses a single ``list_recent`` scan with a generous limit rather than
    N per-id ``get`` calls — the mail sentinel writes at most one
    decision per email so the mapping is 1-to-1.
    """
    if not email_ids:
        return {}
    # limit=500 matches the list_recent hard cap; 50 runs × ~1 decision
    # each fits comfortably.
    rows = spam_decisions.list_recent(limit=500)
    id_set = set(email_ids)
    result: dict[str, spam_decisions.SpamDecision] = {}
    for d in rows:
        if d.email_id in id_set and d.email_id not in result:
            result[d.email_id] = d
    return result


__all__ = ["MailSentinelRun", "router"]
