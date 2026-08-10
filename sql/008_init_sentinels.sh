#!/bin/bash
# Provision sentinel + mail_sentinel_* tables, indexes, triggers, and seed row.
# Runs once on first-boot volume init. For existing volumes:
#   docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/008_init_sentinels.sh
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<-'EOSQL'

    -- =========================================================
    -- 5.1 Framework tables: sentinel + sentinel_run
    -- =========================================================

    CREATE TABLE IF NOT EXISTS public.sentinel (
        name           TEXT PRIMARY KEY
                       CHECK (name ~ '^[a-z][a-z0-9_-]{0,63}$'),
        display_name   TEXT NOT NULL,
        description    TEXT NOT NULL,
        version        TEXT NOT NULL,
        enabled        BOOLEAN NOT NULL DEFAULT true,
        config_schema  JSONB NOT NULL DEFAULT '{}'::jsonb,
        config_values  JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS public.sentinel_run (
        id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        sentinel_name  TEXT NOT NULL REFERENCES sentinel(name) ON DELETE CASCADE,
        event_ref      TEXT NOT NULL,
        started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        completed_at   TIMESTAMPTZ,
        duration_ms    INTEGER,
        outcome        TEXT NOT NULL
                       CHECK (outcome IN ('ignored','processed','mission_created','delegated','error')),
        mission_id     UUID,
        llm_calls      INTEGER NOT NULL DEFAULT 0,
        error_repr     TEXT,
        trace          JSONB NOT NULL DEFAULT '[]'::jsonb
    );

    CREATE INDEX IF NOT EXISTS sentinel_run_by_sentinel_started
        ON sentinel_run (sentinel_name, started_at DESC);
    CREATE INDEX IF NOT EXISTS sentinel_run_by_mission
        ON sentinel_run (mission_id) WHERE mission_id IS NOT NULL;

    -- Triggers: use pg_notify() function form (regression fix 1b7b58d 2026-08-03;
    -- NEVER use NOTIFY channel, %s — that is the broken form).
    CREATE OR REPLACE FUNCTION public.notify_sentinel_changed() RETURNS trigger AS $NOTIFYFN$
    BEGIN
      PERFORM pg_notify('sentinel_changed', COALESCE(NEW.name, OLD.name, 'ALL'));
      RETURN COALESCE(NEW, OLD);
    END;
    $NOTIFYFN$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS sentinel_notify ON public.sentinel;
    CREATE TRIGGER sentinel_notify
      AFTER UPDATE ON public.sentinel
      FOR EACH ROW EXECUTE FUNCTION public.notify_sentinel_changed();

    CREATE OR REPLACE FUNCTION public.sentinel_bump_updated_at() RETURNS trigger AS $BUMPFN$
    BEGIN
      NEW.updated_at := now();
      RETURN NEW;
    END;
    $BUMPFN$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS sentinel_touch_updated_at ON public.sentinel;
    CREATE TRIGGER sentinel_touch_updated_at
      BEFORE UPDATE ON public.sentinel
      FOR EACH ROW EXECUTE FUNCTION public.sentinel_bump_updated_at();

    -- =========================================================
    -- 5.2 Mail-vertical tables
    -- =========================================================

    CREATE TABLE IF NOT EXISTS public.mail_sentinel_rule (
        id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name           TEXT NOT NULL UNIQUE
                       CHECK (name ~ '^[a-z][a-z0-9_-]{0,63}$'),
        description    TEXT NOT NULL DEFAULT '',
        conditions     JSONB NOT NULL DEFAULT '[]'::jsonb
                       CHECK (jsonb_typeof(conditions) = 'array'),
        combinator     TEXT NOT NULL DEFAULT 'OR' CHECK (combinator IN ('OR','AND')),
        actions        JSONB NOT NULL DEFAULT '[]'::jsonb
                       CHECK (jsonb_typeof(actions) = 'array'),
        priority       INTEGER NOT NULL DEFAULT 100,
        enabled        BOOLEAN NOT NULL DEFAULT true,
        run_on_threads BOOLEAN NOT NULL DEFAULT true,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS mail_sentinel_rule_priority
        ON mail_sentinel_rule (priority) WHERE enabled;

    CREATE TABLE IF NOT EXISTS public.mail_sentinel_memory (
        id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        kind           TEXT NOT NULL
                       CHECK (kind IN ('fact','procedure','preference')),
        scope          TEXT NOT NULL
                       CHECK (scope IN ('sender','domain','global')),
        scope_value    TEXT NOT NULL,
        content        TEXT NOT NULL,
        evidence       JSONB NOT NULL DEFAULT '[]'::jsonb,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at     TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '7 days'),
        UNIQUE (kind, scope, scope_value, content)
    );
    CREATE INDEX IF NOT EXISTS mail_sentinel_memory_scope_lookup
        ON mail_sentinel_memory (scope, scope_value, kind);
    CREATE INDEX IF NOT EXISTS mail_sentinel_memory_ttl
        ON mail_sentinel_memory (expires_at);

    CREATE TABLE IF NOT EXISTS public.mail_sentinel_learned_pattern (
        id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        sender_email      TEXT NOT NULL,
        rule_name         TEXT NOT NULL,
        confidence        NUMERIC(3,2) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
        evidence_count    INTEGER NOT NULL DEFAULT 1 CHECK (evidence_count >= 1),
        first_seen        TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_confirmed    TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (sender_email, rule_name)
    );
    CREATE INDEX IF NOT EXISTS mail_sentinel_pattern_by_sender
        ON mail_sentinel_learned_pattern (sender_email);

EOSQL

# Second heredoc: unquoted so we can splice JSON via shell cat.
# The dollar-tag markers are escaped so Bash does not interpret them.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<EOSQL

    INSERT INTO public.sentinel
        (name, display_name, description, version, config_schema, config_values)
    VALUES (
        'mail',
        'Mail sentinel',
        'Autonomous email triage: rule cascade, learned patterns, memories, draft reply.',
        '1.0.0',
        \$SCHEMA\$$(cat <<'MAIL_SCHEMA_EOF'
{
  "type": "object",
  "properties": {
    "event_source":                  {"type": "string",  "default": "jmap_poll"},
    "memory_candidate_pool":         {"type": "integer", "minimum": 10, "maximum": 500, "default": 100},
    "memory_inject_max":             {"type": "integer", "minimum": 1,  "maximum": 32,  "default": 16},
    "pattern_min_samples":           {"type": "integer", "minimum": 2,  "maximum": 10,  "default": 3},
    "pattern_confidence_threshold":  {"type": "number",  "minimum": 0.5,"maximum": 1.0, "default": 0.9}
  },
  "additionalProperties": false
}
MAIL_SCHEMA_EOF
)\$SCHEMA\$::jsonb,
        \$VALUES\$$(cat <<'MAIL_VALUES_EOF'
{
  "event_source": "jmap_poll",
  "memory_candidate_pool": 100,
  "memory_inject_max": 16,
  "pattern_min_samples": 3,
  "pattern_confidence_threshold": 0.9
}
MAIL_VALUES_EOF
)\$VALUES\$::jsonb
    )
    ON CONFLICT (name) DO NOTHING;


EOSQL
