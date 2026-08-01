"""Pydantic Mission model + PlanStep + state enum."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from twaky.missions.models import Mission, MissionState, PlanStep


class TestMissionState:
    def test_all_states(self):
        assert set(MissionState) == {
            MissionState.DECLARED, MissionState.PLANNING, MissionState.RUNNING,
            MissionState.AWAITING_USER, MissionState.DONE, MissionState.FAILED,
            MissionState.CANCELLED,
        }

    def test_terminal_helper(self):
        assert MissionState.DONE.is_terminal
        assert MissionState.FAILED.is_terminal
        assert MissionState.CANCELLED.is_terminal
        assert not MissionState.RUNNING.is_terminal


class TestPlanStep:
    def test_default_status_pending(self):
        s = PlanStep(agent="chronos", tool="list_events", args={})
        assert s.status == "pending"

    def test_bad_status_rejected(self):
        with pytest.raises(ValidationError):
            PlanStep(agent="x", tool="y", args={}, status="lol")  # type: ignore[arg-type]


class TestMission:
    def test_minimal_construction(self):
        m = Mission(
            id=uuid4(),
            owner_email="a@x",
            declared_by="a@x",
            declared_at=datetime.now(UTC),
            intent_text="do stuff",
            state=MissionState.DECLARED,
            artifacts=[],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        assert m.state == MissionState.DECLARED
        assert m.plan is None

    def test_plan_typed(self):
        m = Mission(
            id=uuid4(), owner_email="a@x", declared_by="a@x",
            declared_at=datetime.now(UTC), intent_text="do stuff",
            state=MissionState.RUNNING,
            plan=[PlanStep(agent="chronos", tool="list_events", args={"date": "2026-08-01"})],
            artifacts=[],
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        )
        assert m.plan and m.plan[0].agent == "chronos"

    def test_roundtrip_via_json(self):
        m1 = Mission(
            id=UUID("00000000-0000-0000-0000-000000000001"),
            owner_email="a@x", declared_by="a@x",
            declared_at=datetime(2026, 8, 1, tzinfo=UTC),
            intent_text="do stuff", state=MissionState.PLANNING,
            plan=[PlanStep(agent="atlas", tool="plan", args={})],
            artifacts=[], created_at=datetime(2026, 8, 1, tzinfo=UTC),
            updated_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        m2 = Mission.model_validate_json(m1.model_dump_json())
        assert m1 == m2
