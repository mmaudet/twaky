# SP5c — Observer + Pipeline Fixes (Design)

**Date** : 2026-08-14
**Status** : Draft
**Author** : Michel-Marie Maudet + Claude
**Sub-project** : SP5c (fix the 3 architectural blockers surfaced by SP5b final review)

## 1. Problem

The final whole-branch review of SP5b (branch `sp5b-write-side`, merged as PR #18) surfaced 3 architectural gaps that prevent flipping `MAIL_SENTINEL_OBSERVER_ENABLED=true` in production. This spec closes those gaps so the flag can be safely activated.

## 2. Goal

After SP5c ships :

1. When an active learned pattern (`label:X`, `trust_sender`, `block_sender`) matches an inbound mail's sender, the pipeline actually short-circuits — no LLM call, no spam_triage on trusted senders, and the corresponding action (label / spam / draft) is applied.
2. When the user restores a mail from Spam to Inbox, an `unmarked_spam` observation is emitted, the `trust_sender` pattern accumulates, and after 3 consistent restores the pattern activates.
3. Each observer tick calls `Email/changes` **once** globally and dispatches each changed email based on its current `mailboxIds` — no more per-mailbox loop that produces false dispatches.

Feature flag stays `MAIL_SENTINEL_OBSERVER_ENABLED=False` by default until this ships, then can be flipped to True on athena.

## 3. Non-goals

- No new stores or migrations. Reuses `mail_sentinel_mailbox_state`, `mail_sentinel_observation`, `mail_sentinel_spam_decision`.
- No frontend changes.
- No API changes.
- No rewrite of the extractors (Tasks 7-9 of SP5b) — they stay identical.

## 4. Fix A — Pipeline routing for learned patterns

**Current pipeline** (`src/twaky/sentinels/mail/pipeline.py`) :
```
load_thread → spam_triage → match_rules → apply_actions
                                         ↓
                                    learn_pattern (if matched_by="ai")
```

**Problem** : `match_rules` emits `matched_by="learned_pattern"` + `rule_name="label:X"|"trust_sender"|"block_sender"` + `skip_spam_triage`, but :
- `spam_triage` has already run.
- `apply_actions` calls `rules_store.by_name("label:X")` which returns `None` → empty actions applied → silent-dead.

**Fix** :
1. **Reorder** : put `match_rules` BEFORE `spam_triage`. New order :
   ```
   load_thread → match_rules → [route: skip_spam_triage?] → spam_triage OR direct → apply_actions
   ```
2. **New conditional edge** `_route_after_match_rules` :
   - `matched_by == "learned_pattern"` AND `rule_name in ("trust_sender",)` → skip `spam_triage`, go straight to `apply_actions`.
   - `matched_by == "learned_pattern"` AND `rule_name == "block_sender"` → skip `spam_triage`, `apply_actions` will emit spam bucket.
   - `matched_by == "learned_pattern"` AND `rule_name.startswith("label:")` → skip `spam_triage`, `apply_actions` will apply the label.
   - Otherwise → `spam_triage` (existing path).
3. **Synthesize actions in `apply_actions`** — before calling `rules_store.by_name`, check for synthetic rule names :
   - `rule_name.startswith("label:")` → extract label name, call `ctx.mail.label(email_id, label_name)`, record `actions_applied=[rule_name]`.
   - `rule_name == "trust_sender"` → no-op action (letting the mail through is the "action"), record `actions_applied=["trust_sender"]`.
   - `rule_name == "block_sender"` → move to junk mailbox (via `ctx.mail.set_keyword(email_id, "$junk", True)` or dedicated method), record `actions_applied=["block_sender"]`.
   - Otherwise → existing `rules_store.by_name` path.

**Semantic note** : `trust_sender` and `block_sender` are meta-rules; they don't have user-visible "actions" to configure. The synthetic action list is one entry for auditability in `sentinel_run.trace`.

## 5. Fix B — `unmarked_spam` detection

**Current** : observer only detects `marked_spam` (mail arrives in `role=junk`). Never emits `unmarked_spam` (mail moves OUT of junk).

**Fix** : ride on Fix C. Once we dispatch by current `mailboxIds` (not by the polled mailbox's role), we naturally see when a mail's mailboxIds no longer includes the junk mailbox. The signal to emit `unmarked_spam` :
- Email is in current `mailboxIds` an INBOX mailbox (or any non-junk mailbox).
- AND either :
  - A `mail_sentinel_spam_decision` row exists with `email_id = this_email_id AND restored_at IS NULL` (Twaky itself had flagged as spam), OR
  - A `mail_sentinel_observation` row exists with `email_id = this_email_id AND observation_type = 'marked_spam'` (we had observed a marking).

For simplicity in the MVP, use the first condition alone (spam_decision row). The observation-history join can come as a follow-up if needed.

**Path** : the observer, when it detects `unmarked_spam`, calls `extract_reclassification(email_id, mailbox_id=<current inbox mailbox>, sender_email, direction="out")`. That extractor already knows how to handle direction="out" (writes trust_sender pattern + restores the spam_decision).

## 6. Fix C — Single global `Email/changes` per tick

**Current** (`observer.py:MailObserver.run_tick`) : loops over N watched mailboxes, calls `changes(mailbox_id, since_state)` N times. But `Email/changes` is a global mail-collection delta — every call returns the same list. Dispatch is then based on the polling mailbox's role, causing false dispatches for the same email across multiple mailboxes.

**Fix** :
1. **New table state** : instead of `mail_sentinel_mailbox_state (mailbox_id, jmap_state)`, add a single global row `mail_sentinel_observer_state (owner_email PRIMARY KEY, jmap_state)`. Keep the existing per-mailbox table for backward compat but stop writing to it in the new path.

   Actually simpler : add a new row in the existing `mail_sentinel_mailbox_state` with `mailbox_id = "__global__"` — that's a magic key. No schema change needed. The existing rows for per-mailbox tracking become deprecated but harmless.

2. **New observer flow** :
   ```
   run_tick:
     state = mailbox_state.get("__global__")
     if state is None:
       bootstrap: get current global state via a query, store, return
     changes = adapter.changes(since_state=state.jmap_state)  # global, no mailbox_id
     for email_id in changes.created ∪ changes.updated:
       email = adapter.get_email(email_id)
       dispatch(email)   # ← based on email.mailboxIds
     mailbox_state.upsert("__global__", new_state)
     + style analysis check (unchanged from SP7)
   ```

3. **New dispatch logic** :
   ```
   dispatch(email):
     mailboxIds = email.mailboxIds
     current_roles = {resolve_role(mid) for mid in mailboxIds}

     # Sent → draft_diff (unchanged)
     if "sent" in current_roles: return extract_draft_diff(...)

     # Currently in junk → marked_spam (matches SP5b behaviour)
     if "junk" in current_roles: return extract_reclassification(direction="in")

     # Currently in inbox AND had a non-restored spam_decision → unmarked_spam (NEW, Fix B)
     if "inbox" in current_roles:
       has_open_spam_decision = query mail_sentinel_spam_decision
         WHERE email_id = X AND restored_at IS NULL LIMIT 1
       if has_open_spam_decision:
         return extract_reclassification(direction="out")

     # Currently in a custom folder → moved_to_custom
     for mid in mailboxIds:
       role = resolve_role(mid)
       name = resolve_name(mid)
       if role is None and name not in SYSTEM_FOLDERS:
         return extract_folder_move(...)
   ```

4. **Adapter change** : `JmapObserverClient.changes` signature becomes `changes(since_state: str) -> dict` (drop the unused `mailbox_id` param — was already ignored per SP5b review). Add a `resolve_mailboxes()` helper that returns `{id: (role, name)}` dict, cached per tick.

**Semantic note** : this removes the per-mailbox `mail_sentinel_mailbox_state` maintenance. The old rows can be manually deleted post-deploy or left to be garbage-collected later. The row for `__global__` is the only one written by the new path.

## 7. File changes summary

- Modified :
  - `src/twaky/sentinels/mail/pipeline.py` — reorder + new conditional edge
  - `src/twaky/sentinels/mail/nodes.py` — `make_apply_actions` synthesizes actions for learned patterns
  - `src/twaky/sentinels/mail/observer.py` — global tick + new dispatch by mailboxIds
  - `src/twaky/sentinels/mail/jmap_observer_client.py` — `changes(since_state)` signature
- New :
  - `tests/sentinels/mail/test_pipeline_learned_pattern_routing.py` — 3 tests (label:X, trust_sender, block_sender)
  - `tests/sentinels/mail/test_apply_actions_synthetic.py` — 3 tests
  - `tests/sentinels/mail/test_observer_global_tick.py` — 5 tests (bootstrap, global delta, mailboxIds dispatch × 4 branches)

## 8. Testing strategy

- Reuse existing extractor tests (Tasks 7-9 from SP5b) — no changes there.
- New unit tests for `apply_actions` synthetic path (mock `ctx.mail`).
- New unit tests for `_route_after_match_rules` conditional edge (state-driven, no LLM).
- New integration test : full observer tick with a FakeAdapter that returns 3 emails (one in Sent, one restored from Junk to Inbox with a pre-existing spam_decision row, one in custom folder). Verifies each is dispatched correctly.

## 9. Rollout

- Feature flag `MAIL_SENTINEL_OBSERVER_ENABLED` remains False by default.
- Deploy on athena with flag off, verify no regression (30 min).
- Flip flag on for 48h monitoring per rollout playbook (`docs/superpowers/investigations/2026-08-14-sp5b-rollout-playbook.md`).
- Success criteria :
  - `SELECT count(*) FROM mail_sentinel_learned_pattern WHERE confidence >= 0.9 AND evidence_count >= 3` > 0 after 48h.
  - At least one mail from an active `label:X` pattern sender is auto-labeled without LLM (verifiable in `sentinel_run.trace.llm_calls = 0`).

## 10. Effort

- Fix A (pipeline + apply_actions) : ~2h
- Fix B (unmarked_spam) : ~1h (piggybacks on Fix C)
- Fix C (global tick + dispatch by mailboxIds) : ~2h
- Tests : ~2h
- **Total : ~7h**
