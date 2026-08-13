#!/bin/bash
# SP5b write-side learning: extend mail_sentinel_memory + add mailbox_state and observation tables.
# For existing volumes:
#   docker exec -e POSTGRES_USER=twaky -i twaky-pg bash < sql/012_init_write_side.sh
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<-'EOSQL'

    -- =========================================================
    -- SP5b: extend mail_sentinel_memory with 4 columns
    -- =========================================================

    ALTER TABLE public.mail_sentinel_memory
      ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual','auto_diff','auto_reclass','auto_move')),
      ADD COLUMN IF NOT EXISTS sender_email TEXT,
      ADD COLUMN IF NOT EXISTS mission_id UUID
        REFERENCES public.mission(id) ON DELETE SET NULL,
      ADD COLUMN IF NOT EXISTS confidence NUMERIC(3,2)
        CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1));

    -- Allow "Keep permanent" memories: expires_at can now be NULL
    ALTER TABLE public.mail_sentinel_memory
      ALTER COLUMN expires_at DROP NOT NULL;

    CREATE INDEX IF NOT EXISTS mail_sentinel_memory_by_source
      ON public.mail_sentinel_memory (source, created_at DESC);

    -- =========================================================
    -- SP5b: mail_sentinel_mailbox_state
    -- =========================================================

    CREATE TABLE IF NOT EXISTS public.mail_sentinel_mailbox_state (
        mailbox_id  TEXT PRIMARY KEY,
        role        TEXT,
        name        TEXT,
        jmap_state  TEXT NOT NULL,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    -- =========================================================
    -- SP5b: mail_sentinel_observation
    -- =========================================================

    CREATE TABLE IF NOT EXISTS public.mail_sentinel_observation (
        id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        email_id           TEXT NOT NULL,
        mailbox_id         TEXT NOT NULL,
        observation_type   TEXT NOT NULL
                           CHECK (observation_type IN (
                               'draft_sent',
                               'marked_spam',
                               'unmarked_spam',
                               'moved_to_custom'
                           )),
        observed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        extraction_outcome TEXT NOT NULL
                           CHECK (extraction_outcome IN ('extracted','skipped_trivial','skipped_no_match','error')),
        memory_ids         UUID[] NOT NULL DEFAULT '{}',
        pattern_ids        UUID[] NOT NULL DEFAULT '{}',
        error_repr         TEXT,
        UNIQUE (email_id, mailbox_id, observation_type)
    );

    CREATE INDEX IF NOT EXISTS mail_sentinel_observation_recent
      ON public.mail_sentinel_observation (observed_at DESC);

EOSQL
