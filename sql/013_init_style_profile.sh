#!/bin/bash
# SP7 / Task 141: mail_sentinel_style_profile table.
# Stores per-owner writing-style profiles auto-computed from the Sent folder.
# For existing volumes:
#   docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/013_init_style_profile.sh
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<-'EOSQL'

    -- =========================================================
    -- SP7 / Task 141: mail_sentinel_style_profile
    -- =========================================================

    CREATE TABLE IF NOT EXISTS public.mail_sentinel_style_profile (
        owner_email             TEXT PRIMARY KEY,
        profile                 TEXT NOT NULL,
        computed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
        sent_count_at_compute   INTEGER NOT NULL,
        sample_size             INTEGER NOT NULL,
        model                   TEXT
    );

    CREATE INDEX IF NOT EXISTS mail_sentinel_style_profile_by_computed_at
        ON public.mail_sentinel_style_profile (computed_at DESC);

EOSQL
