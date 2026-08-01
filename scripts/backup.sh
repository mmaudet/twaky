#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# twaky/backup.sh — daily backup of all three Langfuse backing stores.
#
# Dumps to /home/mmaudet/backups/twaky/YYYY-MM-DD/:
#   postgres/twaky.dump              (pg_dump -Fc,      DB=twaky)
#   postgres/langfuse.dump           (pg_dump -Fc,      DB=langfuse)
#   clickhouse/<table>.native.gz     (per-table SELECT ... FORMAT Native, gzipped)
#   clickhouse/<table>.schema.sql    (per-table SHOW CREATE TABLE)
#   clickhouse/databases.sql         (CREATE DATABASE statements)
#   seaweedfs/langfuse.tar.gz        (S3 bucket contents mirrored via mc, then tarred)
#   MANIFEST                         (versions, sizes, sha256)
#
# Then prunes date-stamped dirs older than RETENTION_DAYS (default 14).
#
# Usage:
#   scripts/backup.sh              # run backup
#   scripts/backup.sh --dry-run    # print what would happen, touch nothing
#
# Designed to be idempotent (safe to re-run on the same day: overwrites).
# Everything runs on this host via `docker exec` on the twake-network —
# no host ports are required.
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── config ──────────────────────────────────────────────────────────
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_ROOT="${BACKUP_ROOT:-/home/mmaudet/backups/twaky}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
NETWORK="${NETWORK:-twake-network}"
MC_IMAGE="${MC_IMAGE:-minio/mc:latest}"

PG_CONTAINER="${PG_CONTAINER:-twaky-pg}"
CH_CONTAINER="${CH_CONTAINER:-twaky-clickhouse}"
SW_CONTAINER="${SW_CONTAINER:-twaky-seaweedfs}"
SW_S3_ENDPOINT="${SW_S3_ENDPOINT:-http://twaky-seaweedfs:8333}"
SW_BUCKET="${SW_BUCKET:-langfuse}"

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=1
fi

# ── logging ─────────────────────────────────────────────────────────
ts()   { date -u +%Y-%m-%dT%H:%M:%SZ; }
info() { echo "[$(ts)] INFO  $*"; }
warn() { echo "[$(ts)] WARN  $*" >&2; }
die()  { echo "[$(ts)] FATAL $*" >&2; exit 1; }

run() {
    # run "<description>" <cmd...>
    local desc="$1"; shift
    if (( DRY_RUN )); then
        echo "[$(ts)] DRY   $desc"
        echo "                  \$ $*"
    else
        info "$desc"
        "$@"
    fi
}

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

command -v docker >/dev/null || die "docker not on PATH"
command -v gzip   >/dev/null || die "gzip not on PATH"
command -v tar    >/dev/null || die "tar not on PATH"

for c in "$PG_CONTAINER" "$CH_CONTAINER" "$SW_CONTAINER"; do
    if ! docker inspect "$c" >/dev/null 2>&1; then
        die "container $c does not exist — is the stack up?"
    fi
done

# ── paths ───────────────────────────────────────────────────────────
DATE="$(date -u +%Y-%m-%d)"
DEST="$BACKUP_ROOT/$DATE"
PG_DIR="$DEST/postgres"
CH_DIR="$DEST/clickhouse"
SW_DIR="$DEST/seaweedfs"

info "backup date        : $DATE (UTC)"
info "output root        : $DEST"
info "retention          : ${RETENTION_DAYS} days"
info "dry-run            : $DRY_RUN"

if (( DRY_RUN )); then
    echo "[$(ts)] DRY   mkdir -p $PG_DIR $CH_DIR $SW_DIR"
else
    mkdir -p "$PG_DIR" "$CH_DIR" "$SW_DIR"
fi

# ── 1. PostgreSQL ───────────────────────────────────────────────────
# pg_dump -Fc (custom format) gives us a compressed, restorable dump
# that pg_restore can filter and parallelise. One dump per DB.
info "── postgres ─────────────────────────────────────────────"

pg_dump_db() {
    local db="$1" user="$2" pw="$3" out="$4"
    if (( DRY_RUN )); then
        echo "[$(ts)] DRY   pg_dump $db → $out"
        echo "                  \$ docker exec -e PGPASSWORD=*** $PG_CONTAINER pg_dump -Fc -U $user -d $db > $out"
    else
        info "pg_dump $db → $out"
        docker exec -e PGPASSWORD="$pw" "$PG_CONTAINER" \
            pg_dump -Fc --no-owner --no-privileges -U "$user" -d "$db" > "$out.part"
        mv "$out.part" "$out"
    fi
}

pg_dump_db "$TWAKY_PG_DB"    "$TWAKY_PG_USER"    "$TWAKY_PG_PASSWORD"    "$PG_DIR/twaky.dump"
pg_dump_db "$LANGFUSE_PG_DB" "$LANGFUSE_PG_USER" "$LANGFUSE_PG_PASSWORD" "$PG_DIR/langfuse.dump"

# Also dump global roles/tablespaces so a full-cluster restore is possible.
if (( DRY_RUN )); then
    echo "[$(ts)] DRY   pg_dumpall --globals-only → $PG_DIR/globals.sql"
    echo "                  \$ docker exec -e PGPASSWORD=*** $PG_CONTAINER pg_dumpall -U $TWAKY_PG_USER --globals-only > $PG_DIR/globals.sql"
else
    info "pg_dumpall --globals-only → $PG_DIR/globals.sql"
    docker exec -e PGPASSWORD="$TWAKY_PG_PASSWORD" "$PG_CONTAINER" \
        pg_dumpall -U "$TWAKY_PG_USER" --globals-only > "$PG_DIR/globals.sql.part"
    mv "$PG_DIR/globals.sql.part" "$PG_DIR/globals.sql"
fi

# ── 2. ClickHouse ───────────────────────────────────────────────────
# Portable, config-free approach:
#   - dump CREATE DATABASE for user-created databases
#   - per user table: SHOW CREATE TABLE  +  SELECT * ... FORMAT Native (gz)
# Restorable with `clickhouse-client --query "INSERT ... FORMAT Native"`.
info "── clickhouse ───────────────────────────────────────────"

ch_query() {
    docker exec "$CH_CONTAINER" clickhouse-client \
        --user default --password "$CLICKHOUSE_PASSWORD" \
        --query "$1"
}

# List user databases (skip built-ins).
CH_DBS_RAW="$(ch_query "SELECT name FROM system.databases WHERE name NOT IN ('system','INFORMATION_SCHEMA','information_schema')")"
CH_DBS=()
while IFS= read -r line; do
    [[ -n "$line" ]] && CH_DBS+=("$line")
done <<< "$CH_DBS_RAW"

info "clickhouse databases to back up: ${CH_DBS[*]:-<none>}"

if (( DRY_RUN )); then
    echo "[$(ts)] DRY   write $CH_DIR/databases.sql (CREATE DATABASE statements)"
else
    : > "$CH_DIR/databases.sql"
    for db in "${CH_DBS[@]}"; do
        ch_query "SHOW CREATE DATABASE \`$db\`" >> "$CH_DIR/databases.sql"
        echo ";" >> "$CH_DIR/databases.sql"
    done
fi

for db in "${CH_DBS[@]}"; do
    TABLES_RAW="$(ch_query "SELECT name FROM system.tables WHERE database='$db' AND engine NOT LIKE '%View%' AND is_temporary=0")"
    TABLES=()
    while IFS= read -r t; do
        [[ -n "$t" ]] && TABLES+=("$t")
    done <<< "$TABLES_RAW"

    for tbl in "${TABLES[@]}"; do
        schema_out="$CH_DIR/${db}.${tbl}.schema.sql"
        data_out="$CH_DIR/${db}.${tbl}.native.gz"
        if (( DRY_RUN )); then
            echo "[$(ts)] DRY   ch dump ${db}.${tbl} → ${data_out} (+ schema)"
        else
            info "ch dump ${db}.${tbl}"
            ch_query "SHOW CREATE TABLE \`$db\`.\`$tbl\`" > "${schema_out}.part"
            mv "${schema_out}.part" "$schema_out"
            docker exec "$CH_CONTAINER" clickhouse-client \
                --user default --password "$CLICKHOUSE_PASSWORD" \
                --query "SELECT * FROM \`$db\`.\`$tbl\` FORMAT Native" \
                | gzip -c > "${data_out}.part"
            mv "${data_out}.part" "$data_out"
        fi
    done
done

# ── 3. SeaweedFS (S3 bucket) ────────────────────────────────────────
# `mc mirror` walks the bucket and pulls every object to a local dir,
# which we then tar+gz. `mc` runs in a one-shot container on twake-network
# so it can reach twaky-seaweedfs:8333 without any host port.
info "── seaweedfs (bucket=${SW_BUCKET}) ──────────────────────"

MC_STAGE="$SW_DIR/_stage"
SW_TARBALL="$SW_DIR/${SW_BUCKET}.tar.gz"

if (( DRY_RUN )); then
    echo "[$(ts)] DRY   mkdir -p $MC_STAGE"
    echo "[$(ts)] DRY   mc mirror ${SW_S3_ENDPOINT}/${SW_BUCKET} → $MC_STAGE (via one-shot $MC_IMAGE on $NETWORK)"
    echo "[$(ts)] DRY   tar czf $SW_TARBALL -C $MC_STAGE ."
    echo "[$(ts)] DRY   rm -rf $MC_STAGE"
else
    mkdir -p "$MC_STAGE"
    info "mc mirror ${SW_S3_ENDPOINT}/${SW_BUCKET} → $MC_STAGE"
    docker run --rm --network "$NETWORK" \
        -v "$MC_STAGE:/backup" \
        -e MC_HOST_sw="http://${S3_ACCESS_KEY}:${S3_SECRET_KEY}@twaky-seaweedfs:8333" \
        --entrypoint sh "$MC_IMAGE" -c \
        "mc mirror --overwrite --remove sw/${SW_BUCKET} /backup"
    info "tar czf $SW_TARBALL"
    tar czf "${SW_TARBALL}.part" -C "$MC_STAGE" .
    mv "${SW_TARBALL}.part" "$SW_TARBALL"
    rm -rf "$MC_STAGE"
fi

# ── 4. Manifest ─────────────────────────────────────────────────────
info "── manifest ─────────────────────────────────────────────"
if (( DRY_RUN )); then
    echo "[$(ts)] DRY   write $DEST/MANIFEST"
else
    {
        echo "# twaky backup manifest"
        echo "date_utc: $(ts)"
        echo "host: $(hostname)"
        echo
        echo "## container versions"
        for c in "$PG_CONTAINER" "$CH_CONTAINER" "$SW_CONTAINER"; do
            echo "$c: $(docker inspect --format '{{.Config.Image}}' "$c")"
        done
        echo
        echo "## files (size, sha256)"
        cd "$DEST"
        find . -type f ! -name MANIFEST -print0 \
            | sort -z \
            | xargs -0 -I{} sh -c 'printf "%s  %s  %s\n" "$(stat -c%s "$1")" "$(sha256sum "$1" | cut -d" " -f1)" "$1"' _ {}
    } > "$DEST/MANIFEST"
fi

# ── 5. Prune old backups ────────────────────────────────────────────
info "── prune (keep last ${RETENTION_DAYS} days) ─────────────"
if [[ ! -d "$BACKUP_ROOT" ]]; then
    warn "backup root $BACKUP_ROOT missing, skipping prune"
else
    # Find date-stamped dirs older than RETENTION_DAYS.
    mapfile -t OLD < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d \
        -regextype posix-extended -regex '.*/[0-9]{4}-[0-9]{2}-[0-9]{2}$' \
        -mtime "+${RETENTION_DAYS}" | sort)

    if (( ${#OLD[@]} == 0 )); then
        info "nothing to prune"
    else
        for d in "${OLD[@]}"; do
            if (( DRY_RUN )); then
                echo "[$(ts)] DRY   rm -rf $d"
            else
                info "rm -rf $d"
                rm -rf "$d"
            fi
        done
    fi
fi

info "backup done → $DEST"
