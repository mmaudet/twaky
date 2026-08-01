#!/bin/bash
# Provision the `mission` table (state coarse-grained) inside the twaky DB.
# Runs once on first-boot volume init. For existing volumes:
#   docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/004_init_mission.sh
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<-'EOSQL'
    CREATE TABLE IF NOT EXISTS public.mission (
        id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_email         TEXT NOT NULL,
        declared_by         TEXT NOT NULL,
        declared_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        intent_text         TEXT NOT NULL,
        plan                JSONB,
        state               TEXT NOT NULL DEFAULT 'declared'
                            CHECK (state IN ('declared','planning','running',
                                             'awaiting_user','done','failed','cancelled')),
        state_reason        TEXT,
        due_at              TIMESTAMPTZ,
        artifacts           JSONB NOT NULL DEFAULT '[]'::jsonb,
        langfuse_session_id TEXT,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS mission_live_idx
        ON public.mission (state)
        WHERE state IN ('declared','planning','running','awaiting_user');
    CREATE INDEX IF NOT EXISTS mission_owner_state_idx
        ON public.mission (owner_email, state);
    -- pgcrypto for gen_random_uuid() — usually preinstalled but be safe
    CREATE EXTENSION IF NOT EXISTS pgcrypto;
EOSQL
