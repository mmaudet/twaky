"""Unit tests for the Cypher mappers.

Pure functions — no infra needed. Assert generated Cypher structure so
we catch regressions in mapper output shape.
"""

from __future__ import annotations

from twaky.mappers import get_mapper
from twaky.mappers._cypher import cql_literal, props


class TestCypherHelpers:
    def test_cql_literal_string(self):
        assert cql_literal("alice@x") == '"alice@x"'

    def test_cql_literal_int(self):
        assert cql_literal(3) == "3"

    def test_cql_literal_escapes_double_quotes(self):
        assert cql_literal('he said "hi"') == '"he said \\"hi\\""'

    def test_cql_literal_none(self):
        assert cql_literal(None) == "null"

    def test_props_skips_none(self):
        assert props({"a": 1, "b": None, "c": "x"}) == '{a: 1, c: "x"}'


class TestCalendarEventCreated:
    def _mapper(self):
        m = get_mapper("calendar:event:created")
        assert m is not None
        return m

    def test_no_uid_returns_empty(self):
        assert self._mapper()({}) == []

    def test_minimal_event(self):
        stmts = self._mapper()({"uid": "e-1"})
        assert len(stmts) == 1
        assert stmts[0].startswith("MERGE (e:CalendarEvent {")
        assert 'uid: "e-1"' in stmts[0]

    def test_full_event_has_organizer_and_attendees(self):
        stmts = self._mapper()(
            {
                "uid": "e-1",
                "summary": "Standup",
                "start": "2026-08-01T09:00:00Z",
                "organizer": {"email": "a@x", "cn": "Alice"},
                "attendees": [{"email": "b@x", "cn": "Bob"}, {"email": "c@x"}],
            }
        )
        # 1 MERGE for event + 1 for organizer + 2 for attendees = 4
        assert len(stmts) == 4
        combined = "\n".join(stmts)
        assert "ORGANIZED" in combined
        assert combined.count("ATTENDED") == 2
        assert '"a@x"' in combined
        assert '"b@x"' in combined
        assert '"c@x"' in combined

    def test_attendees_without_email_are_skipped(self):
        stmts = self._mapper()(
            {"uid": "e-1", "attendees": [{"cn": "Anon"}, {"email": "b@x"}]}
        )
        assert len(stmts) == 2  # event + one valid attendee
        assert "b@x" in stmts[1]


class TestSabreContactCreated:
    def _mapper(self):
        m = get_mapper("sabre:contact:created")
        assert m is not None
        return m

    def test_no_email_returns_empty(self):
        assert self._mapper()({"fn": "Anon"}) == []

    def test_minimal(self):
        stmts = self._mapper()({"email": "a@x"})
        assert len(stmts) == 1
        assert 'MERGE (p:Person {email: "a@x"})' in stmts[0]

    def test_with_org(self):
        stmts = self._mapper()({"email": "a@x", "fn": "Alice", "org": "Linagora"})
        assert len(stmts) == 2
        assert 'MERGE (o:Organization {name: "Linagora"})' in stmts[1]
        assert "WORKS_AT" in stmts[1]


def test_unknown_exchange_has_no_mapper():
    assert get_mapper("unknown:exchange") is None


class TestExchangeAliases:
    """created/updated/request share a mapper; deleted/cancel too; update/updated."""

    def test_calendar_upsert_aliases(self):
        m = get_mapper("calendar:event:created")
        assert get_mapper("calendar:event:updated") is m
        assert get_mapper("calendar:event:request") is m

    def test_calendar_delete_aliases(self):
        m = get_mapper("calendar:event:deleted")
        assert get_mapper("calendar:event:cancel") is m

    def test_sabre_contact_upsert_aliases(self):
        m = get_mapper("sabre:contact:created")
        assert get_mapper("sabre:contact:updated") is m
        assert get_mapper("sabre:contact:update") is m

    def test_reply_is_distinct(self):
        assert get_mapper("calendar:event:reply") is not None


class TestCalendarEventDeleted:
    def _mapper(self):
        m = get_mapper("calendar:event:deleted")
        assert m is not None
        return m

    def test_no_uid_returns_empty(self):
        assert self._mapper()({}) == []

    def test_marks_deleted_true(self):
        stmts = self._mapper()({"uid": "e-x"})
        assert len(stmts) == 1
        assert "SET e.deleted = true" in stmts[0]
        assert '"e-x"' in stmts[0]


class TestCalendarEventReply:
    def _mapper(self):
        m = get_mapper("calendar:event:reply")
        assert m is not None
        return m

    def test_no_uid_returns_empty(self):
        assert self._mapper()({"attendee": {"email": "a@x"}}) == []

    def test_single_attendee_accepted(self):
        stmts = self._mapper()(
            {
                "uid": "e-y",
                "attendee": {"email": "b@x", "partstat": "ACCEPTED"},
            }
        )
        assert len(stmts) == 1
        assert "MERGE (p)-[r:ATTENDED]->(e)" in stmts[0]
        assert 'SET r.status = "accepted"' in stmts[0]

    def test_multi_attendee_bulk(self):
        stmts = self._mapper()(
            {
                "uid": "e-y",
                "attendees": [
                    {"email": "b@x", "partstat": "DECLINED"},
                    {"email": "c@x", "partstat": "TENTATIVE"},
                ],
            }
        )
        assert len(stmts) == 2
        assert '"declined"' in stmts[0]
        assert '"tentative"' in stmts[1]

    def test_unknown_partstat_normalised(self):
        stmts = self._mapper()(
            {
                "uid": "e-y",
                "attendee": {"email": "b@x", "partstat": "NEEDS-ACTION"},
            }
        )
        assert '"needs-action"' in stmts[0]


class TestSabreContactDeleted:
    def _mapper(self):
        m = get_mapper("sabre:contact:deleted")
        assert m is not None
        return m

    def test_marks_deleted(self):
        stmts = self._mapper()({"email": "a@x"})
        assert len(stmts) == 1
        assert "SET p.deleted = true" in stmts[0]


class TestCalendarEventUpsertWithVisio:
    def _mapper(self):
        return get_mapper("calendar:event:updated")

    def test_meet_url_from_meetUrl_field(self):
        stmts = self._mapper()(
            {
                "uid": "e-vz",
                "meetUrl": "https://meet.example.com/room/abc",
            }
        )
        combined = "\n".join(stmts)
        assert 'meet_url: "https://meet.example.com/room/abc"' in combined

    def test_meet_url_from_conference_uri(self):
        stmts = self._mapper()(
            {
                "uid": "e-vz",
                "conference": {"uri": "https://meet.example.com/xyz"},
            }
        )
        assert 'meet_url: "https://meet.example.com/xyz"' in stmts[0]

    def test_meet_url_from_location_when_url(self):
        stmts = self._mapper()(
            {
                "uid": "e-vz",
                "location": "https://meet.example.com/loc",
            }
        )
        assert 'meet_url: "https://meet.example.com/loc"' in stmts[0]

    def test_deleted_re_asserted_false(self):
        stmts = self._mapper()({"uid": "e-vz"})
        assert "deleted: false" in stmts[0]
