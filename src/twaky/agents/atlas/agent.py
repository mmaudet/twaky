"""Atlas orchestrator StateGraph — Supervisor pattern."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_litellm import ChatLiteLLM
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from twaky.agents.atlas.tools import ALL_TOOLS, FINISH_MARKER
from twaky.agents.registry import load_agent_config
from twaky.agents.state import AtlasState
from twaky.agents_config.models import AgentConfig
from twaky.config import settings
from twaky.skills.agent_tools import merged_tools_for


def _make_llm(cfg: AgentConfig) -> BaseChatModel:
    kwargs: dict = {
        "model": cfg.model or settings.model,
        "api_base": settings.litellm_api_base,
    }
    if cfg.temperature is not None:
        kwargs["temperature"] = cfg.temperature
    return ChatLiteLLM(**kwargs)


def _atlas_node(state: AtlasState):
    cfg = load_agent_config("atlas")
    llm = _make_llm(cfg).bind_tools(merged_tools_for("atlas", ALL_TOOLS))
    messages = state.get("messages", [])
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=cfg.system_prompt), *messages]
    ai = llm.invoke(messages)
    step_count = state.get("step_count", 0) + 1
    call_tokens: int = 0
    usage = getattr(ai, "usage_metadata", None)
    if isinstance(usage, dict):
        call_tokens = usage.get("total_tokens", 0) or 0
    total_tokens = state.get("total_tokens", 0) + call_tokens
    return {"messages": [ai], "step_count": step_count, "total_tokens": total_tokens}


def _route(state: AtlasState):
    if state.get("step_count", 0) > settings.atlas_max_steps:
        return END
    msgs = state.get("messages", [])
    if not msgs:
        return END
    last = msgs[-1]
    if getattr(last, "type", "") == "tool":
        content = getattr(last, "content", "") or ""
        if isinstance(content, str) and content.startswith(FINISH_MARKER):
            return END
        return "atlas"
    if getattr(last, "tool_calls", None):
        return "tools"
    return END


def _atlas_tools_node(state: AtlasState):
    tools = merged_tools_for("atlas", ALL_TOOLS)
    return ToolNode(tools).invoke(state)


def build_atlas_agent(checkpointer=None):
    g = StateGraph(AtlasState)
    g.add_node("atlas", _atlas_node)
    g.add_node("tools", _atlas_tools_node)
    g.add_edge(START, "atlas")
    g.add_conditional_edges("atlas", _route, {"tools": "tools", END: END})
    g.add_conditional_edges("tools", _route, {"atlas": "atlas", END: END})
    return g.compile(checkpointer=checkpointer)


__all__ = ["build_atlas_agent"]
