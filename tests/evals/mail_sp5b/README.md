# SP5b Eval Fixtures

Scaffolding for the SP5b write-side features: memory extraction from draft diffs, 
cumulative trust pattern learning, and folder move observation.

Each fixture describes an input to a specific extractor and the expected memory/pattern 
rows after execution.

## Status

**No runner yet.** The eval harness for SP5b extractors does not exist in this task.
These YAML fixtures are a specification of what SHOULD be tested once the harness 
is implemented (planned for SP6a or later).

See `docs/superpowers/investigations/2026-08-14-sp5b-rollout-playbook.md` for the 
design rationale and rollout plan.

## Fixtures

- `draft_diff_preference_change.yaml` — greeting preference lesson from text substitution.
- `reclassification_3_samples.yaml` — cumulative trust_sender activation after 3 unmark_spam events.
- `folder_move_no_repetition.yaml` — single move to Archive, no pattern activation yet.

## Fixture Schema

### `expected.memories`

Array of expected inserts in `mail_sentinel_memory`:

- `scope` (string): "sender", "recipient", or "domain"
- `scope_value` (string): email address or domain
- `kind` (string): "preference" or "filter"
- `content_contains` (string): case-insensitive substring that must appear in the memory content
- `min_confidence` (float): confidence floor (0.0 to 1.0)

Example:
```yaml
memories:
  - scope: sender
    scope_value: alex@example.com
    kind: preference
    content_contains: bonjour
    min_confidence: 0.7
```

### `expected.patterns`

Array of expected state in `mail_sentinel_learned_pattern` at end of sequence:

- `sender_email` (string): sender address
- `rule_name` (string): pattern identifier (e.g., "trust_sender", "label:Archive")
- `is_active` (bool): whether pattern meets activation threshold (confidence >= 0.9, evidence >= 3)
- `evidence_count` (int, optional): number of observed confirmations

Example:
```yaml
patterns:
  - sender_email: newsletter@medium.com
    rule_name: trust_sender
    is_active: true
```

### `sequence` (for multi-action fixtures)

Array of actions to apply before asserting:

- `action` (string): one of "unmark_spam", "move_folder", "mark_spam"
- `sender_email` (string): sender address
- `folder_name` (string, optional): for "move_folder" action

Example:
```yaml
sequence:
  - action: unmark_spam
    sender_email: newsletter@medium.com
  - action: unmark_spam
    sender_email: newsletter@medium.com
```

## Future: Running Tests

Once the SP5b harness is built:

```bash
pytest tests/evals/mail_sp5b/ -v --eval-report
```

The harness will:
1. Load each YAML fixture
2. Apply input or sequence actions to an in-memory or test-DB observer
3. Assert memory and pattern records against the `expected` block
4. Report pass/fail per fixture
