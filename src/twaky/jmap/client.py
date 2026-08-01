"""Thin async JMAP client. Supports Email/query + Email/get only (read paths)."""

from __future__ import annotations

from typing import Any

import httpx

_JMAP_CORE = "urn:ietf:params:jmap:core"
_JMAP_MAIL = "urn:ietf:params:jmap:mail"


class JmapClient:
    # TBD spec §13: fetch accountId dynamically via JMAP session call once
    # the tmail-backend session endpoint is confirmed.
    def __init__(self, endpoint: str, token: str, account_id: str | None = None):
        self.endpoint = endpoint
        self.token = token
        self.account_id = account_id or ""

    async def _call(self, method_calls: list[list[Any]]) -> dict:
        body = {
            "using": [_JMAP_CORE, _JMAP_MAIL],
            "methodCalls": method_calls,
        }
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(self.endpoint, json=body, headers=headers)
            resp.raise_for_status()
            return resp.json()

    async def email_query(
        self,
        *,
        mailbox_role: str | None = "inbox",
        from_addr: str | None = None,
        limit: int = 20,
    ) -> list[str]:
        """Return a list of Email ids matching the filter, most recent first."""
        f: dict[str, Any] = {}
        if mailbox_role:
            f["inMailboxRole"] = mailbox_role
        if from_addr:
            f["from"] = from_addr
        method: list[Any] = [
            "Email/query",
            {
                "accountId": self.account_id,
                "filter": f or None,
                "sort": [{"property": "receivedAt", "isAscending": False}],
                "limit": limit,
            },
            "c0",
        ]
        data = await self._call([method])
        # methodResponses is [[<method>, <resp>, <cid>], ...]
        return data["methodResponses"][0][1].get("ids", [])

    async def email_get(self, ids: list[str], properties: list[str]) -> list[dict]:
        method: list[Any] = [
            "Email/get",
            {
                "accountId": self.account_id,
                "ids": ids,
                "properties": properties,
            },
            "c0",
        ]
        data = await self._call([method])
        return data["methodResponses"][0][1].get("list", [])


__all__ = ["JmapClient"]
