"""Pydantic validation for the future P2P envelope."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from twaky.missions.envelope import Envelope, Intent


class TestIntent:
    def test_all_initial_intents_present(self):
        assert Intent.ASK_AVAILABILITY in Intent
        assert Intent.PROPOSE_MEETING in Intent
        assert Intent.DELEGATE_TASK in Intent
        assert Intent.SHARE_INFO in Intent
        assert Intent.ACK in Intent


class TestEnvelope:
    def _base(self, **kw):
        now = datetime.now(UTC)
        return {
            "envelope_version": "1",
            "message_id": f"urn:uuid:{uuid4()}",
            "correlation_id": f"urn:uuid:{uuid4()}",
            "from_email": "alice@x",
            "to_email": "bob@x",
            "sent_at": now,
            "expires_at": now + timedelta(minutes=5),
            "intent": Intent.ACK,
            "payload": {"ok": True},
            **kw,
        }

    def test_minimal_ok(self):
        e = Envelope(**self._base())
        assert e.intent == Intent.ACK

    def test_expires_after_sent_at(self):
        now = datetime.now(UTC)
        with pytest.raises(ValidationError, match="expires_at"):
            Envelope(**self._base(sent_at=now, expires_at=now - timedelta(seconds=1)))

    def test_serialize_roundtrip(self):
        e1 = Envelope(**self._base())
        e2 = Envelope.model_validate_json(e1.model_dump_json())
        assert e1 == e2
