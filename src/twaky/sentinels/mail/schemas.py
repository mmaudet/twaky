"""Pydantic v2 schemas for mail-sentinel LLM structured outputs."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .state import ThreadStatus


class ChooseRuleOutput(BaseModel):
    """Output from the choose-rule node (match_rules step)."""

    model_config = ConfigDict(extra="forbid")

    rule: str | None = None
    matched_by: Literal["ai", "empty"] = "ai"
    reasoning: str = Field(default="", max_length=800)


class LearnPatternOutput(BaseModel):
    """Output from the learn-pattern node."""

    model_config = ConfigDict(extra="forbid")

    should_learn: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(default="", max_length=800)


class ThreadStatusOutput(BaseModel):
    """Output from the thread-status classification node."""

    model_config = ConfigDict(extra="forbid")

    status: ThreadStatus
    reasoning: str = Field(default="", max_length=800)


class SelectMemoriesOutput(BaseModel):
    """Output from the select-memories node."""

    model_config = ConfigDict(extra="forbid")

    memory_ids: list[UUID] = Field(default_factory=list, max_length=32)


class DraftReplyOutput(BaseModel):
    """Output from the draft-reply node."""

    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=32768)
    language: str = Field(min_length=2, max_length=8)

    @field_validator("language")
    @classmethod
    def lowercase_language(cls, v: str) -> str:
        """Normalize language code to lowercase."""
        return v.lower()


class ExtractedMemory(BaseModel):
    """A memory extracted from a draft edit."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["fact", "procedure", "preference"]
    scope: Literal["sender", "domain", "global"]
    scope_value: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=3, max_length=800)


class ExtractMemoriesOutput(BaseModel):
    """Output from the extract-memories node."""

    model_config = ConfigDict(extra="forbid")

    memories: list[ExtractedMemory] = Field(default_factory=list, max_length=8)


class SpamCheckOutput(BaseModel):
    """Output from the spam-check node."""

    model_config = ConfigDict(extra="forbid")

    bucket: Literal["spam", "newsletter", "phishing-alert", "none"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(max_length=400)


__all__ = [
    "ChooseRuleOutput",
    "DraftReplyOutput",
    "ExtractMemoriesOutput",
    "ExtractedMemory",
    "LearnPatternOutput",
    "SelectMemoriesOutput",
    "SpamCheckOutput",
    "ThreadStatusOutput",
]
