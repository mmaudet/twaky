"""SP5c 5.1: periodic learned-pattern health check.

Purpose : detect learned patterns that have drifted (sender changed
behaviour, faux positif stabilisé early) and either bump their
``last_confirmed`` or decay/delete them.

Trigger : called weekly from the sentinel housekeeping loop
(``twaky.sentinels.runtime._housekeeping``).

Algorithm per tick :
1. Fetch up to N (default 5) ACTIVE patterns whose ``last_confirmed``
   is older than 7 days (oldest first).
2. For each pattern, ask the observer JMAP client for the most recent
   mail from that sender (last 30 days).
3. If no recent mail → skip (no fresh evidence to judge; try next week).
4. If a mail exists → call the ``confirm_pattern`` LLM prompt.
   - LLM confirms → ``mark_confirmed`` (bumps last_confirmed=now()).
   - LLM refutes → ``decay_confidence(factor=0.7)``. If confidence
     drops below 0.5 the pattern is DELETED entirely.

Best-effort : LLM failures are logged and skipped; the health check
never blocks ingest.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from twaky.sentinels.mail.llm.hardening import Hardening
from twaky.sentinels.mail.llm.invoke import structured_call
from twaky.sentinels.mail.llm.tiers import UseCase
from twaky.sentinels.mail.prompts.confirm_pattern import confirm_pattern_prompt
from twaky.sentinels.mail.store import learned_patterns as lp_store

log = logging.getLogger(__name__)

STALE_DAYS: int = 7
LOOKBACK_DAYS: int = 30
BATCH_LIMIT: int = 5


class PatternConfirmOutput(BaseModel):
    """LLM verdict on whether a learned pattern still fits a recent mail."""

    confirms: bool
    reason: str


async def _find_recent_mail_from_sender(
    adapter: Any, sender_email: str, lookback_days: int = LOOKBACK_DAYS
) -> dict[str, Any] | None:
    """Ask the observer JMAP client for the most recent mail from *sender_email*.

    Adapter contract: ``list_recent_emails_from(sender_email, since_days, limit)``
    OR falls back to ``list_recent_emails`` filtered client-side. Returns
    ``None`` when no mail found in the lookback window.
    """
    # Preferred path: dedicated method if adapter exposes it.
    if hasattr(adapter, "list_recent_emails_from"):
        try:
            mails = await adapter.list_recent_emails_from(
                sender_email=sender_email,
                since_days=lookback_days,
                limit=1,
            )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "pattern_health: list_recent_emails_from failed for %s: %r",
                sender_email,
                e,
            )
            return None
        return mails[0] if mails else None

    # Fallback: no dedicated method → return None (skip this pattern).
    log.debug(
        "pattern_health: adapter has no list_recent_emails_from; skipping %s",
        sender_email,
    )
    return None


def _verdict_for_pattern(
    email: dict[str, Any], rule_name: str
) -> PatternConfirmOutput | None:
    """Call the LLM to confirm/refute the pattern for this specific mail.

    Returns ``None`` on LLM failure (best-effort — health check never
    blocks on LLM errors).
    """
    prompt = confirm_pattern_prompt(email=email, rule_name=rule_name)
    try:
        return structured_call(
            prompt,
            PatternConfirmOutput,
            hardening=Hardening.COMPACT,
            use_case=UseCase.CONFIRM_PATTERN,
        )
    except Exception as e:  # noqa: BLE001
        log.warning(
            "pattern_health: LLM confirm_pattern failed for rule=%r: %r",
            rule_name,
            e,
        )
        return None


async def run_pattern_health_check(
    adapter: Any,
    *,
    stale_days: int = STALE_DAYS,
    lookback_days: int = LOOKBACK_DAYS,
    batch_limit: int = BATCH_LIMIT,
) -> dict[str, int]:
    """Run one pass of the pattern health check.

    Returns a stats dict:
      {"scanned", "confirmed", "decayed", "deleted", "skipped_no_mail",
       "skipped_llm_error"}
    """
    stats = {
        "scanned": 0,
        "confirmed": 0,
        "decayed": 0,
        "deleted": 0,
        "skipped_no_mail": 0,
        "skipped_llm_error": 0,
    }

    stale = lp_store.list_active_stale(days=stale_days, limit=batch_limit)
    stats["scanned"] = len(stale)
    if not stale:
        log.info(
            "pattern_health: no stale active patterns (age > %d days)",
            stale_days,
        )
        return stats

    log.info(
        "pattern_health: checking %d stale active patterns",
        stats["scanned"],
    )

    for pattern in stale:
        email = await _find_recent_mail_from_sender(
            adapter, pattern.sender_email, lookback_days
        )
        if email is None:
            stats["skipped_no_mail"] += 1
            log.info(
                "pattern_health: no recent mail from %s (lookback=%dd) → skip",
                pattern.sender_email,
                lookback_days,
            )
            continue

        verdict = _verdict_for_pattern(email, pattern.rule_name)
        if verdict is None:
            stats["skipped_llm_error"] += 1
            continue

        if verdict.confirms:
            lp_store.mark_confirmed(pattern.sender_email, pattern.rule_name)
            stats["confirmed"] += 1
            log.info(
                "pattern_health: CONFIRMED %s → %s (reason: %s)",
                pattern.sender_email,
                pattern.rule_name,
                verdict.reason[:120],
            )
        else:
            result = lp_store.decay_confidence(
                pattern.sender_email, pattern.rule_name
            )
            if result is None:
                stats["deleted"] += 1
                log.warning(
                    "pattern_health: DELETED %s → %s (below 0.5 after decay; reason: %s)",
                    pattern.sender_email,
                    pattern.rule_name,
                    verdict.reason[:120],
                )
            else:
                stats["decayed"] += 1
                log.info(
                    "pattern_health: DECAYED %s → %s (conf %s; reason: %s)",
                    pattern.sender_email,
                    pattern.rule_name,
                    result.confidence,
                    verdict.reason[:120],
                )

    log.info("pattern_health: done — %r", stats)
    return stats


__all__ = [
    "BATCH_LIMIT",
    "LOOKBACK_DAYS",
    "STALE_DAYS",
    "PatternConfirmOutput",
    "run_pattern_health_check",
]
