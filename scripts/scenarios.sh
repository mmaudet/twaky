#!/usr/bin/env bash
# Multi-scenario end-to-end battery for twaky.
#
# Publishes a coherent set of events + attendee replies + a delete, then
# asks the agent 6 varied questions — all grouped in one Langfuse session
# so the whole flow is a single navigable trace tree in the UI.
#
# Requires: the twaky stack is up (docker compose from deploy root), an
# LLM API key is set in .env, and Langfuse creds are configured.

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
step() { echo -e "\n${BLUE}══ $* ══${NC}"; }
info() { echo -e "${YELLOW}··${NC} $*"; }
ok() { echo -e "${GREEN}✔${NC} $*"; }

# Absolute paths so we can run from anywhere.
TWAKY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY_COMPOSE="/home/mmaudet/deploy/kickstart-maudet-cloud/docker-compose.yaml"

# Single session id groups every ask made in this run.
SESSION_ID="scenario-$(date +%s)"
USER_ID="${USER_ID:-scenario-runner}"

# Helper: docker compose run wrapper — no-deps so it doesn't try to recreate long-running services.
RUN="docker compose -f ${DEPLOY_COMPOSE} run --rm --no-deps twaky-agent"

step "SESSION_ID = ${SESSION_ID}"
info "USER_ID    = ${USER_ID}"

step "1/9  Publish 3 events (2 with visio, 1 without)"

$RUN python -m twaky.cli demo \
    --uid "sprint-retro" \
    --summary "Sprint retrospective — team X" \
    --meet-url "" \
    --question "How many CalendarEvents exist in the graph right now?" \
    --wait-s 3 \
    --session "${SESSION_ID}" \
    --user "${USER_ID}" 2>&1 | grep -E '^(Q:|A:|Cypher|Trace:|▶|◷|\?)' || true

# (The event above published with an empty meet_url — treat it as a no-visio event.)
info "waiting 2s..."; sleep 2

$RUN python -c "
import asyncio
from twaky.verify_publish import publish_meeting
asyncio.run(publish_meeting(
    'design-review',
    'Design review with client Acme',
    'https://meet.twake-dev.maudet.cloud/room/design-review'
))
"
ok "published design-review with visio"

$RUN python -c "
import asyncio
from twaky.verify_publish import publish_meeting
asyncio.run(publish_meeting(
    'release-deploy',
    'Release deploy v2.1 — coordination',
    'https://meet.twake-dev.maudet.cloud/room/release-deploy'
))
"
ok "published release-deploy with visio"

step "2/9  Attendee replies (RSVP): Bob accepts, Carol declines"

$RUN python -c "
import asyncio
from twaky.verify_publish import publish_reply
asyncio.run(publish_reply('sprint-retro', 'bob@twake-dev.maudet.cloud', 'ACCEPTED'))
asyncio.run(publish_reply('sprint-retro', 'carol@twake-dev.maudet.cloud', 'DECLINED'))
"
ok "sent 2 replies"

step "3/9  Update the design-review event (moved to a different time)"

$RUN python -c "
import asyncio
from twaky.verify_publish import _meeting_event, _publish
ev = _meeting_event(
    'design-review',
    'Design review with client Acme (RESCHEDULED)',
    'https://meet.twake-dev.maudet.cloud/room/design-review',
)
asyncio.run(_publish('calendar:event:updated', ev, 'update-design-review'))
"
ok "sent calendar:event:updated for design-review"

step "4/9  Delete the sprint-retro event"

$RUN python -c "
import asyncio
from twaky.verify_publish import publish_delete
asyncio.run(publish_delete('sprint-retro'))
"
ok "sent calendar:event:deleted for sprint-retro"

info "waiting 4s for the projector to settle..."; sleep 4

step "5/9  Sanity check the graph directly (bypass the agent)"

docker exec -i twaky-pg psql -tAU twaky -d twaky <<'SQL'
LOAD 'age';
SET search_path = ag_catalog, "$user", public;
SELECT * FROM cypher('twake', $CQR$
    MATCH (e:CalendarEvent) RETURN e.uid AS uid, e.summary AS summary, e.deleted AS deleted
$CQR$) AS (uid agtype, summary agtype, deleted agtype);
SQL
ok "graph state printed above"

step "6/9  Q1 — simple count"
$RUN python -m twaky.cli ask \
    "How many CalendarEvents are in the graph, including deleted ones?" \
    --session "${SESSION_ID}" --user "${USER_ID}" --tag scenario --tag q1-count \
    2>&1 | grep -E '^(Q:|A:|Cypher|Trace:)' || true

step "7/9  Q2 — property lookup"
$RUN python -m twaky.cli ask \
    "What is the visio URL for the release-deploy event?" \
    --session "${SESSION_ID}" --user "${USER_ID}" --tag scenario --tag q2-property \
    2>&1 | grep -E '^(Q:|A:|Cypher|Trace:)' || true

step "8/9  Q3 — attendee filter on RSVP status"
$RUN python -m twaky.cli ask \
    "Which people declined the sprint-retro event?" \
    --session "${SESSION_ID}" --user "${USER_ID}" --tag scenario --tag q3-rsvp \
    2>&1 | grep -E '^(Q:|A:|Cypher|Trace:)' || true

step "9/9  Q4-Q6 — traversal, filter, multi-hop"

for q_and_tag in \
    "Which events does Alice organize?::q4-relationship" \
    "How many people attend the release-deploy event?::q5-count-rel" \
    "List the events that have a visio URL and their attendees.::q6-multi-hop"
do
    QUESTION="${q_and_tag%%::*}"
    TAG="${q_and_tag##*::}"
    info "Q: ${QUESTION}"
    $RUN python -m twaky.cli ask "${QUESTION}" \
        --session "${SESSION_ID}" --user "${USER_ID}" \
        --tag scenario --tag "${TAG}" \
        2>&1 | grep -E '^(Q:|A:|Cypher|Trace:)' || true
done

echo
echo -e "${GREEN}══════════════════════════════════════════════════════════════${NC}"
ok "Scenario complete — session_id=${SESSION_ID}"
echo -e "  Open the session in Langfuse:"
echo -e "    ${YELLOW}https://langfuse.twake-dev.maudet.cloud/project/twaky-project/sessions/${SESSION_ID}${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════════════${NC}"
