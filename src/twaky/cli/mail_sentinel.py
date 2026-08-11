"""twaky mail-sentinel CLI group.

Provides operational commands for the mail sentinel vertical that are
useful outside the main daemon loop (`twaky sentinel run`).

Currently ships:

- ``replay``: run the LangGraph pipeline on N historical INBOX emails
  filtered by a ``--since`` cutoff. Useful to smoke-test the spam
  triage cascade against a known corpus without waiting for real
  incoming mail. Decisions are still written to
  ``mail_sentinel_spam_decision``; JMAP side-effects (labels /
  keywords) can be suppressed with ``--dry-run``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx
import typer

from twaky.config import settings
from twaky.oauth.refresh_manager import get_manager
from twaky.sentinels import repository as sentinel_repository
from twaky.sentinels.mail.adapter import MailAdapter
from twaky.sentinels.mail.nodes import NodeContext
from twaky.sentinels.mail.pipeline import process_email
from twaky.sentinels.mail.sentinel import MailSentinel
from twaky.sentinels.runtime import _build_context

log = logging.getLogger(__name__)

app = typer.Typer(help="Mail sentinel operational commands.")


_CORE = "urn:ietf:params:jmap:core"
_MAIL = "urn:ietf:params:jmap:mail"


class _DryRunAdapter:
    """Wrap a real MailAdapter and neutralise side-effect methods.

    Reads still hit JMAP (the pipeline needs `get_email`, `get_thread`
    etc.). Writes (`label`, `unlabel`, `set_keyword`, `set_keywords_bulk`,
    `archive`, `move_to`) are logged but not executed against JMAP. This
    keeps the spam decision row written to Postgres (owner can inspect
    it) while leaving the mailbox untouched.
    """

    def __init__(self, wrapped: MailAdapter) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str):
        return getattr(self._wrapped, name)

    def label(self, email_id: str, label: str) -> None:
        log.info("DRY-RUN: would label email=%s label=%s", email_id, label)

    def unlabel(self, email_id: str, label: str) -> None:
        log.info("DRY-RUN: would unlabel email=%s label=%s", email_id, label)

    def set_keyword(self, email_id: str, keyword: str, value: bool) -> None:
        log.info(
            "DRY-RUN: would set_keyword email=%s keyword=%s value=%s",
            email_id,
            keyword,
            value,
        )

    def set_keywords_bulk(
        self,
        email_id: str,
        patches: dict[str, bool],
        *,
        mailbox_patches: dict[str, bool] | None = None,
    ) -> None:
        log.info(
            "DRY-RUN: would set_keywords_bulk email=%s patches=%s mailbox_patches=%s",
            email_id,
            patches,
            mailbox_patches or {},
        )

    def archive(self, email_id: str) -> None:
        log.info("DRY-RUN: would archive email=%s", email_id)

    def move_to(self, email_id: str, mailbox_id: str) -> None:
        log.info("DRY-RUN: would move_to email=%s mailbox=%s", email_id, mailbox_id)

    def save_draft(
        self,
        *,
        in_reply_to: str,
        body: str,
        language: str,
        from_addr: list[dict[str, str]] | None = None,
        to_addr: list[dict[str, str]] | None = None,
        cc_addr: list[dict[str, str]] | None = None,
        subject: str | None = None,
        references: list[str] | None = None,
    ) -> str:
        """Skip real JMAP draft creation; return a fake id + log the intent.

        Previously ``save_draft`` fell through ``__getattr__`` to the real
        adapter and created actual drafts in the user's Drafts folder during
        ``--dry-run`` — which defeats the whole purpose of dry-run.
        """
        log.info(
            "DRY-RUN: would save_draft in_reply_to=%s to=%s cc=%s subject=%r "
            "body_len=%d language=%s",
            in_reply_to,
            [a.get("email") for a in (to_addr or [])],
            [a.get("email") for a in (cc_addr or [])],
            subject,
            len(body),
            language,
        )
        return f"dry-run-draft-{id(body)}"


def _parse_since(since: str) -> str:
    """Accept ``24h`` / ``7d`` / ISO-8601 and return an ISO-8601 UTC string.

    JMAP `Email/query` expects the `after` filter as an ISO-8601 date-time.
    """
    since = since.strip()
    if since.endswith("h") and since[:-1].isdigit():
        cutoff = datetime.now(UTC) - timedelta(hours=int(since[:-1]))
    elif since.endswith("d") and since[:-1].isdigit():
        cutoff = datetime.now(UTC) - timedelta(days=int(since[:-1]))
    else:
        cutoff = datetime.fromisoformat(since)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)
    return cutoff.isoformat().replace("+00:00", "Z")


async def _fetch_email_ids(
    session_url: str,
    account_id: str,
    api_url: str,
    token: str,
    inbox_id: str | None,
    after_iso: str,
    limit: int,
) -> list[str]:
    """Run Email/query with an `after` filter, newest first, up to *limit* ids."""
    filt: dict[str, object] = {"after": after_iso}
    if inbox_id:
        filt["inMailbox"] = inbox_id
    body = {
        "using": [_CORE, _MAIL],
        "methodCalls": [
            [
                "Email/query",
                {
                    "accountId": account_id,
                    "filter": filt,
                    "sort": [{"property": "receivedAt", "isAscending": False}],
                    "limit": limit,
                    "calculateTotal": False,
                },
                "0",
            ]
        ],
    }
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(
            api_url, headers={"Authorization": f"Bearer {token}"}, json=body
        )
        resp.raise_for_status()
        data = resp.json()
    result = data["methodResponses"][0][1]
    return list(result.get("ids") or [])


async def _resolve_inbox_id(account_id: str, api_url: str, token: str) -> str | None:
    """Return the inbox mailbox id, or None if it can't be found."""
    body = {
        "using": [_CORE, _MAIL],
        "methodCalls": [
            [
                "Mailbox/get",
                {"accountId": account_id, "ids": None, "properties": ["id", "role"]},
                "0",
            ]
        ],
    }
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(
            api_url, headers={"Authorization": f"Bearer {token}"}, json=body
        )
        resp.raise_for_status()
        data = resp.json()
    for mbox in data["methodResponses"][0][1].get("list", []):
        if mbox.get("role") == "inbox":
            return mbox["id"]
    return None


@app.command("replay")
def replay_command(
    since: str = typer.Option(
        "24h",
        "--since",
        help="Cutoff: '24h', '7d', or ISO-8601 (e.g. '2026-08-10T00:00:00Z').",
    ),
    limit: int = typer.Option(
        20, "--limit", help="Maximum number of emails to process."
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Skip JMAP write side-effects (labels/keywords). Decisions still write to DB.",
    ),
) -> None:
    """Run the mail LangGraph pipeline on historical INBOX emails.

    Fetches up to ``--limit`` emails received since ``--since`` and runs
    each through the full pipeline (spam_triage → match_rules → …). Prints
    the resulting bucket + signal for each. Useful to smoke-test the
    spam filter against a real corpus without waiting for a live poll.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    after_iso = _parse_since(since)

    cfg = sentinel_repository.get("mail")
    if cfg is None:
        typer.echo("mail sentinel row not found in DB", err=True)
        raise typer.Exit(code=1)

    inst = MailSentinel()
    ctx = _build_context(inst, cfg, settings)
    real_adapter = inst._build_adapter(ctx)

    manager = get_manager("mail")
    token = manager.sync_get_access_token()

    # Discover the mail account api_url (adapter has it but we need it here too)
    resp = httpx.get(
        settings.jmap_session_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    session = resp.json()
    account_id = session["primaryAccounts"][_MAIL]
    api_url = session["apiUrl"]

    inbox_id = asyncio.run(_resolve_inbox_id(account_id, api_url, token))
    typer.echo(f"inbox_id={inbox_id or '(all mailboxes)'} · after={after_iso}")

    email_ids = asyncio.run(
        _fetch_email_ids(
            settings.jmap_session_url,
            account_id,
            api_url,
            token,
            inbox_id,
            after_iso,
            limit,
        )
    )
    typer.echo(f"fetched {len(email_ids)} email id(s) — replaying")

    adapter: MailAdapter = _DryRunAdapter(real_adapter) if dry_run else real_adapter  # type: ignore[assignment]
    node_ctx = NodeContext(
        base=ctx, mail=adapter, owner_email=settings.twaky_owner_email
    )

    bucket_counts: dict[str | None, int] = {}
    rule_counts: dict[str | None, int] = {}
    for i, email_id in enumerate(email_ids, 1):
        try:
            state = process_email(node_ctx, email_id)
            bucket = state.get("spam_bucket")
            rule_name = state.get("rule_name")
            matched_by = state.get("matched_by")
            actions = state.get("actions_applied") or []
            has_draft = bool(state.get("draft"))
            thread = state.get("thread") or []
            sender = ""
            subject = ""
            if thread:
                latest = thread[-1]
                sender = (latest.get("from") or [{}])[0].get("email", "")[:32]
                subject = (latest.get("subject") or "")[:40]

            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
            rule_key = f"{rule_name} ({matched_by})" if rule_name else None
            rule_counts[rule_key] = rule_counts.get(rule_key, 0) + 1

            draft_flag = "draft" if has_draft else "-"
            actions_str = ",".join(actions[:3]) or "-"
            typer.echo(
                f"[{i}/{len(email_ids)}] {email_id[:12]}… "
                f"bucket={bucket or '-':<15} "
                f"rule={(rule_name or '-'):<16} "
                f"via={(matched_by or '-'):<18} "
                f"draft={draft_flag:<5} "
                f"actions={actions_str} "
                f"from={sender} · {subject}"
            )
        except Exception as exc:  # noqa: BLE001
            typer.echo(
                f"[{i}/{len(email_ids)}] email={email_id[:16]}… ERROR: {exc}",
                err=True,
            )

    typer.echo("")
    typer.echo(
        f"bucket summary: {dict(sorted(bucket_counts.items(), key=lambda kv: str(kv[0])))}"
    )
    typer.echo(
        f"rule   summary: {dict(sorted(rule_counts.items(), key=lambda kv: str(kv[0])))}"
    )
    if dry_run:
        typer.echo("DRY-RUN: no JMAP side-effects applied.")
