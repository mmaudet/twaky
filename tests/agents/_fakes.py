"""Test helpers — a scriptable fake LLM matching the ChatLiteLLM interface."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import BaseMessage


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
