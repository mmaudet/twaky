"""Static assertions on the SP6d provenance migration script (013).

Runs without a live Postgres instance. The migration is never executed here —
the operator applies it manually. These tests guard the script's shape and
content to prevent accidental truncation or dangerous statements.
"""

from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "sql" / "013_add_spam_decision_provenance.sh"


def test_script_exists_and_is_executable():
    assert SCRIPT.exists(), f"{SCRIPT} missing"
    assert SCRIPT.stat().st_mode & 0o111, f"{SCRIPT} not executable"


def test_contains_set_euo_pipefail():
    text = SCRIPT.read_text()
    assert "set -euo pipefail" in text


def test_add_column_origin_mailbox_id():
    text = SCRIPT.read_text()
    assert "ADD COLUMN IF NOT EXISTS origin_mailbox_id" in text
    assert "TEXT" in text


def test_add_column_origin_mailbox_role():
    text = SCRIPT.read_text()
    assert "ADD COLUMN IF NOT EXISTS origin_mailbox_role" in text
    assert "TEXT" in text


def test_add_column_envelope_headers():
    text = SCRIPT.read_text()
    assert "ADD COLUMN IF NOT EXISTS envelope_headers" in text
    assert "JSONB" in text


def test_no_destructive_statements():
    text = SCRIPT.read_text().upper()
    assert "DROP " not in text, "migration must not contain DROP statements"
    assert "DELETE " not in text, "migration must not contain DELETE statements"
    assert "TRUNCATE " not in text, "migration must not contain TRUNCATE statements"
