#!/bin/bash
# Provision a dedicated database + role for Langfuse in the same Postgres instance.
# Env vars supplied via docker-compose: LANGFUSE_PG_USER, LANGFUSE_PG_PASSWORD, LANGFUSE_PG_DB.
set -euo pipefail

: "${LANGFUSE_PG_USER:?LANGFUSE_PG_USER must be set}"
: "${LANGFUSE_PG_PASSWORD:?LANGFUSE_PG_PASSWORD must be set}"
: "${LANGFUSE_PG_DB:?LANGFUSE_PG_DB must be set}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${LANGFUSE_PG_USER}') THEN
            CREATE ROLE ${LANGFUSE_PG_USER} LOGIN PASSWORD '${LANGFUSE_PG_PASSWORD}';
        END IF;
    END
    \$\$;
EOSQL

# CREATE DATABASE can't run inside a transaction block; use conditional shell logic.
DB_EXISTS=$(psql -tAc "SELECT 1 FROM pg_database WHERE datname='${LANGFUSE_PG_DB}'" --username "$POSTGRES_USER" --dbname postgres)
if [ "$DB_EXISTS" != "1" ]; then
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
        -c "CREATE DATABASE ${LANGFUSE_PG_DB} OWNER ${LANGFUSE_PG_USER};"
fi
