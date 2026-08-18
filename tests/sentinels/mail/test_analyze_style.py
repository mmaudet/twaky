"""Tests for the writing-style analyzer service."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from twaky.sentinels.mail import analyze_style as az
from twaky.sentinels.mail.analyze_style import StyleProfileOutput
from twaky.sentinels.mail.store import style_profile as sp_store

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _cleanup():
    from twaky.db import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_style_profile")
    yield
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_style_profile")


def test_should_analyze_true_when_no_profile():
    assert az.should_analyze("x@y.com", current_sent_count=10) is True


def test_should_analyze_false_when_delta_below_threshold():
    sp_store.upsert(
        owner_email="x@y.com",
        profile="p",
        sent_count_at_compute=100,
        sample_size=100,
    )
    assert az.should_analyze("x@y.com", current_sent_count=140) is False


def test_should_analyze_true_when_delta_at_threshold():
    sp_store.upsert(
        owner_email="x@y.com",
        profile="p",
        sent_count_at_compute=100,
        sample_size=100,
    )
    # threshold is 50
    assert az.should_analyze("x@y.com", current_sent_count=150) is True


def _make_samples(n: int) -> list[dict]:
    return [
        {
            "subject": f"Re: subject {i}",
            "body": (
                "Bonjour Alexandre,\n\nMerci pour ton message. Je regarde ça "
                "aujourd'hui et je reviens vers toi demain matin.\n\nBien à vous,\n\nMichel-Marie"
            ),
        }
        for i in range(n)
    ]


def test_run_analysis_stores_llm_output():
    output = StyleProfileOutput(
        profile="A very long generated writing-style profile that meets the 100 char minimum "
        "constraint imposed by the Pydantic schema."
    )
    with patch(
        "twaky.sentinels.mail.analyze_style.structured_call",
        return_value=output,
    ):
        stored = az.run_analysis(
            owner_email="michel@linagora.com",
            display_name="Michel-Marie",
            current_sent_count=200,
            samples=_make_samples(10),
            model_id="openai/mistral",
        )
    assert stored is not None
    assert stored.owner_email == "michel@linagora.com"
    assert stored.sample_size == 10
    assert stored.sent_count_at_compute == 200
    assert stored.model == "openai/mistral"
    assert "writing-style" in stored.profile


def test_run_analysis_skips_non_substantive_samples():
    output = StyleProfileOutput(
        profile="A very long generated writing-style profile that meets the 100 char minimum "
        "constraint imposed by the Pydantic schema."
    )
    trivial = [
        {"subject": "auto: out of office", "body": "I'm away."},
        {"subject": "Re: hi", "body": "ok"},
    ]
    with patch(
        "twaky.sentinels.mail.analyze_style.structured_call",
        return_value=output,
    ) as mock_llm:
        stored = az.run_analysis(
            owner_email="x@y.com",
            display_name="X",
            current_sent_count=100,
            samples=trivial + _make_samples(2),
        )
    assert stored is not None
    assert stored.sample_size == 2  # trivial dropped, 2 substantive kept
    # Verify the prompt fed to the LLM only had 2 samples
    call_prompt = mock_llm.call_args[0][0]
    assert "Sample 1" in call_prompt
    assert "Sample 2" in call_prompt
    assert "Sample 3" not in call_prompt


def test_run_analysis_returns_none_when_all_samples_trivial():
    stored = az.run_analysis(
        owner_email="x@y.com",
        display_name="X",
        current_sent_count=100,
        samples=[{"subject": "auto: away", "body": "away"}],
    )
    assert stored is None
    assert sp_store.get("x@y.com") is None


def test_run_analysis_returns_none_on_llm_failure():
    with patch(
        "twaky.sentinels.mail.analyze_style.structured_call",
        side_effect=RuntimeError("llm down"),
    ):
        stored = az.run_analysis(
            owner_email="x@y.com",
            display_name="X",
            current_sent_count=100,
            samples=_make_samples(10),
        )
    assert stored is None
    assert sp_store.get("x@y.com") is None
