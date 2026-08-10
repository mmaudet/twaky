"""Blocs de contexte réutilisés par tous les prompts.

Le balisage XML est systématique : il délimite sans ambiguïté le contenu tiers
non fiable du reste du prompt, ce qui rend le durcissement anti-injection
effectivement applicable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

MAX_BODY_CHARS = 3000


def today_for_llm(now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return now.strftime("%Y-%m-%d (%A)")


def user_info_block(owner_email: str) -> str:
    return f"<user_info>\n  <owner>{owner_email}</owner>\n</user_info>"


def _escape(text: str) -> str:
    """Escape < and > to prevent XML injection from untrusted content."""
    return text.replace("<", "&lt;").replace(">", "&gt;")


def email_list_block(thread: list[dict[str, Any]]) -> str:
    """Wrap thread emails in an XML block.

    Each email dict may contain:
      - from, to, subject, received (string fields — escaped)
      - body or preview string, or JMAP-style bodyValues/textBody fields
    """
    parts: list[str] = []
    for email in thread:
        from_ = _escape(str(email.get("from", "")))
        to = _escape(str(email.get("to", "")))
        subject = _escape(str(email.get("subject", "")))
        received = _escape(str(email.get("received", "")))

        # Body resolution: direct body field, then preview, then JMAP bodyValues
        body_raw: str = ""
        if email.get("body"):
            body_raw = str(email["body"])
        elif email.get("preview"):
            body_raw = str(email["preview"])
        else:
            body_values = email.get("bodyValues", {})
            text_body = email.get("textBody", [])
            if text_body and isinstance(text_body, list):
                part_id = text_body[0].get("partId", "")
                entry = body_values.get(part_id, {})
                body_raw = entry.get("value", "") if isinstance(entry, dict) else ""

        body = _escape(body_raw[:MAX_BODY_CHARS])

        parts.append(
            f"<email>\n"
            f"<from>{from_}</from>\n"
            f"<to>{to}</to>\n"
            f"<subject>{subject}</subject>\n"
            f"<received>{received}</received>\n"
            f"<body>{body}</body>\n"
            f"</email>"
        )

    return "<thread>\n" + "\n".join(parts) + "\n</thread>"
