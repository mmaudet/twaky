"""Static assertions on sql/012_seed_starter_rules.sh.

The rule schema is validated at INSERT time by the mail_sentinel_rule
constraints (CHECK on actions being a jsonb array, unique name, etc.),
so a runtime integration test would only re-check the DB layer. This
suite verifies the migration script itself is well-formed and safe:
executable, idempotent (ON CONFLICT DO NOTHING), and encodes the
intended rule shape.
"""

from __future__ import annotations

import os
from pathlib import Path

MIGRATION = Path(__file__).parents[2] / "sql" / "012_seed_starter_rules.sh"


def test_script_exists_and_is_executable() -> None:
    assert MIGRATION.exists(), f"missing migration: {MIGRATION}"
    assert os.access(MIGRATION, os.X_OK), "migration is not executable (chmod +x)"


def test_script_is_idempotent() -> None:
    """Uses ON CONFLICT (name) DO NOTHING for every INSERT — safe re-run.

    Counts only occurrences inside the SQL heredoc (skips the shell
    comment block at the top which explains what the script does).
    """
    body = MIGRATION.read_text()
    # Extract the SQL heredoc: everything between the "<<-'EOSQL'" marker
    # and the closing "EOSQL" on its own line.
    assert "<<-'EOSQL'" in body, "expected psql heredoc marker"
    sql_only = body.split("<<-'EOSQL'", 1)[1].split("\nEOSQL", 1)[0]
    inserts = sql_only.count("INSERT INTO public.mail_sentinel_rule")
    conflicts = sql_only.count("ON CONFLICT (name) DO NOTHING")
    assert inserts >= 1, "expected at least one INSERT in the SQL heredoc"
    assert inserts == conflicts, (
        f"every INSERT must have ON CONFLICT DO NOTHING "
        f"(inserts={inserts}, conflicts={conflicts})"
    )


def test_github_notifications_rule_shape() -> None:
    """Encodes name, priority 45, static from-contains condition, label-only action."""
    body = MIGRATION.read_text()
    assert "'github_notifications'" in body
    # Priority 45 = between the alias rules (40-44) and the generic
    # Inbox-Zero starter rules (90-200). Tuning this constant is a
    # deliberate product choice; the test locks it against accidental drift.
    assert "45," in body, "expected priority 45 for github_notifications"
    # Static condition — from contains notifications@github.com
    assert '"from"' in body
    assert '"contains"' in body
    assert "notifications@github.com" in body
    # Action must NOT include archive (goal: keep in INBOX).
    # We check that the actions array contains label:github and does
    # not include archive alongside github_notifications INSERT.
    assert '"label:github"' in body
    # Guard: no 'archive' action smuggled in for this rule.
    github_stanza = body.split("'github_notifications'", 1)[1].split("ON CONFLICT")[0]
    assert '"archive"' not in github_stanza, (
        "github_notifications must NOT archive — keep in INBOX for work signal"
    )


def test_uses_correct_uses_capabilities_header() -> None:
    """Matches sql/011 style: psql heredoc pattern, ON_ERROR_STOP, POSTGRES_USER."""
    body = MIGRATION.read_text()
    assert "psql -v ON_ERROR_STOP=1" in body
    assert '--username "$POSTGRES_USER"' in body
    assert "set -euo pipefail" in body
