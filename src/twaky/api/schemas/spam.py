"""Pydantic models for the /mail-sentinel/spam surface."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SpamDecision(BaseModel):
    """Full representation of a mail_sentinel_spam_decision row."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    email_id: str
    thread_id: str | None
    sender_email: str
    subject: str
    received_at: datetime
    bucket: str
    signal_source: str
    score: float | None
    reason: str | None
    restored_at: datetime | None
    restored_by: str | None
    decided_at: datetime


class SpamStats(BaseModel):
    """Aggregated counts for the /mail-sentinel/spam/stats endpoint."""

    model_config = ConfigDict(extra="forbid")

    spam: int
    newsletter: int
    phishing_alert: int
    restored: int
    total_processed: int


__all__ = ["SpamDecision", "SpamStats"]
