#!/usr/bin/env bash
# Idempotent Metabase bootstrap:
#   1. First-run wizard (Twaky admin account) — skipped if already done
#   2. Add datasources: ClickHouse (Langfuse) + Postgres (twaky graph)
#   3. Enable LDAP against the platform's `ldap` container
#   4. Pre-provision `mmaudet` in Metabase as admin, so their first LDAP
#      sign-in inherits admin rights via email matching.
#
# Everything runs against the Metabase HTTP API from inside twake-network
# via a one-shot curlimages/curl container (no host port needed).
#
# Reads secrets from twaky/.env and the platform deploy .env. Safe to run
# multiple times — each step checks state before mutating.

set -euo pipefail

TWAKY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY_ENV="/home/mmaudet/deploy/kickstart-maudet-cloud/.env"

# shellcheck disable=SC1090
source "${TWAKY_DIR}/.env"

# LemonLDAP-NG uses the same LDAP; grab base DN + admin creds from the
# deploy .env (we don't duplicate them in twaky/.env).
LDAP_BASE_DN=$(grep '^LDAP_BASE_DN=' "$DEPLOY_ENV" | cut -d= -f2-)
LDAP_BIND_DN="cn=admin,${LDAP_BASE_DN}"
LDAP_BIND_PW=$(docker inspect ldap --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | grep '^LDAP_ADMIN_PASSWORD=' | cut -d= -f2-)
LDAP_USER_BASE="ou=users,${LDAP_BASE_DN}"

MB="http://twaky-metabase:3000"
SITE_URL="https://metabase.${BASE_DOMAIN:-twake-dev.maudet.cloud}"

MB_ADMIN_EMAIL="admin@twake-dev.maudet.cloud"
MB_ADMIN_PW_FILE="${TWAKY_DIR}/.metabase-admin.password"

# Persist the admin password on first setup so re-runs re-authenticate.
if [ ! -f "$MB_ADMIN_PW_FILE" ]; then
    openssl rand -base64 18 | tr -d '/+=' | head -c 20 > "$MB_ADMIN_PW_FILE"
    chmod 600 "$MB_ADMIN_PW_FILE"
fi
MB_ADMIN_PW=$(cat "$MB_ADMIN_PW_FILE")

# One-shot curl wrapper: returns http body. We use `docker run -i` and stream
# any request body via stdin (curl --data-binary @-) since process substitution
# doesn't cross the container boundary.
curl_() {
    docker run --rm --network twake-network curlimages/curl:8.10.1 -sS "$@"
}
# POST/PUT JSON: pipe the body to stdin, invoke curl with --data-binary @-.
curl_json() {
    local method="$1"; local url="$2"; shift 2
    docker run --rm -i --network twake-network curlimages/curl:8.10.1 -sS \
        -X "$method" -H "Content-Type: application/json" "$@" \
        --data-binary @- "$url"
}

info() { echo -e "\033[1;34m··\033[0m $*"; }
ok()   { echo -e "\033[0;32m✔\033[0m $*"; }

# ─── 1. First-run wizard ────────────────────────────────────────────────
info "checking whether Metabase is already set up..."
PROPS=$(curl_ "${MB}/api/session/properties")
SETUP_DONE=$(echo "$PROPS" | python3 -c "import json,sys; print(json.load(sys.stdin).get('has-user-setup', False))")
if [ "$SETUP_DONE" != "True" ]; then
    SETUP_TOKEN=$(echo "$PROPS" | python3 -c "import json,sys; print(json.load(sys.stdin).get('setup-token', ''))")
    info "running first-run setup wizard..."
    python3 -c "
import json
print(json.dumps({
    'token': '${SETUP_TOKEN}',
    'prefs': {'site_name': 'Twaky', 'site_locale': 'fr', 'allow_tracking': False},
    'user': {
        'email': '${MB_ADMIN_EMAIL}',
        'first_name': 'Admin', 'last_name': 'Twaky',
        'password': '${MB_ADMIN_PW}', 'site_name': 'Twaky'
    },
    'database': None
}))
" | curl_json POST "${MB}/api/setup" >/dev/null
    ok "wizard done — admin=${MB_ADMIN_EMAIL} (password in ${MB_ADMIN_PW_FILE#$TWAKY_DIR/})"
else
    ok "setup already done — reauthenticating"
fi

# ─── 2. Session for API calls ───────────────────────────────────────────
info "opening admin session..."
SESSION_ID=$(python3 -c "import json;print(json.dumps({'username':'${MB_ADMIN_EMAIL}','password':'${MB_ADMIN_PW}'}))" \
    | curl_json POST "${MB}/api/session" \
    | python3 -c "import json,sys; print(json.load(sys.stdin).get('id', ''))")
if [ -z "$SESSION_ID" ]; then
    echo "❌ failed to authenticate as ${MB_ADMIN_EMAIL} — did the password change?"; exit 1
fi
AUTH="-H X-Metabase-Session:${SESSION_ID}"
ok "session ok"

put_setting() {
    local key="$1"; local value="$2"
    echo "{\"value\":${value}}" | curl_json PUT "${MB}/api/setting/${key}" ${AUTH} >/dev/null
}

# ─── 3. Datasources (idempotent — skip if name already exists) ──────────
add_db_if_missing() {
    local name="$1"; local body="$2"
    local exists
    exists=$(curl_ ${AUTH} "${MB}/api/database" | N="$name" python3 -c "
import json, sys, os
d = json.load(sys.stdin)
dbs = d['data'] if isinstance(d, dict) and 'data' in d else d
print(any(isinstance(x, dict) and x.get('name') == os.environ['N'] for x in dbs))
")
    if [ "$exists" = "True" ]; then
        ok "datasource '$name' already present"
    else
        info "creating datasource '$name'..."
        echo "$body" | curl_json POST "${MB}/api/database" ${AUTH} >/dev/null
        ok "created datasource '$name'"
    fi
}

add_db_if_missing "Langfuse (ClickHouse)" "$(cat <<JSON
{
  "name": "Langfuse (ClickHouse)",
  "engine": "clickhouse",
  "details": {
    "host": "twaky-clickhouse", "port": 8123,
    "user": "default", "password": "${CLICKHOUSE_PASSWORD}",
    "dbname": "default", "scan-all-databases": true,
    "ssl": false
  }
}
JSON
)"

add_db_if_missing "Twaky graph (Postgres+AGE)" "$(cat <<JSON
{
  "name": "Twaky graph (Postgres+AGE)",
  "engine": "postgres",
  "details": {
    "host": "twaky-pg", "port": 5432,
    "user": "${TWAKY_PG_USER:-twaky}", "password": "${TWAKY_PG_PASSWORD}",
    "dbname": "${TWAKY_PG_DB:-twaky}",
    "ssl": false
  }
}
JSON
)"

# ─── 4. LDAP settings ───────────────────────────────────────────────────
info "configuring LDAP → twake-dev LDAP container..."
put_setting ldap-host           "\"ldap\""
put_setting ldap-port           "\"389\""
put_setting ldap-security       "\"none\""
put_setting ldap-bind-dn        "\"${LDAP_BIND_DN}\""
put_setting ldap-password       "\"${LDAP_BIND_PW}\""
put_setting ldap-user-base      "\"${LDAP_USER_BASE}\""
put_setting ldap-user-filter    "\"(&(objectClass=inetOrgPerson)(|(uid={login})(mail={login})))\""
put_setting ldap-attribute-email     "\"mail\""
put_setting ldap-attribute-firstname "\"cn\""
put_setting ldap-attribute-lastname  "\"sn\""
put_setting ldap-enabled        "true"
put_setting site-url            "\"${SITE_URL}\""
put_setting anon-tracking-enabled "false"
ok "LDAP enabled — users can now sign in with LDAP uid / password"

# ─── 5. Pre-provision mmaudet as admin ──────────────────────────────────
info "checking whether mmaudet is a Metabase user..."
EXISTS=$(curl_ ${AUTH} "${MB}/api/user?query=michel.maudet" \
    | python3 -c "
import json, sys
d = json.load(sys.stdin)
users = d['data'] if isinstance(d, dict) and 'data' in d else d
print(any(isinstance(u, dict) and u.get('email','').startswith('michel.maudet') for u in users))
")
if [ "$EXISTS" != "True" ]; then
    info "creating mmaudet in Metabase (member of All Users + Administrators)..."
    echo '{
      "email": "michel.maudet@linagora.com",
      "first_name": "Michel", "last_name": "Maudet",
      "user_group_memberships": [{"id": 1}, {"id": 2}]
    }' | curl_json POST "${MB}/api/user" ${AUTH} >/dev/null
    ok "mmaudet provisioned as admin"
else
    ok "mmaudet already exists (admin flag not re-touched — set manually if needed)"
fi

echo
echo -e "\033[0;32m═════════════════════════════════════════════════════════════\033[0m"
ok "Metabase ready at ${SITE_URL}"
echo -e "  Sign in with your LDAP creds (uid=\033[1;33mmmaudet\033[0m, LDAP password)"
echo -e "  Local admin (fallback): ${MB_ADMIN_EMAIL} — password in \033[1;33m${MB_ADMIN_PW_FILE#$TWAKY_DIR/}\033[0m"
echo -e "\033[0;32m═════════════════════════════════════════════════════════════\033[0m"
