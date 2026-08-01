#!/usr/bin/env bash
# End-to-end scenarios for Twaky API (sub-project 3a).
# Requires: live docker compose stack up, TWAKY_OWNER_EMAIL set.
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'
step() { echo -e "\n${BLUE}══ $* ══${NC}"; }
ok()   { echo -e "${GREEN}✔${NC} $*"; }
fail() { echo -e "${RED}✘${NC} $*"; exit 1; }

TWAKY_DIR="$(cd "$(dirname "$0")/.." && pwd)"

step "1 · wait for twaky-api health"
for i in $(seq 1 30); do
  status=$(docker inspect --format '{{.State.Health.Status}}' twaky-api 2>/dev/null || echo starting)
  [[ "$status" == "healthy" ]] && break
  sleep 2
done
[[ "$status" == "healthy" ]] || fail "twaky-api never became healthy"
ok "twaky-api healthy"

step "2 · forge session cookie"
OWNER=$(docker exec twaky-api sh -c 'echo -n $TWAKY_OWNER_EMAIL')
[[ -n "$OWNER" ]] || fail "TWAKY_OWNER_EMAIL not set in the container"
COOKIE=$(cd "$TWAKY_DIR" && uv run python scripts/sign-session.py "$OWNER")
[[ -n "$COOKIE" ]] || fail "failed to sign session cookie"
ok "cookie forged for $OWNER"

step "3 · declare mission via POST /missions"
MID=$(docker exec twaky-api sh -c "curl -sS \
      -H 'Content-Type: application/json' \
      -H 'Cookie: twaky_session=$COOKIE' \
      -d '{\"intent_text\":\"e2e-api-test\"}' \
      http://localhost:8000/missions" | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
[[ -n "$MID" ]] || fail "declare returned no id"
ok "mission declared: $MID"

step "4 · verify it appears in GET /missions"
docker exec twaky-api sh -c "curl -sS \
  -H 'Cookie: twaky_session=$COOKIE' \
  http://localhost:8000/missions" | grep -q "$MID" || fail "mission not in list"
ok "mission listed"

step "5 · cancel"
docker exec twaky-api sh -c "curl -sS -X POST \
  -H 'Content-Type: application/json' \
  -H 'Cookie: twaky_session=$COOKIE' \
  -d '{\"reason\":\"e2e\"}' \
  http://localhost:8000/missions/$MID/cancel" | grep -q '"cancelled"' || fail "cancel did not report cancelled state"
ok "mission cancelled"

step "6 · cleanup"
docker exec twaky-pg psql -U twaky -d twaky -c "DELETE FROM mission WHERE id = '$MID';" >/dev/null
ok "cleaned"

echo
echo -e "${GREEN}══════ API E2E OK ══════${NC}"
