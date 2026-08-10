# SP6c — Early Spam Filter (rspamd-first + 3-bucket triage)

**Status**: design accepted 2026-08-10. Follow-up to SP6 (`docs/superpowers/specs/2026-08-10-sentinels-design.md`, mail-sentinel 7-node pipeline) and SP6b (`docs/superpowers/specs/2026-08-10-sp6b-jmap-oauth-design.md`, JMAP OAuth). Adds a `spam_triage` stage between `load_thread` and `match_rules` that routes clearly-spam mails to one of three buckets — `spam` (silent archive), `newsletter` (label + continue), `phishing-alert` (silent archive + owner mission) — while leaving unclear mail untouched (bias hard toward false-negatives, ≤0.5% HAM false-positive rate).

## 1. Goal

Insert a **short-circuit stage** at the top of the mail-sentinel pipeline that catches the majority of spam **without** incurring the `thread_status` LLM cost and **without** leaving marketing/phishing noise in the owner's inbox. Cost profile: 0 LLM calls for the ~80% of mail that upstream rspamd already classifies definitively; 1 ECONOMY-tier LLM call (Qwen3-VL local per SP6 T13) for the grey-zone residual; 0 additional infrastructure.

Bias contract: an important email silently archived is far worse than a marketing blast reaching `thread_status`. Any residual doubt → PASS THROUGH.

## 2. In scope

- New pipeline node `spam_triage` between `load_thread` and `match_rules`.
- New table `mail_sentinel_spam_decision` (audit trail + restore state).
- Extension of `sentinel.config_values` (row `mail`) with 5 spam-filter fields.
- New REST subrouter `/mail-sentinel/spam` (list + restore + stats).
- New 6th tab "Recent Spam" on `/sentinels/mail` with toggle + list + restore.
- New LLM UseCase `SPAM_CHECK` mapped to ECONOMY tier.
- New prompt module `prompts/spam_check.py`.
- Migration `sql/011_init_spam_decision.sh` (numbering after SP6b T1 `009`).
- Housekeeping cron extension: purge spam_decision rows per retention.
- Playwright E2E for the Restore flow.
- Eval fixtures for 5 spam-triage scenarios.

## 3. Explicitly out of scope

- Learned-pattern-based sender → spam associations (approach B from SP6c draft §5): deferred to SP6d, needs owner-feedback ingestion pipeline via JMAP `Email/changes` on `keywords.$junk` deltas.
- External reputation lookup via SearXNG (approach D): deferred to SP6d as grey-zone tie-breaker if C+heuristics prove insufficient.
- Owner-facing "mark this as spam" button on missions: deferred to SP6d.
- Per-owner threshold tuning UI: MVP exposes threshold in `config_values` but only via PATCH API (owner tunes by hand if needed).
- Rewriting or replacing the SP6 `match_rules` cascade, `learned_patterns`, or `memories` stores.
- Multi-owner spam policies.
- Handling the outbound direction (only inbound INBOX events, unchanged from SP6).
- Support for JMAP servers that don't expose rspamd verdicts via keywords/headers (this SP6c is tightly coupled to Apache James + rspamd upstream, matching the twake-dev target).

## 4. Q4 empirical finding — signals available on the target infrastructure

A live probe of 4 recent inbox mails on 2026-08-10 (`jmap-new.linagora.com`) revealed the following header/keyword landscape:

| Signal | Availability | Usable for A |
|---|---|---|
| JMAP `keywords.$junk` / `nonjunk` (rspamd verdict as keyword) | present on ALL mails | **primary signal** |
| `dkim-signature` header | 4/4 | strong "sender authenticated" signal |
| `arc-authentication-results` header | 1/4 (Microsoft-relayed only) | partial |
| `list-unsubscribe` + `list-unsubscribe-post` headers | 2/4 (legit newsletters) | strong "newsletter" signal |
| `return-path` header (mismatch vs `From:` = phishing signal) | 4/4 | usable |
| `authentication-results` (RFC 8601 standard) | **0/4** | **not available** |
| `received-spf` | 0/4 | not available |
| `precedence: bulk`/`list` | 0/4 | not available |
| `x-spam-status`/`score`/`level` (SpamAssassin style) | 0/4 | not available |
| `org.apache.james.rspamd.status` / `.flag` (custom James headers) | 4/4 | **rich rspamd verdict** |

**Consequence**: the SP6c design is **rspamd-first**. We consume rspamd's upstream verdict (via keywords + custom headers) as the primary signal, and only fall back to header heuristics + LLM for the residual where rspamd hedged (`add header`, `greylist`, or `no action` results).

## 5. Architecture

### 5.1 Graph shape

```
                        load_thread
                            │
                       spam_triage  ────────┐  (new node — this SP)
                            │                │
                            │        ┌───────┴────────┐
                            │        │                │
                            │   bucket=spam      bucket=phishing-alert
                            │        │                │
                            │   silent_archive   silent_archive
                            │        │                │  + emit notify mission
                            │        │                │       (audit trail)
                            │        │                │
                            │      END               END
                            │
                       ┌────┤ bucket=newsletter
                       │    │        → set label:newsletter
                       │    │        → set nonjunk keyword
                       │    │        → CONTINUE to match_rules (owner may
                       │    │          have rules on label:newsletter)
                       │    │
                       │    │ bucket=none (pass-through)
                       │    ▼
                       │  match_rules ── ... (rest of SP6 pipeline unchanged)
```

Terminal buckets are `spam` and `phishing-alert` (both END the graph after archive). `newsletter` labels and continues so the mail stays in the inbox but is filterable by user rules; `none` passes through with zero side effects.

### 5.2 Signal cascade inside `spam_triage`

The cascade is **first-match-wins** — the first stage that produces a definitive verdict returns immediately. No accumulation.

```
Stage 1 — Trust upstream rspamd (via keywords):
  IF keyword $junk present:
    → bucket=spam, signal=rspamd_junk_keyword
  IF keyword nonjunk present:
    → bucket=none, signal=rspamd_nonjunk_keyword
    (rspamd said HAM; we defer to it; no further checks)

Stage 2 — Trust upstream rspamd (via org.apache.james.rspamd.status header):
  Parse the status header value; look for the "action" component.
  IF action in {"reject", "soft reject"}:
    → bucket=spam, signal=rspamd_status_reject
  IF action == "rewrite subject":
    → bucket=spam, signal=rspamd_status_rewrite
  IF action in {"add header", "greylist"}:
    → grey_zone = true
  IF action == "no action":
    → (continue to stage 3, no grey_zone yet)

Stage 3 — Header heuristics:
  Compute a small integer score from headers:
    +2 if list-unsubscribe AND list-unsubscribe-post → newsletter_signal
    +2 if from address in known-marketing domains (env: MAIL_SENTINEL_MARKETING_DOMAINS)
    +3 if dkim-signature absent
    +3 if return_path.domain != from.domain
    +2 if hasAttachment AND dkim absent  (Q8: phishing signal)
  IF newsletter_signal AND total_score < 5:
    → bucket=newsletter, signal=heuristic_newsletter
  IF total_score >= 4:
    → grey_zone = true (defer to LLM)

Stage 4 — LLM SPAM_CHECK (ECONOMY tier) — ONLY IF grey_zone:
  prompt = spam_check_prompt(email, thread, headers_summary)
  out = structured_call(prompt, SpamCheckOutput,
                        hardening=Hardening.COMPACT,
                        use_case=UseCase.SPAM_CHECK)
  # out = {bucket: "spam"|"newsletter"|"phishing-alert"|"none",
  #        confidence: float, reason: str}
  IF out.bucket in {"spam", "phishing-alert"} AND out.confidence >= threshold (default 0.85):
    → bucket=out.bucket, signal=llm_grey_zone, score=out.confidence
  IF out.bucket == "newsletter" AND out.confidence >= 0.70:
    → bucket=newsletter, signal=llm_grey_zone, score=out.confidence
    (newsletter threshold lower — worst case owner sees the label and can create a rule)
  ELSE:
    → bucket=none (LLM not confident enough; pass through, safer)

Stage 5 — default:
  → bucket=none
```

### 5.3 Action per bucket

| Bucket | Adapter calls | Mission emitted? | Node returns |
|---|---|---|---|
| `spam` | `label(email_id, "__spam__")` + `set_keyword(email_id, "$junk", True)` | no | `{"spam_bucket": "spam", "spam_decision_id": UUID, "actions_applied": ["label:__spam__", "keyword:$junk"]}` (state marker triggers routing to END) |
| `phishing-alert` | same as spam | **yes** — `mission_emitter.emit(intent_text=f"Phishing suspected: {subject}", reason="phishing-alert bucket auto-archived", artifact={kind:"phishing_alert", evidence:{email_id, sender, reason, score, spam_decision_id}, hints:{"body_preview": first_500_chars}})` | same as spam (state marker triggers END routing) |
| `newsletter` | `label(email_id, "newsletter")` + `set_keyword(email_id, "nonjunk", True)` (marks as HAM to upstream so rspamd doesn't re-flag) | no | `{"spam_bucket": "newsletter", "spam_decision_id": UUID}` — pipeline continues to `match_rules` |
| `none` | none | no | `{"spam_bucket": None}` — pipeline continues to `match_rules` |

**Note on "silent archive" wording**: elsewhere in this spec (§2, §5.1 diagram, §7 UI) the phrase "silent_archive" or "auto-archived" describes the *user-visible effect* — the mail is out of the owner's attention. Mechanically the sentinel does NOT call `JmapMailAdapter.archive()` (which would touch `mailboxIds`); it only labels + sets `$junk` keyword. Twake Mail's own filter setup (owner-controlled, outside SP6c scope) can move `$junk`-keyword mails to a Junk folder. This decoupling makes Restore trivial (single `set_keyword` op, §7.2 + §15 constraints).

Every non-`none` bucket writes an `INSERT INTO mail_sentinel_spam_decision` row synchronously via `spam_decisions.insert()`.

### 5.4 Routing edges added to `build_graph`

In `sentinels/mail/pipeline.py`, replace the current `load_thread → match_rules` edge with:

```
load_thread → spam_triage
spam_triage → conditional {
    "END"          if state.get("spam_bucket") in {"spam", "phishing-alert"},
    "match_rules"  otherwise (bucket in {"newsletter", None}),
}
```

Bucket `newsletter` intentionally continues through `match_rules` — owner may define rules that match on `label:newsletter`.

### 5.5 Enabled/disabled gate

The `spam_triage` node body checks `ctx.base.sentinel_row.config_values.get("spam_filter_enabled", False)` FIRST. If `False`, return `{"spam_bucket": None}` immediately — zero cost, pipeline continues normally. This is how the toggle in §7 UI takes effect without redeploying.

Cache invalidation on toggle: SP6 T3's `sentinel_changed` NOTIFY already fires on `sentinel` UPDATE; the registry cache expires; next event reads the new config_values.

## 6. Data model

### 6.1 New table `mail_sentinel_spam_decision`

`sql/011_init_spam_decision.sh` (numbered after SP6b T1's `009`; SP6b's actual filename is `009_init_oauth_credential.sh`, and this SP6c gets `011` to leave `010` unused as a naming-convention buffer that matches the "one migration per sub-project" rhythm).

```sql
CREATE TABLE IF NOT EXISTS public.mail_sentinel_spam_decision (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email_id           TEXT NOT NULL,
    thread_id          TEXT,
    sender_email       TEXT NOT NULL,
    subject            TEXT NOT NULL DEFAULT '',
    received_at        TIMESTAMPTZ NOT NULL,
    bucket             TEXT NOT NULL
                       CHECK (bucket IN ('spam','newsletter','phishing-alert')),
    signal_source      TEXT NOT NULL
                       CHECK (signal_source IN (
                           'rspamd_junk_keyword',
                           'rspamd_nonjunk_pass_through',
                           'rspamd_status_reject',
                           'rspamd_status_rewrite',
                           'heuristic_newsletter',
                           'llm_grey_zone'
                       )),
    score              NUMERIC(4,3),
    reason             TEXT,
    restored_at        TIMESTAMPTZ,
    restored_by        TEXT,
    decided_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS mail_sentinel_spam_decision_by_decided_at
    ON mail_sentinel_spam_decision (decided_at DESC);
CREATE INDEX IF NOT EXISTS mail_sentinel_spam_decision_by_sender
    ON mail_sentinel_spam_decision (sender_email);
CREATE INDEX IF NOT EXISTS mail_sentinel_spam_decision_active
    ON mail_sentinel_spam_decision (decided_at DESC)
    WHERE restored_at IS NULL;
```

No NOTIFY trigger (unlike SP6b) — spam_decision writes are single-writer (sentinel container only) and readers (API) tolerate a few-second cache-miss window without needing invalidation.

Retention (housekeeping cron): DELETE rows where `restored_at IS NULL AND decided_at < now() - INTERVAL '30 days'`, plus DELETE rows where `restored_at IS NOT NULL AND decided_at < now() - INTERVAL '90 days'`.

### 6.2 Config values additions

The `mail` sentinel's `config_values` JSONB (row-level, existing SP6 T1 table `sentinel`) gains 5 new keys, validated via `config_schema` per SP6 T25:

```json
{
  "spam_filter_enabled": false,
  "spam_llm_confidence_threshold": 0.85,
  "spam_llm_newsletter_threshold": 0.70,
  "spam_purge_active_days": 30,
  "spam_purge_restored_days": 90
}
```

The seed migration `sql/008_init_sentinels.sh` seed row is NOT re-migrated (would break existing deployments); instead the sentinel node reads with `.get("spam_filter_enabled", False)`-style defaults, so missing keys behave like disabled. Operator sets them via PATCH `/sentinels/mail` (SP6 T25) or via UI toggle (§7).

The `config_schema` on the seed row gets extended with 5 new properties (JSON Schema), but SP6 T25's PATCH validates dynamically against the DB's `config_schema` column, so this update requires an operator-side `UPDATE sentinel SET config_schema=... WHERE name='mail'` OR the migration `sql/011_...sh` also updates the row's `config_schema` via `jsonb_set()`. The migration does the update — one heredoc updates `config_schema` for the existing `mail` row.

### 6.3 New LLM UseCase + prompt

`sentinels/mail/llm/tiers.py` — extend `UseCase` enum:

```python
class UseCase(str, Enum):
    # ... existing 6
    SPAM_CHECK = "spam_check"

_MAPPING = {
    # ... existing
    UseCase.SPAM_CHECK: Tier.ECONOMY,
}
```

The `SPAM_CHECK` UseCase is mapped to `Tier.ECONOMY` (cheapest, local Qwen3-VL per SP6 T13). Regression test `test_persistent_decisions_are_not_economy` from SP6 T12 does NOT list SPAM_CHECK as persistent (each call is atomic, no learned state; a wrong spam call creates a spam_decision row that the owner can restore — recoverable, unlike a wrong learned_pattern).

`sentinels/mail/schemas.py` — add:

```python
class SpamCheckOutput(BaseModel):
    bucket: Literal["spam", "newsletter", "phishing-alert", "none"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(max_length=400)
```

`sentinels/mail/prompts/spam_check.py` — new prompt module. The prompt tells the LLM to be conservative (bias to `bucket=none` unless clearly one of the other three), summarizes rspamd's grey verdict as context, includes owner_email for personalization ("legitimate mail addressed to <owner_email> personally is very unlikely to be spam"), and asks for `<400 char reason` for the owner-facing "why was this flagged".

## 7. UI

### 7.1 New 6th tab "Recent Spam"

Extends `frontend/src/app/sentinels/mail/page.tsx` — adds `<TabsTrigger value="recent-spam">Recent Spam</TabsTrigger>` + `<TabsContent value="recent-spam"><RecentSpamTab /></TabsContent>` after the SP6b Auth tab.

New client component `frontend/src/app/sentinels/mail/recent-spam-tab.tsx`:

**Header section** (fixed at top of the tab):
- Toggle switch labeled "Spam filter" — reads `sentinel.config_values.spam_filter_enabled` via SP6 T25 `useSentinel("mail")`; writes via SP6 T25 `usePatchSentinel("mail")` with `{config_values: {..., spam_filter_enabled: <new>}}`.
- Stats row: "Last 30 days · N archived · M restored" — from `GET /mail-sentinel/spam/stats?days=30`.
- Optional "Purge settings" line (informational): "Active retention: 30d · Restored retention: 90d" — reads from same sentinel config.

**Table section** (paginated, 50 rows/page):
- Columns: Bucket (icon: 🔴 phishing / 🟠 spam / 📰 newsletter), From, Subject, Received (relative), Signal (rspamd_junk_keyword / llm_grey_zone / …), Score (only if present), Actions (`[Restore]` button if `restored_at is null`, else "Restored on <date>").
- Pagination cursor-based: `before=<iso>` from the last row's `decided_at`, `limit=50`.
- Empty state: "Spam filter is off / Spam filter is on, no decisions yet."

### 7.2 Restore action

Click **[Restore]** → confirm dialog "Restore this email to your inbox? It will be marked as not-spam and re-appear in your inbox." → POST `/mail-sentinel/spam/{id}/restore` → onSuccess invalidates the query.

Backend implementation: `POST /mail-sentinel/spam/{id}/restore`:
1. `spam_decisions.get(id)` → 404 if missing.
2. If `restored_at IS NOT NULL` → 409 `{"code": "already_restored"}`.
3. Call `JmapMailAdapter.set_keywords_bulk(email_id, {"nonjunk": True, "$junk": False, "__spam__": False, "newsletter": False})` — the ONLY JMAP write on the restore path. Idempotent, doesn't touch `mailboxIds` (per §5.3 constraint, the sentinel never called `archive()`, only labeled + keyword-flagged the mail).
4. `UPDATE mail_sentinel_spam_decision SET restored_at=now(), restored_by=<owner_email> WHERE id=%s`.
5. Return 200 with the updated `SpamDecision`.

*Note*: `set_keywords_bulk` is a helper wrapping a single JMAP `Email/set` call with multiple keyword patches — see the JMAP spec adaptation in §9 (adapter's `set_keyword` supports patching a single keyword; the restore path uses a small `_restore_keywords` helper on the adapter that packs the 4 keyword patches into one JMAP request for atomicity).

### 7.3 Hook + tests

`frontend/src/hooks/use-mail-sentinel-spam.ts`:
- `useSpamDecisions({bucket?, limit=50, before?})` — GET, query key `['mail-spam-decisions', filters]`.
- `useSpamStats(days=30)` — GET stats.
- `useRestoreSpam()` — POST restore, invalidates list + stats.

MSW tests + Vitest component tests per SP6b T11 pattern.

## 8. REST API

New subrouter `src/twaky/api/routers/mail_sentinel_spam.py`, prefix `/mail-sentinel/spam`, all endpoints `Depends(require_owner)`:

| Method + Path | Body | Response | Errors |
|---|---|---|---|
| `GET /mail-sentinel/spam?bucket=&limit=50&before=<iso>` | — | `list[SpamDecision]` (limit bounded 1..500) | 401 |
| `POST /mail-sentinel/spam/{id}/restore` | — | `SpamDecision` (updated) | 401, 404 spam_decision_not_found, 409 already_restored, 502 jmap_restore_failed |
| `GET /mail-sentinel/spam/stats?days=30` | — | `SpamStats {spam, newsletter, phishing_alert, restored, total_processed}` | 401 |

Pydantic schemas in `src/twaky/api/schemas/spam.py`.

Follows SP6 T25/T26 error envelope conventions.

## 9. Node signature (Python-level sketch)

`src/twaky/sentinels/mail/nodes.py` — extend with:

```python
from twaky.sentinels.mail.store import spam_decisions
from twaky.sentinels.mail.llm.hardening import Hardening
from twaky.sentinels.mail.llm.invoke import structured_call
from twaky.sentinels.mail.llm.tiers import UseCase
from twaky.sentinels.mail.prompts.spam_check import spam_check_prompt
from twaky.sentinels.mail.schemas import SpamCheckOutput


def make_spam_triage(ctx: NodeContext) -> Callable[[MailAgentState], MailAgentState]:
    def _node(state: MailAgentState) -> MailAgentState:
        cfg = ctx.base.sentinel_row.config_values
        if not cfg.get("spam_filter_enabled", False):
            return {"spam_bucket": None}

        thread = state.get("thread") or []
        if not thread:
            return {"spam_bucket": None}
        latest = thread[-1]

        # Stage 1 — rspamd via keywords
        kw = latest.get("keywords") or {}
        if kw.get("$junk"):
            return _terminate(ctx, latest, bucket="spam",
                              signal="rspamd_junk_keyword", score=None,
                              reason="upstream rspamd marked $junk")
        if kw.get("nonjunk"):
            return {"spam_bucket": None}

        # Stage 2 — rspamd via custom header
        rspamd_action = _parse_rspamd_status(latest.get("headers") or [])
        if rspamd_action in {"reject", "soft reject"}:
            return _terminate(ctx, latest, bucket="spam",
                              signal="rspamd_status_reject", score=None,
                              reason=f"rspamd action={rspamd_action}")
        if rspamd_action == "rewrite subject":
            return _terminate(ctx, latest, bucket="spam",
                              signal="rspamd_status_rewrite", score=None,
                              reason="rspamd action=rewrite subject")
        grey_zone = rspamd_action in {"add header", "greylist"}

        # Stage 3 — heuristics
        h = _header_heuristic_score(latest)
        if h.newsletter_signal and h.total_score < 5:
            return _terminate(ctx, latest, bucket="newsletter",
                              signal="heuristic_newsletter", score=None,
                              reason=f"list-unsubscribe + score={h.total_score}")
        if h.total_score >= 4:
            grey_zone = True

        # Stage 4 — LLM only if grey_zone
        if not grey_zone:
            return {"spam_bucket": None}

        prompt = spam_check_prompt(state, headers_summary=h.summary,
                                   rspamd_action=rspamd_action,
                                   owner_email=ctx.owner_email)
        out = structured_call(prompt, SpamCheckOutput,
                              hardening=Hardening.COMPACT,
                              use_case=UseCase.SPAM_CHECK)

        spam_thresh = float(cfg.get("spam_llm_confidence_threshold", 0.85))
        news_thresh = float(cfg.get("spam_llm_newsletter_threshold", 0.70))
        if out.bucket in {"spam", "phishing-alert"} and out.confidence >= spam_thresh:
            return _terminate(ctx, latest, bucket=out.bucket,
                              signal="llm_grey_zone", score=out.confidence,
                              reason=out.reason)
        if out.bucket == "newsletter" and out.confidence >= news_thresh:
            return _terminate(ctx, latest, bucket="newsletter",
                              signal="llm_grey_zone", score=out.confidence,
                              reason=out.reason)

        return {"spam_bucket": None}

    return _node


def _terminate(ctx, email, *, bucket, signal, score, reason):
    """Apply adapter actions + persist decision + emit mission if phishing-alert.
    Returns state marker triggering END routing (bucket=spam/phishing-alert)
    or newsletter marker (pipeline continues)."""
    email_id = email["id"]
    ctx.mail.label(email_id, "__spam__" if bucket != "newsletter" else "newsletter")
    if bucket in {"spam", "phishing-alert"}:
        ctx.mail.set_keyword(email_id, "$junk", True)   # new adapter method
    if bucket == "newsletter":
        ctx.mail.set_keyword(email_id, "nonjunk", True)

    decision_id = spam_decisions.insert(
        email_id=email_id,
        thread_id=email.get("threadId"),
        sender_email=_sender_email(email),
        subject=email.get("subject", ""),
        received_at=_parse_iso(email.get("receivedAt")),
        bucket=bucket, signal_source=signal, score=score, reason=reason,
    )

    if bucket == "phishing-alert":
        preview = (email.get("preview") or "")[:500]
        ctx.base.mission_emitter.emit(
            intent_text=f"Phishing suspected: {email.get('subject', '(no subject)')}",
            reason="phishing-alert bucket auto-archived by spam_triage",
            artifact={
                "kind": "phishing_alert",
                "evidence": {
                    "email_id": email_id, "sender": _sender_email(email),
                    "reason": reason, "score": score,
                    "spam_decision_id": str(decision_id),
                },
                "hints": {"body_preview": preview},
            },
        )

    return {"spam_bucket": bucket, "spam_decision_id": decision_id,
            "actions_applied": ["label:" + ("__spam__" if bucket != "newsletter" else "newsletter"),
                                "set_keyword"]}
```

### `_parse_rspamd_status` helper

The `org.apache.james.rspamd.status` header value is not standardized; the safest parse is regex-based:

```python
_RSPAMD_ACTION_RE = re.compile(r'action=([\w\s]+?)(?:;|$)', re.IGNORECASE)

def _parse_rspamd_status(headers: list[dict]) -> str | None:
    for h in headers:
        if h.get("name", "").lower() == "org.apache.james.rspamd.status":
            m = _RSPAMD_ACTION_RE.search(h.get("value", ""))
            if m:
                return m.group(1).strip().lower()
    return None
```

### New `MailAdapter.set_keyword` method

`sentinels/mail/adapter.py` — extend the `MailAdapter` Protocol + both `InMemoryMailAdapter` and `JmapMailAdapter`:

```python
def set_keyword(self, email_id: str, keyword: str, value: bool) -> None: ...
```

JMAP impl: `Email/set` with `update={email_id: {f"keywords/{keyword}": value}}`. Restore uses the same op with `nonjunk=True, $junk=False` in a single call: `update={email_id: {"keywords/$junk": False, "keywords/nonjunk": True}}` (JMAP `Email/set` accepts multiple keyword updates in one patch).

## 10. Store module

`src/twaky/sentinels/mail/store/spam_decisions.py` — dataclass + CRUD, style-matching SP6 T14/T15/T16 stores:

```python
@dataclass(frozen=True)
class SpamDecision:
    id: UUID
    email_id: str
    thread_id: str | None
    sender_email: str
    subject: str
    received_at: datetime
    bucket: str
    signal_source: str
    score: Decimal | None
    reason: str | None
    restored_at: datetime | None
    restored_by: str | None
    decided_at: datetime


def insert(*, email_id, thread_id, sender_email, subject, received_at,
           bucket, signal_source, score, reason) -> UUID: ...

def get(decision_id: UUID) -> SpamDecision | None: ...

def list_recent(*, bucket: str | None = None, limit: int = 50,
                before: datetime | None = None) -> list[SpamDecision]: ...

def restore(decision_id: UUID, restored_by: str) -> SpamDecision:
    """Sets restored_at=now(), restored_by=<email>. Raises AlreadyRestored
    if already set; returns the updated row."""

def stats(days: int = 30) -> dict[str, int]:
    """Returns {'spam': N, 'newsletter': N, 'phishing_alert': N,
    'restored': N, 'total_processed': N} over the window."""

def purge_active(older_than_days: int) -> int:
    """DELETE non-restored older than N days. Returns row count."""

def purge_restored(older_than_days: int) -> int:
    """DELETE restored older than N days. Returns row count."""


class SpamDecisionNotFound(Exception): ...
class AlreadyRestored(Exception): ...
```

Housekeeping cron extension in `src/twaky/sentinels/runtime.py::_housekeeping` — one call each per hour:

```python
from twaky.sentinels.mail.store import spam_decisions
active_purged = await asyncio.to_thread(spam_decisions.purge_active, 30)
restored_purged = await asyncio.to_thread(spam_decisions.purge_restored, 90)
```

Retention values are read from the `mail` sentinel's `config_values` if present, else defaults.

## 11. Testing strategy

### 11.1 Unit tests

`tests/sentinels/mail/test_nodes_spam_triage.py` — one file per stage of the cascade:

- `test_disabled_when_config_flag_off` — `spam_filter_enabled=false` → return `{"spam_bucket": None}` immediately, `structured_call` NOT called, `spam_decisions.insert` NOT called.
- `test_stage1_junk_keyword_hard_archive` — email keywords={"$junk": True} → bucket=spam, `label("__spam__")` + `set_keyword("$junk", True)` called on adapter, `spam_decisions.insert` called with signal_source=rspamd_junk_keyword.
- `test_stage1_nonjunk_pass_through` — keywords={"nonjunk": True} → bucket=None, NO adapter side-effects, NO DB write, NO LLM call.
- `test_stage2_rspamd_reject_archives` — `org.apache.james.rspamd.status: default: action=reject; score=15.0` header → bucket=spam.
- `test_stage2_rspamd_greylist_triggers_llm` — `action=greylist` → grey_zone=True, LLM IS called.
- `test_stage3_newsletter_heuristic_labels` — list-unsubscribe present + score < 5 → bucket=newsletter without LLM.
- `test_stage3_dkim_absent_returnpath_mismatch_triggers_grey` — heuristic score >= 4 → LLM called.
- `test_stage4_llm_below_threshold_pass_through` — LLM returns confidence=0.60 → bucket=None (safer).
- `test_stage4_llm_phishing_above_threshold_emits_mission` — LLM returns `{bucket: "phishing-alert", confidence: 0.92}` → adapter labeled + `mission_emitter.emit` called with kind=phishing_alert artifact.
- `test_stage4_llm_newsletter_lower_threshold_accepts` — LLM `{bucket: "newsletter", confidence: 0.75}` (>0.70 threshold) → labels newsletter.
- `test_llm_never_called_when_no_grey_zone` — Stage 1-3 all resolve to non-grey verdict → LLM stays untouched.

### 11.2 Store tests

`tests/sentinels/mail/store/test_spam_decisions.py`:
- Insert + get.
- List pagination + before cursor + bucket filter.
- Restore success + AlreadyRestored on 2nd call + SpamDecisionNotFound.
- Stats aggregation.
- Purge active only touches non-restored.
- Purge restored only touches restored.

### 11.3 API tests

`tests/api/routers/test_mail_sentinel_spam.py`:
- 401 unauthenticated × 3 endpoints.
- GET pagination shape.
- POST restore happy + 409 already + 404 unknown + 502 jmap fail (mock adapter raising).
- GET stats shape.

### 11.4 Eval fixtures

`tests/evals/mail/spam/*.yaml`:
- `phishing_hard_attachment_dkim_none.yaml` — expect `bucket=phishing-alert` via LLM.
- `newsletter_list_unsub.yaml` — expect `bucket=newsletter` via heuristic (no LLM).
- `promo_marketing_greylist.yaml` — grey zone; LLM decides; assert `bucket in {spam, newsletter, none}` (LLM-dependent, but MUST NOT emit phishing-alert).
- `personal_reply_thread.yaml` — thread continuation; expect `bucket=none` always.
- `ham_edge_invoice.yaml` — automated invoice from known domain that could look bulk-ish; expect `bucket=none` (FP protection).

Deterministic LLM fake per SP6 T30 pattern; opt-in live variant via `EVAL_LIVE=1`.

### 11.5 Playwright E2E

`frontend/tests/e2e/sentinels-mail-recent-spam.spec.ts`:
- Navigate to `/sentinels/mail?tab=recent-spam`.
- Toggle "Spam filter" OFF → ON via switch.
- Precondition setup via API: insert a spam_decision row for a fake email.
- Verify row visible in the list.
- Click Restore → confirm dialog → confirm.
- Verify row shows "Restored on <date>" state.

## 12. Observability + metrics

Every `spam_triage` invocation appends to `sentinel_run.trace` (SP6 T2's JSONB column):

```json
{
  "stage": "spam_triage",
  "enabled": true,
  "bucket": "phishing-alert",
  "signal_source": "llm_grey_zone",
  "score": 0.92,
  "spam_decision_id": "…",
  "llm_called": true,
  "llm_tokens": {"in": 340, "out": 42},
  "ms": 380
}
```

Structured logs:
- `spam_triage.disabled` (email_id) — DEBUG.
- `spam_triage.decision` (email_id, bucket, signal_source, score) — INFO.
- `spam_triage.llm_called` (email_id, grey_reason, tokens, ms) — INFO.
- `spam_triage.restore` (spam_decision_id, restored_by) — INFO.

Ops dashboard queries (SQL, run manually or via a future Metabase board):
- Last 7d spam volume by bucket.
- Last 7d LLM-called count vs rspamd-only count (efficiency indicator).
- Last 30d restored rate = restored_count / total_decisions × 100 (running FP proxy).
- Distribution of `score` for `llm_grey_zone` decisions (should skew toward 1.0 as prompt is tuned).

The `GET /mail-sentinel/spam/stats` endpoint (§8) surfaces the top 4 metrics for the tab header.

## 13. Rollout

1. **Prereq**: operator applies migration `sql/011_init_spam_decision.sh` on the live volume (`docker exec twaky-pg bash /docker-entrypoint-initdb.d/011_init_spam_decision.sh`).
2. **Deploy**: PR merged → `docker compose up -d twaky-sentinel twaky-api` — the `spam_triage` node is present in the graph but `spam_filter_enabled=false` so it's a no-op.
3. **First activation**: owner opens `/sentinels/mail#recent-spam`, sees the empty table + Off toggle, flips to On. `sentinel_changed` NOTIFY fires; next inbox event runs `spam_triage`.
4. **Observation window**: owner monitors the Recent Spam tab for ~1 week. If restored rate > 5% (i.e. >5% of decisions were FPs), tune `spam_llm_confidence_threshold` upward via PATCH `/sentinels/mail` `{config_values: {spam_llm_confidence_threshold: 0.90}}`.
5. **SP6d follow-up** if tuning insufficient: implement approach B (learned-pattern-based sender bulk filter) + approach D (SearXNG tie-breaker).

## 14. File impact preview

**Created**
- `sql/011_init_spam_decision.sh` + `tests/sql/test_spam_decision_migration.py`
- `src/twaky/sentinels/mail/store/spam_decisions.py` + `tests/sentinels/mail/store/test_spam_decisions.py`
- `src/twaky/sentinels/mail/prompts/spam_check.py` + `tests/sentinels/mail/prompts/test_spam_check.py`
- `src/twaky/api/routers/mail_sentinel_spam.py` + `src/twaky/api/schemas/spam.py` + `tests/api/routers/test_mail_sentinel_spam.py`
- `frontend/src/hooks/use-mail-sentinel-spam.ts` + `.test.tsx`
- `frontend/src/app/sentinels/mail/recent-spam-tab.tsx` + `.test.tsx`
- 5 YAML fixtures under `tests/evals/mail/spam/`
- 1 Playwright spec `frontend/tests/e2e/sentinels-mail-recent-spam.spec.ts`
- `tests/sentinels/mail/test_nodes_spam_triage.py`

**Modified**
- `src/twaky/sentinels/mail/nodes.py` — new `make_spam_triage`, helpers `_parse_rspamd_status`, `_header_heuristic_score`.
- `src/twaky/sentinels/mail/pipeline.py` — insert node + routing edge.
- `src/twaky/sentinels/mail/state.py` — new state fields `spam_bucket`, `spam_decision_id`.
- `src/twaky/sentinels/mail/schemas.py` — `SpamCheckOutput`.
- `src/twaky/sentinels/mail/llm/tiers.py` — add `UseCase.SPAM_CHECK → Tier.ECONOMY`.
- `src/twaky/sentinels/mail/adapter.py` — new `set_keyword(email_id, keyword, value)` on protocol + both impls.
- `src/twaky/sentinels/runtime.py` — housekeeping calls `spam_decisions.purge_active` + `purge_restored`.
- `src/twaky/api/main.py` — `include_router(mail_sentinel_spam.router)`.
- `frontend/src/app/sentinels/mail/page.tsx` — add 6th tab.
- `docs/api/openapi.yaml` — regenerated.
- `frontend/src/lib/api-types.d.ts` — regenerated.
- `README.md` — new sub-section under Sentinels: "Recent Spam tab + toggle + restore".

## 15. Global constraints (verbatim for the plan)

- **Endpoints**: `/mail-sentinel/spam/*` at API root, no `/api` prefix.
- **Table**: `mail_sentinel_spam_decision` (singular, unquoted).
- **No NOTIFY channel** for spam_decisions — single-writer, readers tolerate few-second staleness.
- **Migration convention**: `sql/011_init_spam_decision.sh` matches SP6 T1 / SP6b T1 template.
- **Signal source enum values** (CHECK constraint): exactly `{'rspamd_junk_keyword','rspamd_nonjunk_pass_through','rspamd_status_reject','rspamd_status_rewrite','heuristic_newsletter','llm_grey_zone'}`.
- **Bucket enum values** (CHECK constraint): exactly `{'spam','newsletter','phishing-alert'}` (hyphen in `phishing-alert` intentional — matches UI label).
- **`spam_filter_enabled` default false** — SP6c ships inactive; owner opts in via UI toggle. Node checks this FIRST before any other work.
- **LLM SPAM_CHECK use case**: mapped to `Tier.ECONOMY`; MUST use `Hardening.COMPACT` (third-party content is instruction-adversarial).
- **Confidence thresholds**: default `spam_llm_confidence_threshold=0.85` (spam/phishing-alert), `spam_llm_newsletter_threshold=0.70` (newsletter). Both exposed in `config_values` for tuning.
- **Retention defaults**: 30 days active + 90 days restored. Purge in hourly housekeeping.
- **Restore JMAP op**: single `Email/set` call with `keywords/$junk=False` + `keywords/nonjunk=True`. Do NOT touch `mailboxIds` (mail was never moved out of INBOX; only labeled/keyword-flagged).
- **Adapter contract**: mails classified as spam/phishing-alert stay in INBOX with `label:__spam__` + `$junk=True` keyword. Twake Mail's own filter (if configured by owner) can move to Junk folder. **The sentinel does NOT call `archive()` for spam decisions** — decoupling avoids the "restore must move back" complexity.
- **Newsletter continuation**: bucket=newsletter labels + sets `nonjunk=True` + returns to pipeline (goes through match_rules etc.). Bucket in {spam, phishing-alert} returns to END via `_route_after_spam_triage` conditional edge.
- **Only phishing-alert emits a mission** (per Q9). Spam and newsletter are silent; observability via Recent Spam tab.
- **Order of decision cascade**: first-match-wins. No score accumulation across stages.
- **Grey-zone LLM safety**: if LLM `bucket=none` or confidence below threshold, pipeline PASSES THROUGH (bucket=None) — never archives on uncertain LLM output.
- **`declared_by` prefix for phishing-alert missions**: `"sentinel:mail"` (unchanged from SP6).
- **Mono-user unchanged**: `require_owner` on all `/mail-sentinel/spam` endpoints; single owner in `settings.twaky_owner_email`.
