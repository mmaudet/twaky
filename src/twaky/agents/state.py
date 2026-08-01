"""Shared TypedDicts for Atlas + sub-agent StateGraphs."""

from __future__ import annotations

from typing import Annotated, TypedDict
from uuid import UUID

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AtlasState(TypedDict, total=False):
    mission_id: UUID
    owner_email: str
    intent_text: str
    messages: Annotated[list[BaseMessage], add_messages]
    artifacts: list[dict]
    step_count: int
    pending_user_input: dict | None
    total_tokens: int  # accumulated token usage across all LLM calls in this mission


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]


__all__ = ["AgentState", "AtlasState"]
