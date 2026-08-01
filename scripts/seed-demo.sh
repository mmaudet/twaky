#!/usr/bin/env bash
# Seed the AGE graph with synthetic contacts, calendar events, and Email
# nodes for the demo missions of sub-project 2. Idempotent — re-running
# overwrites (MERGE-on-key semantics from Foundations mappers).
set -euo pipefail

TWAKY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY="${TWAKY_DIR}/docker-compose.yml"

info() { echo -e "\033[1;34m··\033[0m $*"; }
ok()   { echo -e "\033[0;32m✔\033[0m $*"; }

info "seeding calendar events for tomorrow"
TOMORROW=$(date -d "tomorrow" +%Y-%m-%d)
docker exec -i twaky-pg psql -U twaky -d twaky <<SQL
LOAD 'age';
SET search_path = ag_catalog, "\$user", public;
SELECT * FROM cypher('twake', \$CQR\$
    MERGE (bob:Person {email: "bob@twake-dev.maudet.cloud"})
      SET bob.fn = "Bob Builder"
    MERGE (carol:Person {email: "carol@twake-dev.maudet.cloud"})
      SET carol.fn = "Carol Chen"
    MERGE (alice:Person {email: "michel.maudet@linagora.com"})
      SET alice.fn = "Michel Maudet"
    MERGE (acme:Organization {name: "Acme Corp"})
    MERGE (bob)-[:WORKS_AT]->(acme)
    MERGE (e1:CalendarEvent {uid: "demo-standup-${TOMORROW}"})
      SET e1.summary = "Team standup",
          e1.start_at = "${TOMORROW}T09:00:00+00:00",
          e1.end_at   = "${TOMORROW}T09:30:00+00:00",
          e1.deleted  = false
    MERGE (alice)-[:ORGANIZED]->(e1)
    MERGE (bob)-[:ATTENDED]->(e1)
    MERGE (carol)-[:ATTENDED]->(e1)
    MERGE (e2:CalendarEvent {uid: "demo-acme-review-${TOMORROW}"})
      SET e2.summary = "Acme design review",
          e2.start_at = "${TOMORROW}T14:00:00+00:00",
          e2.end_at   = "${TOMORROW}T15:00:00+00:00",
          e2.meet_url = "https://meet.twake-dev.maudet.cloud/room/demo",
          e2.deleted  = false
    MERGE (alice)-[:ORGANIZED]->(e2)
    MERGE (bob)-[:ATTENDED]->(e2)
    RETURN 1
\$CQR\$) AS (v agtype);
SQL
ok "calendar seeded"

info "seeding Email nodes (metadata only — Plume fetches body via JMAP)"
docker exec -i twaky-pg psql -U twaky -d twaky <<SQL
LOAD 'age';
SET search_path = ag_catalog, "\$user", public;
SELECT * FROM cypher('twake', \$CQR\$
    MERGE (m1:Email {message_id: "demo-msg-1"})
      SET m1.user = "michel.maudet@linagora.com",
          m1.mailbox_path = "#private/michel.maudet@linagora.com/INBOX",
          m1.received_at = "$(date -u -Iseconds)",
          m1.deleted = false,
          m1.read = false
    MERGE (m2:Email {message_id: "demo-msg-2"})
      SET m2.user = "michel.maudet@linagora.com",
          m2.mailbox_path = "#private/michel.maudet@linagora.com/INBOX",
          m2.received_at = "$(date -u -Iseconds)",
          m2.deleted = false,
          m2.read = false
    RETURN 1
\$CQR\$) AS (v agtype);
SQL
ok "email metadata seeded"

echo ""
ok "seed complete — mission demos ready to declare"
echo "  twaky mission declare 'Résume ma journée de demain' --wait"
echo "  twaky mission declare 'Draft a reply to demo-msg-1' --wait"
