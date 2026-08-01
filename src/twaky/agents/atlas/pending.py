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
    """Walk the last few messages, return the first pending_user_input found."""
    msgs = state.get("messages", []) or []
    for m in reversed(msgs[-6:]):
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
            return parsed["pending_user_input"]
    return None


__all__ = ["extract_pending_from_output"]
