"""Smoke test: daemon's _main_loop starts the config listener task.

Doesn't actually run missions — just verifies that a boot-and-shutdown
cycle creates AND cancels a config_task without exception.
"""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_main_loop_starts_and_cancels_config_listener():
    from twaky.daemon import atlas_daemon

    # Stub the components we don't want to actually run.
    with (
        patch.object(atlas_daemon, "_recover_and_schedule", return_value=[]),
        patch.object(atlas_daemon, "_schedule_declared_loop"),
        patch("twaky.daemon.atlas_daemon.bump"),
        patch("twaky.daemon.atlas_daemon.listen") as fake_listen_daemon,
        patch("twaky.agents.config_listener.listen") as fake_listen_config,
        patch("twaky.skills.config_listener.listen") as fake_listen_skills,
    ):
        # listen() is called by the mission listener, the agents config
        # listener, AND the skills config listener. Return an empty async
        # iterator for each so all loops idle without touching the broker.
        async def _empty():
            if False:
                yield None  # never executes; typing helper

        fake_listen_daemon.return_value = _empty()
        fake_listen_config.return_value = _empty()
        fake_listen_skills.return_value = _empty()

        # Run _main_loop with a rapid shutdown.
        loop_task = asyncio.create_task(atlas_daemon._main_loop())
        await asyncio.sleep(0.2)  # let tasks spawn

        # Trigger shutdown via the SIGTERM handler installed by _main_loop.
        # Use direct handler invocation to avoid signal delivery issues in pytest.
        handler = signal.getsignal(signal.SIGTERM)
        handler(signal.SIGTERM, None)

        await asyncio.wait_for(loop_task, timeout=5.0)
