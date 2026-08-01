"""SSEBroker — subscribe / unsubscribe / broadcast / queue-full drop-oldest."""

from __future__ import annotations

import asyncio

import pytest

from twaky.api.sse.broker import SSEBroker


class TestSubscribeUnsubscribe:
    def test_subscribe_returns_uuid_and_queue(self):
        broker = SSEBroker()
        sub_id, queue = broker.subscribe()
        assert sub_id in broker.subscribers
        assert isinstance(queue, asyncio.Queue)
        broker.unsubscribe(sub_id)
        assert sub_id not in broker.subscribers

    def test_unsubscribe_unknown_is_noop(self):
        from uuid import uuid4

        broker = SSEBroker()
        broker.unsubscribe(uuid4())  # no raise


class TestBroadcast:
    @pytest.mark.asyncio
    async def test_broadcast_delivers_to_all_subscribers(self):
        broker = SSEBroker()
        _, q1 = broker.subscribe()
        _, q2 = broker.subscribe()
        broker._broadcast({"mission_id": "abc", "state": "declared", "at": "t"})
        got1 = await asyncio.wait_for(q1.get(), timeout=1)
        got2 = await asyncio.wait_for(q2.get(), timeout=1)
        assert got1["state"] == "declared"
        assert got2["state"] == "declared"

    @pytest.mark.asyncio
    async def test_queue_full_drops_oldest(self):
        broker = SSEBroker(queue_maxsize=2)
        _, q = broker.subscribe()
        broker._broadcast({"n": 1})
        broker._broadcast({"n": 2})
        broker._broadcast({"n": 3})  # forces drop of oldest (n=1)
        drained = []
        while not q.empty():
            drained.append(q.get_nowait())
        # After overflow, the queue holds the two most recent.
        ns = [e["n"] for e in drained]
        assert ns == [2, 3]
