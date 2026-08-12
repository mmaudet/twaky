# Mail sentinel rules cookbook

Practical recipes for the `mail_sentinel_rule` table. Rules run
BEFORE the LLM pipeline and short-circuit it — a matching rule ends
processing with the listed `actions` applied.

## Rule shape

Each row in `mail_sentinel_rule` carries:

- `name` — unique slug; used by the CLI (`rules toggle <name>`).
- `enabled` — boolean gate. Disable a rule without deleting it.
- `priority` — integer, lower runs first. Ties broken by `name`.
- `condition` — JSON object; supported operators today:
  `from_contains`, `subject_contains`, `list_id_contains`,
  `header_matches` (`{name, regex}`), `all`, `any`.
- `actions` — list of strings. Recognized:
  `archive`, `mark_read`, `label:<slug>`, `notify:<channel>`.

Rules that only `label:` are safe to layer — the mail stays in INBOX
and downstream rules still run (unless one archives). Rules that
`archive` are terminal for the pipeline.

## Recipes

### 1. GitHub notifications → label, keep in INBOX

Priority 45 (fires before the LLM). Label only, no archive: keeps
the notification visible so the operator can triage the noisy PRs
themselves. Seeded by `sql/012_seed_starter_rules.sh`.

```sql
INSERT INTO mail_sentinel_rule (name, enabled, priority, condition, actions)
VALUES (
  'github_notifications',
  true,
  45,
  '{"from_contains": "notifications@github.com"}',
  '["label:github"]'
);
```

### 2. Newsletter with `List-Unsubscribe` → archive + label

Marketing newsletters that carry the RFC 2369 unsubscribe header
are safe to archive automatically — the pipeline's `newsletter`
bucket catches most of them, but a rule is cheaper (no LLM call).

```sql
INSERT INTO mail_sentinel_rule (name, enabled, priority, condition, actions)
VALUES (
  'newsletter_unsub',
  true,
  30,
  '{"header_matches": {"name": "List-Unsubscribe", "regex": ".+"}}',
  '["archive", "label:newsletter"]'
);
```

### 3. Internal ops mail (from your CI) → mark_read, no label

CI emails you never read but occasionally grep in the archive.
Do not label — grepping on subject is enough.

```sql
INSERT INTO mail_sentinel_rule (name, enabled, priority, condition, actions)
VALUES (
  'ci_noise',
  true,
  20,
  '{"all": [
      {"from_contains": "ci@example.com"},
      {"subject_contains": "[BUILD]"}
   ]}',
  '["mark_read"]'
);
```

### 4. VIP sender → notify, never archive

Priority `5` beats every other rule. Never `archive` — the operator
wants to see the mail themselves.

```sql
INSERT INTO mail_sentinel_rule (name, enabled, priority, condition, actions)
VALUES (
  'vip_ceo',
  true,
  5,
  '{"from_contains": "ceo@yourcompany.com"}',
  '["label:vip", "notify:ceo"]'
);
```

## Anti-patterns

- **Do not** duplicate the pipeline's spam classifier with a broad
  regex rule. The classifier has FP protection (invoice / thread /
  DKIM checks); a raw regex on `subject_contains: "unsubscribe"`
  will trash legitimate mail.
- **Do not** use `priority < 10` for anything but VIP short-circuits.
  Low priorities crowd out the seeded ops rules and are hard to
  audit.
- **Do not** add `archive` to a `label:` rule "just in case". Archive
  is terminal — later rules never fire.

## Inspecting live state

```bash
uv run twaky mail-sentinel rules list
uv run twaky mail-sentinel rules list --enabled-only
uv run twaky mail-sentinel rules toggle <name>
```

Rules changes go live immediately — no daemon restart. The pipeline
reads rules fresh on every event via `rules_store.list_all()`.
