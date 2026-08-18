"""Mission A — 'Draft a reply'. Ends awaiting_user with the draft artifact."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

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


def test_mission_a_ends_awaiting_user():
    pending_payload = json.dumps(
        {
            "answer": "Draft ready",
            "pending_user_input": {
                "kind": "approve_draft",
                "artifact": {
                    "draft": "Hi Bob — thanks!",
                    "to": "bob@x",
                    "subject": "Re: hi",
                },
            },
        }
    )

    atlas_msgs = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "delegate_to_plume",
                    "id": "c1",
                    "args": {"query": "draft reply to demo-msg-1"},
                }
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "finish_mission",
                    "id": "c2",
                    "args": {"final_answer": pending_payload, "outcome": "done"},
                }
            ],
        ),
    ]
    plume_msgs = [AIMessage(content=pending_payload)]

    from twaky.daemon import atlas_daemon
    from twaky.missions import engine, repository

    # Isolated owner_email (RFC 6761 .invalid TLD) — otherwise the live
    # atlas daemon claims the mission via mission_declared NOTIFY before
    # our patched _run_mission_sync fires, races through the real graph,
    # and leaves the state at awaiting_user with wrong artifacts.
    # See docs/superpowers/investigations/2026-08-10-nine-flakes.md flake #5.
    _isolated_owner = "mission-a-test@test.invalid"
    m = engine.declare(
        intent_text="Draft a reply to demo-msg-1",
        owner_email=_isolated_owner,
        declared_by=_isolated_owner,
    )

    with (
        patch("twaky.agents.atlas.agent._make_llm", return_value=scripted(atlas_msgs)),
        patch("twaky.agents.plume.agent._make_llm", return_value=scripted(plume_msgs)),
        patch("twaky.agents.plume.tools.JmapClient") as C,
        patch("twaky.agents.plume.tools.bearer_token_for_owner", return_value="TOK"),
    ):
        inst = C.return_value
        inst.email_get = AsyncMock(
            return_value=[
                {
                    "id": "demo-msg-1",
                    "subject": "Hi",
                    "from": [{"email": "bob@x"}],
                    "textBody": [{"partId": "1"}],
                    "bodyValues": {"1": {"value": "Hello"}},
                }
            ]
        )
        atlas_daemon._run_mission_sync(m.id)

    got = repository.get(m.id)
    assert got.state.value == "awaiting_user"
    # ``kind`` in the fake payload maps to ``state_reason`` on the mission
    # via ``engine.request_user_input(reason=pending.get("kind"), …)``.
    # The artifact dict itself carries {draft, to, subject} — not a
    # ``kind`` key. Previous assertion (``"approve_draft" in kinds``)
    # only ever passed by accident when the live atlas daemon raced
    # ahead of the test's patched graph and produced a different
    # artifact shape. See flake #5 in
    # docs/superpowers/investigations/2026-08-10-nine-flakes.md.
    assert got.state_reason == "approve_draft"
    assert any((a.get("draft") == "Hi Bob — thanks!") for a in got.artifacts), (
        f"expected draft artifact, got {got.artifacts!r}"
    )
    # Cleanup.
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mission WHERE id = %s", (m.id,))
        conn.commit()
