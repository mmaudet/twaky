#!/bin/bash
# Seed idempotent starter mail-sentinel rules (SP6c UAT follow-up).
#
# What & why
# ----------
# The mail sentinel Rules table is empty by design: rules are per-owner
# preferences. But some starter rules dramatically improve first-run UX
# for owners with typical mixed inboxes (aliases, GitHub notifications,
# newsletters). This script upserts a minimal starter kit that:
#
# - GitHub notifications for owner's own projects should stay in INBOX
#   (they're work signal, not noise) — so we add a dedicated
#   ``github_notifications`` rule at priority 45 (before the generic
#   ``newsletter``/``notification`` rules at 100+) that just labels
#   without archiving.
#
# All INSERTs use ``ON CONFLICT (name) DO NOTHING`` so this script is
# safe to re-run and safe to run against a fresh volume that already
# has rules seeded by prior sessions.
#
# How to apply
# ------------
# First-boot: dropped into the twaky-pg image's
# /docker-entrypoint-initdb.d/ and runs automatically.
# Existing volumes:
#   docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/012_seed_starter_rules.sh
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<-'EOSQL'

    -- =========================================================
    -- SP6c UAT: starter rule github_notifications
    -- =========================================================
    -- Priority 45: after the aliases (40-44), before the generic
    -- Inbox-Zero starter rules (90-200). GitHub notifications for the
    -- owner's own projects (linagora/twaky, tmail-flutter, etc.) are
    -- signal, not noise — label them but keep them in INBOX so they
    -- surface at review time. Static condition (no LLM cost).

    INSERT INTO public.mail_sentinel_rule
        (name, description, conditions, combinator, actions, priority, enabled, run_on_threads)
    VALUES (
        'github_notifications',
        'GitHub notifications from notifications@github.com — PR review requests, issue comments, workflow failures on user own projects. Label as github + keep in INBOX (work signal). Static match: no LLM cost.',
        '[{"field":"from","operator":"contains","value":"notifications@github.com"}]'::jsonb,
        'OR',
        '["label:github"]'::jsonb,
        45,
        TRUE,
        TRUE
    )
    ON CONFLICT (name) DO NOTHING;

EOSQL

echo "sql/012_seed_starter_rules.sh: done"
