#!/bin/bash
# Add provenance columns to mail_sentinel_spam_decision (SP6d T1).
# Idempotent: uses ADD COLUMN IF NOT EXISTS. Non-blocking on Postgres 11+.
# Run manually on existing volumes:
#   docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/013_add_spam_decision_provenance.sh
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<-'EOSQL'

    -- =========================================================
    -- SP6d T1: provenance columns for origin mailbox + envelope
    -- =========================================================

    ALTER TABLE public.mail_sentinel_spam_decision
        ADD COLUMN IF NOT EXISTS origin_mailbox_id   TEXT,
        ADD COLUMN IF NOT EXISTS origin_mailbox_role TEXT,
        ADD COLUMN IF NOT EXISTS envelope_headers    JSONB;

EOSQL
