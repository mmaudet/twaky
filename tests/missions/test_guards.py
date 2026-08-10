"""Pure state-machine tests — no DB, no LangGraph, just the transition table."""

from __future__ import annotations

import pytest

from twaky.missions.guards import InvalidTransition, check_transition
from twaky.missions.models import MissionState as S


class TestLegalTransitions:
    def test_declared_to_planning(self):
        check_transition(S.DECLARED, S.PLANNING)

    def test_planning_to_running(self):
        check_transition(S.PLANNING, S.RUNNING)

    def test_running_to_awaiting_user(self):
        check_transition(S.RUNNING, S.AWAITING_USER)

    def test_awaiting_user_to_running(self):
        check_transition(S.AWAITING_USER, S.RUNNING)

    def test_running_to_done(self):
        check_transition(S.RUNNING, S.DONE)

    def test_running_to_failed(self):
        check_transition(S.RUNNING, S.FAILED)

    def test_all_non_terminal_can_cancel(self):
        for s in (S.DECLARED, S.PLANNING, S.RUNNING, S.AWAITING_USER):
            check_transition(s, S.CANCELLED)

    def test_declared_to_failed_allowed(self):
        check_transition(S.DECLARED, S.FAILED)  # recovery path

    def test_planning_to_failed_allowed(self):
        check_transition(S.PLANNING, S.FAILED)  # recovery path

    def test_declared_to_awaiting_user_allowed(self):
        # Sentinel-emitted missions skip Atlas planning and go straight to
        # owner attention.  guards.py must allow DECLARED → AWAITING_USER.
        check_transition(S.DECLARED, S.AWAITING_USER)
        # Verify that the other DECLARED edges are still intact.
        check_transition(S.DECLARED, S.PLANNING)
        check_transition(S.DECLARED, S.CANCELLED)
        check_transition(S.DECLARED, S.FAILED)


class TestIllegalTransitions:
    def test_declared_to_running_forbidden(self):
        with pytest.raises(InvalidTransition):
            check_transition(S.DECLARED, S.RUNNING)

    def test_running_to_planning_forbidden(self):
        with pytest.raises(InvalidTransition):
            check_transition(S.RUNNING, S.PLANNING)

    def test_terminal_states_have_no_exit(self):
        for start in (S.DONE, S.FAILED, S.CANCELLED):
            for end in S:
                if start == end:
                    continue
                with pytest.raises(InvalidTransition):
                    check_transition(start, end)

    def test_error_message_includes_states(self):
        with pytest.raises(InvalidTransition) as ei:
            check_transition(S.DONE, S.RUNNING)
        assert "done" in str(ei.value)
        assert "running" in str(ei.value)
