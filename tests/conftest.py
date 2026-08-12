"""Top-level pytest configuration and shared fixtures for the twaky test suite."""

from __future__ import annotations

import os
import uuid

import httpx
import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# jmap_live marker machinery
# ---------------------------------------------------------------------------


def _jmap_live_enabled() -> bool:
    return os.environ.get("TWAKY_JMAP_LIVE", "").lower() in {"1", "true", "yes"}


@pytest.fixture(autouse=True)
def _skip_jmap_live(request: pytest.FixtureRequest) -> None:
    """Auto-skip @pytest.mark.jmap_live tests when TWAKY_JMAP_LIVE is unset."""
    if request.node.get_closest_marker("jmap_live") and not _jmap_live_enabled():
        pytest.skip("TWAKY_JMAP_LIVE=1 required to run @jmap_live tests")


# ---------------------------------------------------------------------------
# jmap_live_folder fixture
#
# Uses httpx directly against the JMAP Mailbox/set API — JmapClient only
# wraps Email/query and Email/get (read paths), and extending it solely to
# support a test fixture would be scope creep (controller decision 2026-08-12).
#
# JMAP Mailbox/set create envelope:
#   ["Mailbox/set", {"accountId": ..., "create": {"k": {"name": ..., ...}}}, "c0"]
#
# JMAP Mailbox/set destroy envelope:
#   ["Mailbox/set", {"accountId": ..., "destroy": [<id>]}, "c1"]
#
# Spec ref: RFC 8620 §5.3 (set) + RFC 8621 §2 (Mailbox).
# ---------------------------------------------------------------------------

_JMAP_CORE = "urn:ietf:params:jmap:core"
_JMAP_MAIL = "urn:ietf:params:jmap:mail"


@pytest_asyncio.fixture
async def jmap_live_folder() -> object:
    """Provision a throwaway JMAP mailbox for the duration of one test.

    Reads JMAP_ENDPOINT, JMAP_ACCOUNT_ID, JMAP_TOKEN from env
    (fail-fast if any missing).

    Yields the mailbox id (str).

    Cleanup: Mailbox/set destroy. Leaves the folder in place if
    destroy fails — the prefix ``zzz-twaky-test-`` lets a manual
    cleanup script sweep it later.
    """
    for var in ("JMAP_ENDPOINT", "JMAP_ACCOUNT_ID", "JMAP_TOKEN"):
        if not os.environ.get(var):
            pytest.fail(f"@jmap_live fixture requires env var {var}")

    endpoint: str = os.environ["JMAP_ENDPOINT"]
    account_id: str = os.environ["JMAP_ACCOUNT_ID"]
    token: str = os.environ["JMAP_TOKEN"]

    name = f"zzz-twaky-test-{uuid.uuid4().hex[:8]}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # JMAP Mailbox/set create — RFC 8621 §2 + RFC 8620 §5.3
    create_body = {
        "using": [_JMAP_CORE, _JMAP_MAIL],
        "methodCalls": [
            [
                "Mailbox/set",
                {
                    "accountId": account_id,
                    "create": {
                        "new": {
                            "name": name,
                            "role": None,
                            "sortOrder": 0,
                        }
                    },
                },
                "c0",
            ]
        ],
    }

    mailbox_id: str
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(endpoint, json=create_body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            created = data["methodResponses"][0][1].get("created", {})
            if "new" not in created:
                pytest.skip(
                    f"Failed to provision jmap_live_folder: "
                    f"Mailbox/set create returned no 'new' key — {data}"
                )
            mailbox_id = created["new"]["id"]
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Failed to provision jmap_live_folder: {exc}")

    try:
        yield mailbox_id
    finally:
        # JMAP Mailbox/set destroy — RFC 8620 §5.3
        destroy_body = {
            "using": [_JMAP_CORE, _JMAP_MAIL],
            "methodCalls": [
                [
                    "Mailbox/set",
                    {
                        "accountId": account_id,
                        "destroy": [mailbox_id],
                    },
                    "c1",
                ]
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                await client.post(endpoint, json=destroy_body, headers=headers)
        except Exception:  # noqa: BLE001,S110
            pass  # leave zzz-twaky-test-* for manual cleanup
