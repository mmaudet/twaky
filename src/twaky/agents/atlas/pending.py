"""Cooperative pending_user_input seam — inspection helper for the daemon.

Sub-agents (Plume) return a final message whose content is a JSON string
with shape:

    {"answer": "...", "pending_user_input": {"kind": "...", "artifact": {...}}}

The Atlas orchestrator's delegate tool returns that content verbatim to
the atlas_router, which usually then calls finish_mission. When the
daemon receives the final AtlasState, it walks recent messages, tries to
parse them as JSON, and extracts the pending_user_input if any — that
value goes to engine.request_user_input.
"""

from __future__ import annotations

import json
from typing import Any


def extract_pending_from_output(state: dict) -> dict | None:
    """Return the latest pending_user_input JSON in the message history,
    UNLESS the user has already responded to it.

    Walks the last 6 messages. Finds the newest message whose content is a
    JSON dict with a ``pending_user_input`` field. If a ``HumanMessage``
    (``msg.type == "human"``) appears after that pending JSON in the same
    window, the user has already answered (engine.resume injects the
    user_response as a HumanMessage seed on the next graph.invoke) — the
    pending is stale and must NOT re-trigger awaiting_user.

    Without this guard, resumed missions loop: the LLM finishes with a
    finish_mission marker, but the pending JSON is still in message
    history, so this helper re-fires request_user_input and the mission
    bounces back to awaiting_user forever.
    """
    msgs = state.get("messages", []) or []
    window = msgs[-6:]
    latest_pending: dict | None = None
    latest_pending_idx: int = -1
    for idx, m in enumerate(window):
        content: Any = getattr(m, "content", "")
        if not isinstance(content, str) or not content:
            continue
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict) and isinstance(
            parsed.get("pending_user_input"), dict
        ):
            latest_pending = parsed["pending_user_input"]
            latest_pending_idx = idx
    if latest_pending is None:
        return None
    # A HumanMessage after the pending means the user already responded
    # (engine.resume seeded it) — the pending is stale.
    for later in window[latest_pending_idx + 1 :]:
        if getattr(later, "type", "") == "human":
            return None
    return latest_pending


__all__ = ["extract_pending_from_output"]
