"""twaky-api FastAPI application.

Exposes the mission engine over REST + SSE for the Twaky Control Tower.
Auth is cookie-only OIDC session against LemonLDAP-NG (see spec §5).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from twaky.api.routers import health
from twaky.api.session import SESSION_COOKIE_NAME
from twaky.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup hooks (broker, etc.) go here in later tasks.
    yield
    # Shutdown hooks go here.


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


__all__ = ["app"]
