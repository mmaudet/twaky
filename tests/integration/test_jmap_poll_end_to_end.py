"""JMAP live smoke test — opt-in via EVAL_LIVE=1.

Skipped by default in CI.  To run manually against the real Linagora JMAP
endpoint:

    EVAL_LIVE=1 \\
    JMAP_SESSION_URL=https://jmap-new.linagora.com/jmap/session \\
    JMAP_BEARER_TOKEN=<oidc_token> \\
    uv run pytest tests/integration/test_jmap_poll_end_to_end.py -v

The test is **side-effect-free**: it discovers the JMAP session and captures
the seed queryState, but does NOT process or modify any real email.  It
asserts either:
- The seed state was captured (no previous jmap_last_state in DB), OR
- At least one delta poll cycle completes without error.

The test exits before the poll-interval sleep by setting stop_event early.
"""

from __future__ import annotations

import asyncio
import os

import pytest

# ---------------------------------------------------------------------------
# Skip guards
# ---------------------------------------------------------------------------

_EVAL_LIVE = os.environ.get("EVAL_LIVE", "0") == "1"
_JMAP_SESSION_URL = os.environ.get("JMAP_SESSION_URL", "")
_JMAP_BEARER_TOKEN = os.environ.get("JMAP_BEARER_TOKEN", "")

_SKIP_REASON = "JMAP live test disabled — set EVAL_LIVE=1, JMAP_SESSION_URL, and JMAP_BEARER_TOKEN to enable"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(
    not (_EVAL_LIVE and _JMAP_SESSION_URL and _JMAP_BEARER_TOKEN),
    reason=_SKIP_REASON,
)
async def test_jmap_session_discovery_and_seed() -> None:
    """Verify JMAP session discovery succeeds and seed-state capture works.

    Steps:
    1. Instantiate JmapPollingEventSource with real env creds.
    2. Call _discover_session directly — asserts accountId, apiUrl, inboxId
       are non-empty strings.
    3. If jmap_last_state is absent in the sentinel DB row, call _seed_state
       and verify a non-empty queryState is returned.
    4. Set stop_event immediately so the generator does not start a full
       delta poll loop.

    No real emails are fetched or modified.
    """
    import httpx

    from twaky.sentinels.sources.jmap_poll import JmapPollingEventSource

    source = JmapPollingEventSource(
        sentinel_name="mail",
        session_url=_JMAP_SESSION_URL,
        bearer_token=_JMAP_BEARER_TOKEN,
        account_email=os.environ.get("JMAP_ACCOUNT_EMAIL", ""),
        mailbox_name="INBOX",
        poll_interval_s=3600,  # long — we stop before sleeping
    )

    headers = {
        "Authorization": f"Bearer {_JMAP_BEARER_TOKEN}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        account_id, api_url, inbox_id = await source._discover_session(client)

    assert account_id, "accountId must be non-empty"
    assert api_url, "apiUrl must be non-empty"
    assert inbox_id, "inboxId must be non-empty"

    # If we have no persisted state, seed and verify the queryState.
    current_state = source._load_state()
    if current_state is None:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
            seed_state = await source._seed_state(client, api_url, account_id, inbox_id)
        assert seed_state, "Seed queryState must be non-empty"
        # NOTE: we do NOT persist the state here to keep the test side-effect-free.
    else:
        # State already seeded — just verify it is a non-empty string.
        assert current_state, f"Persisted state {current_state!r} should be non-empty"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(
    not (_EVAL_LIVE and _JMAP_SESSION_URL and _JMAP_BEARER_TOKEN),
    reason=_SKIP_REASON,
)
async def test_jmap_stream_bounded_cycles() -> None:
    """Drive stream() for at most MAX_CYCLES poll iterations, side-effect-free.

    The sentinel name is set to a temp value so _load_state() returns None and
    the source enters the seed path.  We set stop_event immediately after the
    seed branch to avoid sleeping the full poll_interval_s.

    Assertions:
    - The generator terminates cleanly when stop_event is set.
    - No exception escapes from stream().
    """
    from unittest.mock import patch

    from twaky.sentinels.sources.jmap_poll import JmapPollingEventSource

    # Use a sentinel name that has no DB row so _load_state() returns None
    # without needing DB access.
    source = JmapPollingEventSource(
        sentinel_name="_jmap_live_smoke",
        session_url=_JMAP_SESSION_URL,
        bearer_token=_JMAP_BEARER_TOKEN,
        account_email=os.environ.get("JMAP_ACCOUNT_EMAIL", ""),
        mailbox_name="INBOX",
        poll_interval_s=1,  # short so the test doesn't hang
    )

    events_received: list = []
    stop_event = asyncio.Event()

    # Patch _load_state to return None (force seed path) and
    # _persist_state to be a no-op (side-effect-free).
    with (
        patch.object(source, "_load_state", return_value=None),
        patch.object(source, "_persist_state", return_value=None),
    ):
        # Set stop_event after 2 seconds — enough time to seed, not to delta-poll.
        async def _stop_after_seed() -> None:
            await asyncio.sleep(2.0)
            stop_event.set()

        stop_task = asyncio.create_task(_stop_after_seed())

        try:
            async for event, _ack in source.stream(stop_event=stop_event):
                events_received.append(event)
                # Consume without processing — side-effect-free.
                stop_event.set()  # stop immediately after first event
                break
        finally:
            stop_task.cancel()
            try:
                await stop_task
            except asyncio.CancelledError:
                pass

    # Either zero events (seed path, nothing new) or some events (delta path).
    # Either way the generator must have exited cleanly.
    for ev in events_received:
        assert ev["source_kind"] == "jmap_poll"
        assert ev["message_id"]
