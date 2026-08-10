"""Tests for mail-sentinel pydantic schemas."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from twaky.sentinels.mail.schemas import (
    ChooseRuleOutput,
    DraftReplyOutput,
    ExtractedMemory,
    ExtractMemoriesOutput,
    LearnPatternOutput,
    SelectMemoriesOutput,
    ThreadStatusOutput,
)
from twaky.sentinels.mail.state import ThreadStatus


class TestChooseRuleOutput:
    """Tests for ChooseRuleOutput schema."""

    def test_happy_path_with_rule(self) -> None:
        """Assert valid ChooseRuleOutput with rule name."""
        output = ChooseRuleOutput(
            rule="archive_old_emails",
            matched_by="ai",
            reasoning="Email older than 30 days, matches archive pattern.",
        )
        assert output.rule == "archive_old_emails"
        assert output.matched_by == "ai"
        assert output.reasoning == "Email older than 30 days, matches archive pattern."

    def test_rule_none_accepted(self) -> None:
        """Assert that rule=None is accepted."""
        output = ChooseRuleOutput(rule=None)
        assert output.rule is None
        assert output.matched_by == "ai"
        assert output.reasoning == ""

    def test_matched_by_default(self) -> None:
        """Assert matched_by defaults to 'ai'."""
        output = ChooseRuleOutput(rule="some_rule")
        assert output.matched_by == "ai"

    def test_matched_by_empty_literal(self) -> None:
        """Assert matched_by='empty' is accepted."""
        output = ChooseRuleOutput(rule=None, matched_by="empty")
        assert output.matched_by == "empty"

    def test_reasoning_max_length(self) -> None:
        """Assert reasoning is rejected if > 800 chars."""
        long_text = "x" * 801
        with pytest.raises(ValidationError) as exc_info:
            ChooseRuleOutput(rule="test", reasoning=long_text)
        # Pydantic validation error key is "too_long"
        assert (
            "too_long" in str(exc_info.value).lower()
            or "string_too_long" in str(exc_info.value).lower()
        )

    def test_reasoning_exactly_800_chars(self) -> None:
        """Assert reasoning of exactly 800 chars is accepted."""
        text_800 = "x" * 800
        output = ChooseRuleOutput(rule="test", reasoning=text_800)
        assert len(output.reasoning) == 800


class TestLearnPatternOutput:
    """Tests for LearnPatternOutput schema."""

    def test_happy_path(self) -> None:
        """Assert valid LearnPatternOutput."""
        output = LearnPatternOutput(should_learn=True, confidence=0.95)
        assert output.should_learn is True
        assert output.confidence == 0.95
        assert output.reasoning == ""

    def test_confidence_at_bounds(self) -> None:
        """Assert confidence accepts 0.0 and 1.0."""
        output1 = LearnPatternOutput(should_learn=True, confidence=0.0)
        assert output1.confidence == 0.0

        output2 = LearnPatternOutput(should_learn=True, confidence=1.0)
        assert output2.confidence == 1.0

    def test_confidence_above_1_rejected(self) -> None:
        """Assert confidence > 1.0 raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            LearnPatternOutput(should_learn=True, confidence=1.5)
        assert "less than or equal to 1" in str(exc_info.value).lower()

    def test_confidence_below_0_rejected(self) -> None:
        """Assert confidence < 0.0 raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            LearnPatternOutput(should_learn=True, confidence=-0.1)
        assert "greater than or equal to 0" in str(exc_info.value).lower()

    def test_reasoning_max_length(self) -> None:
        """Assert reasoning is validated for length."""
        long_text = "y" * 801
        with pytest.raises(ValidationError):
            LearnPatternOutput(should_learn=False, confidence=0.5, reasoning=long_text)


class TestThreadStatusOutput:
    """Tests for ThreadStatusOutput schema."""

    def test_happy_path_all_statuses(self) -> None:
        """Assert all ThreadStatus values are accepted."""
        for status in [
            ThreadStatus.TO_REPLY,
            ThreadStatus.ACTIONED,
            ThreadStatus.FYI,
            ThreadStatus.AWAITING_REPLY,
        ]:
            output = ThreadStatusOutput(status=status)
            assert output.status == status

    def test_string_parsing(self) -> None:
        """Assert ThreadStatusOutput parses string status values."""
        output = ThreadStatusOutput(status="TO_REPLY")  # type: ignore
        assert output.status == ThreadStatus.TO_REPLY

    def test_invalid_status_rejected(self) -> None:
        """Assert invalid string status raises ValidationError."""
        with pytest.raises(ValidationError):
            ThreadStatusOutput(status="INVALID_STATUS")  # type: ignore

    def test_reasoning_default_and_max_length(self) -> None:
        """Assert reasoning defaults to '' and respects max_length."""
        output = ThreadStatusOutput(status=ThreadStatus.FYI)
        assert output.reasoning == ""

        long_text = "z" * 801
        with pytest.raises(ValidationError):
            ThreadStatusOutput(status=ThreadStatus.FYI, reasoning=long_text)


class TestSelectMemoriesOutput:
    """Tests for SelectMemoriesOutput schema."""

    def test_happy_path_with_ids(self) -> None:
        """Assert SelectMemoriesOutput accepts a list of UUIDs."""
        ids = [uuid4(), uuid4(), uuid4()]
        output = SelectMemoriesOutput(memory_ids=ids)
        assert output.memory_ids == ids

    def test_default_empty_list(self) -> None:
        """Assert memory_ids defaults to empty list."""
        output = SelectMemoriesOutput()
        assert output.memory_ids == []

    def test_max_32_ids_accepted(self) -> None:
        """Assert exactly 32 UUIDs are accepted."""
        ids = [uuid4() for _ in range(32)]
        output = SelectMemoriesOutput(memory_ids=ids)
        assert len(output.memory_ids) == 32

    def test_33_ids_rejected(self) -> None:
        """Assert 33 UUIDs raise ValidationError."""
        ids = [uuid4() for _ in range(33)]
        with pytest.raises(ValidationError) as exc_info:
            SelectMemoriesOutput(memory_ids=ids)
        assert "too_long" in str(exc_info.value).lower()

    def test_single_id(self) -> None:
        """Assert single UUID is accepted."""
        uid = uuid4()
        output = SelectMemoriesOutput(memory_ids=[uid])
        assert output.memory_ids == [uid]


class TestDraftReplyOutput:
    """Tests for DraftReplyOutput schema."""

    def test_happy_path(self) -> None:
        """Assert valid DraftReplyOutput."""
        output = DraftReplyOutput(body="Thank you for your message.", language="EN")
        assert output.body == "Thank you for your message."
        assert output.language == "en"  # lowercased by validator

    def test_language_lowercasing(self) -> None:
        """Assert language is lowercased by the field_validator."""
        output = DraftReplyOutput(body="Test", language="FR")
        assert output.language == "fr"

        output2 = DraftReplyOutput(body="Test", language="EN-US")
        assert output2.language == "en-us"

    def test_language_min_length(self) -> None:
        """Assert language with length < 2 is rejected."""
        with pytest.raises(ValidationError):
            DraftReplyOutput(body="Test", language="a")

    def test_language_max_length(self) -> None:
        """Assert language with length > 8 is rejected."""
        with pytest.raises(ValidationError):
            DraftReplyOutput(body="Test", language="x" * 9)

    def test_body_min_length(self) -> None:
        """Assert empty body is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            DraftReplyOutput(body="", language="en")
        assert "at least 1 character" in str(exc_info.value).lower()

    def test_body_max_length(self) -> None:
        """Assert body > 32768 chars is rejected."""
        long_body = "x" * 32769
        with pytest.raises(ValidationError):
            DraftReplyOutput(body=long_body, language="en")

    def test_body_at_limits(self) -> None:
        """Assert body of exactly 1 and 32768 chars are accepted."""
        output1 = DraftReplyOutput(body="x", language="en")
        assert len(output1.body) == 1

        output2 = DraftReplyOutput(body="x" * 32768, language="en")
        assert len(output2.body) == 32768


class TestExtractedMemory:
    """Tests for ExtractedMemory schema."""

    def test_happy_path_fact_sender(self) -> None:
        """Assert valid ExtractedMemory with kind=fact, scope=sender."""
        mem = ExtractedMemory(
            kind="fact",
            scope="sender",
            scope_value="alice@example.com",
            content="Alice prefers concise replies.",
        )
        assert mem.kind == "fact"
        assert mem.scope == "sender"
        assert mem.scope_value == "alice@example.com"
        assert mem.content == "Alice prefers concise replies."

    def test_all_kind_values(self) -> None:
        """Assert all kind literals are accepted."""
        for kind in ["fact", "procedure", "preference"]:
            mem = ExtractedMemory(
                kind=kind,  # type: ignore
                scope="global",
                scope_value="global",
                content="Test content here",
            )
            assert mem.kind == kind

    def test_all_scope_values(self) -> None:
        """Assert all scope literals are accepted."""
        for scope in ["sender", "domain", "global"]:
            mem = ExtractedMemory(
                kind="fact",
                scope=scope,  # type: ignore
                scope_value="test_value",
                content="Test content here",
            )
            assert mem.scope == scope

    def test_scope_value_min_length(self) -> None:
        """Assert scope_value with length < 1 is rejected."""
        with pytest.raises(ValidationError):
            ExtractedMemory(
                kind="fact",
                scope="sender",
                scope_value="",
                content="Test content",
            )

    def test_scope_value_max_length(self) -> None:
        """Assert scope_value > 255 chars is rejected."""
        long_value = "x" * 256
        with pytest.raises(ValidationError):
            ExtractedMemory(
                kind="fact",
                scope="sender",
                scope_value=long_value,
                content="Test content",
            )

    def test_content_min_length(self) -> None:
        """Assert content with length < 3 is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            ExtractedMemory(
                kind="fact", scope="sender", scope_value="alice", content="hi"
            )
        assert "at least 3 character" in str(exc_info.value).lower()

    def test_content_max_length(self) -> None:
        """Assert content > 800 chars is rejected."""
        long_content = "x" * 801
        with pytest.raises(ValidationError):
            ExtractedMemory(
                kind="fact",
                scope="sender",
                scope_value="alice",
                content=long_content,
            )

    def test_content_at_limits(self) -> None:
        """Assert content of exactly 3 and 800 chars are accepted."""
        mem1 = ExtractedMemory(
            kind="fact", scope="sender", scope_value="alice", content="abc"
        )
        assert len(mem1.content) == 3

        mem2 = ExtractedMemory(
            kind="fact",
            scope="sender",
            scope_value="alice",
            content="x" * 800,
        )
        assert len(mem2.content) == 800


class TestExtractMemoriesOutput:
    """Tests for ExtractMemoriesOutput schema."""

    def test_happy_path_with_memories(self) -> None:
        """Assert ExtractMemoriesOutput with multiple memories."""
        memories = [
            ExtractedMemory(
                kind="fact",
                scope="sender",
                scope_value="alice@example.com",
                content="Alice prefers bullet points.",
            ),
            ExtractedMemory(
                kind="preference",
                scope="domain",
                scope_value="example.com",
                content="Company uses CET timezone.",
            ),
        ]
        output = ExtractMemoriesOutput(memories=memories)
        assert len(output.memories) == 2
        assert output.memories[0].kind == "fact"
        assert output.memories[1].kind == "preference"

    def test_default_empty_list(self) -> None:
        """Assert memories defaults to empty list."""
        output = ExtractMemoriesOutput()
        assert output.memories == []

    def test_max_8_memories_accepted(self) -> None:
        """Assert exactly 8 memories are accepted."""
        memories = [
            ExtractedMemory(
                kind="fact",
                scope="global",
                scope_value="global",
                content=f"Memory {i}",
            )
            for i in range(8)
        ]
        output = ExtractMemoriesOutput(memories=memories)
        assert len(output.memories) == 8

    def test_9_memories_rejected(self) -> None:
        """Assert 9 memories raise ValidationError."""
        memories = [
            ExtractedMemory(
                kind="fact",
                scope="global",
                scope_value="global",
                content=f"Memory {i}",
            )
            for i in range(9)
        ]
        with pytest.raises(ValidationError) as exc_info:
            ExtractMemoriesOutput(memories=memories)
        assert "too_long" in str(exc_info.value).lower()

    def test_single_memory(self) -> None:
        """Assert single memory is accepted."""
        memory = ExtractedMemory(
            kind="procedure",
            scope="sender",
            scope_value="bob@example.com",
            content="Bob always signs emails",
        )
        output = ExtractMemoriesOutput(memories=[memory])
        assert len(output.memories) == 1
        assert output.memories[0].kind == "procedure"
