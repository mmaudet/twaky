"""Tests for mail-sentinel state and thread status enum."""

from __future__ import annotations

from uuid import uuid4

from twaky.sentinels.mail.state import MailAgentState, ThreadStatus


class TestThreadStatusEnum:
    """Tests for ThreadStatus enum."""

    def test_thread_status_enum_values(self) -> None:
        """Assert all 4 ThreadStatus values match the expected strings."""
        assert ThreadStatus.TO_REPLY.value == "TO_REPLY"
        assert ThreadStatus.ACTIONED.value == "ACTIONED"
        assert ThreadStatus.FYI.value == "FYI"
        assert ThreadStatus.AWAITING_REPLY.value == "AWAITING_REPLY"

    def test_thread_status_string_coercion(self) -> None:
        """Assert ThreadStatus values are comparable to their string values."""
        # With (str, Enum), the value attribute is the string
        assert ThreadStatus.TO_REPLY.value == "TO_REPLY"
        # And comparison with string works
        assert ThreadStatus.TO_REPLY == "TO_REPLY"


class TestMailAgentState:
    """Tests for MailAgentState TypedDict."""

    def test_state_accepts_partial_dict(self) -> None:
        """Assert that a partial dict with only email_id works."""
        s: MailAgentState = {"email_id": "abc"}
        assert s["email_id"] == "abc"
        # TypedDict with total=False allows accessing present keys
        assert len(s) == 1

    def test_state_full_shape(self) -> None:
        """Assert that all 12 fields can be instantiated and retrieved."""
        memory_id_1 = uuid4()
        memory_id_2 = uuid4()

        s: MailAgentState = {
            "email_id": "test-email-123",
            "thread": [{"id": "1", "from": "alice@example.com"}],
            "matched_by": "ai",
            "rule_name": "auto_reply_rule",
            "status": ThreadStatus.TO_REPLY,
            "memory_ids": [memory_id_1, memory_id_2],
            "draft": "Thanks for your message!",
            "draft_language": "en",
            "learned_pattern": {"sender": "bob@example.com", "confidence": 0.95},
            "actions_applied": ["draft_reply", "label:urgent"],
            "started_at": 1234567890.0,
            "llm_calls": 3,
        }

        # Verify all fields are present
        assert s["email_id"] == "test-email-123"
        assert s["thread"] == [{"id": "1", "from": "alice@example.com"}]
        assert s["matched_by"] == "ai"
        assert s["rule_name"] == "auto_reply_rule"
        assert s["status"] == ThreadStatus.TO_REPLY
        assert s["memory_ids"] == [memory_id_1, memory_id_2]
        assert s["draft"] == "Thanks for your message!"
        assert s["draft_language"] == "en"
        assert s["learned_pattern"] == {"sender": "bob@example.com", "confidence": 0.95}
        assert s["actions_applied"] == ["draft_reply", "label:urgent"]
        assert s["started_at"] == 1234567890.0
        assert s["llm_calls"] == 3

    def test_state_with_none_values(self) -> None:
        """Assert that optional None fields are allowed."""
        s: MailAgentState = {
            "email_id": "test-123",
            "rule_name": None,
            "draft": None,
            "draft_language": None,
            "learned_pattern": None,
        }

        assert s["email_id"] == "test-123"
        assert s["rule_name"] is None
        assert s["draft"] is None
        assert s["draft_language"] is None
        assert s["learned_pattern"] is None
