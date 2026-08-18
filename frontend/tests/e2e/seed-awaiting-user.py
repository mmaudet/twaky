"""Seed a mission into `awaiting_user` state for E2E testing.

Bypasses the Atlas daemon: declares → start_planning → commit_plan →
request_user_input(kind="approve_draft"). Prints the mission id to stdout.

Usage:
    docker compose exec -T twaky-api python -m frontend.tests.e2e.seed_awaiting_user
    (or copy this file into a docker-exec-friendly path)
"""

from __future__ import annotations

import sys

from twaky.missions import engine
from twaky.missions.models import PlanStep


def seed_awaiting_user(owner_email: str) -> str:
    m = engine.declare(
        intent_text="E2E: approve this draft",
        owner_email=owner_email,
        declared_by=owner_email,
    )
    engine.start_planning(m.id)
    engine.commit_plan(m.id, [PlanStep(agent="plume", tool="draft_reply", args={})])
    engine.request_user_input(
        m.id,
        reason="approve_draft",
        artifact={
            "kind": "approve_draft",
            "draft": "Hi Bob — thanks for reaching out.",
            "to": "bob@x.com",
            "subject": "Re: Question about widgets",
        },
    )
    return str(m.id)


if __name__ == "__main__":
    email = sys.argv[1] if len(sys.argv) > 1 else "michel.maudet@linagora.com"
    print(seed_awaiting_user(email))
