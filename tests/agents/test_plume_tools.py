"""Plume tools — JMAP calls are mocked."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from twaky.agents.plume import tools as pt


@pytest.fixture(autouse=True)
def _patch_token(monkeypatch):
    monkeypatch.setattr(pt, "bearer_token_for_owner", lambda: "TOK")


class TestListRecent:
    def test_returns_summary_rows(self, monkeypatch):
        with patch("twaky.agents.plume.tools.JmapClient") as C:
            inst = C.return_value
            inst.email_query = AsyncMock(return_value=["m1", "m2"])
            inst.email_get = AsyncMock(
                return_value=[
                    {
                        "id": "m1",
                        "subject": "S1",
                        "from": [{"email": "a@x", "name": "A"}],
                        "receivedAt": "2026-08-01T10:00:00Z",
                    },
                    {
                        "id": "m2",
                        "subject": "S2",
                        "from": [{"email": "b@x", "name": "B"}],
                        "receivedAt": "2026-08-01T11:00:00Z",
                    },
                ]
            )
            out = pt.list_recent_emails.invoke({"limit": 20})
        assert len(out) == 2
        assert out[0]["subject"] == "S1"
        assert out[0]["from"] == "a@x"


class TestReadEmail:
    def test_returns_body(self, monkeypatch):
        with patch("twaky.agents.plume.tools.JmapClient") as C:
            inst = C.return_value
            inst.email_get = AsyncMock(
                return_value=[
                    {
                        "id": "m1",
                        "subject": "S",
                        "from": [{"email": "a@x"}],
                        "receivedAt": "2026-08-01T10:00:00Z",
                        "textBody": [{"partId": "1"}],
                        "bodyValues": {"1": {"value": "Hello there"}},
                    }
                ]
            )
            out = pt.read_email.invoke({"message_id": "m1"})
        assert out["subject"] == "S"
        assert "Hello there" in out["body"]


class TestSearchEmails:
    def test_filters_by_from(self, monkeypatch):
        with patch("twaky.agents.plume.tools.JmapClient") as C:
            inst = C.return_value
            inst.email_query = AsyncMock(return_value=["m1"])
            inst.email_get = AsyncMock(
                return_value=[
                    {
                        "id": "m1",
                        "subject": "S",
                        "from": [{"email": "bob@x"}],
                        "receivedAt": "2026-08-01T10:00:00Z",
                    }
                ]
            )
            out = pt.search_emails.invoke({"from_addr": "bob@x", "limit": 3})
        assert out[0]["from"] == "bob@x"


class TestDraftReply:
    def test_llm_generates_draft(self, monkeypatch):
        from langchain_core.messages import AIMessage

        class FakeLLM:
            def invoke(self, _messages):
                return AIMessage(content="Thanks Bob — I'll take a look.")

        with (
            patch("twaky.agents.plume.tools.JmapClient") as C,
            patch("twaky.agents.plume.tools._make_llm", return_value=FakeLLM()),
        ):
            inst = C.return_value
            inst.email_get = AsyncMock(
                return_value=[
                    {
                        "id": "m1",
                        "subject": "Question about X",
                        "from": [{"email": "bob@x", "name": "Bob"}],
                        "textBody": [{"partId": "1"}],
                        "bodyValues": {"1": {"value": "Hi, what about X?"}},
                    }
                ]
            )
            out = pt.draft_reply.invoke({"message_id": "m1", "tone": "casual"})
        assert out["to"] == "bob@x"
        assert "Bob" in out["draft"]
        assert out["subject"].startswith("Re: ")
