#!/bin/bash
# Placeholder — the checkpointer tables are created at runtime by
# setup_checkpointer_tables() (called at Atlas boot). This script exists
# so the sql/ layout stays sequential.
set -euo pipefail
echo "langgraph checkpointer tables created lazily by setup_checkpointer_tables()"
