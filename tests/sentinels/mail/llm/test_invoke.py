"""Tests for invoke.py — structured_call() contract."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from twaky.config import settings
from twaky.sentinels.mail.llm.hardening import Hardening
from twaky.sentinels.mail.llm.tiers import UseCase


class _S(BaseModel):
    v: str


# ---------------------------------------------------------------------------
# Guard: missing / wrong-type kwargs
# ---------------------------------------------------------------------------


def test_missing_hardening_kwarg_typerror() -> None:
    """Calling without keyword-only args raises TypeError (Python itself)."""
    from twaky.sentinels.mail.llm.invoke import structured_call

    with pytest.raises(TypeError):
        structured_call("x", _S)  # type: ignore[call-arg]


def test_hardening_must_be_enum() -> None:
    from twaky.sentinels.mail.llm.invoke import structured_call

    with pytest.raises(TypeError):
        structured_call(
            "x",
            _S,
            hardening="full",  # type: ignore[arg-type]
            use_case=UseCase.THREAD_STATUS,
        )


def test_use_case_must_be_enum() -> None:
    from twaky.sentinels.mail.llm.invoke import structured_call

    with pytest.raises(TypeError):
        structured_call(
            "x",
            _S,
            hardening=Hardening.NONE,
            use_case="wrong",  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_success_returns_schema_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ChatLiteLLM so .with_structured_output(...).invoke(...) returns _S."""
    from twaky.sentinels.mail.llm import invoke as invoke_mod

    expected = _S(v="ok")

    mock_chain = MagicMock()
    mock_chain.invoke.return_value = expected

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_chain

    MockChatLiteLLM = MagicMock(return_value=mock_llm)
    monkeypatch.setattr(invoke_mod, "ChatLiteLLM", MockChatLiteLLM)

    result = invoke_mod.structured_call(
        "classify this",
        _S,
        hardening=Hardening.COMPACT,
        use_case=UseCase.THREAD_STATUS,
    )

    assert isinstance(result, _S)
    assert result.v == "ok"


# ---------------------------------------------------------------------------
# Fallback chain
# ---------------------------------------------------------------------------


def test_fallback_on_primary_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the primary model raises, the second model in the list is tried."""
    from twaky.sentinels.mail.llm import invoke as invoke_mod

    primary = "openrouter/moonshotai/kimi-k2"
    fallback = "openrouter/anthropic/claude-haiku-4-5"

    monkeypatch.setattr(
        settings,
        "mail_sentinel_default_llms",
        f"{primary},{fallback}",
    )

    fallback_result = _S(v="fallback")

    def make_llm(model: str, **kwargs: object) -> MagicMock:
        llm = MagicMock()
        if model == primary:
            chain = MagicMock()
            chain.invoke.side_effect = RuntimeError("primary down")
            llm.with_structured_output.return_value = chain
        else:
            chain = MagicMock()
            chain.invoke.return_value = fallback_result
            llm.with_structured_output.return_value = chain
        return llm

    monkeypatch.setattr(invoke_mod, "ChatLiteLLM", make_llm)

    result = invoke_mod.structured_call(
        "classify",
        _S,
        hardening=Hardening.NONE,
        use_case=UseCase.THREAD_STATUS,
    )

    assert isinstance(result, _S)
    assert result.v == "fallback"
