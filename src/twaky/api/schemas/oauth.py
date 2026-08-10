"""Pydantic schemas for the /mail-sentinel/auth CRUD endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuthStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connected: bool
    provider: str | None = None
    account_email: str | None = None
    account_name: str | None = None
    session_url: str | None = None
    access_token_expires_at: datetime | None = None
    last_refresh_at: datetime | None = None
    last_refresh_error: str | None = None


__all__ = ["AuthStatus"]
