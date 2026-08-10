"""Mail sentinel pipeline nodes.

Processes a mail event through a sequence of transformations: loads the email
thread, classifies it, learns patterns, drafts replies, and applies actions.

Nodes in this module:
- ``make_load_thread`` — fetch email & thread context (T17)
- more nodes to follow (T18-T23).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from twaky.sentinels.mail.adapter import MailAdapter
from twaky.sentinels.mail.state import MailAgentState

if TYPE_CHECKING:
    from twaky.sentinels.base import Context

log = logging.getLogger(__name__)


@dataclass
class NodeContext:
    """Mail-specific execution context for pipeline nodes.

    Extends the base sentinel Context with mail-adapter access and owner
    email address. Passed to every node factory.

    Attributes
    ----------
    base
        The base sentinel Context (db_pool, mission_emitter, logger, etc.).
    mail
        The MailAdapter for fetching emails and threads.
    owner_email
        Email address of the sentinel owner (used for reply attribution).
    """

    base: Context
    mail: MailAdapter
    owner_email: str


def make_load_thread(ctx: NodeContext) -> Callable[[MailAgentState], MailAgentState]:
    """Factory for the load_thread node.

    Fetches the email by id, then loads its thread context:
    - If the email has a threadId, fetches all emails in that thread.
    - Otherwise, returns a single-entry thread with just the email.

    Returns a node function that takes the current state and returns
    a partial state dict with the ``thread`` key.

    Parameters
    ----------
    ctx
        Execution context with mail adapter.

    Returns
    -------
    Callable
        A node function ``(MailAgentState) -> MailAgentState``.
    """

    def _node(state: MailAgentState) -> MailAgentState:
        email_id = state["email_id"]
        email = ctx.mail.get_email(email_id)
        thread_id = email.get("threadId")
        thread = ctx.mail.get_thread(thread_id) if thread_id else [email]
        return {"thread": thread}

    return _node


__all__ = ["NodeContext", "make_load_thread"]
