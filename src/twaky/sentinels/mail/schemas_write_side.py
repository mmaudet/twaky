"""Pydantic output schemas for SP5b write-side extractors."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ExtractedMemory(BaseModel):
    kind: Literal["fact", "procedure", "preference"]
    scope: Literal["sender", "domain", "global"]
    scope_value: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=200)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("scope_value")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()


class DraftDiffOutput(BaseModel):
    memories: list[ExtractedMemory] = Field(default_factory=list)
    should_delete_previous_memory_ids: list[UUID] = Field(default_factory=list)


class FolderMoveOutput(BaseModel):
    should_extract: bool
    memory: ExtractedMemory | None = None


__all__ = ["DraftDiffOutput", "ExtractedMemory", "FolderMoveOutput"]
