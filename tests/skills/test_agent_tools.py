"""Collision-guard tests for skills.agent_tools.merged_tools_for."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from twaky.skills import agent_tools
from twaky.skills_config.models import Skill


def _mk_skill(name: str) -> Skill:
    return Skill(
        id=uuid4(),
        name=name,
        description="d",
        python_source="def run(**kw): return 1",
        config_schema={},
        config_values={},
        bound_agents=["atlas"],
        enabled=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _fake_builtin(name: str):
    # StructuredTool-like shape — just needs a `.name` attribute.
    return SimpleNamespace(name=name)


def test_merged_appends_skills_after_builtins():
    builtins = [_fake_builtin("finish_mission")]
    with patch.object(
        agent_tools, "load_skills_for_agent", return_value=[_mk_skill("echo")]
    ):
        merged = agent_tools.merged_tools_for("atlas", builtins)
    assert len(merged) == 2
    assert merged[0].name == "finish_mission"
    assert merged[1].name == "echo"


def test_colliding_skill_is_dropped():
    builtins = [_fake_builtin("finish_mission")]
    with patch.object(
        agent_tools,
        "load_skills_for_agent",
        return_value=[_mk_skill("finish_mission"), _mk_skill("safe")],
    ):
        merged = agent_tools.merged_tools_for("atlas", builtins)
    names = [t.name for t in merged]
    assert names == ["finish_mission", "safe"]


def test_multiple_collisions_dropped_and_warned(caplog):
    builtins = [_fake_builtin("finish_mission"), _fake_builtin("delegate_to_plume")]
    with (
        patch.object(
            agent_tools,
            "load_skills_for_agent",
            return_value=[
                _mk_skill("finish_mission"),
                _mk_skill("delegate_to_plume"),
                _mk_skill("ok"),
            ],
        ),
        caplog.at_level("WARNING", logger="twaky.skills.agent_tools"),
    ):
        merged = agent_tools.merged_tools_for("atlas", builtins)
    assert [t.name for t in merged] == ["finish_mission", "delegate_to_plume", "ok"]
    assert "dropped 2 skill(s)" in caplog.text
    # Also pin the agent_id in the warning — regression guard against a
    # future log-format change that could obscure WHICH agent had the drop.
    assert "agent=atlas" in caplog.text


def test_no_skills_returns_builtins_unchanged():
    builtins = [_fake_builtin("finish_mission")]
    with patch.object(agent_tools, "load_skills_for_agent", return_value=[]):
        merged = agent_tools.merged_tools_for("atlas", builtins)
    assert [t.name for t in merged] == ["finish_mission"]
