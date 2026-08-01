"""Main loop — unit test with mocked engine, checkpointer, and Atlas graph."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from twaky.daemon import atlas_daemon


@pytest.mark.asyncio
async def test_claim_next_returns_mission_id(monkeypatch):
    with patch("twaky.daemon.atlas_daemon.get_pool") as p:
        cur = MagicMock()
        mid = uuid4()
        cur.fetchone.return_value = (mid,)
        p.return_value.connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cur
        result = atlas_daemon._claim_next("a@x")
    assert result == mid


@pytest.mark.asyncio
async def test_bounded_run_drives_mission_to_finish(monkeypatch):
    mid = uuid4()

    # Fake atlas graph: returns a final state with an AI final answer, no pending input.
    class FakeGraph:
        def invoke(self, state, config=None):
            return {
                "messages": [MagicMock(content="__ATLAS_FINISH__|done|all done")],
                "artifacts": [{"final": "all done"}],
            }

    with (
        patch("twaky.daemon.atlas_daemon.build_atlas_agent", return_value=FakeGraph()),
        patch("twaky.daemon.atlas_daemon.get_checkpointer", return_value=None),
        patch("twaky.daemon.atlas_daemon.repository") as repo,
        patch("twaky.daemon.atlas_daemon.engine") as eng,
        patch(
            "twaky.daemon.atlas_daemon.extract_pending_from_output", return_value=None
        ),
    ):
        m = MagicMock()
        m.id = mid
        m.owner_email = "a@x"
        m.intent_text = "test"
        repo.get.return_value = m
        sem = asyncio.Semaphore(1)
        await atlas_daemon._bounded_run(sem, mid)
        eng.start_planning.assert_called_once_with(mid)
        eng.commit_plan.assert_called_once()
        eng.finish.assert_called_once()
        args, kwargs = eng.finish.call_args
        # positional: (mid, outcome="done", ...)
        assert kwargs.get("outcome") == "done" or (len(args) >= 2 and args[1] == "done")
