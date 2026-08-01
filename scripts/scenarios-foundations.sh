#!/usr/bin/env bash
# End-to-end verification of Twaky Foundations (sub-project 1).
#
# Requires the live stack (docker compose from deploy root) + TWAKY_OWNER_EMAIL
# set in twaky/.env.
#
# Verifies:
#   T1 · owner filter drops events not for the owner
#   T2 · mail:message:received lands in the graph as an Email node
#   T3 · mission lifecycle (declared → planning → running → awaiting_user → done)
#   T4 · crash-mid-flight recovery (checkpoint_lost → failed)

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
step() { echo -e "\n${BLUE}══ $* ══${NC}"; }
ok()   { echo -e "${GREEN}✔${NC} $*"; }
fail() { echo -e "${RED}✘${NC} $*"; exit 1; }

TWAKY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY="${TWAKY_DIR}/docker-compose.yml"
RUN="docker compose -f ${DEPLOY} run --rm --no-deps twaky-agent"

# shellcheck disable=SC1091
source "${TWAKY_DIR}/.env"
OWNER="${TWAKY_OWNER_EMAIL}"

step "T1 · owner filter — publish 2 mails (owner + stranger), only owner survives"
MID_OWNER="scenario-$(date +%s)-owner"
MID_STRANGER="scenario-$(date +%s)-stranger"
$RUN python -c "
import asyncio, json, aio_pika
from twaky.config import settings

async def pub(mid, user):
    conn = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with conn:
        ch = await conn.channel()
        ex = await ch.get_exchange('mail:message:received', ensure=True)
        await ex.publish(aio_pika.Message(
            body=json.dumps({'message_id': mid, 'user': user,
                             'mailbox_path': {'namespace': '#private',
                                              'user': user, 'name': 'INBOX'},
                             'timestamp': '2026-08-01T12:00:00Z'}).encode(),
            message_id='scenario-' + mid,
        ), routing_key='')

asyncio.run(pub('${MID_OWNER}', '${OWNER}'))
asyncio.run(pub('${MID_STRANGER}', 'stranger@example.com'))
"
sleep 3
COUNT_OWNER=$(docker exec twaky-pg psql -tAU twaky -d twaky -c \
    "SELECT count(*) FROM event_log WHERE payload->>'message_id'='${MID_OWNER}';")
COUNT_STRANGER=$(docker exec twaky-pg psql -tAU twaky -d twaky -c \
    "SELECT count(*) FROM event_log WHERE payload->>'message_id'='${MID_STRANGER}';")
[[ "$COUNT_OWNER"    == "1" ]] || fail "owner mail count = $COUNT_OWNER, expected 1"
[[ "$COUNT_STRANGER" == "0" ]] || fail "stranger mail count = $COUNT_STRANGER, expected 0"
ok "T1 · owner=1, stranger=0 in event_log"

step "T2 · Email node in graph for the owner's mail"
GRAPH_COUNT=$(docker exec -i twaky-pg psql -tAU twaky -d twaky <<SQL | tail -1 | tr -d '"'
LOAD 'age';
SET search_path = ag_catalog, "\$user", public;
SELECT * FROM cypher('twake', \$CQR\$ MATCH (e:Email {message_id: "${MID_OWNER}"}) RETURN count(e) AS n \$CQR\$) AS (n agtype);
SQL
)
[[ "$GRAPH_COUNT" == "1" ]] || fail "graph count for Email{message_id=${MID_OWNER}} = $GRAPH_COUNT"
ok "T2 · Email node present"

step "T3 · mission lifecycle happy path"
MISSION_ID=$($RUN python -c "
from twaky.missions import engine
from twaky.missions.models import PlanStep
m = engine.declare(intent_text='scenario check', owner_email='${OWNER}', declared_by='${OWNER}')
engine.start_planning(m.id)
engine.commit_plan(m.id, [PlanStep(agent='chronos', tool='list_events', args={})])
engine.request_user_input(m.id, reason='approve', artifact={'draft': 'hi'})
engine.resume(m.id, user_response={'ok': True})
engine.finish(m.id, outcome='done', artifacts=[{'final': 'ok'}])
print(m.id)
" | tail -1)
STATE=$(docker exec twaky-pg psql -tAU twaky -d twaky -c \
    "SELECT state FROM mission WHERE id = '${MISSION_ID}';")
[[ "$STATE" == "done" ]] || fail "mission ${MISSION_ID} state = $STATE, expected done"
ok "T3 · mission ${MISSION_ID} traversed all 6 states"

step "T4 · crash recovery — mission stuck in running with no checkpoint"
STUCK_ID=$($RUN python -c "
from twaky.missions.checkpointer import setup_checkpointer_tables
setup_checkpointer_tables()
from twaky.missions import engine
from twaky.missions.models import PlanStep
m = engine.declare(intent_text='stuck', owner_email='${OWNER}', declared_by='${OWNER}')
engine.start_planning(m.id)
engine.commit_plan(m.id, [PlanStep(agent='chronos', tool='list_events', args={})])
print(m.id)
" | tail -1)
RECOVERY_ACTION=$($RUN python -c "
from twaky.missions.recovery import resume_missions_after_restart
for mid, action in resume_missions_after_restart(owner_email='${OWNER}'):
    if str(mid) == '${STUCK_ID}':
        print(action)
        break
" | tail -1)
[[ "$RECOVERY_ACTION" == "failed_checkpoint_lost" ]] || fail "recovery for ${STUCK_ID} = $RECOVERY_ACTION"
STATE=$(docker exec twaky-pg psql -tAU twaky -d twaky -c "SELECT state FROM mission WHERE id = '${STUCK_ID}';")
[[ "$STATE" == "failed" ]] || fail "stuck mission state after recovery = $STATE"
ok "T4 · stuck mission ${STUCK_ID} auto-failed"

# cleanup
docker exec twaky-pg psql -U twaky -d twaky -c \
    "DELETE FROM mission WHERE id IN ('${MISSION_ID}', '${STUCK_ID}');" >/dev/null
docker exec twaky-pg psql -U twaky -d twaky -c \
    "DELETE FROM event_log WHERE message_id IN ('scenario-${MID_OWNER}');" >/dev/null
docker exec -i twaky-pg psql -tAU twaky -d twaky >/dev/null <<SQL
LOAD 'age';
SET search_path = ag_catalog, "\$user", public;
SELECT * FROM cypher('twake', \$CQR\$ MATCH (e:Email {message_id: "${MID_OWNER}"}) DETACH DELETE e \$CQR\$) AS (v agtype);
SQL

echo -e "\n${GREEN}══════ ALL FOUNDATIONS CHECKS PASSED ══════${NC}"
