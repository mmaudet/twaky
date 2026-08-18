#!/bin/bash
# Manual rollback for SP5b write-side learning (no alembic in this codebase).
# Run only after disabling MAIL_SENTINEL_OBSERVER_ENABLED and stopping twaky-sentinel.
#
# For existing volumes:
#   docker exec -e POSTGRES_USER=twaky -i twaky-pg bash < sql/012_downgrade_write_side.sh
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<-'EOSQL'

    -- =========================================================
    -- Step 1: Re-satisfy NOT NULL on expires_at before restoring constraint
    -- =========================================================

    UPDATE public.mail_sentinel_memory
      SET expires_at = now() + INTERVAL '7 days'
      WHERE expires_at IS NULL;

    -- =========================================================
    -- Step 2: Restore NOT NULL on expires_at
    -- =========================================================

    ALTER TABLE public.mail_sentinel_memory
      ALTER COLUMN expires_at SET NOT NULL;

    -- =========================================================
    -- Step 3: Drop the 4 SP5b columns from mail_sentinel_memory
    -- =========================================================

    ALTER TABLE public.mail_sentinel_memory
      DROP COLUMN IF EXISTS source,
      DROP COLUMN IF EXISTS sender_email,
      DROP COLUMN IF EXISTS mission_id,
      DROP COLUMN IF EXISTS confidence;

    -- =========================================================
    -- Step 4: Drop SP5b index on mail_sentinel_memory
    -- =========================================================

    DROP INDEX IF EXISTS mail_sentinel_memory_by_source;

    -- =========================================================
    -- Step 5: Drop SP5b tables (destroys audit log — acceptable per rollback semantics)
    -- =========================================================

    DROP TABLE IF EXISTS public.mail_sentinel_observation;
    DROP TABLE IF EXISTS public.mail_sentinel_mailbox_state;

EOSQL
