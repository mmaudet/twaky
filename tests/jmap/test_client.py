"""Async JMAP client — Email/query, Email/get."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from twaky.jmap.client import JmapClient


class FakeAsyncClient:
    def __init__(self, response_payload: dict, status: int = 200):
        self._payload = response_payload
        self._status = status
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def post(self, url: str, json: dict, headers: dict[str, str]) -> Any:
        self.calls.append({"url": url, "json": json, "headers": headers})

        # closure over outer self
        outer = self

        class R:
            def raise_for_status(self):
                if outer._status >= 400:
                    raise RuntimeError("bad")

            def json(self):
                return outer._payload

        return R()


class TestEmailQuery:
    @pytest.mark.asyncio
    async def test_email_query_payload_shape(self, monkeypatch):
        fake = FakeAsyncClient(
            {
                "methodResponses": [
                    ["Email/query", {"ids": ["m1", "m2"], "accountId": "a"}, "c0"]
                ],
                "sessionState": "s",
                "accountId": "a",
            }
        )

        def _fake_ctor(*a, **kw):
            return fake

        monkeypatch.setattr(httpx, "AsyncClient", _fake_ctor)

        c = JmapClient(endpoint="http://tmail/jmap", token="TOKEN")
        ids = await c.email_query(from_addr="bob@x", limit=5)
        assert ids == ["m1", "m2"]
        assert fake.calls[0]["headers"]["Authorization"] == "Bearer TOKEN"
        body = fake.calls[0]["json"]
        assert body["using"][0].startswith("urn:ietf:params:jmap")
        # methodCalls has a filter with from
        method_calls = body["methodCalls"]
        assert method_calls[0][0] == "Email/query"
        assert method_calls[0][1]["filter"]["from"] == "bob@x"
        assert method_calls[0][1]["limit"] == 5


class TestEmailGet:
    @pytest.mark.asyncio
    async def test_email_get_payload_shape(self, monkeypatch):
        fake = FakeAsyncClient(
            {
                "methodResponses": [
                    [
                        "Email/get",
                        {
                            "list": [
                                {"id": "m1", "subject": "S", "from": [{"email": "b@x"}]}
                            ]
                        },
                        "c0",
                    ]
                ]
            }
        )

        def _fake_ctor(*a, **kw):
            return fake

        monkeypatch.setattr(httpx, "AsyncClient", _fake_ctor)

        c = JmapClient(endpoint="http://tmail/jmap", token="TOKEN")
        rows = await c.email_get(["m1"], properties=["subject", "from"])
        assert rows[0]["subject"] == "S"
