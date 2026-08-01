"""Chronos tools — Cypher shape assertions with a mocked psycopg pool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from twaky.agents.chronos import tools as ct


class TestListEvents:
    def test_generates_expected_cypher(self):
        with patch("twaky.agents.chronos.tools.get_pool") as p:
            cur = MagicMock()
            cur.fetchall.return_value = []
            p.return_value.connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cur
            ct.list_events.invoke(
                {"from_iso": "2026-08-01T00:00:00Z", "to_iso": "2026-08-01T23:59:59Z"}
            )
            # Inspect the last cypher() call:
            sql = cur.execute.call_args_list[-1].args[0]
            assert "CalendarEvent" in sql
            assert "start_at" in sql or "start" in sql


class TestGetEvent:
    def test_returns_none_when_missing(self):
        with patch("twaky.agents.chronos.tools.get_pool") as p:
            cur = MagicMock()
            cur.fetchall.return_value = []
            p.return_value.connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cur
            out = ct.get_event.invoke({"uid": "nope"})
            assert out is None


class TestFindConflictsInterface:
    def test_takes_person_email_and_window(self):
        # Signature check only — implementation queries the graph.
        assert "person_email" in ct.find_conflicts.args_schema.model_fields


class TestNextFreeSlot:
    def test_signature(self):
        fields = ct.next_free_slot.args_schema.model_fields
        assert "participant_emails" in fields
        assert "duration_min" in fields
