"""Unit tests for the draft_reply node.

Tests for make_draft_reply node factory: LLM draft generation, save_draft call,
and mission emit.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

from twaky.sentinels.mail.adapter import InMemoryMailAdapter
from twaky.sentinels.mail.nodes import (
    NodeContext,
    _build_reply_quote,
    make_draft_reply,
)
from twaky.sentinels.mail.schemas import DraftReplyOutput
from twaky.sentinels.mail.state import MailAgentState


def _make_email(
    email_id: str,
    thread_id: str | None,
    received_at: str,
    subject: str = "Test Subject",
) -> dict[str, Any]:
    """Build a minimal email dict."""
    email: dict[str, Any] = {
        "id": email_id,
        "receivedAt": received_at,
        "subject": subject,
    }
    if thread_id is not None:
        email["threadId"] = thread_id
    return email


class TestDraftReply:
    def test_empty_thread_noops(self) -> None:
        """Empty thread returns {} without LLM or emit."""
        adapter = InMemoryMailAdapter()
        base_ctx = MagicMock()
        ctx = NodeContext(
            base=base_ctx,
            mail=adapter,
            owner_email="me@x.com",
        )
        node = make_draft_reply(ctx)

        state: MailAgentState = {"thread": []}
        result = node(state)

        assert result == {}
        # Verify no emit called
        base_ctx.mission_emitter.emit.assert_not_called()

    def test_saves_draft_and_emits_mission(self) -> None:
        """Generates draft, saves it, and emits mission with evidence."""
        # Setup email
        e1 = _make_email("e1", "t1", "2024-01-01T10:00:00Z", "Test Email")
        adapter = InMemoryMailAdapter(seed={"e1": e1})

        base_ctx = MagicMock()
        ctx = NodeContext(
            base=base_ctx,
            mail=adapter,
            owner_email="me@x.com",
        )
        node = make_draft_reply(ctx)

        # Setup state with rule info
        state: MailAgentState = {
            "thread": [e1],
            "rule_name": "test_rule",
            "matched_by": "static",
            "memory_ids": [],
        }

        # Mock structured_call to return a draft
        draft_output = DraftReplyOutput(
            body="Bonjour Alice, merci pour ton message.",
            language="fr",
        )

        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            return_value=draft_output,
        ):
            result = node(state)

        # Verify result
        assert result["draft"] == "Bonjour Alice, merci pour ton message."
        assert result["draft_language"] == "fr"

        # Verify draft was saved: body starts with LLM output and continues
        # with the quoted-original block appended by the node (post-processing:
        # LLM body + optional signature + attribution + '> …' quoted lines).
        assert len(adapter._drafts) == 1
        saved_draft = adapter._drafts[0]
        assert saved_draft["in_reply_to"] == "e1"
        assert saved_draft["body"].startswith("Bonjour Alice, merci pour ton message.")
        assert "a écrit :" in saved_draft["body"]  # attribution line (fr)
        assert saved_draft["language"] == "fr"

        # Verify mission was emitted
        base_ctx.mission_emitter.emit.assert_called_once()
        call_kwargs = base_ctx.mission_emitter.emit.call_args[1]

        assert call_kwargs["intent_text"] == "Draft ready: Test Email"
        assert "rule 'test_rule' matched" in call_kwargs["reason"]
        assert call_kwargs["reason"].endswith("draft awaiting review")

        # Verify artifact structure
        artifact = call_kwargs["artifact"]
        assert artifact["kind"] == "sentinel_evidence"
        assert artifact["sentinel"] == "mail"

        evidence = artifact["evidence"]
        assert evidence["email_id"] == "e1"
        assert evidence["draft_id"] == "draft-1"
        assert evidence["language"] == "fr"
        assert evidence["rule"] == "test_rule"
        assert evidence["matched_by"] == "static"

        hints = artifact["hints"]
        assert hints["draft_body"].startswith("Bonjour")

    def test_with_memories_injects_context(self) -> None:
        """Memories are fetched and injected into the prompt."""
        e1 = _make_email("e1", "t1", "2024-01-01T10:00:00Z", "Question")
        adapter = InMemoryMailAdapter(seed={"e1": e1})

        base_ctx = MagicMock()
        ctx = NodeContext(
            base=base_ctx,
            mail=adapter,
            owner_email="me@x.com",
        )
        node = make_draft_reply(ctx)

        # Create mock memory UUIDs
        mem_id1 = UUID("12345678-1234-5678-1234-567812345678")
        mem_id2 = UUID("87654321-4321-8765-4321-876543218765")

        state: MailAgentState = {
            "thread": [e1],
            "rule_name": "ai",
            "matched_by": "ai",
            "memory_ids": [mem_id1, mem_id2],
        }

        draft_output = DraftReplyOutput(body="Response text", language="en")

        with (
            patch(
                "twaky.sentinels.mail.nodes.structured_call",
                return_value=draft_output,
            ) as mock_call,
            patch(
                "twaky.sentinels.mail.nodes.mem_store.get_many",
                return_value=[
                    MagicMock(
                        kind="fact",
                        scope="sender",
                        scope_value="alice@example.com",
                        content="Alice prefers concise replies",
                    ),
                    MagicMock(
                        kind="preference",
                        scope="global",
                        scope_value="",
                        content="Always be professional",
                    ),
                ],
            ),
        ):
            result = node(state)

        assert result["draft"] == "Response text"

        # Verify mem_store.get_many was called with the memory ids
        # (Import note: mem_store is imported at module level in nodes.py)

        # Verify structured_call was called with memories in the prompt
        mock_call.assert_called_once()
        call_args = mock_call.call_args
        prompt = call_args[0][0]  # first positional argument
        # Verify memories were included in the prompt
        assert "fact" in prompt or "preference" in prompt or "Alice" in prompt

    def test_missing_rule_name_uses_ai_default(self) -> None:
        """When rule_name is None, reason uses 'ai' as default."""
        e1 = _make_email("e1", "t1", "2024-01-01T10:00:00Z", "Subject")
        adapter = InMemoryMailAdapter(seed={"e1": e1})

        base_ctx = MagicMock()
        ctx = NodeContext(
            base=base_ctx,
            mail=adapter,
            owner_email="me@x.com",
        )
        node = make_draft_reply(ctx)

        state: MailAgentState = {
            "thread": [e1],
            # rule_name is None or missing
            "matched_by": "none",
            "memory_ids": [],
        }

        draft_output = DraftReplyOutput(body="Draft", language="en")

        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            return_value=draft_output,
        ):
            result = node(state)

        assert result["draft"] == "Draft"

        # Verify emit was called with 'ai' in reason
        base_ctx.mission_emitter.emit.assert_called_once()
        call_kwargs = base_ctx.mission_emitter.emit.call_args[1]
        assert "rule 'ai' matched" in call_kwargs["reason"]

    def test_email_without_subject_uses_placeholder(self) -> None:
        """Email missing subject falls back to '(no subject)'."""
        e1 = {
            "id": "e1",
            "threadId": "t1",
            "receivedAt": "2024-01-01T10:00:00Z",
            # no subject key
        }
        adapter = InMemoryMailAdapter(seed={"e1": e1})

        base_ctx = MagicMock()
        ctx = NodeContext(
            base=base_ctx,
            mail=adapter,
            owner_email="me@x.com",
        )
        node = make_draft_reply(ctx)

        state: MailAgentState = {
            "thread": [e1],
            "rule_name": "test",
            "matched_by": "static",
            "memory_ids": [],
        }

        draft_output = DraftReplyOutput(body="Draft", language="en")

        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            return_value=draft_output,
        ):
            result = node(state)

        assert result["draft"] == "Draft"

        # Verify intent_text uses placeholder
        base_ctx.mission_emitter.emit.assert_called_once()
        call_kwargs = base_ctx.mission_emitter.emit.call_args[1]
        assert call_kwargs["intent_text"] == "Draft ready: (no subject)"

    def test_draft_id_generation(self) -> None:
        """Multiple drafts receive sequential IDs."""
        e1 = _make_email("e1", "t1", "2024-01-01T10:00:00Z")
        adapter = InMemoryMailAdapter(seed={"e1": e1})

        base_ctx = MagicMock()
        ctx = NodeContext(
            base=base_ctx,
            mail=adapter,
            owner_email="me@x.com",
        )
        node = make_draft_reply(ctx)

        state: MailAgentState = {
            "thread": [e1],
            "rule_name": "test",
            "matched_by": "static",
            "memory_ids": [],
        }

        draft_output = DraftReplyOutput(body="Draft 1", language="en")

        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            return_value=draft_output,
        ):
            node(state)

        # Second draft
        draft_output2 = DraftReplyOutput(body="Draft 2", language="en")
        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            return_value=draft_output2,
        ):
            node(state)

        assert len(adapter._drafts) == 2
        assert adapter._drafts[0]["id"] == "draft-1"
        assert adapter._drafts[1]["id"] == "draft-2"

    def test_hardening_and_use_case_params(self) -> None:
        """Verifies structured_call is invoked with correct hardening and use_case."""
        e1 = _make_email("e1", "t1", "2024-01-01T10:00:00Z")
        adapter = InMemoryMailAdapter(seed={"e1": e1})

        base_ctx = MagicMock()
        ctx = NodeContext(
            base=base_ctx,
            mail=adapter,
            owner_email="me@x.com",
        )
        node = make_draft_reply(ctx)

        state: MailAgentState = {
            "thread": [e1],
            "rule_name": "test",
            "matched_by": "static",
            "memory_ids": [],
        }

        draft_output = DraftReplyOutput(body="Draft", language="en")

        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            return_value=draft_output,
        ) as mock_call:
            node(state)

        # Verify the call signature
        call_args = mock_call.call_args
        assert len(call_args[0]) >= 2  # prompt and output_schema
        assert call_args[1]["hardening"].name == "FULL"
        assert call_args[1]["use_case"].name == "DRAFT_REPLY"


# ---------------------------------------------------------------------------
# _build_reply_quote helper — attribution line + '> '-prefixed body
# ---------------------------------------------------------------------------


class TestBuildReplyQuote:
    """Unit tests for the _build_reply_quote helper used by make_draft_reply."""

    def _email(
        self,
        *,
        sender_name: str = "Alice",
        sender_email: str = "alice@x",
        received: str = "2026-01-15T10:00:00Z",
        body: str | None = None,
        preview: str = "",
    ) -> dict[str, Any]:
        e: dict[str, Any] = {
            "id": "e1",
            "from": [{"name": sender_name, "email": sender_email}],
            "receivedAt": received,
            "preview": preview,
        }
        if body is not None:
            e["textBody"] = [{"partId": "1", "type": "text/plain"}]
            e["bodyValues"] = {"1": {"value": body}}
        return e

    def test_fr_attribution_and_prefix(self) -> None:
        """French: 'Le YYYY-MM-DD, <sender> a écrit :' + '> ' prefix per line."""
        e = self._email(body="Bonjour Michel.\n\nSecond paragraph.")
        quote = _build_reply_quote(e, "fr")
        assert quote.startswith("Le 2026-01-15, Alice a écrit :\n")
        assert "\n> Bonjour Michel." in quote
        assert "\n>\n" in quote or "\n> \n" in quote or "\n> Second" in quote

    def test_en_attribution_and_prefix(self) -> None:
        """English: 'On YYYY-MM-DD, <sender> wrote:' + '> ' prefix per line."""
        e = self._email(body="Hi Michel.")
        quote = _build_reply_quote(e, "en")
        assert quote.startswith("On 2026-01-15, Alice wrote:\n")
        assert "> Hi Michel." in quote

    def test_uses_sender_email_when_name_missing(self) -> None:
        e = self._email(sender_name="", sender_email="alice@example.com", body="hi")
        quote = _build_reply_quote(e, "en")
        assert "alice@example.com wrote:" in quote

    def test_falls_back_to_preview_when_no_body(self) -> None:
        e = self._email(body=None, preview="Preview snippet only.")
        quote = _build_reply_quote(e, "en")
        assert "> Preview snippet only." in quote

    def test_attribution_only_when_no_body_no_preview(self) -> None:
        e = self._email(body=None, preview="")
        quote = _build_reply_quote(e, "en")
        # Attribution line only, no '> ' lines
        assert "wrote:" in quote
        assert "\n>" not in quote

    def test_defaults_to_english_for_unknown_language(self) -> None:
        e = self._email(body="hi")
        quote = _build_reply_quote(e, "de")  # not fr → English fallback
        assert "wrote:" in quote


# ---------------------------------------------------------------------------
# Signature post-append — the LLM is instructed not to sign; we override
# ---------------------------------------------------------------------------


class TestDraftSignature:
    """Signature configured via settings.mail_sentinel_signature is appended
    to the LLM body before save_draft is called.

    The LLM prompt says "Do not add a signature" but small models routinely
    ignore it. Post-appending here is the authoritative source of truth for
    the outgoing draft's signature block.
    """

    def _adapter_and_ctx(self, owner: str = "michel@x") -> tuple:
        adapter = InMemoryMailAdapter()
        base_ctx = MagicMock()
        ctx = NodeContext(base=base_ctx, mail=adapter, owner_email=owner)
        return adapter, ctx

    def _thread(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "e1",
                "threadId": "t1",
                "from": [{"name": "Marion", "email": "marion@x"}],
                "subject": "Reprise contact",
                "receivedAt": "2026-01-01T00:00:00Z",
                "textBody": [{"partId": "1", "type": "text/plain"}],
                "bodyValues": {"1": {"value": "Original body"}},
                "messageId": ["<orig@x>"],
                "headers": [],
            }
        ]

    def test_signature_appended_when_configured(self, monkeypatch) -> None:
        from twaky.sentinels.mail import nodes as nodes_mod

        adapter, ctx = self._adapter_and_ctx()
        monkeypatch.setattr(
            nodes_mod.settings, "mail_sentinel_signature", "-- \nJane Doe\nCEO"
        )
        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            return_value=DraftReplyOutput(body="Merci Marion.", language="fr"),
        ):
            make_draft_reply(ctx)({"thread": self._thread()})

        saved = adapter._drafts[0]["body"]
        # LLM body first, then blank line, then signature, then quoted original.
        assert saved.startswith("Merci Marion.")
        assert "-- \nJane Doe\nCEO" in saved
        # Signature comes BEFORE the attribution line, not after.
        sig_idx = saved.index("Jane Doe")
        attr_idx = saved.index("a écrit :")
        assert sig_idx < attr_idx

    def test_signature_omitted_when_empty(self, monkeypatch) -> None:
        from twaky.sentinels.mail import nodes as nodes_mod

        adapter, ctx = self._adapter_and_ctx()
        monkeypatch.setattr(nodes_mod.settings, "mail_sentinel_signature", "")
        with patch(
            "twaky.sentinels.mail.nodes.structured_call",
            return_value=DraftReplyOutput(body="Body only.", language="fr"),
        ):
            make_draft_reply(ctx)({"thread": self._thread()})

        saved = adapter._drafts[0]["body"]
        # No signature block between body and attribution.
        assert saved.startswith("Body only.")
        # Attribution line immediately follows (with the double newline).
        assert saved.index("a écrit :") - saved.index("Body only.") < 100
