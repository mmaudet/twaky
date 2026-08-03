"""Wrap a Skill row into a LangChain StructuredTool.

The LLM sees each skill as a callable whose name is skill.name and whose
description is skill.description. Errors are converted to human-readable
strings — the LLM gets to decide whether to retry, apologize, or abandon.
"""

from __future__ import annotations

import json

from langchain_core.tools import StructuredTool

from twaky.skills.executor import (
    SkillCrashed,
    SkillError,
    SkillTimeout,
    run_skill,
)
from twaky.skills_config.models import Skill


def skill_to_tool(skill: Skill) -> StructuredTool:
    def _invoke(**kwargs) -> str:
        try:
            result = run_skill(
                python_source=skill.python_source,
                args=kwargs,
                config=skill.config_values,
                timeout_s=30,
                memory_limit_mb=256,
                cpu_seconds=60,
            )
        except SkillTimeout:
            return f"skill '{skill.name}' timed out after 30s"
        except SkillCrashed as exc:
            return f"skill '{skill.name}' crashed: {exc}"
        except SkillError as exc:
            return f"skill '{skill.name}' raised: {exc}"

        if isinstance(result, str):
            return result
        return json.dumps(result, default=str)

    return StructuredTool.from_function(
        name=skill.name,
        description=skill.description,
        func=_invoke,
        # Raw JSON Schema dict — accepted per
        # ``langchain_core.tools.base.py`` ArgsSchema = ``TypeBaseModel |
        # dict[str, Any]``. Bypasses LangChain's auto-derivation from
        # ``_invoke``'s signature: default derivation introspects
        # ``**kwargs``, generates a synthetic ``kwargs`` field, and then
        # STRIPS that field back out in ``_parse_input`` (base.py:843) —
        # ``_invoke`` receives an empty dict on every call. Do not remove.
        args_schema={"type": "object", "additionalProperties": True},
    )


__all__ = ["skill_to_tool"]
