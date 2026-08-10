"""Tests for tiers.py — Tier/UseCase enums, tier_for(), models_for()."""

from __future__ import annotations

import pytest

from twaky.config import settings
from twaky.sentinels.mail.llm.tiers import Tier, UseCase, models_for, tier_for


def test_every_use_case_has_a_tier() -> None:
    """Every UseCase member must have an entry in _MAPPING — no raises allowed."""
    for uc in UseCase:
        tier_for(uc)  # must not raise


def test_persistent_decisions_are_not_economy() -> None:
    """MATCH_RULES_AI and LEARN_PATTERN create persistent state; they must not
    be routed to the cheapest tier."""
    assert tier_for(UseCase.MATCH_RULES_AI) is not Tier.ECONOMY
    assert tier_for(UseCase.LEARN_PATTERN) is not Tier.ECONOMY


def test_draft_reply_is_draft_tier() -> None:
    assert tier_for(UseCase.DRAFT_REPLY) is Tier.DRAFT


def test_spam_check_is_economy_tier() -> None:
    """Assert UseCase.SPAM_CHECK maps to Tier.ECONOMY."""
    assert tier_for(UseCase.SPAM_CHECK) is Tier.ECONOMY


def test_models_for_parses_comma_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mail_sentinel_default_llms", "a/b,c/d, e/f ")
    assert models_for(Tier.DEFAULT) == ["a/b", "c/d", "e/f"]


def test_models_for_filters_empties(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mail_sentinel_economy_llms", "x/y,,z/w,")
    assert models_for(Tier.ECONOMY) == ["x/y", "z/w"]
