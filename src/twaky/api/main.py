"""twaky-api FastAPI application.

Exposes the mission engine over REST + SSE for the Twaky Control Tower.
Auth is cookie-only OIDC session against LemonLDAP-NG (see spec §5).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from twaky.api.routers import health


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

app.include_router(health.router)


__all__ = ["app"]
