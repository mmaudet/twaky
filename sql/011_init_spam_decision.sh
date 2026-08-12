#!/bin/bash
# Provision mail_sentinel_spam_decision table, indexes, and extend config_schema.
# Runs once on first-boot volume init. For existing volumes:
#   docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/011_init_spam_decision.sh
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<-'EOSQL'

    -- =========================================================
    -- SP6c: mail_sentinel_spam_decision table
    -- =========================================================

    CREATE TABLE IF NOT EXISTS public.mail_sentinel_spam_decision (
        id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        email_id           TEXT NOT NULL,
        thread_id          TEXT,
        sender_email       TEXT NOT NULL,
        subject            TEXT NOT NULL DEFAULT '',
        received_at        TIMESTAMPTZ NOT NULL,
        bucket             TEXT NOT NULL
                           CHECK (bucket IN ('spam','newsletter','phishing-alert')),
        signal_source      TEXT NOT NULL
                           CHECK (signal_source IN (
                               'rspamd_junk_keyword',
                               'rspamd_nonjunk_pass_through',
                               'rspamd_status_reject',
                               'rspamd_status_rewrite',
                               'heuristic_newsletter',
                               'llm_grey_zone'
                           )),
        score              NUMERIC(4,3),
        reason             TEXT,
        restored_at        TIMESTAMPTZ,
        restored_by        TEXT,
        decided_at         TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE INDEX IF NOT EXISTS mail_sentinel_spam_decision_by_decided_at
        ON mail_sentinel_spam_decision (decided_at DESC);
    CREATE INDEX IF NOT EXISTS mail_sentinel_spam_decision_by_sender
        ON mail_sentinel_spam_decision (sender_email);
    CREATE INDEX IF NOT EXISTS mail_sentinel_spam_decision_active
        ON mail_sentinel_spam_decision (decided_at DESC)
        WHERE restored_at IS NULL;

EOSQL

# Second heredoc: unquoted so we can splice config_schema updates via jsonb_set.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<EOSQL

    UPDATE sentinel SET config_schema = jsonb_set(config_schema, '{properties,spam_filter_enabled}', '{"type":"boolean","default":false}'::jsonb, true) WHERE name='mail';
    UPDATE sentinel SET config_schema = jsonb_set(config_schema, '{properties,spam_llm_confidence_threshold}', '{"type":"number","minimum":0,"maximum":1,"default":0.70}'::jsonb, true) WHERE name='mail';
    UPDATE sentinel SET config_schema = jsonb_set(config_schema, '{properties,spam_llm_newsletter_threshold}', '{"type":"number","minimum":0,"maximum":1,"default":0.70}'::jsonb, true) WHERE name='mail';
    UPDATE sentinel SET config_schema = jsonb_set(config_schema, '{properties,spam_purge_active_days}', '{"type":"integer","minimum":1,"default":30}'::jsonb, true) WHERE name='mail';
    UPDATE sentinel SET config_schema = jsonb_set(config_schema, '{properties,spam_purge_restored_days}', '{"type":"integer","minimum":1,"default":90}'::jsonb, true) WHERE name='mail';

EOSQL
