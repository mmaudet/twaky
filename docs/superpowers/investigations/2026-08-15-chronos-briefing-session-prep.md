# Chronos MVP-1 — Session prep for 2026-08-15

**Date** : 2026-08-14 (prepared)
**Session target** : 2026-08-15
**Status** : Ready to execute — all 4 decisions locked
**Related** : [2026-08-14-chronos-briefing-scoping.md](./2026-08-14-chronos-briefing-scoping.md)

## Decisions locked (2026-08-14)

| # | Question | Decision |
|---|---|---|
| Q1 | Data source | **Path A** — CalDAV direct via `sabre_dav` |
| Q2 | Auth strategy | **OIDC token exchange** (same flow as JMAP OAuth SP6b) |
| Q3 | Push notif in MVP-1 | **Mission + mail-to-self** as fallback push |
| Q4 | Session scheduling | **Fresh session 2026-08-15**, not tonight |

## Revised effort estimate

- CalDAV client + OIDC integration : **6h** (was 4h with dedicated password — OIDC adds token refresh plumbing)
- Briefing pipeline (5 LangGraph nodes) : 4h
- LLM prompt + structured schema : 1h
- Store + migration : 1h
- Runtime wiring + feature flag : 1h
- Mail-to-self dispatch (mission emitter helper) : 30 min
- Integration tests + eval fixtures : 2h
- **Total : ~16h = 2 focused days** (was 14h)

## First-thing-to-verify at session start (30 min pre-flight)

**⚠️ Blocker probe** : does `sabre_dav` accept OIDC Bearer tokens ?

Sabre DAV standard config uses HTTP Basic. OIDC Bearer support may require :
- A custom auth backend module (sabre-dav-auth-oidc or similar Composer package)
- Or a reverse-proxy (Traefik with ForwardAuth plugin) that validates Bearer + passes user identity via header
- Or Twake ships a patched sabre_dav — need to check `/home/mmaudet/deploy/kickstart-maudet-cloud/` for `calendar_app/patches/`

If OIDC Bearer is NOT natively supported :
- **Fallback A** : add ForwardAuth middleware in Traefik (~2h server-side work)
- **Fallback B** : switch Q2 decision to "dedicated password" and revisit OIDC later

**Command to probe** :
```bash
# Get a fresh OIDC token via the JMAP oauth_credential (same issuer)
TOKEN=$(docker exec twaky-sentinel /app/.venv/bin/python -c "
import asyncio
from twaky.oauth.refresh_manager import get_manager
mgr = get_manager('mail')
print(asyncio.run(mgr.get_access_token()))
")

# Try CalDAV PROPFIND with Bearer
curl -sk -X PROPFIND \
  -H "Authorization: Bearer $TOKEN" \
  -H "Depth: 0" \
  https://sabre-dav.twake-dev.maudet.cloud/ \
  -w "\nHTTP %{http_code}\n"
```

Expected outcomes :
- **200/207** → Bearer accepted, path clear, proceed with plan
- **401 with WWW-Authenticate: Basic** → Bearer NOT accepted, decide Fallback A or B
- **404** → wrong URL, need to find the correct calendar collection endpoint

## Execution plan (assuming Bearer probe green)

### Phase 1 — CalDAV client (6h)

Files to create :
- `src/twaky/sentinels/chronos/__init__.py`
- `src/twaky/sentinels/chronos/caldav_client.py`
- `tests/sentinels/chronos/test_caldav_client.py`

Client contract :
```python
class ChronosCalDavClient:
    def __init__(self, *, base_url: str, principal_path: str, token_provider: Callable[[], str]):
        ...

    async def list_events_in_window(
        self, *, start: datetime, end: datetime
    ) -> list[CalendarEvent]:
        """Return events whose DTSTART falls within [start, end]."""

    async def get_event(self, uid: str) -> CalendarEvent | None:
        """Fetch one event with attendees + description + location."""


@dataclass(frozen=True)
class CalendarEvent:
    uid: str
    summary: str
    description: str | None
    location: str | None
    start_at: datetime
    end_at: datetime
    organizer_email: str | None
    attendee_emails: list[str]
```

Uses Python stdlib for HTTP (`httpx`) and either the `icalendar` package OR raw parsing for ICS format. Prefer `icalendar` (well-maintained, ~50KB dep).

### Phase 2 — Store + migration (1h)

`sql/014_init_chronos_briefing.sh` :
```sql
CREATE TABLE IF NOT EXISTS public.chronos_briefing (
    event_uid           TEXT PRIMARY KEY,
    event_start_at      TIMESTAMPTZ NOT NULL,
    briefed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    mission_id          UUID REFERENCES public.mission(id) ON DELETE SET NULL,
    brief_body          TEXT NOT NULL,
    attendee_count      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS chronos_briefing_by_start
    ON public.chronos_briefing (event_start_at DESC);
```

Store CRUD : `get(uid)`, `insert(event_uid, ...)`, `list_recent(days=30)`.

### Phase 3 — Briefing pipeline (4h + 1h prompt)

`src/twaky/sentinels/chronos/briefing.py` — orchestrator using existing helpers.

Pipeline nodes (functions, no LangGraph needed for MVP — single-flow) :
1. `_load_event(caldav, uid)` → `CalendarEvent`
2. `_gather_attendee_mails(jmap, attendee_emails, since_days=14, limit_per_attendee=3)` → `list[dict]`
3. `_compose_briefing(event, attendee_mails)` → `BriefOutput` (LLM Mistral chat tier)
4. `_emit_mission_and_mail(brief, event)` → mission id + optional mail sent

Prompt file : `src/twaky/sentinels/chronos/prompts/compose_briefing.py`.

### Phase 4 — Runtime wiring (1h)

New async task in `SentinelRuntime` : `_chronos_briefing_loop`, runs every 5 min :
```python
async def _chronos_briefing_loop(settings, stop_event):
    while not stop_event.is_set():
        await asyncio.wait_for(stop_event.wait(), timeout=300.0)  # 5 min
        if not settings.chronos_meeting_brief_enabled:
            continue
        try:
            await run_briefing_tick()
        except Exception:
            log.exception("chronos_briefing: tick failed")
```

`run_briefing_tick()` :
- Find events starting in [now+25min, now+35min]
- Filter out already-briefed (via `chronos_briefing.get(uid)`)
- For each : run pipeline + insert row

### Phase 5 — Mail-to-self fallback (30 min)

After mission emission, if `settings.chronos_meeting_brief_mail_fallback` (default True) :
- Use existing `JmapMailAdapter.save_draft` (or a new `send_mail` method — check what exists)
- Send to `settings.jmap_account_email`, subject `"[Chronos] Brief: <event summary>"`, body = brief

### Phase 6 — Tests (2h)

- Unit : caldav_client (respx mock), briefing pipeline (mocked LLM), store CRUD
- Integration : full tick with in-memory CalDAV fixture (skip live)
- 20+ tests target

### Phase 7 — Deploy + validate

- Migration `docker exec -i twaky-pg bash < sql/014_init_chronos_briefing.sh`
- Rebuild + redeploy `twaky-sentinel`
- Enable feature flag `CHRONOS_MEETING_BRIEF_ENABLED=true` in `.env`
- Wait for first brief (next real meeting in 30 min window)
- Verify : mission created, mail-to-self arrived, brief useful

## Config additions

New settings :
```python
# Chronos
chronos_meeting_brief_enabled: bool = Field(default=False)
chronos_meeting_brief_mail_fallback: bool = Field(default=True)
chronos_caldav_base_url: str = Field(default="https://sabre-dav.twake-dev.maudet.cloud")
chronos_caldav_principal_path: str = Field(default="")  # to be discovered at pre-flight
```

## Pre-flight checklist for tomorrow

- [ ] Bearer probe green (or decide fallback A/B)
- [ ] `chronos_caldav_principal_path` discovered (usually `/principals/<user>/` or `/calendars/<user>/`)
- [ ] `settings.jmap_account_email` populated (already used by mail sentinel — should be `mmaudet@linagora.com`)
- [ ] `chat.lucie.ovh.linagora.com` LLM reachable (already used by mail sentinel)
- [ ] User calendar has at least 1 event in the next 24h to test the flow

## Success criteria

- MVP-1 shipped as PR, mergeable
- 20+ tests pass, ruff+mypy clean
- Deployed on athena with flag ON for 24h
- 1 brief created for a real meeting → visible in `/missions/<id>` AND arrived by mail
- User validates : "the brief actually helps me prep the meeting"

---

**Ready to execute tomorrow.** Fresh session, ~2 focused days budget, all decisions locked, pre-flight checklist ready.
