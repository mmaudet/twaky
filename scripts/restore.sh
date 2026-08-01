#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# twaky/restore.sh — restore all three stores from a dated backup.
#
# Usage:
#   scripts/restore.sh <YYYY-MM-DD> [component ...]
#
# Components (default = all):  postgres  clickhouse  seaweedfs
#
# Examples:
#   scripts/restore.sh 2026-07-31
#   scripts/restore.sh 2026-07-31 postgres
#   scripts/restore.sh 2026-07-31 clickhouse seaweedfs
#
# Environment:
#   BACKUP_ROOT   default /home/mmaudet/backups/twaky
#   FORCE=1       skip the interactive "type YES to overwrite" prompt
#
# What it does per component:
#
#   postgres    — DROP + CREATE each DB (twaky, langfuse), then pg_restore
#                 the corresponding .dump file.
#   clickhouse  — DROP each user database, replay databases.sql +
#                 per-table schema.sql, then INSERT ... FORMAT Native from
#                 the .native.gz dumps.
#   seaweedfs   — untar into a stage dir, then mc mirror --overwrite --remove
#                 back into the langfuse bucket.
#
# Restore is destructive: the target DBs / bucket are wiped and repopulated
# from the snapshot. Stop workers before running to avoid confusing them.
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_ROOT="${BACKUP_ROOT:-/home/mmaudet/backups/twaky}"
NETWORK="${NETWORK:-twake-network}"
MC_IMAGE="${MC_IMAGE:-minio/mc:latest}"

PG_CONTAINER="${PG_CONTAINER:-twaky-pg}"
CH_CONTAINER="${CH_CONTAINER:-twaky-clickhouse}"
SW_CONTAINER="${SW_CONTAINER:-twaky-seaweedfs}"
SW_BUCKET="${SW_BUCKET:-langfuse}"

ts()   { date -u +%Y-%m-%dT%H:%M:%SZ; }
info() { echo "[$(ts)] INFO  $*"; }
warn() { echo "[$(ts)] WARN  $*" >&2; }
die()  { echo "[$(ts)] FATAL $*" >&2; exit 1; }

# ── args ────────────────────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
    echo "usage: $0 <YYYY-MM-DD> [postgres|clickhouse|seaweedfs ...]" >&2
    exit 2
fi
DATE="$1"; shift || true

# Validate DATE format.
[[ "$DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || die "date must be YYYY-MM-DD, got '$DATE'"

# Which components to restore.
if [[ $# -eq 0 ]]; then
    COMPONENTS=(postgres clickhouse seaweedfs)
else
    COMPONENTS=("$@")
fi

# ── prerequisites ───────────────────────────────────────────────────
[[ -f "$REPO_DIR/.env" ]] || die "cannot find $REPO_DIR/.env"
# shellcheck disable=SC1091
set -a; source "$REPO_DIR/.env"; set +a

: "${TWAKY_PG_DB:=twaky}"
: "${TWAKY_PG_USER:=twaky}"
: "${TWAKY_PG_PASSWORD:?TWAKY_PG_PASSWORD not set in .env}"
: "${LANGFUSE_PG_DB:=langfuse}"
: "${LANGFUSE_PG_USER:=langfuse}"
: "${LANGFUSE_PG_PASSWORD:?LANGFUSE_PG_PASSWORD not set in .env}"
: "${CLICKHOUSE_PASSWORD:?CLICKHOUSE_PASSWORD not set in .env}"
: "${S3_ACCESS_KEY:?S3_ACCESS_KEY not set in .env}"
: "${S3_SECRET_KEY:?S3_SECRET_KEY not set in .env}"

SRC="$BACKUP_ROOT/$DATE"
[[ -d "$SRC" ]] || die "backup dir $SRC does not exist"

info "restore source     : $SRC"
info "components         : ${COMPONENTS[*]}"

# ── safety prompt ───────────────────────────────────────────────────
if [[ "${FORCE:-0}" != "1" ]]; then
    echo
    echo "This will DESTROY current data in the selected stores and replace it"
    echo "with the snapshot from $DATE. Stop any writers (workers, ingest) first."
    read -r -p "Type YES to proceed: " ANSWER
    [[ "$ANSWER" == "YES" ]] || die "aborted"
fi

# ── helpers ─────────────────────────────────────────────────────────
has_component() {
    local want="$1"
    for c in "${COMPONENTS[@]}"; do
        [[ "$c" == "$want" ]] && return 0
    done
    return 1
}

# ── PostgreSQL ──────────────────────────────────────────────────────
if has_component postgres; then
    info "── postgres restore ─────────────────────────────────────"
    PG_DIR="$SRC/postgres"
    [[ -d "$PG_DIR" ]] || die "missing $PG_DIR"

    restore_db() {
        local db="$1" user="$2" pw="$3" dump="$4"
        [[ -f "$dump" ]] || die "missing $dump"

        info "drop + create db $db (owner=$user)"
        # Terminate open sessions, then drop.
        docker exec -e PGPASSWORD="$TWAKY_PG_PASSWORD" "$PG_CONTAINER" \
            psql -U "$TWAKY_PG_USER" -d postgres -v ON_ERROR_STOP=1 -c \
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$db' AND pid <> pg_backend_pid();" >/dev/null

        docker exec -e PGPASSWORD="$TWAKY_PG_PASSWORD" "$PG_CONTAINER" \
            psql -U "$TWAKY_PG_USER" -d postgres -v ON_ERROR_STOP=1 -c \
            "DROP DATABASE IF EXISTS \"$db\";"

        docker exec -e PGPASSWORD="$TWAKY_PG_PASSWORD" "$PG_CONTAINER" \
            psql -U "$TWAKY_PG_USER" -d postgres -v ON_ERROR_STOP=1 -c \
            "CREATE DATABASE \"$db\" OWNER \"$user\";"

        info "pg_restore $dump → $db"
        docker exec -i -e PGPASSWORD="$pw" "$PG_CONTAINER" \
            pg_restore --no-owner --no-privileges --exit-on-error \
            -U "$user" -d "$db" < "$dump"
    }

    restore_db "$TWAKY_PG_DB"    "$TWAKY_PG_USER"    "$TWAKY_PG_PASSWORD"    "$PG_DIR/twaky.dump"
    restore_db "$LANGFUSE_PG_DB" "$LANGFUSE_PG_USER" "$LANGFUSE_PG_PASSWORD" "$PG_DIR/langfuse.dump"
fi

# ── ClickHouse ──────────────────────────────────────────────────────
if has_component clickhouse; then
    info "── clickhouse restore ───────────────────────────────────"
    CH_DIR="$SRC/clickhouse"
    [[ -d "$CH_DIR" ]] || die "missing $CH_DIR"
    [[ -f "$CH_DIR/databases.sql" ]] || die "missing $CH_DIR/databases.sql"

    ch_exec() {
        docker exec -i "$CH_CONTAINER" clickhouse-client \
            --user default --password "$CLICKHOUSE_PASSWORD" \
            --multiquery "$@"
    }

    # Discover databases from schema filenames (db.table.schema.sql).
    mapfile -t CH_DBS < <(
        find "$CH_DIR" -maxdepth 1 -type f -name '*.schema.sql' -printf '%f\n' \
            | sed 's/\.[^.]*\.schema\.sql$//' | sort -u
    )
    info "clickhouse databases to restore: ${CH_DBS[*]:-<none>}"

    # Drop then recreate each db.
    for db in "${CH_DBS[@]}"; do
        info "drop database $db"
        ch_exec --query "DROP DATABASE IF EXISTS \`$db\`"
    done

    info "replay databases.sql"
    ch_exec < "$CH_DIR/databases.sql"

    # Replay schemas (per table).
    for schema in "$CH_DIR"/*.schema.sql; do
        [[ -f "$schema" ]] || continue
        info "replay schema $(basename "$schema")"
        ch_exec < "$schema"
    done

    # Load data per table.
    for gz in "$CH_DIR"/*.native.gz; do
        [[ -f "$gz" ]] || continue
        # Filename is <db>.<table>.native.gz
        base="$(basename "$gz" .native.gz)"
        db="${base%%.*}"
        tbl="${base#*.}"
        info "load $db.$tbl ← $(basename "$gz")"
        gunzip -c "$gz" | docker exec -i "$CH_CONTAINER" clickhouse-client \
            --user default --password "$CLICKHOUSE_PASSWORD" \
            --query "INSERT INTO \`$db\`.\`$tbl\` FORMAT Native"
    done
fi

# ── SeaweedFS ───────────────────────────────────────────────────────
if has_component seaweedfs; then
    info "── seaweedfs restore ────────────────────────────────────"
    SW_DIR="$SRC/seaweedfs"
    SW_TARBALL="$SW_DIR/${SW_BUCKET}.tar.gz"
    [[ -f "$SW_TARBALL" ]] || die "missing $SW_TARBALL"

    STAGE="$(mktemp -d -t twaky-restore-sw.XXXXXX)"
    trap 'rm -rf "$STAGE"' EXIT

    info "untar $SW_TARBALL → $STAGE"
    tar xzf "$SW_TARBALL" -C "$STAGE"

    info "mc mirror --overwrite --remove $STAGE → sw/${SW_BUCKET}"
    docker run --rm --network "$NETWORK" \
        -v "$STAGE:/backup:ro" \
        -e MC_HOST_sw="http://${S3_ACCESS_KEY}:${S3_SECRET_KEY}@twaky-seaweedfs:8333" \
        --entrypoint sh "$MC_IMAGE" -c \
        "mc mb --ignore-existing sw/${SW_BUCKET} && mc mirror --overwrite --remove /backup sw/${SW_BUCKET}"
fi

info "restore done ← $SRC"
