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
    """Delete all rows from mail_sentinel_spam_decision before/after each test."""
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
