#!/usr/bin/env bash
# Download the community ClickHouse driver JAR into ./metabase/plugins/
# (bind-mounted into the twaky-metabase container as /plugins).
#
# Metabase 0.49+ bundles many drivers but not ClickHouse — it's maintained
# externally by the ClickHouse team at:
#   https://github.com/ClickHouse/metabase-clickhouse-driver
set -euo pipefail

TWAKY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLUGINS_DIR="${TWAKY_DIR}/metabase/plugins"
DRIVER_URL="https://github.com/ClickHouse/metabase-clickhouse-driver/releases/latest/download/clickhouse.metabase-driver.jar"
DRIVER_JAR="${PLUGINS_DIR}/clickhouse.metabase-driver.jar"

mkdir -p "$PLUGINS_DIR"

if [ -s "$DRIVER_JAR" ]; then
    echo "✔ driver already present at ${DRIVER_JAR#$TWAKY_DIR/}"
else
    echo "· downloading ClickHouse driver from ${DRIVER_URL}..."
    curl -fsSL "$DRIVER_URL" -o "$DRIVER_JAR"
    echo "✔ downloaded $(du -h "$DRIVER_JAR" | cut -f1)"
fi

# Metabase container runs as root but writes need world-writable perms because
# it copies the JAR to a tmp path for JAR extraction and its own user is often
# non-root in newer images.
chmod 777 "$PLUGINS_DIR" 2>/dev/null || true
