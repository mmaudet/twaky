# Morning Report — Night of 2026-08-13 → 2026-08-14

Autonomous night session summary for Michel.

---

## TL;DR

1. **SP5b write-side learning** — 15/15 tasks complete via subagent-driven-development. PR #18 open on `mmaudet/twaky`. Ships as scaffolding with `MAIL_SENTINEL_OBSERVER_ENABLED=False` (default). 3 architectural fixes needed before flipping the flag in prod.
2. **tmail-flutter labels coexistence** — feasibility study revised (JMAP standard + proprietary) + design doc for 7 PRs + PR-A shipped as PR #1 on `mmaudet/tmail-flutter`.

**Nothing deployed in prod. Both branches ready for morning review.**

---

## Workstream 1 — SP5b write-side learning (Twaky)

**Branch**: `sp5b-write-side`
**PR**: https://github.com/mmaudet/twaky/pull/18
**Commits**: 21 (see `git log main..sp5b-write-side`)
**Plan**: `docs/superpowers/plans/2026-08-13-sp5b-write-side-learning.md`

### What shipped

- Config + feature flag (`MAIL_SENTINEL_OBSERVER_ENABLED=False` default, `MAIL_SENTINEL_WATCHED_MAILBOX_ROLES="sent,junk,trash"`).
- SQL migration `sql/012_init_write_side.sh` + downgrade companion `sql/012_downgrade_write_side.sh`. Migration applied to twaky-pg dev.
- 3 new stores : `mailbox_state` (delta polling), `observations` (idempotent audit), extended `memories` (source / touch / list_for_prompt / set_persist / delete).
- 3 extractors : `reclassification` (deterministic), `folder_move` (LLM guard), `draft_diff` (LLM-based, mission-matched).
- 2 LLM prompts + 3 Pydantic schemas (COMPACT hardening, `EXTRACT_MEMORY_DIFF→Tier.CHAT`, `EXTRACT_MEMORY_MOVE→Tier.ECONOMY`).
- `MailObserver` + `JmapObserverClient` (async, standalone from sync JmapMailAdapter). Wired into `_poll_once` behind the flag.
- Node modifications : `select_memories` uses ranked SQL + `touch()`, `match_rules` adds 3 pattern-based short-circuit branches.
- 3 REST endpoints : `PATCH /memories/{id}` (persist toggle), `GET /observations`, `DELETE /memories/{id}` (gap-fill for "Forget" button).
- Frontend : `MemoryCard` component with source badge + Forget + Keep permanent, LearnedPattern type badges (🏷️/✅/🚫), Observations sub-tab in Runs, Memories tab filters.
- 3 YAML eval fixtures (scaffolding — no runner exists yet).
- Rollout playbook : `docs/superpowers/investigations/2026-08-14-sp5b-rollout-playbook.md`.

### Test coverage

- 4/4 mailbox_state store, 4/4 observations store, 15/15 memories extended, 5/5 reclassification, 4/4 folder_move, 7/7 draft_diff, 4/4 observer dispatch, 12/12 JmapObserverClient, 3/3 write-side integration.
- 158/158 frontend vitest incl. 5/5 MemoryCard.
- 44/44 mail-sentinel API + 7/7 write-side endpoints + gap-fill DELETE tests.

### 3 ship blockers surfaced by final review (deferred for daylight)

**These 3 gaps prevent flipping the flag in production. Documented in the plan's "Post-implementation status" + rollout playbook prereq warning.**

1. **Pipeline never routes on match_rules' learned-pattern short-circuits**. `match_rules` emits `skip_spam_triage=True`, `bucket="spam"`, `rule_name="label:X"|"trust_sender"|"block_sender"`, but `pipeline.py` doesn't act on them and `rules_store.by_name("label:X")` returns None. **Goals #2 (auto-label sender→folder) and #3 (trust_sender/block_sender) silent-dead**. Fix : add a `_route_after_match` hop in `pipeline.py` that skips spam_triage on `skip_spam_triage=True` and short-circuits to `apply_actions` for the 3 synthetic rule names (needs a synthetic rules resolver or a new pipeline path).

2. **Observer never emits `unmarked_spam`**. Acknowledged in `observer.py:211-214`. When user restores a mail from Spam to INBOX, no observation is created → trust_sender pattern never accumulates. Fix : track per-email prior mailbox (either via `mail_sentinel_spam_decision` join or a new lightweight cache).

3. **`Email/changes` is global not per-mailbox**. Every watched mailbox in a tick fetches the same delta list; dispatch is by *polled mailbox's role*, not by "email transitioned into/out of this mailbox". False dispatches produce spurious observations. Fix : filter dispatched emails to those whose `mailboxIds` includes the polled mailbox, OR switch to per-mailbox `Email/query` deltas.

### Deferred minors (post-merge)

See ledger `.superpowers/sdd/2026-08-13-sp5b-write-side-learning/progress.md` for full list. Summary : docstring updates, `candidate_pool` NULL handling, Levenshtein truncation, sender_email misnomer rename, case-sensitive folder name check, full-table scan for history count, 30s hardcoded timeout, cleanup fixture inconsistency, subject param unused.

### Recommended morning actions

1. Review PR #18. Merge if the "scaffolding + flag OFF" model is acceptable.
2. If yes, deploy on athena with flag OFF, verify no ingest regression for 30 min via `SELECT count(*), max(started_at) FROM sentinel_run WHERE sentinel_name='mail'`.
3. Plan SP5c to fix the 3 architectural blockers before enabling the flag.

---

## Workstream 2 — tmail-flutter labels coexistence

**Branch (fork)**: `feat/labels-origin-enum` on `mmaudet/tmail-flutter`
**PR**: https://github.com/mmaudet/tmail-flutter/pull/1 (base = `feat/jmap-standard-keywords`, i.e. stacked on the still-in-review PR #4756 upstream)
**Design doc**: `/home/mmaudet/work/tmail-flutter/docs/dev/labels-gmail-coexistence-design.md`
**Work log**: `docs/tmail-flutter-labels-work-log.md`
**Study**: `docs/tmail-flutter-labels-gmail-study.md` (revised after your feedback — v2 addresses coexistence properly)

### What shipped

- **Study v2** : coexistence JMAP standard keywords (RFC 8621) × proprietary Linagora Labels. 17-18 jours estimés (contre 8-12 pour la v1 qui ignorait le sujet).
- **Design doc** : 7 PRs (PR-A à PR-G) avec fichiers exacts, effort, dépendances, ordre de merge, tests, risques upstream.
- **PR-A shipped** : `LabelOrigin { system, user, orphan }` enum + `systemKeywordDisplayNames` dictionary (9 RFC 8621 keywords → display names). 29 nouveaux tests verts, 0 régression. Commit `eeac9f647`.

### Note infra

Flutter SDK absent de athena. L'agent a utilisé `docker run ghcr.io/instrumentisto/flutter:3.38.9` — transparent. Tu peux `docker run` la même image pour rejouer les tests localement.

### Ce qui reste (6 PRs)

- **PR-B** : chips orphan/system cliquables (2 j) — risque : `PresentationLabelMailbox.initial(label)` requiert `label.id!` non nul → workaround = filtre `hasKeyword` direct sans passer par PresentationLabelMailbox
- **PR-C** : `AddLabelToEmailModal` section system keywords + user labels (3 j)
- **PR-D** : page `AccountMenuItem.labelSettings` avec 2 sections (4 j)
- **PR-E** : filtrage par system keyword dans dropdown avancé (2 j)
- **PR-F** : syntaxe textuelle `label:<name>` avec alias system→keyword (3 j)
- **PR-G** : tests + stabilisation + intégration (2-3 j)

### Recommandation

Reviewer la PR-A d'abord (petite, isolée), puis décider si tu veux enchaîner les 6 restantes ou t'arrêter là pour laisser le temps à Linagora de reviewer les 2 PRs upstream (#4756 + PR-A stackée).

---

## Housekeeping

- Ledger source of truth : `.superpowers/sdd/2026-08-13-sp5b-write-side-learning/progress.md`
- All commits use `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.
- No `.env` committed. No `--no-verify`, `--force`, `reset --hard`.
- Feature flag `MAIL_SENTINEL_OBSERVER_ENABLED` remains `False` in production `.env` (never touched).
- Branch `sp5b-write-side` pushed to `origin` (your fork).

---

Bonne matinée ! 🌅

*Toutes les décisions autonomes prises cette nuit sont documentées dans les rapports individuels de chaque task + le ledger. Rien de destructif exécuté sans idempotence garantie.*
