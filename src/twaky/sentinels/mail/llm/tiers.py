"""LLM tier registry for mail-sentinel use cases."""

from __future__ import annotations

from enum import Enum

from twaky.config import settings


class Tier(str, Enum):
    ECONOMY = "economy"
    DEFAULT = "default"
    CHAT = "chat"
    DRAFT = "draft"


class UseCase(str, Enum):
    MATCH_RULES_AI = "match_rules_ai"
    LEARN_PATTERN = "learn_pattern"
    THREAD_STATUS = "thread_status"
    SELECT_MEMORIES = "select_memories"
    EXTRACT_MEMORIES = "extract_memories"
    DRAFT_REPLY = "draft_reply"
    SPAM_CHECK = "spam_check"


_MAPPING: dict[UseCase, Tier] = {
    UseCase.MATCH_RULES_AI: Tier.CHAT,
    UseCase.LEARN_PATTERN: Tier.CHAT,
    UseCase.THREAD_STATUS: Tier.DEFAULT,
    UseCase.SELECT_MEMORIES: Tier.ECONOMY,
    UseCase.EXTRACT_MEMORIES: Tier.ECONOMY,
    UseCase.DRAFT_REPLY: Tier.DRAFT,
    UseCase.SPAM_CHECK: Tier.ECONOMY,
}


def tier_for(use_case: UseCase) -> Tier:
    """Return the LLM tier for the given use case.

    Raises ValueError if the use case is not mapped (defensive — startup
    tests walk all UseCase members to catch missing entries).
    """
    try:
        return _MAPPING[use_case]
    except KeyError:
        raise ValueError(f"No tier mapping for use case: {use_case!r}") from None


def models_for(tier: Tier) -> list[str]:
    """Return the ordered list of model strings configured for *tier*.

    Reads the corresponding ``mail_sentinel_<tier>_llms`` setting, splits on
    commas, strips whitespace, and filters empty entries.
    """
    field_name = f"mail_sentinel_{tier.value}_llms"
    raw: str = getattr(settings, field_name)
    return [m.strip() for m in raw.split(",") if m.strip()]


__all__ = ["_MAPPING", "Tier", "UseCase", "models_for", "tier_for"]
