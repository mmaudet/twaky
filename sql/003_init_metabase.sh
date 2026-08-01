#!/bin/bash
# Provision a dedicated database + role for Metabase's own metadata storage
# (dashboards, users, sessions). Uses the same twaky-pg instance.
set -euo pipefail

: "${METABASE_PG_USER:?METABASE_PG_USER must be set}"
: "${METABASE_PG_PASSWORD:?METABASE_PG_PASSWORD must be set}"
: "${METABASE_PG_DB:?METABASE_PG_DB must be set}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${METABASE_PG_USER}') THEN
            CREATE ROLE ${METABASE_PG_USER} LOGIN PASSWORD '${METABASE_PG_PASSWORD}';
        END IF;
    END
    \$\$;
EOSQL

DB_EXISTS=$(psql -tAc "SELECT 1 FROM pg_database WHERE datname='${METABASE_PG_DB}'" --username "$POSTGRES_USER" --dbname postgres)
if [ "$DB_EXISTS" != "1" ]; then
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
        -c "CREATE DATABASE ${METABASE_PG_DB} OWNER ${METABASE_PG_USER};"
fi
