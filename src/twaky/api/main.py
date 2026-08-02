"""twaky-api FastAPI application.

Exposes the mission engine over REST + SSE for the Twaky Control Tower.
Auth is cookie-only OIDC session against LemonLDAP-NG (see spec §5).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from twaky.api.errors import register_exception_handlers
from twaky.api.routers import agents, events, health, me, missions, oauth
from twaky.api.session import SESSION_COOKIE_NAME
from twaky.api.sse.broker import SSEBroker
from twaky.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    broker = SSEBroker()
    await broker.start()
    app.state.broker = broker
    try:
        yield
    finally:
        await broker.stop()


app = FastAPI(
    title="Twaky API",
    version="0.1.0",
    description="HTTP + SSE surface over the Twaky mission engine.",
    lifespan=lifespan,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.api_session_secret,
    session_cookie=SESSION_COOKIE_NAME,
    max_age=28800,
    same_site="lax",
    https_only=True,
    path="/",
)

app.include_router(health.router)
app.include_router(me.router)
app.include_router(missions.router)
app.include_router(oauth.router)
app.include_router(events.router)
app.include_router(agents.router, prefix="/api")

register_exception_handlers(app)


__all__ = ["app"]
