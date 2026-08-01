"""P2P envelope for future federation (documented, not deployed yet).

Fixing the contract here lets sub-project 2+ code against a stable shape.
Signature scheme is deliberately deferred — see sub-project 4.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class Intent(StrEnum):
    ASK_AVAILABILITY = "ask_availability"
    PROPOSE_MEETING = "propose_meeting"
    DELEGATE_TASK = "delegate_task"
    SHARE_INFO = "share_info"
    ACK = "ack"


class Envelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    envelope_version: str = "1"
    message_id: str            # urn:uuid:<uuid4>
    correlation_id: str        # urn:uuid:<uuid4>
    from_email: str            # sender twaky owner
    to_email: str              # recipient twaky owner (used as routing key)
    sent_at: datetime
    expires_at: datetime
    intent: Intent
    payload: dict[str, Any]

    @model_validator(mode="after")
    def _check_time_ordering(self) -> Envelope:
        if self.expires_at <= self.sent_at:
            raise ValueError("expires_at must be strictly after sent_at")
        return self


__all__ = ["Envelope", "Intent"]
