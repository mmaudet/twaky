"""Unit tests for MailSentinel and its module-level SentinelClass alias."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from twaky.sentinels.base import Outcome
from twaky.sentinels.mail.sentinel import MailSentinel, SentinelClass

# ---------------------------------------------------------------------------
# Discovery contract
# ---------------------------------------------------------------------------


class TestSentinelClassAlias:
    def test_exposes_sentinel_class(self) -> None:
        assert SentinelClass is MailSentinel

    def test_name(self) -> None:
        assert MailSentinel.name == "mail"

    def test_event_source_kind(self) -> None:
        assert MailSentinel.event_source_kind == "jmap_poll"

    def test_version(self) -> None:
        assert MailSentinel.version == "1.0.0"


# ---------------------------------------------------------------------------
# process() outcome translation
# ---------------------------------------------------------------------------


def _make_event(
    payload: dict[str, Any] | None = None,
    message_id: str = "email-123",
) -> dict[str, Any]:
    return {
        "source_kind": "jmap_poll",
        "source_ref": "test-account",
        "message_id": message_id,
        "payload": payload or {},
    }


def _mock_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.sentinel_row.config_values = {}
    return ctx


class TestProcessOutcomeTranslation:
    def test_process_mission_created_when_draft_set(self) -> None:
        sentinel = MailSentinel()
        event = _make_event(message_id="e42")
        ctx = _mock_ctx()

        state = {"draft": "Bonjour", "draft_language": "fr", "actions_applied": []}

        with (
            patch.object(sentinel, "_build_adapter", return_value=MagicMock()),
            patch(
                "twaky.sentinels.mail.sentinel.process_email",
                return_value=state,
            ),
        ):
            outcome = sentinel.process(event, ctx)  # type: ignore[arg-type]

        assert outcome is Outcome.MISSION_CREATED

    def test_process_delegated_when_delegate_action(self) -> None:
        sentinel = MailSentinel()
        event = _make_event(message_id="e43")
        ctx = _mock_ctx()

        state = {"actions_applied": ["delegate_to_atlas"]}

        with (
            patch.object(sentinel, "_build_adapter", return_value=MagicMock()),
            patch(
                "twaky.sentinels.mail.sentinel.process_email",
                return_value=state,
            ),
        ):
            outcome = sentinel.process(event, ctx)  # type: ignore[arg-type]

        assert outcome is Outcome.DELEGATED

    def test_process_processed_default(self) -> None:
        sentinel = MailSentinel()
        event = _make_event(message_id="e44")
        ctx = _mock_ctx()

        state = {"actions_applied": ["archive"]}

        with (
            patch.object(sentinel, "_build_adapter", return_value=MagicMock()),
            patch(
                "twaky.sentinels.mail.sentinel.process_email",
                return_value=state,
            ),
        ):
            outcome = sentinel.process(event, ctx)  # type: ignore[arg-type]

        assert outcome is Outcome.PROCESSED


# ---------------------------------------------------------------------------
# Email id resolution
# ---------------------------------------------------------------------------


class TestResolveEmailId:
    def test_resolves_from_payload_email_id_field(self) -> None:
        sentinel = MailSentinel()
        event = _make_event(payload={"email_id": "from-payload"}, message_id="fallback")
        assert sentinel._resolve_email_id(event) == "from-payload"  # type: ignore[arg-type]

    def test_resolves_from_payload_email_object(self) -> None:
        sentinel = MailSentinel()
        event = _make_event(
            payload={"email": {"id": "nested-id"}}, message_id="fallback"
        )
        assert sentinel._resolve_email_id(event) == "nested-id"  # type: ignore[arg-type]

    def test_falls_back_to_message_id(self) -> None:
        sentinel = MailSentinel()
        event = _make_event(payload={}, message_id="mgs-99")
        assert sentinel._resolve_email_id(event) == "mgs-99"  # type: ignore[arg-type]
