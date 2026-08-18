#!/bin/bash
# SP7 / Task 141 downgrade — drop mail_sentinel_style_profile table.
# Manual rollback (no alembic in this codebase).
# Run only after MAIL_SENTINEL_OBSERVER_ENABLED is set to False and
# twaky-sentinel is restarted, otherwise a concurrent analysis tick may
# recreate rows just before the drop.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<-'EOSQL'

    DROP INDEX IF EXISTS mail_sentinel_style_profile_by_computed_at;
    DROP TABLE IF EXISTS public.mail_sentinel_style_profile;

EOSQL
