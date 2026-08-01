"""Plume mail tools — JMAP read + LLM drafting."""

from __future__ import annotations

import asyncio
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_litellm import ChatLiteLLM

from twaky.auth.jmap import bearer_token_for_owner
from twaky.config import settings
from twaky.jmap.client import JmapClient


def _make_llm() -> BaseChatModel:
    return ChatLiteLLM(
        model=settings.plume_model or settings.model,
        api_base=settings.litellm_api_base,
    )


def _client() -> JmapClient:
    return JmapClient(
        endpoint=settings.jmap_endpoint,
        token=bearer_token_for_owner(),
        account_id=settings.jmap_account_id,
    )


def _from_addr(row: dict) -> str:
    src = row.get("from") or []
    return src[0].get("email", "") if src else ""


def _extract_body(row: dict) -> str:
    parts = row.get("textBody") or []
    values = row.get("bodyValues") or {}
    chunks = [values.get(p.get("partId"), {}).get("value", "") for p in parts]
    return "\n".join([c for c in chunks if c])


@tool
def list_recent_emails(limit: int = 20) -> list[dict]:
    """List recent emails in the inbox with subject, from, receivedAt."""

    async def _run():
        c = _client()
        ids = await c.email_query(mailbox_role="inbox", limit=limit)
        if not ids:
            return []
        rows = await c.email_get(ids, properties=["subject", "from", "receivedAt"])
        return [
            {
                "id": r["id"],
                "subject": r.get("subject", ""),
                "from": _from_addr(r),
                "received_at": r.get("receivedAt", ""),
            }
            for r in rows
        ]

    return asyncio.run(_run())


@tool
def read_email(message_id: str) -> dict:
    """Return subject, from, receivedAt, and body text for the given message id."""

    async def _run():
        c = _client()
        rows = await c.email_get(
            [message_id],
            properties=["subject", "from", "receivedAt", "textBody", "bodyValues"],
        )
        if not rows:
            return {}
        r = rows[0]
        return {
            "id": r.get("id"),
            "subject": r.get("subject", ""),
            "from": _from_addr(r),
            "received_at": r.get("receivedAt", ""),
            "body": _extract_body(r),
        }

    return asyncio.run(_run())


@tool
def search_emails(from_addr: str, limit: int = 10) -> list[dict]:
    """Search inbox emails by sender address."""

    async def _run():
        c = _client()
        ids = await c.email_query(
            mailbox_role="inbox", from_addr=from_addr, limit=limit
        )
        if not ids:
            return []
        rows = await c.email_get(ids, properties=["subject", "from", "receivedAt"])
        return [
            {
                "id": r["id"],
                "subject": r.get("subject", ""),
                "from": _from_addr(r),
                "received_at": r.get("receivedAt", ""),
            }
            for r in rows
        ]

    return asyncio.run(_run())


@tool
def draft_reply(
    message_id: str,
    tone: Literal["formal", "casual"] = "formal",
    extra_context: str = "",
) -> dict:
    """Read the given email and produce a reply draft.

    Does NOT send. Returns {"draft": str, "to": str, "subject": str}.
    """

    async def _fetch():
        c = _client()
        rows = await c.email_get(
            [message_id],
            properties=["subject", "from", "receivedAt", "textBody", "bodyValues"],
        )
        return rows[0] if rows else {}

    row = asyncio.run(_fetch())
    if not row:
        return {"draft": "", "to": "", "subject": "", "error": "message not found"}
    body = _extract_body(row)
    from_addr = _from_addr(row)
    subject = row.get("subject", "")
    system = SystemMessage(
        content=(
            f"You are Plume, a mail assistant. Write a {tone} reply to the email "
            f"below. Keep it under 120 words. Sign off simply. Do NOT invent facts."
        )
    )
    user = HumanMessage(
        content=(
            f"From: {from_addr}\nSubject: {subject}\n\n{body}\n\n"
            f"Additional context (may be empty): {extra_context}\n\nReply:"
        )
    )
    llm = _make_llm()
    ai = llm.invoke([system, user])
    return {
        "draft": ai.content if isinstance(ai.content, str) else str(ai.content),
        "to": from_addr,
        "subject": subject if subject.startswith("Re: ") else f"Re: {subject}",
    }


__all__ = ["draft_reply", "list_recent_emails", "read_email", "search_emails"]
