"""Tool adapter tests. Uses the real executor — same in-process fork tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest

from twaky.skills import tool_adapter
from twaky.skills.executor import SkillCrashed, SkillError, SkillTimeout
from twaky.skills_config.models import Skill

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _mk(**overrides) -> Skill:
    defaults = {
        "id": uuid4(),
        "name": "echo",
        "description": "Echo tool",
        "python_source": "def run(**kwargs):\n    return str(kwargs)",
        "config_schema": {},
        "config_values": {},
        "bound_agents": ["atlas"],
        "enabled": True,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return Skill(**defaults)


def test_skill_to_tool_returns_structured_tool_with_correct_metadata():
    tool = tool_adapter.skill_to_tool(_mk())
    assert tool.name == "echo"
    assert tool.description == "Echo tool"


def test_tool_invoke_returns_string_result():
    tool = tool_adapter.skill_to_tool(
        _mk(python_source="def run(**kwargs): return 'ok:' + kwargs.get('x', '')")
    )
    assert tool.invoke({"x": "hi"}) == "ok:hi"


def test_tool_invoke_serializes_dict_result_as_json():
    tool = tool_adapter.skill_to_tool(
        _mk(python_source="def run(**kwargs): return {'a': 1, 'b': [2, 3]}")
    )
    result = tool.invoke({})
    assert result == '{"a": 1, "b": [2, 3]}'


def test_timeout_maps_to_readable_string():
    tool = tool_adapter.skill_to_tool(_mk(name="slow"))
    with patch("twaky.skills.tool_adapter.run_skill", side_effect=SkillTimeout("boom")):
        assert tool.invoke({}) == "skill 'slow' timed out after 30s"


def test_crash_maps_to_readable_string():
    tool = tool_adapter.skill_to_tool(_mk(name="bad"))
    with patch(
        "twaky.skills.tool_adapter.run_skill", side_effect=SkillCrashed("exit 1")
    ):
        assert tool.invoke({}) == "skill 'bad' crashed: exit 1"


def test_error_maps_to_readable_string():
    tool = tool_adapter.skill_to_tool(_mk(name="raiser"))
    with patch(
        "twaky.skills.tool_adapter.run_skill",
        side_effect=SkillError("ValueError: nope"),
    ):
        assert tool.invoke({}) == "skill 'raiser' raised: ValueError: nope"


def test_config_values_forwarded_as_kwargs():
    tool = tool_adapter.skill_to_tool(
        _mk(
            python_source="def run(query, endpoint): return f'{endpoint}?q={query}'",
            config_values={"endpoint": "https://x"},
        )
    )
    assert tool.invoke({"query": "twake"}) == "https://x?q=twake"
