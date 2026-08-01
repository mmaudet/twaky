#!/bin/bash
# Init the twaky database with Apache AGE + event_log table.
# Executed once by the apache/age image entrypoint on first boot (as postgres user, against POSTGRES_DB=twaky).
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
    CREATE EXTENSION IF NOT EXISTS age;
    LOAD 'age';
    SET search_path = ag_catalog, "$user", public;

    -- The named property graph for Twake entities.
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'twake') THEN
            PERFORM ag_catalog.create_graph('twake');
        END IF;
    END;
    $$;

    -- Immutable event log — the replayable source of truth.
    CREATE TABLE IF NOT EXISTS public.event_log (
        id            BIGSERIAL PRIMARY KEY,
        exchange      TEXT        NOT NULL,
        routing_key   TEXT        NOT NULL DEFAULT '',
        message_id    TEXT        UNIQUE,
        payload       JSONB       NOT NULL,
        received_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        processed_at  TIMESTAMPTZ,
        status        TEXT        NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'processed', 'error')),
        error         TEXT
    );

    CREATE INDEX IF NOT EXISTS event_log_pending_idx
        ON public.event_log (id) WHERE status = 'pending';
    CREATE INDEX IF NOT EXISTS event_log_exchange_idx
        ON public.event_log (exchange);
EOSQL
