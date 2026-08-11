"""Unit tests for the mail-sentinel robustness helpers.

Covers:
- ``LLMCircuitBreaker`` state machine (closed → open → cool-off → probe).
- ``resilient_node`` wrapping: successful pass-through, exception trap,
  wall-time timeout.
- ``structured_call`` integration with the process-wide breaker: raises
  ``LLMCircuitOpen`` when open, records success/failure on the breaker.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from twaky.sentinels.mail.llm.hardening import Hardening
from twaky.sentinels.mail.llm.invoke import LLMCircuitOpen, structured_call
from twaky.sentinels.mail.llm.tiers import UseCase
from twaky.sentinels.mail.robustness import (
    LLMCircuitBreaker,
    get_llm_breaker,
    resilient_node,
)


@pytest.fixture(autouse=True)
def _reset_breaker() -> None:
    """The breaker is a process-wide singleton — reset before every test."""
    get_llm_breaker().reset()


# ---------------------------------------------------------------------------
# LLMCircuitBreaker
# ---------------------------------------------------------------------------


class TestLLMCircuitBreaker:
    def test_closed_by_default(self) -> None:
        b = LLMCircuitBreaker()
        assert b.should_skip() is False

    def test_opens_after_threshold_consecutive_failures(self) -> None:
        b = LLMCircuitBreaker(failure_threshold=3, cool_off_s=60.0)
        b.record_failure()
        b.record_failure()
        assert b.should_skip() is False  # still under threshold
        b.record_failure()  # third failure trips the breaker
        assert b.should_skip() is True

    def test_success_resets_and_closes(self) -> None:
        b = LLMCircuitBreaker(failure_threshold=3)
        b.record_failure()
        b.record_failure()
        b.record_success()
        # Counter reset — one more failure should not open the breaker
        b.record_failure()
        assert b.should_skip() is False

    def test_cool_off_elapses_then_probe_allowed(self) -> None:
        b = LLMCircuitBreaker(failure_threshold=2, cool_off_s=0.1)
        b.record_failure()
        b.record_failure()
        assert b.should_skip() is True
        # Wait for the cool-off to elapse
        time.sleep(0.15)
        # Probe: should_skip returns False (probe allowed through)
        assert b.should_skip() is False


# ---------------------------------------------------------------------------
# resilient_node — wall-time budget + exception trap
# ---------------------------------------------------------------------------


class TestResilientNode:
    def test_success_passes_through_output(self) -> None:
        def _node(state: dict) -> dict:
            return {"result": state["email_id"].upper()}

        wrapped = resilient_node("dummy", _node)
        out = wrapped({"email_id": "e1"})
        assert out == {"result": "E1"}

    def test_exception_caught_returns_empty_dict(self, caplog) -> None:
        def _crashing_node(state: dict) -> dict:
            raise ValueError("boom")

        wrapped = resilient_node("crashy", _crashing_node)
        out = wrapped({"email_id": "e1"})
        assert out == {}
        assert any(
            "crashy" in r.message and "exception" in r.message for r in caplog.records
        )

    def test_timeout_returns_empty_dict(self, caplog) -> None:
        def _slow_node(state: dict) -> dict:
            time.sleep(0.3)
            return {"result": "too late"}

        wrapped = resilient_node("slowly", _slow_node, timeout_s=0.1)
        out = wrapped({"email_id": "e1"})
        assert out == {}
        assert any(
            "slowly" in r.message and "TIMEOUT" in r.message for r in caplog.records
        )

    def test_wrapped_name_is_useful(self) -> None:
        def _n(state: dict) -> dict:
            return {}

        assert resilient_node("foo", _n).__name__ == "resilient_foo"


# ---------------------------------------------------------------------------
# structured_call integration with the breaker
# ---------------------------------------------------------------------------


class TestStructuredCallCircuitBreaker:
    def test_raises_llmcircuitopen_when_open(self, monkeypatch) -> None:
        # Force the breaker open
        b = get_llm_breaker()
        for _ in range(b.failure_threshold):
            b.record_failure()
        assert b.should_skip() is True

        # No LLM should be constructed — patch ChatLiteLLM to explode if called
        with (
            patch(
                "twaky.sentinels.mail.llm.invoke.ChatLiteLLM",
                side_effect=AssertionError("must not be called when breaker open"),
            ),
            pytest.raises(LLMCircuitOpen),
        ):
            structured_call(
                "hi", dict, hardening=Hardening.NONE, use_case=UseCase.SPAM_CHECK
            )

    def test_records_failure_on_all_models_failing(self, monkeypatch) -> None:
        """When every configured model raises, the breaker is incremented once."""
        b = get_llm_breaker()
        assert b._consecutive_failures == 0

        # Force a single-model list.
        monkeypatch.setattr(
            "twaky.sentinels.mail.llm.invoke.models_for",
            lambda tier: ["dummy-model"],
        )

        class _BoomLLM:
            def __init__(self, *_a, **_kw) -> None: ...
            def with_structured_output(self, _s):
                raise RuntimeError("upstream 503")

        with (
            patch("twaky.sentinels.mail.llm.invoke.ChatLiteLLM", _BoomLLM),
            pytest.raises(RuntimeError),
        ):
            structured_call(
                "hi", dict, hardening=Hardening.NONE, use_case=UseCase.SPAM_CHECK
            )

        assert b._consecutive_failures == 1
