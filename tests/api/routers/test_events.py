"""GET /events SSE handler — unit-level."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from twaky.api.routers.events import _stream_events


class TestStreamEvents:
    @pytest.mark.asyncio
    async def test_emits_retry_first(self):
        broker = MagicMock()
        queue = asyncio.Queue()
        from uuid import uuid4

        broker.subscribe.return_value = (uuid4(), queue)
        broker.unsubscribe = MagicMock()

        gen = _stream_events(broker)
        first = await asyncio.wait_for(gen.__anext__(), timeout=1)
        assert first.startswith("retry:")

    @pytest.mark.asyncio
    async def test_yields_mission_changed_events(self):
        broker = MagicMock()
        queue: asyncio.Queue = asyncio.Queue()
        from uuid import uuid4

        broker.subscribe.return_value = (uuid4(), queue)
        broker.unsubscribe = MagicMock()

        gen = _stream_events(broker)
        _ = await gen.__anext__()  # consume retry

        await queue.put({"mission_id": "abc", "state": "running", "at": "t"})
        chunk = await asyncio.wait_for(gen.__anext__(), timeout=1)
        assert "event: mission_changed" in chunk
        assert '"state": "running"' in chunk
