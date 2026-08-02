"""Test helpers — a scriptable fake LLM matching the ChatLiteLLM interface."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import BaseMessage

from twaky.agents_config.models import AgentConfig


class FakeToolLLM(FakeMessagesListChatModel):
    """Wraps FakeMessagesListChatModel so `.bind_tools` returns self.

    FakeMessagesListChatModel from langchain_core replays a canned list of
    AIMessages including tool_calls, which is what we need to drive
    StateGraph tests without a real API.
    """

    def bind_tools(self, tools: list[Any], **kwargs: Any):  # type: ignore[override]
        return self


def scripted(messages: list[BaseMessage]) -> FakeToolLLM:
    return FakeToolLLM(responses=messages)


def make_fake_config(
    agent_id: str,
    system_prompt: str = "TEST SYSTEM PROMPT",
    model: str | None = None,
    temperature: float | None = None,
) -> AgentConfig:
    return AgentConfig(
        id=agent_id,
        display_name=agent_id.capitalize(),
        role="orchestrator" if agent_id == "atlas" else "specialist",
        system_prompt=system_prompt,
        model=model,
        temperature=temperature,
        updated_at=datetime.now(UTC),
    )


def stub_registry_for(agent_id: str, **cfg_kwargs):
    """Returns a context manager that stubs load_agent_config for one agent."""
    fake = make_fake_config(agent_id, **cfg_kwargs)
    patch_path = f"twaky.agents.{agent_id}.agent.load_agent_config"
    return patch(patch_path, return_value=fake)
