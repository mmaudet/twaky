"""Unit tests for the ``twaky mail-sentinel replay`` CLI --dry-run behaviour.

The CLI wraps the real MailAdapter in ``_DryRunAdapter`` which must intercept
every JMAP write method (label, unlabel, set_keyword, set_keywords_bulk,
archive, move_to, save_draft) so ``--dry-run`` truly leaves the mailbox
untouched. A previous bug let ``save_draft`` fall through ``__getattr__`` to
the real adapter and created real drafts in the user's Drafts folder.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from twaky.cli.mail_sentinel import _DryRunAdapter  # type: ignore[import-untyped]


@pytest.fixture
def real_adapter() -> MagicMock:
    return MagicMock()


@pytest.fixture
def dry(real_adapter: MagicMock) -> _DryRunAdapter:
    return _DryRunAdapter(real_adapter)


def test_save_draft_does_not_touch_real_adapter(
    dry: _DryRunAdapter, real_adapter: MagicMock, caplog
) -> None:
    """save_draft returns a fake id and logs; real adapter never called."""
    with caplog.at_level(logging.INFO, logger="twaky.cli.mail_sentinel"):
        draft_id = dry.save_draft(
            in_reply_to="<x@y>",
            body="body",
            language="fr",
            to_addr=[{"email": "recipient@x"}],
            cc_addr=[{"email": "cc@y"}],
            subject="Re: test",
        )

    real_adapter.save_draft.assert_not_called()
    assert draft_id.startswith("dry-run-draft-")
    assert any("would save_draft" in r.message for r in caplog.records)


def test_label_does_not_touch_real_adapter(
    dry: _DryRunAdapter, real_adapter: MagicMock, caplog
) -> None:
    with caplog.at_level(logging.INFO, logger="twaky.cli.mail_sentinel"):
        dry.label("e1", "ventes")
    real_adapter.label.assert_not_called()
    assert any("would label" in r.message for r in caplog.records)


def test_set_keywords_bulk_intercepts_mailbox_patches(
    dry: _DryRunAdapter, real_adapter: MagicMock, caplog
) -> None:
    """New restore path passes mailbox_patches — dry-run must still block it."""
    with caplog.at_level(logging.INFO, logger="twaky.cli.mail_sentinel"):
        dry.set_keywords_bulk(
            "e1",
            {"$junk": False, "nonjunk": True},
            mailbox_patches={"inbox-uuid": True},
        )
    real_adapter.set_keywords_bulk.assert_not_called()
    msg = " ".join(r.message for r in caplog.records)
    assert "would set_keywords_bulk" in msg
    assert "inbox-uuid" in msg  # mailbox_patches logged for observability


def test_archive_and_move_to_intercepted(
    dry: _DryRunAdapter, real_adapter: MagicMock, caplog
) -> None:
    with caplog.at_level(logging.INFO, logger="twaky.cli.mail_sentinel"):
        dry.archive("e1")
        dry.move_to("e2", "mbox-1")
    real_adapter.archive.assert_not_called()
    real_adapter.move_to.assert_not_called()


def test_read_methods_delegate_to_real_adapter(
    dry: _DryRunAdapter, real_adapter: MagicMock
) -> None:
    """__getattr__ delegates non-overridden methods (get_email, get_thread) to real."""
    real_adapter.get_email.return_value = {"id": "e1", "subject": "hi"}
    result = dry.get_email("e1")
    real_adapter.get_email.assert_called_once_with("e1")
    assert result == {"id": "e1", "subject": "hi"}
