"""Mission B — 'Résume ma journée de demain'. Chronos-heavy, ends done."""

from __future__ import annotations

import os
from unittest.mock import patch

import psycopg
import pytest
from langchain_core.messages import AIMessage

from tests.agents._fakes import scripted
from twaky.config import settings


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable")


@pytest.fixture(autouse=True)
def _checkpointer_tables():
    """Create the langgraph checkpoint_* tables before the mission runs.

    sql/005_init_checkpointer.sh is a deliberate no-op: the tables are created
    at Atlas boot by setup_checkpointer_tables(). Nothing boots Atlas here, so
    against a freshly provisioned database these tests failed with
    UndefinedTable: relation "checkpoints" does not exist. Idempotent.
    """
    from twaky.missions.checkpointer import setup_checkpointer_tables

    setup_checkpointer_tables()


def test_mission_b_completes_done():
    # Script Atlas + Chronos LLMs so no real API is hit.
    atlas_msgs = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "delegate_to_chronos",
                    "id": "c1",
                    "args": {"query": "events tomorrow"},
                }
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "finish_mission",
                    "id": "c2",
                    "args": {
                        "final_answer": "You have 2 events tomorrow: standup and review.",
                        "outcome": "done",
                    },
                }
            ],
        ),
    ]
    chronos_msgs = [
        AIMessage(
            content="You have 2 events tomorrow: standup at 09:00 and review at 14:00."
        )
    ]

    from twaky.daemon import atlas_daemon
    from twaky.missions import engine, repository

    m = engine.declare(
        intent_text="Résume ma journée de demain",
        owner_email=settings.twaky_owner_email,
        declared_by=settings.twaky_owner_email,
    )

    with (
        patch("twaky.agents.atlas.agent._make_llm", return_value=scripted(atlas_msgs)),
        patch(
            "twaky.agents.chronos.agent._make_llm", return_value=scripted(chronos_msgs)
        ),
    ):
        atlas_daemon._run_mission_sync(m.id)

    got = repository.get(m.id)
    assert got.state.value == "done"
    assert got.artifacts
    # Cleanup.
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mission WHERE id = %s", (m.id,))
        conn.commit()
