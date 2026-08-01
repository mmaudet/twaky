"""GET /events — Server-Sent Events stream of mission_changed."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from twaky.api.deps import get_broker, require_owner
from twaky.api.sse.broker import SSEBroker

router = APIRouter(tags=["events"])

_KEEP_ALIVE_S = 15


async def _stream_events(broker: SSEBroker) -> AsyncIterator[str]:
    sub_id, queue = broker.subscribe()
    try:
        yield "retry: 3000\n\n"
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=_KEEP_ALIVE_S)
                yield f"event: mission_changed\ndata: {json.dumps(payload)}\n\n"
            except TimeoutError:
                yield ": keep-alive\n\n"
    finally:
        broker.unsubscribe(sub_id)


@router.get("/events")
async def events(
    _: Annotated[str, Depends(require_owner)],
    broker: Annotated[SSEBroker, Depends(get_broker)],
) -> StreamingResponse:
    return StreamingResponse(_stream_events(broker), media_type="text/event-stream")


__all__ = ["router"]
