"""Owner-filter dispatch table — one unit test per family."""

from __future__ import annotations

from twaky.owner_filter import matches_owner

OWNER = "alice@example.com"


class TestCalendar:
    def test_owner_is_organizer(self):
        p = {"uid": "e1", "organizer": {"email": OWNER}, "attendees": []}
        assert matches_owner("calendar:event:created", p, OWNER)

    def test_owner_is_attendee(self):
        p = {"uid": "e1", "organizer": {"email": "x@y"}, "attendees": [{"email": OWNER}]}
        assert matches_owner("calendar:event:updated", p, OWNER)

    def test_owner_neither(self):
        p = {"uid": "e1", "organizer": {"email": "x@y"}, "attendees": [{"email": "z@y"}]}
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
