# GPT Pro Deep Research Review Prompt

Date: 2026-06-02

Use this prompt when asking GPT Pro Deep Research to review the repository
through GitHub or an uploaded repository archive.

## Access Setup

Repository:

```text
https://github.com/Nicefree19/DrawingCompareWorkbench
branch: main
review revision: latest pushed main containing this document
```

The repository appears private from an unauthenticated web request. For GPT Pro
Deep Research, use one of these access paths:

1. Connect the same GitHub account that can access `Nicefree19/DrawingCompareWorkbench`.
2. Temporarily make the repository public/read-only for the review window.
3. Upload a repository archive generated from the current `main` commit.

Do not review stale local code. Confirm that
`docs/collab/GPT_PRO_DEEP_RESEARCH_REVIEW_PROMPT.md` and
`docs/collab/DRAWING_COMPARE_COMPLETION_CRITERIA.md` are visible on the
selected `main` revision before drawing conclusions.

## Prompt

```text
You are performing a deep technical review of the DrawingCompareWorkbench
repository.

Repository:
https://github.com/Nicefree19/DrawingCompareWorkbench
Branch: main
Review revision: latest pushed main containing
docs/collab/GPT_PRO_DEEP_RESEARCH_REVIEW_PROMPT.md

If the repository is inaccessible or the prompt/completion-criteria documents
are not visible at the selected revision, stop and report the access/version
mismatch first.

Review goal:
Evaluate whether DrawingCompareWorkbench is ready to be described as a
customer-usable drawing comparison module, while explicitly separating that from
modern/all-version native DWG support. Do not assume AC1018+ native DWG support
is complete. Treat any claim of AC1018/AC1021/AC1024/AC1027/AC1032 native support
or "all DWG versions supported" as suspect unless version-specific ADR-004 Phase
1 gates are proven.

Primary context to read first:
1. AGENTS.md
2. docs/collab/DRAWING_COMPARE_COMPLETION_CRITERIA.md
3. docs/adr/ADR-004-ac1032-dwg-native-support-roadmap.md
4. docs/collab/ADR004_PHASE1_NATIVE_DWG_IMPLEMENTATION_PLAN.md
5. docs/collab/ADR004_PHASE1_IMPLEMENTATION_CHECKLIST.md
6. docs/collab/ADR004_PHASE0C_BASELINE_METRICS.md
7. docs/collab/ADR004_COMPACT_COMPARE_CANDIDATES_REPORT.md
8. docs/collab/WORKLOG.md
9. docs/THIRD_PARTY_LICENSE_POLICY.md
10. scripts/cad_policy_gate.py

Important project constraints:
- Default/customer paths must not invoke ODA SDK, ODA File Converter, LibreDWG,
  GPL, AGPL, or derived material.
- `oda_converter` is allowed only as an explicit local/internal fallback mode.
- `user_converter` and registered/converted DXF fallback are valid customer
  workflows when provenance is preserved.
- `src/gui/drawing_compare_workbench.py` is structurally frozen. New GUI widgets
  or workers should not be added there without an approved exception.
- PDF-first implementation/prototype work remains frozen unless the accepted ADR
  state explicitly permits it.
- Do not treat local evidence paths such as `D:\도면 비교` as portable customer
  assets; review whether the evidence is documented and reproducible enough.

Recent implementation to inspect:
- Explicit `oda_converter` backend mode and cache:
  src/services/comparison/import_pipeline.py
  src/services/comparison/dwg_differ.py
  src/cli/cad_compare.py
  tests/unit/services/comparison/test_import_compare_pipeline.py
  tests/unit/cli/test_cad_compare_cli.py
- Closed-polyline normalization performance optimization:
  src/services/comparison/drawing_normalizer.py
  tests/unit/services/comparison/test_drawing_normalizer.py
- Completion criteria:
  docs/collab/DRAWING_COMPARE_COMPLETION_CRITERIA.md

Known current state:
- Latest local validation reported:
  `python -m pytest tests\unit\services\comparison\test_drawing_normalizer.py tests\unit\services\comparison\test_import_compare_pipeline.py tests\unit\services\comparison\test_dwg_importer.py tests\unit\cli\test_cad_compare_cli.py -q`
  passed with 50 tests.
- `python scripts\cad_policy_gate.py` passed.
- `git diff --check` passed before commit.
- Cached ODA AC1032 import+normalize improved from >120s timeout to about 22.8s.
- Cached ODA AC1032 compare with `--max-dxf-tokens 12000000` completed in about
  87.9s with `status=partial`.
- Default ODA token budget path fails fast with `CAD_TOKEN_LIMIT_EXCEEDED`.
- AC1018/AC1021 still lack real before/after compare baselines.
- AC1024/AC1027/AC1032 have fallback baselines, but native DWG support is not
  implemented or claimable.

Review questions:
1. Is the customer-ready completion criteria document coherent, complete, and
   consistent with the code and ADRs?
2. Are ODA/GPL/AGPL boundaries actually enforced by code, tests, and
   `cad_policy_gate.py`?
3. Does the explicit `oda_converter` fallback avoid accidental default/customer
   invocation?
4. Is cache provenance sufficient: source DWG identity, cache hit/miss,
   converted DXF path, source signature, and failure details?
5. Are timeout, token, failure, cleanup, and partial-import cases handled
   clearly enough for customer use?
6. Does the closed-polyline optimization preserve correctness while fixing the
   large-DXF performance issue?
7. What remaining risks block customer-ready release?
8. What remaining risks block any modern/all-version native DWG claim?
9. Are there contradictions between docs, tests, code, and release wording?
10. What should be the next three implementation priorities?

Expected output format:

Start with a concise verdict:
- Customer-ready fallback workflow: Ready / Not ready / Conditionally ready
- Modern DWG native support: Ready / Not ready
- All-DWG-version claim: Allowed / Not allowed

Then provide findings ordered by severity. Use this exact format for each:
- Severity: CRITICAL | HIGH | MEDIUM | LOW
- Impact: concrete runtime, security, licensing, data, performance, UX, or
  maintainability risk
- Evidence: file path, line number, command output, documented claim, or
  reproducible scenario
- Recommendation: specific fix direction
- Tests: targeted test or validation scenario

After findings, include:
- Gate assessment table:
  policy, default ODA disabled, explicit ODA fallback, converted-DXF fallback,
  cache provenance, large-DXF performance, partial import visibility, customer
  evidence, ADR-004 native gates, release wording
- Documentation consistency assessment
- Test coverage gaps
- Release wording that is safe to use now
- Release wording that must remain forbidden
- Prioritized next actions

Do not rewrite large portions of code. This is a review, not an implementation
task. If evidence is unavailable because the repository or selected revision
cannot be accessed, report that as the top blocker.
```

## Optional Short Prompt

```text
Review https://github.com/Nicefree19/DrawingCompareWorkbench on the latest
pushed `main` revision containing
docs/collab/GPT_PRO_DEEP_RESEARCH_REVIEW_PROMPT.md. Focus on whether the
drawing comparison module is customer-ready through
PDF/DXF/converted-DXF/fallback workflows while modern native DWG support
remains unclaimable under ADR-004. Read AGENTS.md and
docs/collab/DRAWING_COMPARE_COMPLETION_CRITERIA.md first. Return
severity-ranked findings with evidence, recommendations, tests, gate
assessment, safe/forbidden release wording, and next actions.
```
