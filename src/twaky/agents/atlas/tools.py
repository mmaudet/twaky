"""Atlas orchestrator @tools — delegate to sub-agents + terminate."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

FINISH_MARKER = "__ATLAS_FINISH__"


@lru_cache(maxsize=1)
def _chronos():
    from twaky.agents.chronos.agent import build_chronos_agent

    return build_chronos_agent()


@lru_cache(maxsize=1)
def _plume():
    from twaky.agents.plume.agent import build_plume_agent

    return build_plume_agent()


@lru_cache(maxsize=1)
def _iris():
    from twaky.agents.iris.agent import build_iris_agent

    return build_iris_agent()


def _last_content(state: dict) -> str:
    msgs = state.get("messages", [])
    if not msgs:
        return ""
    last = msgs[-1]
    c = getattr(last, "content", "")
    return c if isinstance(c, str) else str(c)


@tool
def delegate_to_chronos(query: str) -> str:
    """Delegate a calendar-related sub-question to Chronos."""
    state = _chronos().invoke({"messages": [HumanMessage(content=query)]})
    return _last_content(state)


@tool
def delegate_to_plume(query: str) -> str:
    """Delegate a mail-related sub-question to Plume."""
    state = _plume().invoke({"messages": [HumanMessage(content=query)]})
    return _last_content(state)


@tool
def delegate_to_iris(query: str) -> str:
    """Delegate a research / lookup sub-question to Iris."""
    state = _iris().invoke({"messages": [HumanMessage(content=query)]})
    return _last_content(state)


@tool
def finish_mission(
    final_answer: str, outcome: Literal["done", "failed"] = "done"
) -> str:
    """Signal that the mission is complete. `outcome` is 'done' or 'failed'."""
    return f"{FINISH_MARKER}|{outcome}|{final_answer}"


DELEGATION_TOOLS = [delegate_to_chronos, delegate_to_plume, delegate_to_iris]
ALL_TOOLS = [*DELEGATION_TOOLS, finish_mission]

__all__ = [
    "ALL_TOOLS",
    "DELEGATION_TOOLS",
    "FINISH_MARKER",
    "delegate_to_chronos",
    "delegate_to_iris",
    "delegate_to_plume",
    "finish_mission",
]
