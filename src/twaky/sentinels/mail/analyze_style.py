"""SP7 / Task 141: writing-style profile analyzer.

Fetches recent Sent mails for an owner, sends them to the LLM,
and stores the resulting writing-style profile in
``mail_sentinel_style_profile``.

Trigger policy: run when ``current_sent_count - sent_count_at_compute
>= SENT_DELTA_THRESHOLD`` (default 50), OR when no profile exists yet
(bootstrap).
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from twaky.sentinels.mail.llm.hardening import Hardening
from twaky.sentinels.mail.llm.invoke import structured_call
from twaky.sentinels.mail.llm.tiers import UseCase
from twaky.sentinels.mail.prompts.analyze_style import analyze_style_prompt
from twaky.sentinels.mail.store import style_profile as sp_store

log = logging.getLogger(__name__)

SENT_DELTA_THRESHOLD: int = 50
SAMPLE_SIZE: int = 100


class StyleProfileOutput(BaseModel):
    """Wrapper — the LLM returns a single ``profile`` text field."""

    profile: str = Field(min_length=100)


def should_analyze(owner_email: str, current_sent_count: int) -> bool:
    """Return True if a fresh analysis should be triggered.

    Rules:
    - No existing profile → bootstrap → True.
    - current_sent_count - sent_count_at_compute >= SENT_DELTA_THRESHOLD → True.
    - Else → False.
    """
    existing = sp_store.get(owner_email)
    if existing is None:
        return True
    delta = current_sent_count - existing.sent_count_at_compute
    return delta >= SENT_DELTA_THRESHOLD


def _substantive_sample(sample: dict[str, Any]) -> bool:
    body = str(sample.get("body") or "").strip()
    if len(body) < 100:
        return False
    subject = str(sample.get("subject") or "")
    return not subject.lower().startswith(("auto:", "out-of-office", "vacation"))


def run_analysis(
    *,
    owner_email: str,
    display_name: str,
    current_sent_count: int,
    samples: list[dict[str, Any]],
    model_id: str | None = None,
) -> sp_store.StyleProfile | None:
    """Analyze *samples* via LLM and upsert the resulting profile.

    Returns the stored profile, or None on LLM failure.
    Filters samples to substantive replies (body >= 100 chars, not auto-replies).
    """
    substantive = [s for s in samples if _substantive_sample(s)]
    if not substantive:
        log.info("analyze_style: no substantive samples for %s", owner_email)
        return None

    prompt = analyze_style_prompt(
        owner_email=owner_email,
        display_name=display_name,
        samples=substantive,
    )
    try:
        out: StyleProfileOutput = structured_call(
            prompt,
            StyleProfileOutput,
            hardening=Hardening.COMPACT,
            use_case=UseCase.ANALYZE_STYLE,
        )
    except Exception as e:  # noqa: BLE001 — analysis is best-effort, never blocks caller
        log.warning("analyze_style: LLM failed for %s: %r", owner_email, e)
        return None

    return sp_store.upsert(
        owner_email=owner_email,
        profile=out.profile,
        sent_count_at_compute=current_sent_count,
        sample_size=len(substantive),
        model=model_id,
    )


__all__ = [
    "SAMPLE_SIZE",
    "SENT_DELTA_THRESHOLD",
    "StyleProfileOutput",
    "run_analysis",
    "should_analyze",
]
