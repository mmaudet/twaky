#!/bin/bash
# Provision the `agent` table + reload triggers, seed the 4 built-in agents.
# Runs once on first-boot volume init. For existing volumes:
#   docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/006_init_agents.sh
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<-'EOSQL'
    CREATE TABLE IF NOT EXISTS public.agent (
        id            TEXT PRIMARY KEY,
        display_name  TEXT NOT NULL,
        role          TEXT NOT NULL CHECK (role IN ('orchestrator', 'specialist')),
        system_prompt TEXT NOT NULL CHECK (length(system_prompt) BETWEEN 1 AND 8000),
        model         TEXT,
        temperature   REAL CHECK (temperature IS NULL OR temperature BETWEEN 0.0 AND 2.0),
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE OR REPLACE FUNCTION public.notify_agent_changed() RETURNS trigger AS $NOTIFYFN$
    BEGIN
      PERFORM pg_notify('agent_config_changed', NEW.id);
      RETURN NEW;
    END;
    $NOTIFYFN$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS agent_config_notify ON public.agent;
    CREATE TRIGGER agent_config_notify
      AFTER UPDATE ON public.agent
      FOR EACH ROW EXECUTE FUNCTION public.notify_agent_changed();

    CREATE OR REPLACE FUNCTION public.agent_bump_updated_at() RETURNS trigger AS $BUMPFN$
    BEGIN
      NEW.updated_at := now();
      RETURN NEW;
    END;
    $BUMPFN$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS agent_touch_updated_at ON public.agent;
    CREATE TRIGGER agent_touch_updated_at
      BEFORE UPDATE ON public.agent
      FOR EACH ROW EXECUTE FUNCTION public.agent_bump_updated_at();
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<EOSQL
    INSERT INTO public.agent (id, display_name, role, system_prompt, model, temperature) VALUES
      ('atlas',   'Atlas',   'orchestrator', \$ATLAS\$$(cat <<'ATLAS_EOF'
You are Atlas, the orchestrator of a personal assistant. Decompose the user's mission by calling delegate_to_chronos (calendar), delegate_to_plume (mail), delegate_to_iris (research). When you have enough information, call finish_mission with a concise final_answer and outcome='done'. If you cannot make progress after several attempts, call finish_mission with outcome='failed'.
ATLAS_EOF
)\$ATLAS\$, NULL, NULL)
    ON CONFLICT (id) DO NOTHING;
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<EOSQL
    INSERT INTO public.agent (id, display_name, role, system_prompt, model, temperature) VALUES
      ('chronos', 'Chronos', 'specialist', \$CHRONOS\$$(cat <<'CHRONOS_EOF'
You are Chronos, the calendar specialist for a personal assistant. You have tools to query the owner's calendar via the twake knowledge graph. Use them, then answer concisely. Never invent events.
CHRONOS_EOF
)\$CHRONOS\$, NULL, NULL)
    ON CONFLICT (id) DO NOTHING;
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<EOSQL
    INSERT INTO public.agent (id, display_name, role, system_prompt, model, temperature) VALUES
      ('plume',   'Plume',   'specialist', \$PLUME\$$(cat <<'PLUME_EOF'
You are Plume, the mail specialist for a personal assistant. Use the tools to read the owner's inbox and draft replies. When you have produced a draft ready for approval, return a final answer whose content is a JSON object of the shape {"answer": "<short summary>", "pending_user_input": {"kind": "approve_draft", "artifact": {"draft": "...", "to": "...", "subject": "..."}}}. For any other outcome, answer plainly.
PLUME_EOF
)\$PLUME\$, NULL, NULL)
    ON CONFLICT (id) DO NOTHING;
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<EOSQL
    INSERT INTO public.agent (id, display_name, role, system_prompt, model, temperature) VALUES
      ('iris',    'Iris',    'specialist', \$IRIS\$$(cat <<'IRIS_EOF'
You are Iris, a research specialist. Use web_search to look things up, read_url to fetch a page's main text, and ask_graph to cross-reference with the Twake knowledge graph. Be concise. Never invent.
IRIS_EOF
)\$IRIS\$, NULL, NULL)
    ON CONFLICT (id) DO NOTHING;
EOSQL
