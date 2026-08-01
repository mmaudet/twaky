"""Owner-filter dispatch table — one unit test per family."""

from __future__ import annotations

import pytest

from twaky.owner_filter import matches_owner

OWNER = "alice@example.com"


class TestCalendar:
    def test_owner_is_organizer(self):
        p = {"uid": "e1", "organizer": {"email": OWNER}, "attendees": []}
        assert matches_owner("calendar:event:created", p, OWNER)

    def test_owner_is_attendee(self):
        p = {
            "uid": "e1",
            "organizer": {"email": "x@y"},
            "attendees": [{"email": OWNER}],
        }
        assert matches_owner("calendar:event:updated", p, OWNER)

    def test_owner_neither(self):
        p = {
            "uid": "e1",
            "organizer": {"email": "x@y"},
            "attendees": [{"email": "z@y"}],
        }
        assert not matches_owner("calendar:event:created", p, OWNER)

    def test_owner_with_missing_fields(self):
        assert not matches_owner("calendar:event:created", {}, OWNER)


class TestSabreContact:
    def test_owner_matches_email(self):
        p = {"email": OWNER, "fn": "Alice"}
        assert matches_owner("sabre:contact:created", p, OWNER)

    def test_owner_no_match(self):
        p = {"email": "someone@else", "fn": "Someone"}
        assert not matches_owner("sabre:contact:updated", p, OWNER)


class TestMail:
    def test_owner_is_mailbox_user(self):
        p = {"user": OWNER, "message_id": "m1"}
        assert matches_owner("mail:message:received", p, OWNER)

    def test_owner_no_match(self):
        p = {"user": "other@example.com", "message_id": "m1"}
        assert not matches_owner("mail:message:expunged", p, OWNER)


class TestUnknown:
    def test_unknown_exchange_drops(self):
        # Safe default: unknown → False (drop). Never pollute the graph.
        assert not matches_owner("something:else", {"anything": True}, OWNER)


class TestIngestWiring:
    """Verify _consume drops non-owner events before insert."""

    @pytest.mark.asyncio
    async def test_non_owner_event_is_acked_and_dropped(self, monkeypatch):
        # Import here so the module-under-test picks up patched settings.
        from twaky import ingest

        # Fake message: calendar event NOT concerning the owner.
        acked = []
        inserted = []

        class FakeMessage:
            exchange = "calendar:event:created"
            routing_key = ""
            body = b'{"uid":"e1","organizer":{"email":"stranger@x"},"attendees":[]}'
            message_id = "verify-e1"

            async def ack(self):
                acked.append(True)

            async def reject(self, requeue):
                pass

        def _fake_insert(*a, **kw):
            inserted.append(True)
            return True

        monkeypatch.setattr(ingest, "_insert_event", _fake_insert)
        monkeypatch.setattr(ingest.settings, "twaky_owner_email", OWNER)

        # Consume ONE message.
        class FakeIter:
            def __init__(self, items):
                self.items = list(items)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self.items:
                    raise StopAsyncIteration
                return self.items.pop(0)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

        class FakeQueue:
            def iterator(self):
                return FakeIter([FakeMessage()])

        await ingest._consume(FakeQueue())

        assert acked == [True]
        assert inserted == []  # dropped, not inserted
