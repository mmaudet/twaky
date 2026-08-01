#!/usr/bin/env bash
# End-to-end verification script for twaky.
# Runs the 7 acceptance checks (T1..T7) from the plan and prints PASS/FAIL.

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
pass() { echo -e "${GREEN}PASS${NC} $*"; }
fail() { echo -e "${RED}FAIL${NC} $*"; exit 1; }
info() { echo -e "${YELLOW}····${NC} $*"; }

cd "$(dirname "$0")/.."
source .env

UID1="verify-e2e-$(date +%s)-1"
UID2="verify-e2e-$(date +%s)-2"

info "T1 · non-regression bus: check existing consumers on calendar:event:created"
BIND_COUNT=$(docker exec rabbitmq rabbitmqctl list_bindings -p / source_name destination_name 2>/dev/null \
    | awk '$1 == "calendar:event:created"' | wc -l)
[[ "$BIND_COUNT" -ge 2 ]] || fail "expected >=2 bindings on calendar:event:created, got $BIND_COUNT"
pass "T1 · $BIND_COUNT bindings on calendar:event:created (agent + existing consumers)"

info "T2 · publish synthetic event uid=$UID1 and expect it in event_log"
docker compose run --rm twaky-agent twaky verify --uid "$UID1" >/dev/null
sleep 3
COUNT=$(docker exec twaky-pg psql -tAU twaky -d twaky -c "SELECT count(*) FROM event_log WHERE payload->>'uid'='$UID1';")
[[ "$COUNT" == "1" ]] || fail "expected 1 event_log row for $UID1, got $COUNT"
pass "T2 · event_log has $COUNT row for $UID1"

info "T2b · projector wrote CalendarEvent to graph"
NODE_COUNT=$(docker exec -i twaky-pg psql -tAU twaky -d twaky <<SQL | tail -1 | tr -d '"'
LOAD 'age';
SET search_path = ag_catalog, "\$user", public;
SELECT * FROM cypher('twake', \$CQR\$ MATCH (e:CalendarEvent {uid: "$UID1"}) RETURN count(e) AS n \$CQR\$) AS (n agtype);
SQL
)
[[ "$NODE_COUNT" == "1" ]] || fail "expected 1 CalendarEvent for $UID1 in graph, got $NODE_COUNT"
pass "T2b · CalendarEvent{uid=$UID1} present in graph"

info "T3 · idempotence: republish same event, expect count stays 1"
docker compose run --rm twaky-agent twaky verify --uid "$UID1" >/dev/null
sleep 3
COUNT=$(docker exec twaky-pg psql -tAU twaky -d twaky -c "SELECT count(*) FROM event_log WHERE payload->>'uid'='$UID1';")
[[ "$COUNT" == "1" ]] || fail "expected event_log still has 1 row (dedup), got $COUNT"
pass "T3 · event_log dedup OK (still $COUNT row)"

info "T4 · replay: mark verify rows as pending, projector re-MERGE, no dupes"
docker exec twaky-pg psql -U twaky -d twaky -c \
    "UPDATE event_log SET status='pending', processed_at=NULL, error=NULL WHERE payload->>'uid'='$UID1'" >/dev/null
sleep 4
NODE_COUNT_AFTER=$(docker exec -i twaky-pg psql -tAU twaky -d twaky <<SQL | tail -1 | tr -d '"'
LOAD 'age';
SET search_path = ag_catalog, "\$user", public;
SELECT * FROM cypher('twake', \$CQR\$ MATCH (e:CalendarEvent {uid: "$UID1"}) RETURN count(e) AS n \$CQR\$) AS (n agtype);
SQL
)
[[ "$NODE_COUNT_AFTER" == "1" ]] || fail "replay created duplicate CalendarEvent (now $NODE_COUNT_AFTER)"
pass "T4 · replay idempotent (still $NODE_COUNT_AFTER CalendarEvent)"

if [[ -z "${OPENROUTER_API_KEY:-}${OPENAI_API_KEY:-}${ANTHROPIC_API_KEY:-}" ]]; then
    echo -e "${YELLOW}SKIP${NC} T5/T7 · no LLM API key in .env (set OPENROUTER/OPENAI/ANTHROPIC_API_KEY)"
else
    info "T5 · twaky ask 'Who attended $UID1?'"
    OUT=$(docker compose run --rm twaky-agent twaky ask "Who attended the event with uid $UID1?" 2>&1)
    echo "$OUT" | grep -q "Cypher generated" || fail "no Cypher generated line in output"
    ANSWER=$(echo "$OUT" | grep -A1 '^Q:' | tail -1 | sed 's/^A: //')
    pass "T5 · agent answered: '$ANSWER'"

    info "T7 · trace visible in Langfuse"
    TRACE_ID=$(echo "$OUT" | grep -oE 'traces/[a-f0-9]+' | head -1 | cut -d/ -f2)
    [[ -n "$TRACE_ID" ]] || fail "no trace id in output"
    sleep 3
    TRACE_JSON=$(docker run --rm --network twake-network curlimages/curl:8.10.1 -fsS \
        -u "${LANGFUSE_PUBLIC_KEY}:${LANGFUSE_SECRET_KEY}" \
        "http://twaky-langfuse-web:3000/api/public/traces/$TRACE_ID" 2>/dev/null)
    echo "$TRACE_JSON" | grep -q '"projectId":"twaky-project"' || fail "trace $TRACE_ID not found in Langfuse"
    pass "T7 · trace $TRACE_ID visible in Langfuse (project=twaky-project)"
fi

info "T6 · pytest suite"
uv run pytest -q 2>&1 | tail -3
pass "T6 · pytest suite green"

echo
echo -e "${GREEN}=== ALL CHECKS PASSED ===${NC}"
