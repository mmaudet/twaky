"""Merge hardcoded agent TOOLS with owner-authored skills.

Skills whose name collides with a built-in tool are DROPPED at bind time
with a warning log. `finish_mission`, `delegate_to_*`, and other hardcoded
tools stay unshadowable.
"""

from __future__ import annotations

import logging
from typing import Any

from twaky.skills.registry import load_skills_for_agent
from twaky.skills.tool_adapter import skill_to_tool

log = logging.getLogger("twaky.skills.agent_tools")


def merged_tools_for(agent_id: str, builtins: list[Any]) -> list[Any]:
    """Return `builtins` + wrapped skills, minus any skill whose name shadows a builtin."""
    builtin_names = {getattr(t, "name", None) for t in builtins}
    skills = load_skills_for_agent(agent_id)
    safe = [s for s in skills if s.name not in builtin_names]
    dropped = len(skills) - len(safe)
    if dropped:
        log.warning(
            "agent=%s dropped %d skill(s) colliding with built-in tool names",
            agent_id,
            dropped,
        )
    return list(builtins) + [skill_to_tool(s) for s in safe]


__all__ = ["merged_tools_for"]
