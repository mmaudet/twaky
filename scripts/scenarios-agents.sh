#!/usr/bin/env bash
# End-to-end scenarios for Twaky Agents+Atlas (sub-project 2).
# Requires the live docker compose stack up, an LLM API key configured,
# and the twaky-plume LemonLDAP client provisioned in the deploy repo.
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
step() { echo -e "\n${BLUE}══ $* ══${NC}"; }
ok()   { echo -e "${GREEN}✔${NC} $*"; }
fail() { echo -e "${RED}✘${NC} $*"; exit 1; }

TWAKY_DIR="$(cd "$(dirname "$0")/.." && pwd)"

step "1 · seed demo data"
bash "$TWAKY_DIR/scripts/seed-demo.sh" >/dev/null
ok "seed complete"

step "2 · ensure twaky-atlas is healthy"
until [ "$(docker inspect --format '{{.State.Health.Status}}' twaky-atlas 2>/dev/null || echo starting)" = "healthy" ]; do
  sleep 3
  echo "  waiting for twaky-atlas..."
done
ok "twaky-atlas healthy"

step "3 · Mission B — Résume ma journée de demain"
BID=$(docker compose exec -T twaky-atlas twaky mission declare "Résume ma journée de demain" --wait 2>&1 | grep -oE '^declared: .+' | cut -d' ' -f2 || true)
if [ -z "${BID:-}" ]; then
  # Fall back: parse the artifact directly.
  BID=$(docker exec twaky-pg psql -tAU twaky -d twaky -c \
    "SELECT id FROM mission WHERE intent_text='Résume ma journée de demain' ORDER BY declared_at DESC LIMIT 1;")
fi
STATE_B=$(docker exec twaky-pg psql -tAU twaky -d twaky -c "SELECT state FROM mission WHERE id = '${BID}';")
[[ "$STATE_B" == "done" ]] || fail "Mission B state = $STATE_B, expected done"
ok "Mission B done"

step "4 · Mission A — Draft a reply to demo-msg-1"
AID_LINE=$(docker compose exec -T twaky-atlas twaky mission declare "Draft a reply to demo-msg-1" --wait 2>&1)
AID=$(echo "$AID_LINE" | grep -oE '^declared: .+' | cut -d' ' -f2 || \
      docker exec twaky-pg psql -tAU twaky -d twaky -c \
        "SELECT id FROM mission WHERE intent_text='Draft a reply to demo-msg-1' ORDER BY declared_at DESC LIMIT 1;")
STATE_A=$(docker exec twaky-pg psql -tAU twaky -d twaky -c "SELECT state FROM mission WHERE id = '${AID}';")
[[ "$STATE_A" == "awaiting_user" ]] || fail "Mission A state = $STATE_A, expected awaiting_user"
ok "Mission A awaiting_user"

step "5 · resume Mission A with approval"
docker compose exec -T twaky-atlas twaky mission resume "$AID" --input '{"approved": true}'
sleep 5
STATE_A_FINAL=$(docker exec twaky-pg psql -tAU twaky -d twaky -c "SELECT state FROM mission WHERE id = '${AID}';")
[[ "$STATE_A_FINAL" == "done" ]] || fail "Mission A after resume state = $STATE_A_FINAL, expected done"
ok "Mission A done after resume"

step "6 · cleanup"
docker exec twaky-pg psql -U twaky -d twaky -c \
  "DELETE FROM mission WHERE id IN ('${AID}', '${BID}');" >/dev/null
ok "test missions removed"

echo
echo -e "${GREEN}══════ AGENTS+ATLAS E2E OK ══════${NC}"
