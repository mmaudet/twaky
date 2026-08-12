# ADR: SP6d Rules CRUD — Design decisions

- **Status:** Accepted
- **Date:** 2026-08-12
- **Author:** T1-T4 implementation team
- **Context:** SP6d ships Rules CRUD UI with mandatory Propose/Apply flow,
  Origin mailbox column on Recent Spam, and backend schema/endpoint support.

## Decisions

### Decision 1: Simulation is mandatory, not optional

**Context:** SP6d gives operators the ability to create and edit rules
from the UI. Rules are the sharpest lever in the mail sentinel —
a wrong priority buries every legitimate rule below it, a wrong
regex silently drops legit mail, and a rule that shadows an
existing one is invisible until the shadowed rule fails to fire.

**Decision:** The Apply button is gated on TWO conditions: (a) a
successful Propose call, and (b) an explicit "I have reviewed the
matches" checkbox tick. No shortcut path. Editing the JSON after a
Propose invalidates the panel (Apply disappears until a fresh
Propose runs).

**Why:** An operator directive during SP6d design. Original spec had
"simulation optionnelle" as an option; the operator picked
"simulation obligatoire" without hesitation. Rationale: "I don't
trust myself to look at the matches if the button is right there
without them."

**How to apply:** When adding future mutation flows in the mail
sentinel (bulk edits, delete-with-preview, learned-pattern
promotion), follow the same Propose/Apply pattern. Cheap
Propose = short reviewed checkbox; expensive Propose = timer +
progress + reviewed checkbox.

### Decision 2: Propose endpoint accepts the actual rules_store schema, not a new one

**Context:** The SP6d spec initially specified `from_contains`,
`subject_contains`, `header_matches` predicates for the propose
endpoint's request. During T2 implementation the reviewer caught
that `rules_store` uses a different schema (`conditions: list[
{field, operator, value}]` + top-level `combinator`) and the two
would diverge — the UI could Propose a rule shape it could not
then Apply.

**Decision:** Propose accepts the exact same schema `rules_store` uses.
Same request body as `POST /mail-sentinel/rules`. Simulation
evaluates via the same function production uses
(`nodes.rule_matches_static`), renamed from private to public as
part of T2.

**Why:** Two schemas = two evaluators = two behaviours. The UI would
have to translate. Better to keep the propose surface aligned
with the mutation surface.

**How to apply:** New endpoints that simulate mutations should accept
the mutation's request shape verbatim and reuse the mutation's
evaluator/executor. If a "simulate-only" transformation is
tempting, ask first whether the mutation should learn to
dry-run itself instead.

### Decision 3: Origin mailbox provenance is opt-in on the API (with_provenance query flag) but always-on in the twaky UI

**Context:** T4 added `origin_mailbox_role` + `origin_mailbox_id` to
the GET /mail-sentinel/spam response. Adding fields to a public
response can surprise third-party consumers.

**Decision:** Backend response strips the two fields to null by
default. The twaky UI passes `?with_provenance=1` explicitly.
Third-party consumers must opt in too.

**Why:** SP6d is a UI change. External API stability matters
independently. A migration operator who takes over an existing
instance may have scripts reading the old shape; a silent shape
change would break them. Opt-in preserves that guarantee while
letting the twaky UI move forward immediately.

**How to apply:** When extending an existing surface with new fields
that aren't strictly additive (i.e., could break parsers that
enumerate fields), add an opt-in query flag AND note the deprecation
window in the response header or docs. Flip the default only after
a migration window (e.g. one release).

**Refinement (SP6e):** `POST /mail-sentinel/spam/{id}/restore`
intentionally does NOT surface `origin_mailbox_role` or
`origin_mailbox_id` even when the client sends `?with_provenance=1`.
Rationale: restore is a mutation response whose shape is considered
stable and additive-free; mutating callers should not depend on
provenance fields being present in the response. Callers that need
provenance after a restore can re-fetch the record via
`GET /mail-sentinel/spam?with_provenance=1`. This is a documented
intentional design choice, not a bug.

## Consequences

- The Propose endpoint cannot fully evaluate rules using `field:
  body` — the simulation intentionally skips body fetches from
  JMAP. The `simulation_partial_reason` field surfaces this
  limitation to the UI.
- Pre-SP6d decisions have NULL provenance. The Origin column shows
  "—" for them; not enough of a wart to backfill.
- `nodes.rule_matches_static` is now a public API surface. If
  future changes to the evaluator break the propose endpoint,
  the fix is in one place, not two.
