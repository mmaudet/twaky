#!/bin/bash
# Provision the `skill` table + NOTIFY/updated_at triggers + partial index.
# Runs once on first-boot volume init. For existing volumes:
#   docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/007_init_skills.sh
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<-'EOSQL'
    CREATE TABLE IF NOT EXISTS public.skill (
        id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name           TEXT NOT NULL UNIQUE
                       CHECK (name ~ '^[a-z][a-z0-9_]{0,63}$'),
        description    TEXT NOT NULL
                       CHECK (length(description) BETWEEN 1 AND 1000),
        python_source  TEXT NOT NULL
                       CHECK (length(python_source) BETWEEN 1 AND 32000),
        config_schema  JSONB NOT NULL DEFAULT '{}'::jsonb,
        config_values  JSONB NOT NULL DEFAULT '{}'::jsonb,
        bound_agents   JSONB NOT NULL DEFAULT '[]'::jsonb
                       CHECK (jsonb_typeof(bound_agents) = 'array'),
        enabled        BOOLEAN NOT NULL DEFAULT true,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE INDEX IF NOT EXISTS skill_enabled_idx
        ON public.skill (enabled) WHERE enabled;

    CREATE OR REPLACE FUNCTION public.notify_skill_changed() RETURNS trigger AS $NOTIFYFN$
    BEGIN
      PERFORM pg_notify('skill_changed',
        COALESCE(NEW.id::text, OLD.id::text, 'ALL'));
      RETURN COALESCE(NEW, OLD);
    END;
    $NOTIFYFN$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS skill_notify ON public.skill;
    CREATE TRIGGER skill_notify
      AFTER INSERT OR UPDATE OR DELETE ON public.skill
      FOR EACH ROW EXECUTE FUNCTION public.notify_skill_changed();

    CREATE OR REPLACE FUNCTION public.skill_bump_updated_at() RETURNS trigger AS $BUMPFN$
    BEGIN
      NEW.updated_at := now();
      RETURN NEW;
    END;
    $BUMPFN$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS skill_touch_updated_at ON public.skill;
    CREATE TRIGGER skill_touch_updated_at
      BEFORE UPDATE ON public.skill
      FOR EACH ROW EXECUTE FUNCTION public.skill_bump_updated_at();
EOSQL
