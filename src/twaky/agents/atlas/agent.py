"""Atlas orchestrator StateGraph — Supervisor pattern."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_litellm import ChatLiteLLM
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from twaky.agents.atlas.tools import ALL_TOOLS, FINISH_MARKER
from twaky.agents.state import AtlasState
from twaky.config import settings

_SYSTEM = (
    "You are Atlas, the orchestrator of a personal assistant. Decompose the "
    "user's mission by calling delegate_to_chronos (calendar), "
    "delegate_to_plume (mail), delegate_to_iris (research). "
    "When you have enough information, call finish_mission with a concise "
    "final_answer and outcome='done'. If you cannot make progress after "
    "several attempts, call finish_mission with outcome='failed'."
)


def _make_llm() -> BaseChatModel:
    return ChatLiteLLM(
        model=settings.atlas_model or settings.model,
        api_base=settings.litellm_api_base,
    )


def _atlas_node(state: AtlasState):
    llm = _make_llm().bind_tools(ALL_TOOLS)
    messages = state.get("messages", [])
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=_SYSTEM), *messages]
    ai = llm.invoke(messages)
    step_count = state.get("step_count", 0) + 1
    return {"messages": [ai], "step_count": step_count}


def _route(state: AtlasState):
    if state.get("step_count", 0) > settings.atlas_max_steps:
        return END
    # Look at the last message (may be an AIMessage with tool_calls or a ToolMessage).
    msgs = state.get("messages", [])
    if not msgs:
        return END
    last = msgs[-1]
    # Tool message carrying the finish marker → end.
    if getattr(last, "type", "") == "tool":
        content = getattr(last, "content", "") or ""
        if isinstance(content, str) and content.startswith(FINISH_MARKER):
            return END
        return "atlas"  # loop back after a normal tool response
    # AIMessage: if tool_calls present, route to tools node.
    if getattr(last, "tool_calls", None):
        return "tools"
    # Otherwise the LLM answered without tools — treat as end.
    return END


def build_atlas_agent(checkpointer=None):
    g = StateGraph(AtlasState)
    g.add_node("atlas", _atlas_node)
    g.add_node("tools", ToolNode(ALL_TOOLS))
    g.add_edge(START, "atlas")
    g.add_conditional_edges("atlas", _route, {"tools": "tools", END: END})
    g.add_conditional_edges("tools", _route, {"atlas": "atlas", END: END})
    return g.compile(checkpointer=checkpointer)


__all__ = ["build_atlas_agent"]
