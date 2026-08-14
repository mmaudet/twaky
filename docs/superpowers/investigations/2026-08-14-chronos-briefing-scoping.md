# Chronos MVP-1 — Meeting prep briefing (scoping)

**Date** : 2026-08-14
**Status** : Scoping — awaiting user decision on data-source path
**Author** : Michel-Marie Maudet + Claude

**Goal reminder** : 30 min before each meeting, sentinel autonomously prepares a compact brief (attendee context + prior meetings + subject decomposition) and emits it as a Twaky mission + optional push notif.

## 1. What's already there

| Component | State |
|---|---|
| **Chronos agent** (LangGraph, delegate_to_chronos) | ✅ Live, has tools `list_events`, `get_event`, `find_conflicts`, `next_free_slot` |
| **AGE graph** (`CalendarEvent`, `Person`, `ATTENDED`, `ORGANIZED`) | ⚠️ Schema OK but populated with 5 test/demo events from 2026-08-01/02 only — real ingestion pipeline appears absent |
| **CalDAV backend** (`sabre_dav` container) | ✅ Running, accessible at `https://sabre-dav.twake-dev.maudet.cloud` (401 without auth) |
| **Twake Calendar frontend + tcalendar-side-service** | ✅ Live, but internal API surface not documented in Twaky |
| **Mail sentinel infrastructure** (Sentinel ABC, runtime, JMAP client, mission emitter) | ✅ Mature |
| **Push notification infrastructure** (webpush / VAPID / service worker) | ❌ Absent — would need to build from scratch |

## 2. Blocker : where to read live calendar events from

The Chronos briefing needs FRESH upcoming events (next 30-60 min window). Three paths :

### Path A — CalDAV direct (via `sabre_dav`)

Add a CalDAV client to Twaky (Python `caldav` package or raw ICS parsing). Query `PROPFIND` on the owner's calendar collection at each poll tick, filter by `time-range`.

- **Pros** : freshest data (bypasses ingestion lag), no dependency on graph.
- **Cons** : need CalDAV auth (dedicated password or delegate credential), CalDAV protocol overhead per tick (~200-500ms), some parsing (ICS iCal format).
- **Effort** : ~1 day (client + auth + ICS parser + tests).

### Path B — tcalendar-side-service HTTP API

If the Twake Calendar side-service exposes a REST endpoint for events (e.g. `/api/v1/events?from=X&to=Y`), Twaky just HTTP-GETs. Simpler than CalDAV.

- **Pros** : simple HTTP+JSON, likely already OIDC-authenticated (same session as Twake).
- **Cons** : API surface UNKNOWN — needs discovery. Might not exist or expose only frontend-oriented paths.
- **Effort** : 4h if API exists as we hope, ~1 day if we need to add endpoints server-side.

### Path C — Build CalDAV → AGE ingestion pipeline

Populate the graph properly (cron ingester : CalDAV → parse → upsert `CalendarEvent` nodes + `Person` + relationships). Then Chronos briefing reads from AGE (fast, indexed).

- **Pros** : long-term architecture, benefits Atlas / Chronos agent / future sentinels.
- **Cons** : biggest upfront investment (~2-3 days). MVP delayed.
- **Effort** : ~2-3 days total (ingester + tests + operationalization).

## 3. Recommended path

**Path A (CalDAV direct)** for MVP-1, with an explicit follow-up to add Path C as SP-later.

Rationale :
1. Simplest to ship AND validate the briefing flow end-to-end.
2. Fresh data (no ingestion lag = crucial for "30 min before" trigger).
3. Reusable client for other calendar features (RSVP tracking, focus block detection, etc.).
4. AGE ingestion becomes a pure optimization once the sentinel has proven value.

## 4. MVP-1 architecture proposal (assuming Path A)

```
┌─────────────────────────────────┐
│ SentinelRuntime (async loop)    │
│  new task: _chronos_briefing_   │
│  loop (poll every 5 min)        │
└──────────────┬──────────────────┘
               │
               ▼
┌────────────────────────────────────────────────┐
│ ChronosCalDavClient                            │
│  list_events_in_window(start, end)             │
│  get_event_attendees(event_uid)                │
│  (authenticated OIDC / dedicated password)     │
└──────────────┬─────────────────────────────────┘
               │
               ▼
┌────────────────────────────────────────────────┐
│ prepare_briefing(event) — LangGraph mini-pipe  │
│  1. load_event                                 │
│  2. gather_attendee_mails (JMAP query)         │
│  3. gather_prior_meetings (CalDAV last 30d)    │
│  4. compose_briefing (LLM: Mistral chat tier)  │
│  5. emit_mission (mission.emit + record row)   │
└──────────────┬─────────────────────────────────┘
               │
               ▼
┌────────────────────────────────────────────────┐
│ chronos_briefing table                         │
│  event_uid PK, event_start_at, briefed_at,     │
│  mission_id FK, brief_body, attendee_count     │
└────────────────────────────────────────────────┘
```

### New Python modules

- `src/twaky/sentinels/chronos/caldav_client.py` — CalDAV HTTP client
- `src/twaky/sentinels/chronos/briefing.py` — orchestrator (poll → prepare → emit)
- `src/twaky/sentinels/chronos/prompts/compose_briefing.py` — LLM prompt
- `src/twaky/sentinels/chronos/store/briefing.py` — CRUD `chronos_briefing`
- `sql/014_init_chronos_briefing.sh` — migration
- Runtime wiring : new `_chronos_briefing_loop` task alongside existing `_housekeeping`
- Feature flag : `CHRONOS_MEETING_BRIEF_ENABLED` (default False)
- Config : `CHRONOS_CALDAV_URL`, `CHRONOS_CALDAV_USER`, `CHRONOS_CALDAV_PASSWORD` (or OIDC token exchange)

### Effort estimate

- CalDAV client + auth + tests : 4h
- Briefing pipeline (4-5 nodes) : 4h
- LLM prompt + schema : 1h
- Store + migration : 1h
- Runtime wiring + feature flag : 1h
- Integration tests + eval fixtures : 2h
- Frontend affordances (link brief to mission) : 1h (optional)
- **Total : ~14h = 2 focused days**

## 5. Push notification (deferred beyond MVP-1)

The user requested "mission + push notif". Push notif requires :
- Backend : webpush endpoint + VAPID key management + subscription table
- Frontend : service worker + subscription flow + browser API integration
- ~2-3 additional days of work

**Recommendation** : ship MVP-1 with mission-only. Add push notif as a separate sub-project (call it SP-Notif or similar) if/when user validates the briefing flow is useful.

Interim fallback : the Twake Mail app on user's phone already shows a system notif when a mission or draft mail arrives. If the mission is exposed via mail-to-self, mobile push works via mail app.

## 6. Decision questions for user

1. **Data source** : Path A (CalDAV direct), B (tcalendar-side-service API discovery), or C (build AGE ingestion first)?
2. **Auth strategy for CalDAV** : dedicated password (simplest), OIDC token exchange (cleanest but complex), or reuse the Twake Calendar cookie/token (fragile)?
3. **Push notif** : accept mission-only for MVP-1 and defer real push?
4. **Session scheduling** : implement now (2 days back-to-back) or brainstorm-only-now + implement in a fresh session next week?

## 7. What THIS scoping unlocks even without implementation

- A clear picture of the infra dependencies (CalDAV auth flow is the critical path).
- A defensible effort estimate (~2 focused days) so the sub-project can be scheduled realistically.
- A concrete list of files to touch so implementation can be delegated to a subagent-driven-development session.

---

**Next step** : user answers Q1-Q4 → implementation session scheduled → SDD execution.
