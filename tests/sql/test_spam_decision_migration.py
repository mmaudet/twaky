"""Static assertions on the mail_sentinel_spam_decision migration script.

Runs without a live Postgres. Full DB behaviour is exercised in integration
tests once the container is available.
"""

from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "sql" / "011_init_spam_decision.sh"


def test_script_exists_and_is_executable():
    assert SCRIPT.exists(), f"{SCRIPT} missing"
    assert SCRIPT.stat().st_mode & 0o111, f"{SCRIPT} not executable"


def test_creates_spam_decision_table():
    text = SCRIPT.read_text()
    assert "CREATE TABLE IF NOT EXISTS public.mail_sentinel_spam_decision" in text


def test_bucket_check_constraint():
    text = SCRIPT.read_text()
    assert "CHECK (bucket IN ('spam','newsletter','phishing-alert'))" in text


def test_signal_source_check_constraint():
    text = SCRIPT.read_text()
    assert "'rspamd_junk_keyword'" in text
    assert "'rspamd_nonjunk_pass_through'" in text
    assert "'rspamd_status_reject'" in text
    assert "'rspamd_status_rewrite'" in text
    assert "'heuristic_newsletter'" in text
    assert "'llm_grey_zone'" in text
    # Verify they're all in a CHECK constraint on signal_source
    assert "signal_source IN" in text
    assert "CHECK (signal_source IN" in text


def test_index_by_decided_at():
    text = SCRIPT.read_text()
    assert "mail_sentinel_spam_decision_by_decided_at" in text
    assert "ON mail_sentinel_spam_decision (decided_at DESC)" in text


def test_index_by_sender():
    text = SCRIPT.read_text()
    assert "mail_sentinel_spam_decision_by_sender" in text
    assert "ON mail_sentinel_spam_decision (sender_email)" in text


def test_index_active_partial():
    text = SCRIPT.read_text()
    assert "mail_sentinel_spam_decision_active" in text
    assert "WHERE restored_at IS NULL" in text


def test_config_schema_spam_filter_enabled():
    text = SCRIPT.read_text()
    assert "jsonb_set(config_schema, '{properties,spam_filter_enabled}'" in text


def test_config_schema_spam_llm_confidence_threshold():
    text = SCRIPT.read_text()
    assert (
        "jsonb_set(config_schema, '{properties,spam_llm_confidence_threshold}'" in text
    )


def test_config_schema_spam_llm_newsletter_threshold():
    text = SCRIPT.read_text()
    assert (
        "jsonb_set(config_schema, '{properties,spam_llm_newsletter_threshold}'" in text
    )


def test_config_schema_spam_purge_active_days():
    text = SCRIPT.read_text()
    assert "jsonb_set(config_schema, '{properties,spam_purge_active_days}'" in text


def test_config_schema_spam_purge_restored_days():
    text = SCRIPT.read_text()
    assert "jsonb_set(config_schema, '{properties,spam_purge_restored_days}'" in text


def test_config_updates_target_mail_sentinel():
    text = SCRIPT.read_text()
    # Count how many times "WHERE name='mail'" appears (should be 5 for the 5 updates)
    count = text.count("WHERE name='mail'")
    assert count >= 5, f"Expected at least 5 'WHERE name='mail'' updates, found {count}"
