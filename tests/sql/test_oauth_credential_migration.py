"""Static assertions on the oauth_credential migration script.

Runs without a live Postgres. Full DB behaviour is exercised in integration
tests once the container is available.
"""

from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "sql" / "009_init_oauth_credential.sh"


def test_script_exists_and_is_executable():
    assert SCRIPT.exists(), f"{SCRIPT} missing"
    assert SCRIPT.stat().st_mode & 0o111, f"{SCRIPT} not executable"


def test_creates_oauth_credential_table():
    text = SCRIPT.read_text()
    assert "CREATE TABLE IF NOT EXISTS public.oauth_credential" in text


def test_sentinel_name_fk_and_cascade():
    text = SCRIPT.read_text()
    # Column definition spans two lines (alignment formatting); check the key constraints.
    assert "sentinel_name" in text
    assert "TEXT NOT NULL UNIQUE" in text
    assert "REFERENCES sentinel(name) ON DELETE CASCADE" in text


def test_provider_check_regex():
    text = SCRIPT.read_text()
    assert "provider ~ '^[a-z][a-z0-9_-]{0,63}$'" in text


def test_encrypted_columns_present():
    text = SCRIPT.read_text()
    assert "refresh_token_enc         TEXT" in text
    assert "access_token_enc          TEXT" in text


def test_access_token_expires_at_present():
    text = SCRIPT.read_text()
    assert "access_token_expires_at   TIMESTAMPTZ" in text


def test_notify_uses_pg_notify_function_form():
    """Regression guard for commit 1b7b58d: must use pg_notify() not NOTIFY."""
    text = SCRIPT.read_text()
    assert "pg_notify('oauth_credential_changed'" in text


def test_after_insert_or_update_or_delete_trigger():
    text = SCRIPT.read_text()
    assert "AFTER INSERT OR UPDATE OR DELETE ON public.oauth_credential" in text


def test_before_update_trigger():
    text = SCRIPT.read_text()
    assert "BEFORE UPDATE ON public.oauth_credential" in text


def test_updated_at_trigger_body():
    text = SCRIPT.read_text()
    assert "NEW.updated_at := now()" in text


def test_trigger_names_present():
    text = SCRIPT.read_text()
    assert "oauth_credential_notify" in text
    assert "oauth_credential_touch_updated_at" in text


def test_trigger_function_names_present():
    text = SCRIPT.read_text()
    assert "notify_oauth_credential_changed" in text
    assert "oauth_credential_bump_updated_at" in text


def test_dollar_quoted_bodies_use_distinct_tags():
    """Dollar-quote tags must differ so PG can parse nested bodies."""
    text = SCRIPT.read_text()
    assert "$NOTIFYFN$" in text
    assert "$BUMPFN$" in text


def test_coalesce_new_old_all_in_notify():
    """pg_notify payload must handle INSERT (no OLD), DELETE (no NEW), and UPDATE."""
    text = SCRIPT.read_text()
    assert "COALESCE(NEW.sentinel_name, OLD.sentinel_name, 'ALL')" in text


def test_no_bare_notify_statement():
    """Guard: NOTIFY channel, %s form must not appear (broken pattern)."""
    text = SCRIPT.read_text()
    assert "NOTIFY oauth_credential_changed" not in text
