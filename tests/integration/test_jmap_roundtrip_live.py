"""Live JMAP roundtrip: provision a throwaway mailbox, verify it exists, destroy it.

Opt-in via TWAKY_JMAP_LIVE=1 (see tests/conftest.py::jmap_live_folder).

Note: JmapClient only exposes read-path methods (Email/query, Email/get).
A send-path (Email/set) does not exist yet, so this demo test validates
the fixture itself via a Mailbox/get probe — still a meaningful smoke test
confirming create → verify → destroy works end-to-end.
"""

from __future__ import annotations

import os

import httpx
import pytest

pytestmark = pytest.mark.jmap_live

_JMAP_CORE = "urn:ietf:params:jmap:core"
_JMAP_MAIL = "urn:ietf:params:jmap:mail"


@pytest.mark.asyncio
async def test_mailbox_create_verify_destroy(jmap_live_folder: str) -> None:
    """Provision a throwaway mailbox, confirm Mailbox/get returns it, let fixture destroy it."""
    endpoint = os.environ["JMAP_ENDPOINT"]
    account_id = os.environ["JMAP_ACCOUNT_ID"]
    token = os.environ["JMAP_TOKEN"]

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # Probe: Mailbox/get to confirm the provisioned folder exists.
    get_body = {
        "using": [_JMAP_CORE, _JMAP_MAIL],
        "methodCalls": [
            [
                "Mailbox/get",
                {
                    "accountId": account_id,
                    "ids": [jmap_live_folder],
                    "properties": ["id", "name"],
                },
                "c0",
            ]
        ],
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(endpoint, json=get_body, headers=headers)

    assert resp.status_code == 200, f"Mailbox/get failed: {resp.text[:200]}"
    data = resp.json()
    mailboxes = data["methodResponses"][0][1].get("list", [])
    assert len(mailboxes) == 1, f"Expected exactly one mailbox, got: {mailboxes}"
    assert mailboxes[0]["id"] == jmap_live_folder, (
        f"Mailbox id mismatch: {mailboxes[0]}"
    )
    assert mailboxes[0]["name"].startswith("zzz-twaky-test-"), (
        f"Unexpected mailbox name: {mailboxes[0]['name']}"
    )
