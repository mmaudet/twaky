"""Engine transition integration tests — one legal + one illegal path each."""

from __future__ import annotations

import os

import psycopg
import pytest

from twaky.config import settings
from twaky.missions import engine, repository
from twaky.missions.guards import InvalidTransition
from twaky.missions.models import MissionState, PlanStep


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable")


def _cleanup(mid):
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mission WHERE id = %s", (mid,))
        conn.commit()


def test_declare_creates_row():
    m = engine.declare(intent_text="do X", owner_email="a@x", declared_by="a@x")
    got = repository.get(m.id)
    assert got is not None
    assert got.state == MissionState.DECLARED
    _cleanup(m.id)


def test_full_happy_path():
    m = engine.declare(intent_text="do X", owner_email="a@x", declared_by="a@x")
    engine.start_planning(m.id)
    assert repository.get(m.id).state == MissionState.PLANNING

    plan = [PlanStep(agent="chronos", tool="list_events", args={})]
    engine.commit_plan(m.id, plan)
    got = repository.get(m.id)
    assert got.state == MissionState.RUNNING
    assert got.plan == plan

    engine.request_user_input(m.id, reason="approve draft", artifact={"draft": "hi"})
    assert repository.get(m.id).state == MissionState.AWAITING_USER

    engine.resume(m.id, user_response={"ok": True})
    assert repository.get(m.id).state == MissionState.RUNNING

    engine.finish(m.id, outcome="done", artifacts=[{"final": "ok"}])
    final = repository.get(m.id)
    assert final.state == MissionState.DONE
    # The brief's equality check on artifacts was a bug: resume() appends a
    # {"kind": "user_response", ...} artifact between the draft and the final,
    # so the list has 3 entries, not 2. Instead we check structural invariants:
    # first artifact is the draft, last is the final, and a user_response is in between.
    assert len(final.artifacts) == 3
    assert final.artifacts[0] == {"draft": "hi"}
    assert final.artifacts[-1] == {"final": "ok"}
    assert final.artifacts[1]["kind"] == "user_response"
    assert final.artifacts[1]["payload"] == {"ok": True}
    _cleanup(m.id)


def test_illegal_transition_rejected():
    m = engine.declare(intent_text="X", owner_email="a@x", declared_by="a@x")
    with pytest.raises(InvalidTransition):
        engine.commit_plan(m.id, [])  # DECLARED → RUNNING skipping PLANNING
    assert repository.get(m.id).state == MissionState.DECLARED  # unchanged
    _cleanup(m.id)


def test_cancel_from_any_non_terminal():
    m = engine.declare(intent_text="X", owner_email="a@x", declared_by="a@x")
    engine.start_planning(m.id)
    engine.cancel(m.id, reason="user_aborted")
    got = repository.get(m.id)
    assert got.state == MissionState.CANCELLED
    assert got.state_reason == "user_aborted"
    _cleanup(m.id)


def test_park_for_review_lands_in_awaiting_user_with_artifact_and_reason():
    artifact = {
        "kind": "sentinel_evidence",
        "sentinel": "mail",
        "evidence": {"email_id": "eml-99"},
    }
    m = engine.park_for_review(
        intent_text="Mail: sentinel smoke test",
        owner_email="owner@example.com",
        declared_by="sentinel:mail",
        reason="needs owner review",
        artifact=artifact,
    )
    got = repository.get(m.id)
    assert got is not None
    assert got.state == MissionState.AWAITING_USER, (
        f"expected AWAITING_USER, got {got.state}"
    )
    assert got.state_reason == "needs owner review"
    assert got.declared_by == "sentinel:mail"
    assert len(got.artifacts) == 1
    assert got.artifacts[0] == artifact
    _cleanup(m.id)


class TestLangfuseInstrumentation:
    def test_declare_emits_trace(self, monkeypatch):
        """When langfuse creds are set, engine.declare should call the client."""
        # Skip if not configured — we don't want CI to require creds.
        if not (settings.langfuse_public_key and settings.langfuse_secret_key):
            pytest.skip("langfuse not configured in this environment")

        seen: list[str] = []

        # Wrap the real Langfuse client to record start_as_current_span names.
        import twaky.observability as obs

        real_client = obs.get_client()
        if real_client is None:
            pytest.skip("langfuse client unavailable")

        orig_span = real_client.start_as_current_span

        def _spy(name, **kw):
            seen.append(name)
            return orig_span(name=name, **kw)

        monkeypatch.setattr(real_client, "start_as_current_span", _spy)

        m = engine.declare(intent_text="X", owner_email="a@x", declared_by="a@x")
        engine.start_planning(m.id)
        engine.cancel(m.id, reason="test_over")
        assert any(n.startswith("mission.declare") for n in seen)
        assert any(n.startswith("mission.start_planning") for n in seen)
        assert any(n.startswith("mission.cancel") for n in seen)
        _cleanup(m.id)


class TestMissionChangedChannel:
    """Every transition emits NOTIFY mission_changed with unified payload."""

    def test_declare_emits_mission_changed(self):
        import json

        import psycopg

        from twaky.missions import engine as _engine

        with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("LISTEN mission_changed")
            m = _engine.declare(intent_text="mc", owner_email="a@x", declared_by="a@x")
            conn.execute("SELECT 1")
            got = []
            for n in conn.notifies(timeout=2):
                got.append(n.payload)
                break
        assert got, "expected at least one mission_changed"
        payload = json.loads(got[0])
        assert payload["mission_id"] == str(m.id)
        assert payload["state"] == "declared"
        assert "at" in payload
        _cleanup(m.id)

    def test_full_lifecycle_emits_seven_events(self):
        import json

        import psycopg

        from twaky.missions import engine as _engine
        from twaky.missions.models import PlanStep

        events: list[dict] = []
        m = _engine.declare(intent_text="mc-lc", owner_email="a@x", declared_by="a@x")
        with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("LISTEN mission_changed")
            _engine.start_planning(m.id)
            _engine.commit_plan(m.id, [PlanStep(agent="atlas", tool="noop", args={})])
            _engine.request_user_input(m.id, reason="ok", artifact={"draft": "x"})
            _engine.resume(m.id, user_response={"approved": True})
            _engine.finish(m.id, outcome="done", artifacts=[])
            conn.execute("SELECT 1")
            for n in conn.notifies(timeout=2):
                events.append(json.loads(n.payload))
                if len(events) >= 5:
                    break
        states = [e["state"] for e in events if e["mission_id"] == str(m.id)]
        # planning, running, awaiting_user, running, done — declare already fired before LISTEN
        assert "planning" in states
        assert "running" in states
        assert "awaiting_user" in states
        assert "done" in states
        _cleanup(m.id)


class TestEngineNotify:
    """engine.declare and engine.resume emit PG NOTIFY events."""

    def test_declare_notifies_mission_declared(self):
        import psycopg

        from twaky.missions import engine as _engine

        with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("LISTEN mission_declared")
            m = _engine.declare(
                intent_text="notify", owner_email="a@x", declared_by="a@x"
            )
            conn.execute("SELECT 1")  # flush
            notified = []
            for n in conn.notifies(timeout=2):
                notified.append(n.payload)
                break
        assert str(m.id) in notified
        _cleanup(m.id)

    def test_resume_notifies_mission_resumed(self):
        import psycopg

        from twaky.missions import engine as _engine
        from twaky.missions.models import PlanStep

        m = _engine.declare(
            intent_text="notify-resume", owner_email="a@x", declared_by="a@x"
        )
        _engine.start_planning(m.id)
        _engine.commit_plan(m.id, [PlanStep(agent="atlas", tool="noop", args={})])
        _engine.request_user_input(m.id, reason="ok", artifact={"draft": "x"})
        with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("LISTEN mission_resumed")
            _engine.resume(m.id, user_response={"ok": True})
            conn.execute("SELECT 1")
            notified = []
            for n in conn.notifies(timeout=2):
                notified.append(n.payload)
                break
        assert str(m.id) in notified
        _cleanup(m.id)
