"""Unit tests for make_thread_status node.

Pure unit tests with no database interaction. Tests the empty thread case and
LLM invocation path.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from twaky.sentinels.mail.nodes import NodeContext, make_thread_status
from twaky.sentinels.mail.schemas import ThreadStatusOutput
from twaky.sentinels.mail.state import MailAgentState, ThreadStatus


@pytest.fixture
def mock_context() -> NodeContext:
    """Fixture providing a mock NodeContext."""
    base = MagicMock()
    mail = MagicMock()
    return NodeContext(base=base, mail=mail, owner_email="user@example.com")


def test_empty_thread_yields_fyi(mock_context: NodeContext) -> None:
    """Test that an empty thread returns ThreadStatus.FYI without calling LLM.

    Scenario:
    - state["thread"] is an empty list
    - Expected: node returns {"status": ThreadStatus.FYI}
    - LLM is NOT called
    """
    node = make_thread_status(mock_context)
    state: MailAgentState = {"thread": [], "email_id": "test@example.com"}

    with patch("twaky.sentinels.mail.nodes.structured_call") as mock_call:
        result = node(state)

        # Verify output
        assert result == {"status": ThreadStatus.FYI}

        # Verify LLM was NOT called
        mock_call.assert_not_called()


def test_llm_choice_propagates(mock_context: NodeContext) -> None:
    """Test that LLM output status is propagated to node output.

    Scenario:
    - state["thread"] has one email
    - Mock structured_call returns ThreadStatusOutput(status=ThreadStatus.TO_REPLY)
    - Expected: node returns {"status": ThreadStatus.TO_REPLY}
    """
    node = make_thread_status(mock_context)
    state: MailAgentState = {
        "thread": [
            {
                "id": "msg1",
                "from": [{"email": "sender@example.com"}],
                "subject": "Hello",
                "preview": "Do you have time?",
            }
        ],
        "email_id": "msg1",
    }

    mock_output = ThreadStatusOutput(
        status=ThreadStatus.TO_REPLY,
        reasoning="User needs to respond to the question.",
    )

    with patch(
        "twaky.sentinels.mail.nodes.structured_call", return_value=mock_output
    ) as mock_call:
        result = node(state)

        # Verify output
        assert result == {"status": ThreadStatus.TO_REPLY}

        # Verify LLM was called once
        mock_call.assert_called_once()

        # Extract the arguments passed to structured_call
        call_args = mock_call.call_args
        assert call_args is not None

        # Verify hardening and use_case arguments
        assert call_args.kwargs["hardening"].value == "compact"
        assert call_args.kwargs["use_case"].value == "thread_status"


def test_awaiting_reply_status_propagates(mock_context: NodeContext) -> None:
    """Test AWAITING_REPLY status propagates correctly."""
    node = make_thread_status(mock_context)
    state: MailAgentState = {
        "thread": [
            {
                "id": "msg1",
                "from": [{"email": "sender@example.com"}],
                "subject": "Question",
                "preview": "Can you provide this?",
            }
        ],
        "email_id": "msg1",
    }

    mock_output = ThreadStatusOutput(
        status=ThreadStatus.AWAITING_REPLY,
        reasoning="Waiting for sender to provide requested info.",
    )

    with patch(
        "twaky.sentinels.mail.nodes.structured_call", return_value=mock_output
    ):
        result = node(state)
        assert result == {"status": ThreadStatus.AWAITING_REPLY}


def test_actioned_status_propagates(mock_context: NodeContext) -> None:
    """Test ACTIONED status propagates correctly."""
    node = make_thread_status(mock_context)
    state: MailAgentState = {
        "thread": [
            {
                "id": "msg1",
                "from": [{"email": "sender@example.com"}],
                "subject": "Done",
                "preview": "All resolved.",
            }
        ],
        "email_id": "msg1",
    }

    mock_output = ThreadStatusOutput(
        status=ThreadStatus.ACTIONED,
        reasoning="Thread is complete.",
    )

    with patch(
        "twaky.sentinels.mail.nodes.structured_call", return_value=mock_output
    ):
        result = node(state)
        assert result == {"status": ThreadStatus.ACTIONED}


def test_fyi_status_propagates(mock_context: NodeContext) -> None:
    """Test FYI status propagates correctly when returned by LLM."""
    node = make_thread_status(mock_context)
    state: MailAgentState = {
        "thread": [
            {
                "id": "msg1",
                "from": [{"email": "sender@example.com"}],
                "subject": "Update",
                "preview": "Just wanted to let you know.",
            }
        ],
        "email_id": "msg1",
    }

    mock_output = ThreadStatusOutput(
        status=ThreadStatus.FYI,
        reasoning="Informational content, no action needed.",
    )

    with patch(
        "twaky.sentinels.mail.nodes.structured_call", return_value=mock_output
    ):
        result = node(state)
        assert result == {"status": ThreadStatus.FYI}


def test_multi_message_thread_invokes_llm(mock_context: NodeContext) -> None:
    """Test that multi-message threads invoke the LLM."""
    node = make_thread_status(mock_context)
    state: MailAgentState = {
        "thread": [
            {
                "id": "msg1",
                "from": [{"email": "sender@example.com"}],
                "subject": "Original request",
                "preview": "Can you do this?",
            },
            {
                "id": "msg2",
                "from": [{"email": "user@example.com"}],
                "subject": "Re: Original request",
                "preview": "Sure, I'll handle it.",
            },
        ],
        "email_id": "msg2",
    }

    mock_output = ThreadStatusOutput(
        status=ThreadStatus.ACTIONED,
        reasoning="User took ownership.",
    )

    with patch(
        "twaky.sentinels.mail.nodes.structured_call", return_value=mock_output
    ) as mock_call:
        result = node(state)

        assert result == {"status": ThreadStatus.ACTIONED}
        mock_call.assert_called_once()

        # Verify prompt was built correctly
        call_args = mock_call.call_args
        assert call_args is not None
        prompt = call_args.args[0]
        assert "Original request" in prompt
        assert "Sure, I'll handle it." in prompt


def test_thread_status_prompt_receives_owner_email(
    mock_context: NodeContext,
) -> None:
    """Test that owner_email is passed to thread_status_prompt."""
    node = make_thread_status(mock_context)
    state: MailAgentState = {
        "thread": [
            {
                "id": "msg1",
                "from": [{"email": "sender@example.com"}],
                "subject": "Test",
                "preview": "Test message",
            }
        ],
        "email_id": "msg1",
    }

    mock_output = ThreadStatusOutput(
        status=ThreadStatus.TO_REPLY,
        reasoning="Test.",
    )

    with patch(
        "twaky.sentinels.mail.nodes.structured_call", return_value=mock_output
    ) as mock_call:
        result = node(state)

        assert result == {"status": ThreadStatus.TO_REPLY}

        # Verify structured_call was called
        call_args = mock_call.call_args
        assert call_args is not None
        prompt = call_args.args[0]

        # The prompt should contain the owner's email
        assert "user@example.com" in prompt


def test_missing_thread_key_defaults_to_empty_list(
    mock_context: NodeContext,
) -> None:
    """Test that missing thread key defaults to empty list."""
    node = make_thread_status(mock_context)
    state: MailAgentState = {"email_id": "msg1"}

    with patch("twaky.sentinels.mail.nodes.structured_call") as mock_call:
        result = node(state)

        assert result == {"status": ThreadStatus.FYI}
        mock_call.assert_not_called()


def test_none_thread_value_defaults_to_empty_list(
    mock_context: NodeContext,
) -> None:
    """Test that None thread value defaults to empty list."""
    node = make_thread_status(mock_context)
    state: MailAgentState = {"thread": None, "email_id": "msg1"}

    with patch("twaky.sentinels.mail.nodes.structured_call") as mock_call:
        result = node(state)

        assert result == {"status": ThreadStatus.FYI}
        mock_call.assert_not_called()
