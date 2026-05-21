# Phase N Self-Review Report — Workbench Integration Gap

> Self-improvement reflection: how a 12-commit feature ship that passed
> 270+ unit tests still failed to actually deliver value to users, and
> what we changed to make it un-recurrable.
>
> Date: 2026-05-07

## TL;DR — the finding

The 3-tier AI classifier cascade we built across Phase H/I/J/K/L
(Sept 2025 – May 2026) was **never actually invoked from the
Drawing Compare Workbench during normal comparison flow**. Users
who turned on Quality / Speed / LLM / RAG modes in the settings
dialog saw ZERO behavioural change because the workbench called
`zone_classifier.classify_zone` (older heuristic-only function)
instead of `ai_classifier.classify_zones` (the new cascade we
built).

**Detection**: independent code-verification-agent during E2E test
plan preparation (this session, 2026-05-07).

**Time-to-detection**: ~6 weeks since Phase H (when the cascade
shipped).

**Why unit tests missed it**: every unit test exercised the cascade
in isolation. None traced the actual workbench call path from
"user clicks pair" → "categories appear in zone list".

**Fix shipped (Phase N, this session)**: new
`zone_classifier_adapter.py` with `classify_zone_with_cascade(zone, cfg)`
+ workbench routing change + cache invalidation fix +
**13 integration tests** that lock in the wiring.

## What went wrong

### Root cause: parallel-evolution drift

Two systems evolved in parallel without an integration plan:

1. `src/services/comparison/zone_classifier.py` — the workbench's
   original heuristic, simple `dict → ZoneCategoryResult` schema.
2. `src/services/comparison/ai_classifier/` — the new 3-tier cascade,
   richer `dict → ChangeClassification` schema (categorty enum,
   severity enum, kds_references, classifier_used, raw_evidence).

Phase H built the new package and had its own `classify_zones`
function. The intent was clearly to replace `zone_classifier`, but
the schema was different so callers couldn't drop-in switch. Phase
H's "shipped" milestone declared completion based on isolated tests,
not integration verification.

Phases I, J, K, L all extended the new cascade without ever
verifying it was reaching the workbench. Each phase added unit tests
that confirmed THE NEW CODE worked — but the workbench's existing
caller (`_compute_zone_categories_for_pair_v2` line 4942) kept
calling the old `zone_classifier.classify_zone`.

### Concrete examples of how a single integration test would have caught this

If at any point during Phase H/I/J/K/L we had written:

```python
def test_workbench_pair_classification_uses_cascade_when_enabled():
    # Set up workbench with use_embedding=True
    # Trigger _compute_zone_categories_for_pair_v2
    # Assert: result rationale contains "[Stage-2]" marker
    #         (would prove cascade ran, not bare heuristic)
```

…the test would have FAILED in Phase H, exposing that the workbench
caller was bypassing the cascade. Instead we shipped 12 commits
worth of cascade enrichment that never reached production users.

### Why my own self-review (commit `39c8aa4b` review report) missed it

The earlier code review (which surfaced 6 critical/important bugs
including `probe_available()` host hardcoding and `clear_llm_dispatcher_cache()`
gap) **did not check call-graph reachability**. The review focused on:
- Correctness within each module
- Thread safety
- Error handling
- Schema validation

It did NOT ask: "is this code path actually reached from anywhere
the user touches?" — which would have surfaced the dead-code finding.

## What we shipped (Phase N)

### `src/services/comparison/zone_classifier_adapter.py` (NEW, ~250 lines)

Two public functions:

- `adapt_change_classification(result) → ZoneCategoryResult`:
  schema bridge that maps every `ChangeCategory` enum value to a
  Korean workbench label, severity to a sort-boost integer, and
  prepends a `[Stage-N marker]` to the rationale so reviewers can
  see WHICH tier produced each result. Defensive against None /
  dict / missing fields.

- `classify_zone_with_cascade(zone, config) → ZoneCategoryResult`:
  drop-in replacement for `zone_classifier.classify_zone`. Routes:
  - `cfg=None` or both `use_embedding`/`use_llm`=False → bare heuristic
  - `cfg.use_embedding=True` or `cfg.use_llm=True` → cascade →
    adapter
  - Cascade exception → falls back to heuristic (defensive)

### `src/gui/drawing_compare_workbench.py` (modified, ~30 lines)

- New import: `classify_zone_with_cascade`
- `_compute_zone_categories_for_pair_v2`: now loads `cfg` once per
  pair (cheap JSON read), routes through `classify_zone_with_cascade(merged, config=cfg)`
- Per-zone try/except so a single failure doesn't crash the whole
  pair's classification
- `_show_ai_settings_dialog_v2`: now ALSO clears
  `self._zone_categories_v2` on settings OK (without this, the
  per-pair cache would freeze the user on their old config until
  pair re-selection)

### `tests/unit/services/comparison/test_zone_classifier_adapter.py` (NEW, 13 tests)

The kind of test that would have prevented the original gap:

| Test | What it locks in |
|---|---|
| `test_adapter_handles_every_change_category` | Every enum value maps to a non-empty label (no orphan future-enum risk) |
| `test_adapter_severity_boost_critical_floats_to_top` | Sort order preserved through the schema bridge |
| `test_adapter_tier_marker_in_rationale` | Each `classifier_used` value gets a visible marker |
| `test_adapter_handles_none_input` | None → safe fallback |
| `test_adapter_handles_dict_input` | Defensive against future dict-passing callers |
| `test_routing_heuristic_only_when_config_none` | Fast path preserved |
| `test_routing_heuristic_when_use_embedding_false_and_use_llm_false` | `heuristic_only()` config → fast path |
| `test_routing_cascade_when_use_embedding_true` | **The smoke test that would have caught Phase N regression** — asserts `[Stage-` marker present (proves cascade ran) |
| `test_routing_cascade_when_use_llm_true` | LLM specifically fires for stub backend |
| `test_routing_falls_back_to_heuristic_when_cascade_raises` | Defensive — cascade contract violation doesn't crash workbench |
| `test_routing_disabled_config_uses_heuristic` | `enabled=False` → heuristic |
| `test_workbench_overlay_shape_classified_via_cascade` | **Real workbench overlay shape** (no text_snippet, with layer + change_type + raw_change_count) flows through cascade end-to-end |
| `test_workbench_overlay_shape_classified_via_heuristic_only` | Mirror for the fast path |

### `docs/AI_CLASSIFIER_E2E_TEST_PLAN.md` (NEW, ~470 lines)

Comprehensive E2E test plan structured by **integration path** (Cat A
through Cat I), not by module. Each category specifies:
- What model files / Ollama / KDS data is needed
- Concrete test scenarios with expected outcomes
- Acceptance criteria
- Failure recovery paths

Crucially: documents the Phase N finding as the motivation +
includes regression-prevention tests in Cat H (workbench integration).

## Lessons learned + protocol changes

### Lesson 1: Unit tests pass ≠ feature ships

The cascade had ~270+ unit tests, all green, for 12 commits. Yet
the feature was effectively never delivered to users. Going forward:

**Protocol**: every new feature MUST have at least ONE
integration test that traces the path from a user-visible action
(button click / API call / config change) to the new code being
exercised. The integration test should fail if the new code is
NOT called.

**Concrete example for AI classifier**:
```python
def test_settings_dialog_quality_mode_actually_changes_workbench_classification():
    # Setup: workbench with default (heuristic) config
    # Run pair classification → assert no AI tier marker in rationales
    # Trigger settings change to Quality mode
    # Re-run pair classification → assert AI tier marker present
```

### Lesson 2: Independent verification > self-review

My earlier review (`AI_CLASSIFIER_REVIEW_REPORT.md`, commit `39c8aa4b`)
found 6 bugs but missed the integration gap. The independent
code-verification-agent in this session caught it within minutes
because it asked a different question: "where is this called
from?" instead of "is this code correct?"

**Protocol**: for any feature spanning ≥3 commits, run an
independent verification pass with the explicit prompt: "trace the
call graph from the user-visible entry point to the new code, and
flag any code that isn't reached from production paths."

### Lesson 3: Schema bridges should ship alongside replacements

When `ai_classifier` shipped its `ChangeClassification` schema
(richer than `ZoneCategoryResult`), there was no migration plan
for callers. Result: callers stayed on the old schema indefinitely.

**Protocol**: when introducing a new schema that's intended to
replace an old one, ship the adapter at the same time as the new
schema. Don't let the "we'll integrate it later" mindset leave
features stranded.

### Lesson 4: Cache invalidation reviews

The Phase N fix also exposed that `_zone_categories_v2` was NOT
cleared on settings change — a pre-existing bug invisible while
the cache was config-independent (heuristic only). The moment the
cache became config-dependent (via the cascade), it became a real
correctness bug.

**Protocol**: when touching code that fronted by a cache, audit
all the cache's invalidation triggers + ensure new dependencies
are reflected.

## Outstanding work after Phase N

### Critical (blocks user PoC)
- None. Cascade is now wired, integration tests lock it in.

### Important (Phase O candidates)
- **O1**: Background warm-up timeout UX — `_poll_ai_prepare_v2`
  silently stops after 30s without surfacing an error. Reviewer
  flagged this as untested + unrecoverable.
- **O2**: text_snippet enrichment — production overlays don't carry
  text_snippet. The cascade gracefully handles empty text but
  Stage-2 embedding is less effective without it. Phase K2 KDS RAG
  partially compensates by injecting standard clauses, but a real
  fix would feed OCR'd text into overlays.

### Lower priority
- M3: registry meta-pattern dedup
- M4: AiClassifierConfig nested split
- M6: real Ollama smoke in CI

## Phase N statistics

- New files: 2 (adapter + integration tests)
- Modified files: 1 (workbench routing + cache invalidation)
- New documentation: 2 (E2E plan + this self-review)
- New tests: 13 (integration coverage that would have caught the
  original gap)
- LOC: ~700 (~250 adapter + ~30 workbench + ~420 tests)

## Closing reflection

The honest answer to "did the AI classifier work the user's
investment paid for, deliver value?" before Phase N was: NO. The
unit tests were green, the docs were thorough, the GUI was polished
— and yet a critical wiring step was missing.

The Phase N fix + the integration tests + the protocol changes
above are the corrective action. Going forward, any feature that
ships without an integration test tracing the user-visible entry
point should be treated as incomplete.

**Trust but verify** — and when verifying, ask not just "is this
code correct?" but "is this code reachable from where the user
actually touches the system?"
