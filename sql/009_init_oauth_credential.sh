#!/bin/bash
# Provision oauth_credential table, indexes, trigger functions, and triggers.
# Runs once on first-boot volume init. For existing volumes:
#   docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/009_init_oauth_credential.sh
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<-'EOSQL'

    -- =========================================================
    -- SP6b: oauth_credential table
    -- =========================================================

    CREATE TABLE IF NOT EXISTS public.oauth_credential (
        id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        sentinel_name             TEXT NOT NULL UNIQUE
                                  REFERENCES sentinel(name) ON DELETE CASCADE,
        provider                  TEXT NOT NULL CHECK (provider ~ '^[a-z][a-z0-9_-]{0,63}$'),
        client_id                 TEXT NOT NULL,
        -- token_endpoint and session_url derived from provider config; stored to avoid
        -- a round-trip discovery on every refresh.
        token_endpoint            TEXT NOT NULL,
        session_url               TEXT NOT NULL,
        scope                     TEXT NOT NULL DEFAULT 'openid profile email offline_access',
        -- Fernet-encrypted (base64 ASCII stored as TEXT).
        refresh_token_enc         TEXT,
        access_token_enc          TEXT,
        access_token_expires_at   TIMESTAMPTZ,
        -- Non-secret metadata from userinfo for the UI.
        account_email             TEXT,
        account_name              TEXT,
        -- Operational metadata.
        last_refresh_at           TIMESTAMPTZ,
        last_refresh_error        TEXT,
        created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    -- Triggers: use pg_notify() function form (regression fix 1b7b58d 2026-08-03;
    -- NEVER use NOTIFY channel, %s — that is the broken form).
    CREATE OR REPLACE FUNCTION public.notify_oauth_credential_changed() RETURNS trigger AS $NOTIFYFN$
    BEGIN
      PERFORM pg_notify('oauth_credential_changed', COALESCE(NEW.sentinel_name, OLD.sentinel_name, 'ALL'));
      RETURN COALESCE(NEW, OLD);
    END;
    $NOTIFYFN$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS oauth_credential_notify ON public.oauth_credential;
    CREATE TRIGGER oauth_credential_notify
      AFTER INSERT OR UPDATE OR DELETE ON public.oauth_credential
      FOR EACH ROW EXECUTE FUNCTION public.notify_oauth_credential_changed();

    CREATE OR REPLACE FUNCTION public.oauth_credential_bump_updated_at() RETURNS trigger AS $BUMPFN$
    BEGIN
      NEW.updated_at := now();
      RETURN NEW;
    END;
    $BUMPFN$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS oauth_credential_touch_updated_at ON public.oauth_credential;
    CREATE TRIGGER oauth_credential_touch_updated_at
      BEFORE UPDATE ON public.oauth_credential
      FOR EACH ROW EXECUTE FUNCTION public.oauth_credential_bump_updated_at();

EOSQL
