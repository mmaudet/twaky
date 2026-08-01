"""Cypher shape assertions for the 4 mail mappers."""

from __future__ import annotations

from twaky.mappers import get_mapper


class TestMailReceived:
    def _m(self):
        m = get_mapper("mail:message:received")
        assert m is not None
        return m

    def test_no_message_id_returns_empty(self):
        assert self._m()({"user": "a@x"}) == []

    def test_full_payload(self):
        stmts = self._m()(
            {
                "message_id": "m1",
                "user": "a@x",
                "mailbox_path": {
                    "namespace": "#private",
                    "user": "a@x",
                    "name": "INBOX",
                },
                "timestamp": "2026-08-01T12:00:00Z",
            }
        )
        assert len(stmts) == 1
        s = stmts[0]
        assert 'MERGE (e:Email {message_id: "m1"})' in s
        assert 'e.user = "a@x"' in s
        assert "e.deleted = false" in s
        assert "INBOX" in s
        assert '"2026-08-01T12:00:00Z"' in s


class TestMailExpunged:
    def _m(self):
        return get_mapper("mail:message:expunged")

    def test_marks_deleted(self):
        stmts = self._m()({"message_id": "m1", "user": "a@x"})
        assert len(stmts) == 1
        assert "SET e.deleted = true" in stmts[0]


class TestMailFlagsUpdated:
    def _m(self):
        return get_mapper("mail:message:flags:updated")

    def test_seen_true(self):
        stmts = self._m()(
            {
                "message_id": "m1",
                "user": "a@x",
                "flags": ["\\Seen", "\\Answered"],
            }
        )
        assert "SET e.read = true" in stmts[0]

    def test_seen_false(self):
        stmts = self._m()({"message_id": "m1", "user": "a@x", "flags": ["\\Answered"]})
        assert "SET e.read = false" in stmts[0]

    def test_missing_flags_treated_as_unread(self):
        stmts = self._m()({"message_id": "m1", "user": "a@x"})
        assert "SET e.read = false" in stmts[0]


class TestMailMoved:
    def _m(self):
        return get_mapper("mail:message:moved")

    def test_updates_mailbox_path(self):
        stmts = self._m()(
            {
                "message_id": "m1",
                "user": "a@x",
                "mailbox_path": {
                    "namespace": "#private",
                    "user": "a@x",
                    "name": "Archive",
                },
            }
        )
        assert "SET e.mailbox_path" in stmts[0]
        assert "Archive" in stmts[0]
