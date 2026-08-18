"""Integration tests for the spam_triage pipeline node (SP6c T6).

Requires a live twaky-pg instance. Mark: pytest.mark.integration + skipif.
Set TWAKY_PG_HOST=172.27.0.33 to target the dev DB.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

import psycopg
import pytest

from twaky.config import settings
from twaky.sentinels.mail.adapter import InMemoryMailAdapter
from twaky.sentinels.mail.nodes import NodeContext, make_spam_triage
from twaky.sentinels.mail.schemas import SpamCheckOutput
from twaky.sentinels.mail.store import spam_decisions as sd_store

# ---------------------------------------------------------------------------
# Reachability helpers
# ---------------------------------------------------------------------------


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _wipe():
    """Delete all rows from mail_sentinel_spam_decision before/after each test.

    Guarded by TWAKY_ALLOW_DESTRUCTIVE_TESTS — see
    ``docs/superpowers/investigations/2026-08-12-spam-decision-purge.md``.
    """
    from tests._conftest_helpers import destructive_wipe_allowed, skip_reason

    if not destructive_wipe_allowed():
        pytest.skip(skip_reason())
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_spam_decision")
    yield
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_spam_decision")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(config_values: dict[str, Any] | None = None) -> NodeContext:
    """Build a NodeContext with MagicMock base + InMemoryMailAdapter.

    Parameters
    ----------
    config_values:
        Dict passed as sentinel_row.config_values.
        Defaults to {"spam_filter_enabled": True}.
    """
    if config_values is None:
        config_values = {"spam_filter_enabled": True}
    base = MagicMock()
    base.sentinel_row.config_values = config_values
    base.mission_emitter.emit = MagicMock()
    return NodeContext(
        base=base,
        mail=InMemoryMailAdapter(),
        owner_email="owner@example.com",
    )


def _email(
    email_id: str = "e1",
    *,
    keywords: dict[str, Any] | None = None,
    headers: list[dict[str, Any]] | None = None,
    subject: str = "Test email",
    sender: str = "sender@example.com",
    has_attachment: bool = False,
    preview: str = "Email body preview",
) -> dict[str, Any]:
    """Build a minimal email dict for testing."""
    return {
        "id": email_id,
        "threadId": "t1",
        "receivedAt": "2026-01-01T10:00:00Z",
        "from": [{"email": sender, "name": "Sender"}],
        "to": [{"email": "owner@example.com", "name": "Owner"}],
        "subject": subject,
        "preview": preview,
        "keywords": keywords or {},
        "headers": headers or [],
        "hasAttachment": has_attachment,
    }


def _state(email: dict[str, Any]) -> dict[str, Any]:
    """Build a minimal MailAgentState containing the email as the thread."""
    return {"email_id": email["id"], "thread": [email]}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSpamTriageGate:
    def test_disabled_when_config_flag_off(self) -> None:
        """Gate check: spam_filter_enabled=False → return {spam_bucket: None} immediately.

        No adapter side-effects, no spam_decisions insert, no LLM call.
        """
        ctx = _ctx(config_values={"spam_filter_enabled": False})
        email = _email()
        node = make_spam_triage(ctx)

        with (
            patch("twaky.sentinels.mail.nodes.structured_call") as mock_llm,
            patch("twaky.sentinels.mail.nodes.spam_decisions.insert") as mock_insert,
        ):
            result = node(_state(email))  # type: ignore[arg-type]

        assert result == {"spam_bucket": None}
        mock_llm.assert_not_called()
        mock_insert.assert_not_called()
        ctx.base.mission_emitter.emit.assert_not_called()
        # No keywords set on adapter
        assert ctx.mail._keywords == {}  # type: ignore[attr-defined]


class TestSpamTriageStage1:
    def test_stage1_junk_keyword_hard_archive(self) -> None:
        """Stage 1: $junk keyword present → bucket=spam, adapter side-effects, DB insert.

        adapter.label(__spam__) + adapter.set_keyword($junk, True) + spam_decisions.insert.
        signal_source must be rspamd_junk_keyword.
        """
        ctx = _ctx()
        email = _email(keywords={"$junk": True})
        node = make_spam_triage(ctx)

        result = node(_state(email))  # type: ignore[arg-type]

        assert result["spam_bucket"] == "spam"
        assert result["spam_decision_id"] is not None
        assert "label:__spam__" in result["actions_applied"]
        assert "keyword:$junk" in result["actions_applied"]

        # Verify adapter side effects
        adapter: InMemoryMailAdapter = ctx.mail  # type: ignore[assignment]
        assert "__spam__" in adapter._labels.get("e1", [])
        assert adapter._keywords.get("e1", {}).get("$junk") is True

        # Verify DB row
        decision = sd_store.get(result["spam_decision_id"])
        assert decision is not None
        assert decision.bucket == "spam"
        assert decision.signal_source == "rspamd_junk_keyword"
        assert decision.email_id == "e1"

        # Mission NOT emitted for spam
        ctx.base.mission_emitter.emit.assert_not_called()

    def test_stage1_nonjunk_pass_through(self) -> None:
        """Stage 1: nonjunk keyword present → pass-through (bucket=None).

        No adapter side-effects, no DB insert, no LLM call.
        """
        ctx = _ctx()
        email = _email(keywords={"nonjunk": True})
        node = make_spam_triage(ctx)

        with (
            patch("twaky.sentinels.mail.nodes.structured_call") as mock_llm,
            patch("twaky.sentinels.mail.nodes.spam_decisions.insert") as mock_insert,
        ):
            result = node(_state(email))  # type: ignore[arg-type]

        assert result == {"spam_bucket": None}
        mock_llm.assert_not_called()
        mock_insert.assert_not_called()
        ctx.base.mission_emitter.emit.assert_not_called()
        assert ctx.mail._keywords == {}  # type: ignore[attr-defined]


class TestSpamTriageStage2:
    def test_stage2_rspamd_reject_archives(self) -> None:
        """Stage 2: rspamd action=reject → bucket=spam.

        org.apache.james.rspamd.status header with action=reject triggers spam bucket.
        """
        ctx = _ctx()
        email = _email(
            headers=[
                {
                    "name": "org.apache.james.rspamd.status",
                    "value": "default: action=reject; score=15.0",
                }
            ]
        )
        node = make_spam_triage(ctx)

        result = node(_state(email))  # type: ignore[arg-type]

        assert result["spam_bucket"] == "spam"
        decision = sd_store.get(result["spam_decision_id"])  # type: ignore[arg-type]
        assert decision is not None
        assert decision.signal_source == "rspamd_status_reject"
        assert decision.bucket == "spam"

        adapter: InMemoryMailAdapter = ctx.mail  # type: ignore[assignment]
        assert adapter._keywords.get("e1", {}).get("$junk") is True

    def test_stage2_rspamd_greylist_triggers_llm(self) -> None:
        """Stage 2: rspamd action=greylist → grey_zone=True → LLM is called.

        The LLM returns bucket=none → final result is pass-through.
        """
        ctx = _ctx()
        email = _email(
            headers=[
                {
                    "name": "org.apache.james.rspamd.status",
                    "value": "default: action=greylist; score=5.0",
                }
            ]
        )
        node = make_spam_triage(ctx)

        llm_output = SpamCheckOutput(bucket="none", confidence=0.3, reason="uncertain")

        with patch(
            "twaky.sentinels.mail.nodes.structured_call", return_value=llm_output
        ) as mock_llm:
            result = node(_state(email))  # type: ignore[arg-type]

        mock_llm.assert_called_once()
        assert result == {"spam_bucket": None}


class TestSpamTriageRspamdParser:
    """Regex + score parser for org.apache.james.rspamd.status.

    James emits ``actions=<action> score=<X> requiredScore=<Y>`` (plural
    ``actions``), which the pre-fix ``action=`` regex missed silently —
    Stage 2 never fired, and the pipeline pass-throughed every mail.
    See 2026-08-12 spam-flow diagnosis.
    """

    def test_parses_actions_plural_from_james(self) -> None:
        """James's actual header: ``No, actions=greylist score=5.4 requiredScore=12.0``."""
        from twaky.sentinels.mail.nodes import _parse_rspamd_status

        headers = [
            {
                "name": "org.apache.james.rspamd.status",
                "value": "No, actions=greylist score=5.47559 requiredScore=12.0",
            }
        ]
        assert _parse_rspamd_status(headers) == "greylist"

    def test_parses_action_singular_backward_compat(self) -> None:
        """Legacy pre-James format ``action=<x>; score=<y>`` still parses."""
        from twaky.sentinels.mail.nodes import _parse_rspamd_status

        headers = [
            {
                "name": "org.apache.james.rspamd.status",
                "value": "default: action=reject; score=15.0",
            }
        ]
        assert _parse_rspamd_status(headers) == "reject"

    def test_parses_no_action_multi_word(self) -> None:
        """``actions=no action`` — two-word action, must not eat ``score=`` after."""
        from twaky.sentinels.mail.nodes import _parse_rspamd_status

        headers = [
            {
                "name": "org.apache.james.rspamd.status",
                "value": "No, actions=no action score=1.299169 requiredScore=12.0",
            }
        ]
        assert _parse_rspamd_status(headers) == "no action"

    def test_score_parser_extracts_float(self) -> None:
        from twaky.sentinels.mail.nodes import _parse_rspamd_score

        headers = [
            {
                "name": "org.apache.james.rspamd.status",
                "value": "No, actions=no action score=5.47559 requiredScore=12.0",
            }
        ]
        assert _parse_rspamd_score(headers) == pytest.approx(5.47559)

    def test_score_parser_negative(self) -> None:
        from twaky.sentinels.mail.nodes import _parse_rspamd_score

        headers = [
            {
                "name": "org.apache.james.rspamd.status",
                "value": "No, actions=no action score=-0.8 requiredScore=12.0",
            }
        ]
        assert _parse_rspamd_score(headers) == pytest.approx(-0.8)

    def test_score_parser_returns_none_when_missing(self) -> None:
        from twaky.sentinels.mail.nodes import _parse_rspamd_score

        assert _parse_rspamd_score([]) is None
        assert (
            _parse_rspamd_score(
                [{"name": "org.apache.james.rspamd.status", "value": "malformed"}]
            )
            is None
        )


class TestSpamTriageScoreOverride:
    """Score-aware grey-zone override: if rspamd score >= threshold, force LLM
    even when James set ``nonjunk``. Motivated by the 2026-08-12 diagnosis:
    James's ``requiredScore=12`` makes ``nonjunk`` an unreliable ham signal.
    """

    def test_high_score_with_nonjunk_forces_llm_grey_zone(self) -> None:
        """nonjunk=True + rspamd score=5.4 (>= 3.0) → LLM called."""
        ctx = _ctx()
        email = _email(
            keywords={"nonjunk": True},
            headers=[
                {
                    "name": "org.apache.james.rspamd.status",
                    "value": "No, actions=no action score=5.4 requiredScore=12.0",
                }
            ],
        )
        node = make_spam_triage(ctx)

        # LLM says newsletter with 0.85 confidence — final bucket=newsletter
        llm_out = SpamCheckOutput(
            bucket="newsletter", confidence=0.85, reason="marketing"
        )
        with patch(
            "twaky.sentinels.mail.nodes.structured_call", return_value=llm_out
        ) as mock_llm:
            result = node(_state(email))  # type: ignore[arg-type]

        mock_llm.assert_called_once()
        assert result["spam_bucket"] == "newsletter"

    def test_low_score_with_nonjunk_still_passes_through(self) -> None:
        """nonjunk=True + rspamd score=1.3 (< 3.0) → pass-through, no LLM."""
        ctx = _ctx()
        email = _email(
            keywords={"nonjunk": True},
            headers=[
                {
                    "name": "org.apache.james.rspamd.status",
                    "value": "No, actions=no action score=1.3 requiredScore=12.0",
                }
            ],
        )
        node = make_spam_triage(ctx)

        with (
            patch("twaky.sentinels.mail.nodes.structured_call") as mock_llm,
            patch("twaky.sentinels.mail.nodes.spam_decisions.insert") as mock_insert,
        ):
            result = node(_state(email))  # type: ignore[arg-type]

        assert result == {"spam_bucket": None}
        mock_llm.assert_not_called()
        mock_insert.assert_not_called()

    def test_no_rspamd_status_with_nonjunk_passes_through(self) -> None:
        """nonjunk=True + no rspamd status header at all → pass-through.

        Preserves the SP6c behaviour for accounts without rspamd
        integration.
        """
        ctx = _ctx()
        email = _email(keywords={"nonjunk": True})
        node = make_spam_triage(ctx)

        with (
            patch("twaky.sentinels.mail.nodes.structured_call") as mock_llm,
            patch("twaky.sentinels.mail.nodes.spam_decisions.insert") as mock_insert,
        ):
            result = node(_state(email))  # type: ignore[arg-type]

        assert result == {"spam_bucket": None}
        mock_llm.assert_not_called()
        mock_insert.assert_not_called()

    def test_high_score_without_nonjunk_forces_llm_via_grey_zone(self) -> None:
        """No keyword at all + rspamd score=5.4 → LLM called."""
        ctx = _ctx()
        email = _email(
            keywords={},
            headers=[
                {
                    "name": "org.apache.james.rspamd.status",
                    "value": "No, actions=no action score=5.4 requiredScore=12.0",
                }
            ],
        )
        node = make_spam_triage(ctx)

        llm_out = SpamCheckOutput(bucket="none", confidence=0.6, reason="uncertain")
        with patch(
            "twaky.sentinels.mail.nodes.structured_call", return_value=llm_out
        ) as mock_llm:
            result = node(_state(email))  # type: ignore[arg-type]

        mock_llm.assert_called_once()
        # LLM below threshold → still pass-through
        assert result == {"spam_bucket": None}


class TestHeuristicImprovements:
    """2026-08-13: loosened list-unsub + added TLD/random-local signals.

    Based on 256-mail INBOX audit finding ~20 newsletters had only single
    list-unsubscribe (not list-unsubscribe-post), and ~15 cold-outreach
    mails came from suspicious TLDs (.click, .homes, .online, .info)
    with random-consonant local parts (bot-generated).
    """

    def test_single_list_unsub_triggers_newsletter_signal(self) -> None:
        """Only list-unsubscribe (no -post) → newsletter_signal=True.

        Regression: SP6c required BOTH headers, missing legit newsletters
        that send only the single header.
        """
        from twaky.sentinels.mail.nodes import _header_heuristic_score

        result = _header_heuristic_score(
            {
                "from": [{"email": "news@example.com"}],
                "headers": [
                    {"name": "list-unsubscribe", "value": "<mailto:u@e.com>"},
                    {"name": "dkim-signature", "value": "v=1; a=rsa-sha256"},
                ],
            }
        )
        assert result.newsletter_signal is True
        assert result.summary["list_unsubscribe"] is True
        assert result.summary["list_unsubscribe_post"] is False

    def test_suspicious_tld_adds_score(self) -> None:
        """TLD in _SUSPICIOUS_TLDS → +2 to total_score."""
        from twaky.sentinels.mail.nodes import _header_heuristic_score

        result = _header_heuristic_score(
            {
                "from": [{"email": "sender@shady.click"}],
                "headers": [
                    {"name": "dkim-signature", "value": "v=1"},
                ],
            }
        )
        # +2 for .click TLD only (dkim present, no list-unsub, no random local)
        assert result.total_score == 2
        assert result.summary["suspicious_tld"] is True

    def test_random_local_part_adds_score(self) -> None:
        """Sender local part matching /^[bcdfghjklmnpqrstvwxz]{5,10}$/ → +2."""
        from twaky.sentinels.mail.nodes import _header_heuristic_score

        result = _header_heuristic_score(
            {
                "from": [{"email": "oltiwbr@somedomain.com"}],
                "headers": [
                    {"name": "dkim-signature", "value": "v=1"},
                ],
            }
        )
        # +2 for random-consonant local (dkim present, no list-unsub,
        # legit TLD)
        assert result.total_score == 2
        assert result.summary["random_local"] is True

    def test_random_local_helper_true_cases(self) -> None:
        from twaky.sentinels.mail.nodes import _is_random_local_part

        # Real bot samples from the 2026-08-13 INBOX audit.
        for local in ("oltiwbr", "eclybnm", "ecbarmd"):
            assert _is_random_local_part(local), f"expected random: {local}"

    def test_random_local_helper_false_cases(self) -> None:
        from twaky.sentinels.mail.nodes import _is_random_local_part

        # Real user local parts must NOT match (no 4-consonant cluster AND
        # vowel ratio > 30%).
        for local in (
            "hello",  # 40% vowels, no cluster
            "michel",  # 33% vowels, no cluster
            "abc",  # too short (< 5)
            "userverylong",  # too long (> 10)
            "user123",  # has digits
            "michel.maudet",  # has dot
            "team",  # too short
            "support",  # 28% vowels but no 4-consonant cluster
            "pradise",  # 43% vowels, no cluster
            "galaxo",  # 50% vowels, no cluster
        ):
            assert not _is_random_local_part(local), f"expected NOT random: {local}"

    def test_suspicious_tld_helper(self) -> None:
        from twaky.sentinels.mail.nodes import _has_suspicious_tld

        for dom in (
            "shady.click",
            "sub.spammy.homes",
            "foo.online",
            "bar.info",
            "grabber.tk",
        ):
            assert _has_suspicious_tld(dom), f"expected suspicious: {dom}"
        for dom in (
            "linagora.com",
            "github.com",
            "google.com",
            "example.fr",
        ):
            assert not _has_suspicious_tld(dom), f"expected clean: {dom}"

    def test_cold_outreach_click_tld_reaches_grey_min(self) -> None:
        """.click TLD + bot-random local + missing DKIM → grey-zone.

        Uses ``oltiwbr`` (real bot from 2026-08-13 UAT). ``pradise`` and
        similar word-shaped locals are intentionally not flagged as
        random — the TLD signal handles them.
        """
        from twaky.sentinels.mail.nodes import _header_heuristic_score

        result = _header_heuristic_score(
            {
                "from": [{"email": "oltiwbr@spammy.click"}],
                "headers": [],  # no DKIM
            }
        )
        # +2 .click + +2 random local + +3 no DKIM = 7 (well above 4)
        assert result.total_score >= 4
        assert result.summary["suspicious_tld"] is True
        assert result.summary["random_local"] is True


class TestBrandImpersonation:
    """Brand-impersonation heuristic (2026-08-13): detects paypal/bank/carrier
    mention combined with a mismatched sender domain OR urgency phrasing,
    and forces the LLM grey-zone even for high-DKIM / low-rspamd mail.

    Motivated by the persistent false-negative on ``service@updates.paypal.com
    · Votre compte PayPal sera fermé`` — DKIM valid + domain looks like
    paypal but the pattern is textbook phishing.
    """

    def test_paypal_urgency_pattern_fires(self) -> None:
        """PayPal legit-looking domain + urgency phrase → impersonation signal."""
        from twaky.sentinels.mail.nodes import _brand_impersonation_signal

        email = {
            "from": [{"email": "service@updates.paypal.com", "name": "PayPal"}],
            "subject": "Votre compte PayPal sera fermé",
            "preview": "Merci de confirmer vos informations sous 24h.",
        }
        fires, reason = _brand_impersonation_signal(email)
        assert fires is True
        assert "paypal" in reason
        assert "sera fermé" in reason

    def test_paypal_from_wrong_domain_fires(self) -> None:
        """PayPal brand mention + non-paypal sender domain → impersonation."""
        from twaky.sentinels.mail.nodes import _brand_impersonation_signal

        email = {
            "from": [{"email": "security@random-alert.tk", "name": "PayPal"}],
            "subject": "Account security notice",
            "preview": "",
        }
        fires, reason = _brand_impersonation_signal(email)
        assert fires is True
        assert "domain mismatch" in reason

    def test_amazon_legit_domain_no_urgency_does_not_fire(self) -> None:
        """Real Amazon order confirmation → no impersonation signal."""
        from twaky.sentinels.mail.nodes import _brand_impersonation_signal

        email = {
            "from": [{"email": "auto-confirm@amazon.fr", "name": "Amazon.fr"}],
            "subject": "Votre commande a été expédiée",
            "preview": "Votre colis arrivera demain.",
        }
        fires, _ = _brand_impersonation_signal(email)
        assert fires is False

    def test_no_brand_mention_does_not_fire(self) -> None:
        from twaky.sentinels.mail.nodes import _brand_impersonation_signal

        email = {
            "from": [{"email": "team@example.com", "name": "Random Team"}],
            "subject": "Quick question",
            "preview": "Hello, can we chat?",
        }
        fires, _ = _brand_impersonation_signal(email)
        assert fires is False

    def test_bank_urgency_fires(self) -> None:
        """BNP brand mention + urgency phrasing → impersonation."""
        from twaky.sentinels.mail.nodes import _brand_impersonation_signal

        email = {
            "from": [{"email": "alert@bnp-secure.info", "name": "BNP"}],
            "subject": "Action requise sur votre compte",
            "preview": "Vérifier votre compte immédiatement.",
        }
        fires, _reason = _brand_impersonation_signal(email)
        assert fires is True

    def test_delivery_scam_fires(self) -> None:
        """Colissimo mention on non-Colissimo domain → impersonation."""
        from twaky.sentinels.mail.nodes import _brand_impersonation_signal

        email = {
            "from": [{"email": "no-reply@random.tk", "name": "Colissimo"}],
            "subject": "Notification de livraison — colis bloqué",
            "preview": "Cliquez ici pour re-programmer la livraison.",
        }
        fires, _reason = _brand_impersonation_signal(email)
        assert fires is True

    def test_subdomain_of_legit_matches(self) -> None:
        """``updates.paypal.com`` is a subdomain of ``paypal.com`` → domain OK.

        Without urgency, no signal (domain OK, no urgency).
        """
        from twaky.sentinels.mail.nodes import _brand_impersonation_signal

        email = {
            "from": [{"email": "news@updates.paypal.com", "name": "PayPal"}],
            "subject": "Nouvelle promotion PayPal",
            "preview": "Découvrez nos avantages.",
        }
        fires, _ = _brand_impersonation_signal(email)
        assert fires is False

    def test_pipeline_wires_brand_signal_into_grey_zone(self) -> None:
        """Full pipeline: brand impersonation forces LLM call and taints reason."""
        ctx = _ctx()
        email = _email(
            keywords={"nonjunk": True},  # rspamd trusts it, would normally pass
            headers=[
                {"name": "dkim-signature", "value": "v=1; a=rsa-sha256"},
                {
                    "name": "org.apache.james.rspamd.status",
                    "value": "No, actions=no action score=0.0 requiredScore=12.0",
                },
            ],
            sender="service@updates.paypal.com",
            subject="Votre compte PayPal sera fermé",
            preview="Merci de confirmer vos informations sous 24h.",
        )
        node = make_spam_triage(ctx)

        # LLM returns phishing-alert with high confidence
        llm_out = SpamCheckOutput(
            bucket="phishing-alert",
            confidence=0.90,
            reason="account-closure urgency pattern",
        )
        with patch(
            "twaky.sentinels.mail.nodes.structured_call", return_value=llm_out
        ) as mock_llm:
            result = node(_state(email))  # type: ignore[arg-type]

        # Brand signal forced LLM despite nonjunk + score 0 + DKIM
        mock_llm.assert_called_once()
        # LLM prompt should carry the brand signal for context
        prompt_arg = mock_llm.call_args[0][0]
        assert "brand_impersonation" in prompt_arg
        assert result["spam_bucket"] == "phishing-alert"


class TestColdOutreachSignal:
    """Cold-outreach / lead-generation / SEO-spam pattern detection.

    Motivated by 2026-08-13 UAT: 4 spammy mails (Nezri recruitment,
    Sara Green SEO, Emma Pearson Dreamforce, Burkhard Berger link
    building) all had rspamd nonjunk=True + score < 3 + no list-unsub
    → passed through with no signal at all. The content-based detector
    forces the LLM grey-zone on these patterns.
    """

    def test_recruitment_profil_disponible_fires(self) -> None:
        from twaky.sentinels.mail.nodes import _cold_outreach_signal

        email = {
            "subject": "Présentation d'un profil disponible – Technicien Support IT",
            "preview": "Bonjour, je vous propose le profil d'Émilien...",
        }
        fires, reason = _cold_outreach_signal(email)
        assert fires
        assert "profil disponible" in reason

    def test_attendees_list_fires(self) -> None:
        from twaky.sentinels.mail.nodes import _cold_outreach_signal

        email = {
            "subject": "Attendees at Dreamforce Conference 2026",
            "preview": "Hi there, I hope you are doing well.",
        }
        fires, _reason = _cold_outreach_signal(email)
        assert fires

    def test_seo_traffic_fires(self) -> None:
        from twaky.sentinels.mail.nodes import _cold_outreach_signal

        email = {
            "subject": "Re: Increase Your Web Traffic..",
            "preview": (
                "Hi there, Just following up on my last email regarding "
                "a few website improvements..."
            ),
        }
        fires, _reason = _cold_outreach_signal(email)
        assert fires

    def test_link_building_mind_if_i_mention_fires(self) -> None:
        from twaky.sentinels.mail.nodes import _cold_outreach_signal

        email = {
            "subject": "Michel-Marie, 5 days",
            "preview": (
                "Hey Michel-Marie, Mind if I mention (and link to) Linagora "
                "in my upcoming blog post?"
            ),
        }
        fires, _reason = _cold_outreach_signal(
            email, owner_email="mmaudet@linagora.com"
        )
        assert fires

    def test_personalization_first_name_comma_subject_fires(self) -> None:
        """Subject starting with owner first name + comma is a personalization trick."""
        from twaky.config import settings
        from twaky.sentinels.mail.nodes import _cold_outreach_signal

        # Simulate the config having twaky_owner_name = Michel-Marie
        original = settings.twaky_owner_name
        try:
            settings.twaky_owner_name = "Michel-Marie Maudet"
            email = {"subject": "Michel-Marie, quick question", "preview": ""}
            fires, reason = _cold_outreach_signal(
                email, owner_email="michel.maudet@linagora.com"
            )
            assert fires
            assert "first name" in reason
        finally:
            settings.twaky_owner_name = original

    def test_re_prefix_does_not_fire_personalization(self) -> None:
        """Legit thread continuations start with Re: — should not fire."""
        from twaky.config import settings
        from twaky.sentinels.mail.nodes import _cold_outreach_signal

        original = settings.twaky_owner_name
        try:
            settings.twaky_owner_name = "Michel-Marie Maudet"
            email = {
                "subject": "Re: Michel-Marie's proposal is approved",
                "preview": "great news",
            }
            fires, _ = _cold_outreach_signal(
                email, owner_email="michel.maudet@linagora.com"
            )
            assert fires is False
        finally:
            settings.twaky_owner_name = original

    def test_legit_business_email_does_not_fire(self) -> None:
        from twaky.sentinels.mail.nodes import _cold_outreach_signal

        email = {
            "subject": "Contrat signé — merci",
            "preview": "Bonjour Michel, contrat renvoyé signé en pièce jointe.",
        }
        fires, _ = _cold_outreach_signal(email)
        assert fires is False

    def test_full_pipeline_forces_llm_on_cold_outreach(self) -> None:
        """End-to-end: nonjunk + score 0 + cold-outreach pattern → LLM called."""
        ctx = _ctx()
        email = _email(
            keywords={"nonjunk": True},
            headers=[
                {"name": "dkim-signature", "value": "v=1; a=rsa-sha256"},
                {
                    "name": "org.apache.james.rspamd.status",
                    "value": "No, actions=no action score=0.5 requiredScore=12.0",
                },
            ],
            sender="cold@outreach.com",
            subject="Attendees at Dreamforce Conference 2026",
            preview="Hi there, I hope you are doing well.",
        )
        node = make_spam_triage(ctx)

        llm_out = SpamCheckOutput(bucket="spam", confidence=0.85, reason="cold pitch")
        with patch(
            "twaky.sentinels.mail.nodes.structured_call", return_value=llm_out
        ) as mock_llm:
            result = node(_state(email))  # type: ignore[arg-type]

        mock_llm.assert_called_once()
        assert result["spam_bucket"] == "spam"


class TestSpamTriageStage3:
    def test_stage3_newsletter_heuristic_labels(self) -> None:
        """Stage 3: list-unsubscribe present + score < 5 → bucket=newsletter, no LLM.

        Only list-unsubscribe headers → score=2 (< 5) + newsletter_signal=True
        → heuristic_newsletter bucket without LLM call.
        """
        ctx = _ctx()
        # DKIM present to keep score low (only +2 from list-unsub)
        email = _email(
            headers=[
                {"name": "list-unsubscribe", "value": "<mailto:unsub@newsletter.com>"},
                {
                    "name": "list-unsubscribe-post",
                    "value": "List-Unsubscribe=One-Click",
                },
                {"name": "dkim-signature", "value": "v=1; a=rsa-sha256; ..."},
            ],
            sender="news@newsletter.com",
        )
        node = make_spam_triage(ctx)

        with patch("twaky.sentinels.mail.nodes.structured_call") as mock_llm:
            result = node(_state(email))  # type: ignore[arg-type]

        mock_llm.assert_not_called()
        # Per spec §5.3: newsletter returns only {spam_bucket, spam_decision_id}
        assert result["spam_bucket"] == "newsletter"
        assert result["spam_decision_id"] is not None
        assert "actions_applied" not in result, (
            "newsletter return dict must NOT include actions_applied (spec §5.3)"
        )
        # Exactly two keys
        assert set(result.keys()) == {"spam_bucket", "spam_decision_id"}

        decision = sd_store.get(result["spam_decision_id"])  # type: ignore[arg-type]
        assert decision is not None
        assert decision.signal_source == "heuristic_newsletter"
        assert decision.bucket == "newsletter"

        adapter: InMemoryMailAdapter = ctx.mail  # type: ignore[assignment]
        assert "newsletter" in adapter._labels.get("e1", [])
        assert adapter._keywords.get("e1", {}).get("nonjunk") is True
        ctx.base.mission_emitter.emit.assert_not_called()

    def test_stage3_dkim_absent_returnpath_mismatch_triggers_grey(self) -> None:
        """Stage 3: DKIM absent + return-path mismatch → score >= 4 → LLM called.

        Score: +3 (no DKIM) + +3 (return-path mismatch) = 6 → grey_zone=True → LLM.
        LLM returns bucket=none → pass-through.
        """
        ctx = _ctx()
        email = _email(
            sender="real@legit.com",
            headers=[
                # No dkim-signature header → +3
                # return-path differs from from domain → +3
                {"name": "return-path", "value": "<bounce@phishing-domain.com>"},
            ],
        )
        node = make_spam_triage(ctx)

        llm_output = SpamCheckOutput(bucket="none", confidence=0.4, reason="uncertain")

        with patch(
            "twaky.sentinels.mail.nodes.structured_call", return_value=llm_output
        ) as mock_llm:
            result = node(_state(email))  # type: ignore[arg-type]

        mock_llm.assert_called_once()
        assert result == {"spam_bucket": None}


class TestSpamTriageStage4:
    def test_stage4_llm_below_threshold_pass_through(self) -> None:
        """Stage 4: LLM returns confidence=0.60 for spam → below 0.85 → bucket=None.

        Safety bias: uncertain LLM output must not archive mail.
        """
        ctx = _ctx()
        email = _email(
            headers=[
                {
                    "name": "org.apache.james.rspamd.status",
                    "value": "default: action=greylist; score=5.0",
                }
            ]
        )
        node = make_spam_triage(ctx)

        llm_output = SpamCheckOutput(
            bucket="spam", confidence=0.60, reason="looks spammy but uncertain"
        )

        with (
            patch(
                "twaky.sentinels.mail.nodes.structured_call", return_value=llm_output
            ) as mock_llm,
            patch("twaky.sentinels.mail.nodes.spam_decisions.insert") as mock_insert,
        ):
            result = node(_state(email))  # type: ignore[arg-type]

        mock_llm.assert_called_once()
        mock_insert.assert_not_called()
        assert result == {"spam_bucket": None}
        ctx.base.mission_emitter.emit.assert_not_called()

    def test_stage4_llm_phishing_above_threshold_emits_mission(self) -> None:
        """Stage 4: LLM returns phishing-alert + confidence=0.92 → mission emitted.

        Only phishing-alert bucket triggers mission_emitter.emit per spec §5.3.
        """
        ctx = _ctx()
        email = _email(
            headers=[
                {
                    "name": "org.apache.james.rspamd.status",
                    "value": "default: action=add header; score=7.0",
                }
            ],
            subject="Your account is at risk — verify now",
            sender="security@evil-clone.com",
        )
        node = make_spam_triage(ctx)

        llm_output = SpamCheckOutput(
            bucket="phishing-alert",
            confidence=0.92,
            reason="credential harvesting attempt",
        )

        with patch(
            "twaky.sentinels.mail.nodes.structured_call", return_value=llm_output
        ):
            result = node(_state(email))  # type: ignore[arg-type]

        assert result["spam_bucket"] == "phishing-alert"
        assert result["spam_decision_id"] is not None

        decision = sd_store.get(result["spam_decision_id"])
        assert decision is not None
        assert decision.bucket == "phishing-alert"
        assert decision.signal_source == "llm_grey_zone"

        ctx.base.mission_emitter.emit.assert_called_once()
        call_kwargs = ctx.base.mission_emitter.emit.call_args
        artifact = call_kwargs.kwargs.get("artifact") or call_kwargs[1].get("artifact")
        assert artifact["kind"] == "phishing_alert"

    def test_stage4_llm_newsletter_lower_threshold_accepts(self) -> None:
        """Stage 4: LLM returns newsletter + confidence=0.75 → accepted (threshold 0.70).

        Newsletter threshold is 0.70, lower than spam/phishing-alert (0.85).
        """
        ctx = _ctx()
        email = _email(
            headers=[
                {
                    "name": "org.apache.james.rspamd.status",
                    "value": "default: action=greylist; score=4.0",
                }
            ]
        )
        node = make_spam_triage(ctx)

        llm_output = SpamCheckOutput(
            bucket="newsletter",
            confidence=0.75,
            reason="newsletter with low-quality signals",
        )

        with patch(
            "twaky.sentinels.mail.nodes.structured_call", return_value=llm_output
        ):
            result = node(_state(email))  # type: ignore[arg-type]

        assert result["spam_bucket"] == "newsletter"
        decision = sd_store.get(result["spam_decision_id"])  # type: ignore[arg-type]
        assert decision is not None
        assert decision.bucket == "newsletter"
        assert decision.signal_source == "llm_grey_zone"
        assert decision.score is not None
        assert abs(decision.score - 0.75) < 0.001

        adapter: InMemoryMailAdapter = ctx.mail  # type: ignore[assignment]
        assert "newsletter" in adapter._labels.get("e1", [])
        assert adapter._keywords.get("e1", {}).get("nonjunk") is True
        ctx.base.mission_emitter.emit.assert_not_called()


class TestSpamTriageLlmNotCalledWhenNoGreyZone:
    def test_llm_never_called_when_no_grey_zone(self) -> None:
        """Stage 1 $junk hit → short-circuit before LLM is ever called.

        Asserts that structured_call is never invoked when Stage 1 resolves
        the bucket definitively.
        """
        ctx = _ctx()
        email = _email(keywords={"$junk": True})
        node = make_spam_triage(ctx)

        with patch("twaky.sentinels.mail.nodes.structured_call") as mock_llm:
            result = node(_state(email))  # type: ignore[arg-type]

        mock_llm.assert_not_called()
        assert result["spam_bucket"] == "spam"


class TestNewsletterReturnShape:
    def test_newsletter_bucket_return_omits_actions_applied(self) -> None:
        """Newsletter bucket return dict must match spec §5.3 exactly.

        Spec §5.3 table: newsletter node returns only
        {"spam_bucket": "newsletter", "spam_decision_id": UUID}.
        The actions_applied key must be absent — newsletter continues through
        the pipeline and downstream nodes (apply_actions) may write to
        actions_applied; including it here would conflict.

        Uses Stage 4 LLM path (greylist → LLM returns newsletter) to exercise
        _terminate with bucket=newsletter from a different code path than Stage 3.
        """
        ctx = _ctx()
        email = _email(
            headers=[
                {
                    "name": "org.apache.james.rspamd.status",
                    "value": "default: action=greylist; score=4.0",
                }
            ]
        )
        node = make_spam_triage(ctx)

        llm_output = SpamCheckOutput(
            bucket="newsletter",
            confidence=0.80,
            reason="newsletter confirmed by LLM",
        )

        with patch(
            "twaky.sentinels.mail.nodes.structured_call", return_value=llm_output
        ):
            result = node(_state(email))  # type: ignore[arg-type]

        # Return shape must be exactly {spam_bucket, spam_decision_id} per spec §5.3
        assert result["spam_bucket"] == "newsletter"
        assert result["spam_decision_id"] is not None
        assert "actions_applied" not in result, (
            "newsletter return dict must NOT include actions_applied (spec §5.3)"
        )
        assert set(result.keys()) == {"spam_bucket", "spam_decision_id"}

        # Adapter side-effects still happen (label + nonjunk keyword)
        adapter: InMemoryMailAdapter = ctx.mail  # type: ignore[assignment]
        assert "newsletter" in adapter._labels.get("e1", [])
        assert adapter._keywords.get("e1", {}).get("nonjunk") is True
        ctx.base.mission_emitter.emit.assert_not_called()


# ---------------------------------------------------------------------------
# Spam / phishing-alert → move to Junk mailbox (RFC 8621 role="junk")
# ---------------------------------------------------------------------------


class TestSpamMoveToJunkMailbox:
    """Spam & phishing-alert buckets move the email to the Junk mailbox.

    Bundled atomically with the $junk keyword patch (single Email/set)
    via the adapter's ``set_keywords_bulk(mailbox_patches=…)`` API.

    Behaviour when the adapter cannot resolve a Junk mailbox (e.g. server
    without one, or read-only test doubles) falls back to setting the
    $junk keyword only. This preserves the SP6c behaviour on JMAP
    implementations that lack a ``role="junk"`` mailbox.
    """

    def _adapter_with_junk_role(
        self, inbox_id: str = "inbox-uuid", junk_id: str = "junk-uuid"
    ) -> MagicMock:
        """MagicMock MailAdapter that exposes resolve_role_mailbox_id + records
        set_keywords_bulk calls."""
        adapter = MagicMock()
        adapter.resolve_role_mailbox_id.return_value = junk_id
        adapter.get_email.return_value = {
            "id": "e1",
            "mailboxIds": {inbox_id: True},
        }
        # In-memory-esque tracking of label calls
        adapter._labels: dict[str, list[str]] = {}
        adapter.label.side_effect = lambda eid, lbl: adapter._labels.setdefault(
            eid, []
        ).append(lbl)
        return adapter

    def _ctx_with_adapter(
        self, adapter: MagicMock, config_values: dict[str, Any] | None = None
    ) -> NodeContext:
        cv = config_values or {"spam_filter_enabled": True}
        base = MagicMock()
        base.sentinel_row.config_values = cv
        base.mission_emitter.emit = MagicMock()
        return NodeContext(base=base, mail=adapter, owner_email="owner@example.com")

    def test_spam_bucket_moves_to_junk_mailbox_atomically(self) -> None:
        """Stage 1 $junk keyword → bucket=spam → single set_keywords_bulk with
        mailbox_patches: {inbox: False, junk: True}."""
        adapter = self._adapter_with_junk_role()
        ctx = self._ctx_with_adapter(adapter)
        email = _email(keywords={"$junk": True})

        node = make_spam_triage(ctx)
        result = node(_state(email))  # type: ignore[arg-type]

        assert result["spam_bucket"] == "spam"
        # $junk was applied atomically WITH the mailbox move.
        adapter.set_keywords_bulk.assert_called_once()
        args, kwargs = adapter.set_keywords_bulk.call_args
        assert args[0] == "e1"
        assert args[1] == {"$junk": True}
        assert kwargs["mailbox_patches"] == {
            "inbox-uuid": False,
            "junk-uuid": True,
        }
        # actions_applied surfaces the move so downstream Runs UI can show it.
        assert any(a.startswith("move:junk(") for a in result["actions_applied"])

    def test_phishing_alert_bucket_also_moves_to_junk(self) -> None:
        """bucket=phishing-alert follows the same move-to-junk path as spam."""
        adapter = self._adapter_with_junk_role()
        ctx = self._ctx_with_adapter(adapter)
        # Trigger phishing-alert via LLM grey-zone with confidence above threshold.
        email = _email(
            headers=[
                {"name": "org.apache.james.rspamd.status", "value": "action=greylist"}
            ]
        )
        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            return_value=SpamCheckOutput(
                bucket="phishing-alert", confidence=0.99, reason="clear phishing"
            ),
        ):
            result = make_spam_triage(ctx)(_state(email))  # type: ignore[arg-type]

        assert result["spam_bucket"] == "phishing-alert"
        adapter.set_keywords_bulk.assert_called_once()
        _, kwargs = adapter.set_keywords_bulk.call_args
        assert kwargs["mailbox_patches"]["junk-uuid"] is True

    def test_falls_back_to_junk_keyword_when_no_junk_mailbox(self) -> None:
        """Adapter without a Junk mailbox → set $junk keyword via set_keyword,
        never crash the node."""
        adapter = MagicMock()
        adapter.resolve_role_mailbox_id.side_effect = RuntimeError(
            "no mailbox with role='junk'"
        )
        adapter._labels = {}
        adapter.label.side_effect = lambda eid, lbl: adapter._labels.setdefault(
            eid, []
        ).append(lbl)
        ctx = self._ctx_with_adapter(adapter)
        email = _email(keywords={"$junk": True})

        result = make_spam_triage(ctx)(_state(email))  # type: ignore[arg-type]

        assert result["spam_bucket"] == "spam"
        # Fallback path: single set_keyword($junk=True), no set_keywords_bulk.
        adapter.set_keyword.assert_called_with("e1", "$junk", True)
        adapter.set_keywords_bulk.assert_not_called()
        # No move:junk action reported in fallback.
        assert not any(a.startswith("move:junk(") for a in result["actions_applied"])


# ---------------------------------------------------------------------------
# SP6d T1 D4: Provenance capture tests
# ---------------------------------------------------------------------------


class TestTerminateProvenanceCapture:
    """Tests for origin_mailbox_id / origin_mailbox_role / envelope_headers
    capture added in SP6d T1 D2.

    These tests patch ``spam_decisions.insert`` to avoid live DB writes and
    to assert the provenance keyword arguments passed by ``_terminate``.
    """

    INBOX_ID = "inbox-uuid"
    JUNK_ID = "junk-uuid"

    def _adapter_with_roles(self) -> InMemoryMailAdapter:
        """InMemoryMailAdapter pre-loaded with inbox + junk mailbox roles."""
        return InMemoryMailAdapter(
            mailbox_roles={
                self.INBOX_ID: "inbox",
                self.JUNK_ID: "junk",
            }
        )

    def _ctx_with_adapter(
        self, adapter: InMemoryMailAdapter, config_values: dict[str, Any] | None = None
    ) -> NodeContext:
        cv = config_values or {"spam_filter_enabled": True}
        base = MagicMock()
        base.sentinel_row.config_values = cv
        base.mission_emitter.emit = MagicMock()
        return NodeContext(base=base, mail=adapter, owner_email="owner@example.com")

    def _email_with_mailboxes(
        self,
        email_id: str = "e1",
        mailbox_ids: list[str] | None = None,
        headers: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Build an email whose mailboxIds list matches *mailbox_ids*."""
        mbox_ids = mailbox_ids or [self.INBOX_ID]
        return {
            "id": email_id,
            "threadId": "t1",
            "receivedAt": "2026-08-12T10:00:00Z",
            "from": [{"email": "spammer@evil.com", "name": "Spammer"}],
            "to": [{"email": "owner@example.com", "name": "Owner"}],
            "subject": "Spam subject",
            "preview": "Spam preview",
            "keywords": {"$junk": True},
            "mailboxIds": {mid: True for mid in mbox_ids},
            "headers": headers or [],
            "hasAttachment": False,
        }

    def test_terminate_captures_origin_mailbox_when_spam(self) -> None:
        """_terminate captures origin_mailbox_id + origin_mailbox_role before Junk move.

        Email has mailboxIds=[inbox_id, junk_id]; the origin is inbox_id
        (first id that is not the junk mailbox) with role 'inbox'.
        """
        adapter = self._adapter_with_roles()
        ctx = self._ctx_with_adapter(adapter)

        # Pre-load email into the adapter so get_email() works in _terminate.
        email = self._email_with_mailboxes(
            mailbox_ids=[self.INBOX_ID, self.JUNK_ID],
        )
        adapter.add(email)

        node = make_spam_triage(ctx)

        with patch(
            "twaky.sentinels.mail.nodes.spam_decisions.insert",
            return_value=__import__("uuid").uuid4(),
        ) as mock_insert:
            result = node({"email_id": email["id"], "thread": [email]})  # type: ignore[arg-type]

        assert result["spam_bucket"] == "spam"
        mock_insert.assert_called_once()
        call_kwargs = mock_insert.call_args.kwargs
        assert call_kwargs["origin_mailbox_id"] == self.INBOX_ID
        assert call_kwargs["origin_mailbox_role"] == "inbox"

    def test_terminate_captures_envelope_headers_subset(self) -> None:
        """_terminate passes only whitelisted header keys to spam_decisions.insert.

        The email has 'list-unsubscribe', 'x-custom-noise', and 'from'.
        Only the whitelisted keys must appear in envelope_headers.
        """
        adapter = self._adapter_with_roles()
        ctx = self._ctx_with_adapter(adapter)

        email = self._email_with_mailboxes(
            mailbox_ids=[self.INBOX_ID],
            headers=[
                {"name": "from", "value": "spammer@evil.com"},
                {"name": "list-unsubscribe", "value": "<mailto:unsub@evil.com>"},
                {"name": "x-custom-noise", "value": "should be excluded"},
                {"name": "subject", "value": "Spam subject"},
            ],
        )
        adapter.add(email)

        node = make_spam_triage(ctx)

        with patch(
            "twaky.sentinels.mail.nodes.spam_decisions.insert",
            return_value=__import__("uuid").uuid4(),
        ) as mock_insert:
            node({"email_id": email["id"], "thread": [email]})  # type: ignore[arg-type]

        mock_insert.assert_called_once()
        envelope_headers = mock_insert.call_args.kwargs["envelope_headers"]
        assert envelope_headers is not None
        assert "from" in envelope_headers
        assert "list-unsubscribe" in envelope_headers
        assert "subject" in envelope_headers
        assert "x-custom-noise" not in envelope_headers

    def test_terminate_newsletter_bucket_does_not_capture_provenance(self) -> None:
        """For bucket='newsletter', origin_* and envelope_headers must be None."""
        # Newsletter is triggered by heuristic_newsletter: list-unsubscribe
        # present + dkim present (score=2, < 5).
        adapter = self._adapter_with_roles()
        cv = {"spam_filter_enabled": True}
        base = MagicMock()
        base.sentinel_row.config_values = cv
        base.mission_emitter.emit = MagicMock()
        ctx = NodeContext(base=base, mail=adapter, owner_email="owner@example.com")

        email: dict[str, Any] = {
            "id": "newsletter-e1",
            "threadId": "t-nl",
            "receivedAt": "2026-08-12T10:00:00Z",
            "from": [{"email": "news@newsletter.com", "name": "Newsletter"}],
            "to": [{"email": "owner@example.com", "name": "Owner"}],
            "subject": "Our weekly digest",
            "preview": "Newsletter content",
            "keywords": {},
            "mailboxIds": {self.INBOX_ID: True},
            "headers": [
                {
                    "name": "list-unsubscribe",
                    "value": "<mailto:unsub@newsletter.com>",
                },
                {
                    "name": "list-unsubscribe-post",
                    "value": "List-Unsubscribe=One-Click",
                },
                {"name": "dkim-signature", "value": "v=1; a=rsa-sha256; ..."},
            ],
            "hasAttachment": False,
        }
        adapter.add(email)

        node = make_spam_triage(ctx)

        with (
            patch(
                "twaky.sentinels.mail.nodes.spam_decisions.insert",
                return_value=__import__("uuid").uuid4(),
            ) as mock_insert,
            patch("twaky.sentinels.mail.nodes.structured_call") as mock_llm,
        ):
            result = node({"email_id": email["id"], "thread": [email]})  # type: ignore[arg-type]

        mock_llm.assert_not_called()  # heuristic, not LLM
        assert result["spam_bucket"] == "newsletter"
        mock_insert.assert_called_once()
        call_kwargs = mock_insert.call_args.kwargs
        assert call_kwargs["origin_mailbox_id"] is None
        assert call_kwargs["origin_mailbox_role"] is None
        assert call_kwargs["envelope_headers"] is None
