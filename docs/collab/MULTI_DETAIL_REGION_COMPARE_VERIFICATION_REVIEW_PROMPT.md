# Multi-Detail Region Compare Verification Review Prompt

Date: 2026-05-27 KST
Audience: independent implementation reviewer / CAD comparison domain reviewer
Scope: multi-detail DWG/DXF drawing comparison by detected detail regions

## Current Evidence To Review

Latest inspected real run:

`C:\Users\user\AppData\Local\DrawingCompareWorkbench\runs\compare_20260527_001354`

Observed outputs:

- Run status: `completed`
- Raw CAD changes: `23,502`
- Generated change zones: `511`
- Viewer overlays: `511`
- Cloud region count: `0`
- Cloud omitted zone count: `511`
- Region-aware sidecar files were produced.
- Region detection:
  - before: `passed/1`
  - after: `passed/1`
  - detected region count: `2`
- Region matching:
  - before regions: `1`
  - after regions: `1`
  - auto matched regions: `0`
  - unmatched before: `1`
  - unmatched after: `1`
  - warning: `no detail regions reached review threshold`
- Localized assignment:
  - total zones: `511`
  - assigned zones: `404`
  - assignment rate: `0.7906066536203522`
  - unassigned zones: `107`
  - gate: `review_required`
- Region-local automatic compare:
  - `automatic_localized_compare_enabled=false`
  - fallback reason: `region-aware output requires review before automatic localized compare`
  - gate reasons:
    - `one or more change bboxes are outside detected detail regions`
    - `one or more detected regions are unmatched`

Important log evidence:

- `Canonical CAD compare failed ... DWG_UNSUPPORTED_VERSION`, then cached ezdxf fallback was used.
- `Skipping CAD spatial region clustering for 14906 entities; frame detection or whole-model fallback will be used`
- Preview/render transforms are not equivalent enough for a confident local-region visual comparison:
  - before render bbox was a small detail-like area
  - after render bbox covered a much larger modelspace area

Initial operator concern:

The user does not see a clear improvement. Their expected feature is: when one DWG/DXF file contains multiple drawings or detail frames, the program should extract each frame, match the same logical drawing/detail between old and new files, and then compare only those matched local regions. The current evidence suggests that sidecar diagnostics exist, but the primary user-visible workflow is still review-gated and does not automatically run accurate region-local comparison.

## Copy-Paste Review Prompt

```text
You are an independent senior implementation reviewer for a CAD Drawing Compare application.

Repository:
- D:\00.Work_AI_Tool\DrawingCompareWorkbench

Review target:
- The recently implemented multi-detail region compare roadmap.
- The feature should support DWG/DXF files where one source file contains multiple drawings/detail frames.
- Expected product behavior: detect detail frames, extract identity/title evidence, match the same logical detail between before/after files, require user review for ambiguous matches, then run region-local comparison and present results by matched detail.

Primary question:
Is the implementation genuinely complete and user-visible, or is it mostly diagnostic/sidecar output that does not yet deliver the intended precise comparison workflow?

Current real-run evidence to audit:
- Latest run directory: C:\Users\user\AppData\Local\DrawingCompareWorkbench\runs\compare_20260527_001354
- Run status: completed
- Raw changes: 23,502
- Change zones: 511
- Viewer overlays: 511
- Region detection: before passed/1, after passed/1
- Auto matched regions: 0
- Unmatched before: 1
- Unmatched after: 1
- Localized assignment: 404/511 zones assigned, 107 unassigned
- Gate status: review_required
- automatic_localized_compare_enabled=false
- fallback reason: region-aware output requires review before automatic localized compare
- Gate reasons:
  - one or more change bboxes are outside detected detail regions
  - one or more detected regions are unmatched
- Important log warning:
  - Skipping CAD spatial region clustering for 14906 entities; frame detection or whole-model fallback will be used

Key files to inspect first:
- docs/collab/MULTI_DETAIL_REGION_COMPARE_AGENT_ROADMAP.md
- docs/collab/MULTI_DETAIL_REGION_COMPARE_PILOT_REPORT.md
- scripts/validate_multi_detail_region_compare.py
- scripts/diagnose_region_detection.py
- src/services/comparison/sheet_region_detector.py
- src/services/comparison/detail_region_matcher.py
- src/services/comparison/region_compare_pipeline.py
- src/services/comparison/region_profile.py
- src/services/comparison/region_match_overrides.py
- src/services/comparison/region_viewer_package.py
- src/services/comparison/folder_compare_pipeline.py
- src/gui/drawing_compare_workbench.py
- src/gui/region_match_dialog.py
- tests/unit/services/comparison/test_region_aware_compare.py
- tests/unit/services/comparison/test_region_profile.py
- tests/unit/services/comparison/test_region_match_overrides.py
- tests/unit/scripts/test_validate_multi_detail_region_compare.py

Artifacts to inspect in the latest run:
- C:\Users\user\AppData\Local\DrawingCompareWorkbench\runs\compare_20260527_001354\run_manifest.json
- C:\Users\user\AppData\Local\DrawingCompareWorkbench\runs\compare_20260527_001354\artifacts\region_detection_summary.json
- C:\Users\user\AppData\Local\DrawingCompareWorkbench\runs\compare_20260527_001354\artifacts\region_match_summary.json
- C:\Users\user\AppData\Local\DrawingCompareWorkbench\runs\compare_20260527_001354\artifacts\localized_compare_summary.json
- C:\Users\user\AppData\Local\DrawingCompareWorkbench\runs\compare_20260527_001354\artifacts\region_aware_status.json
- C:\Users\user\AppData\Local\DrawingCompareWorkbench\runs\compare_20260527_001354\artifacts\multi_frame_validation.json
- logs\drawing_compare_stderr.log
- logs\error_20260527.log

Review tasks:
1. Reconstruct the intended user workflow from docs and code. State what the user should see if the feature is complete.
2. Trace the actual execution path for the latest run. Identify whether it performs:
   - frame/detail detection
   - same-detail before/after matching
   - manual match review UI handoff
   - region-local compare after approval
   - viewer/report display by matched region
3. Determine why the latest run produced `auto_matched_regions=0`.
4. Determine whether `review_required` is correct safety behavior or an implementation gap.
5. Evaluate whether one detected region per side is plausible for a multi-detail drawing. If not, identify why detection collapsed to one cluster.
6. Check whether the warning about skipping CAD spatial clustering for 14,906 entities is compatible with the roadmap requirement "Do not skip clustering only because entity count exceeds 8,000".
7. Check whether DWG fallback through cached DXF loses enough entity/title/block data to break region detection or matching.
8. Check whether title text, drawing number, block attributes, frame lines, and paper/layout evidence are actually used in matching for this case.
9. Check whether tests prove the real workflow, not only helper functions and sidecar JSON generation.
10. Decide whether the implementation can be called complete for the user's requirement.

Commands to run:
- git status --short
- python -m py_compile scripts\validate_multi_detail_region_compare.py scripts\diagnose_region_detection.py src\services\comparison\sheet_region_detector.py src\services\comparison\detail_region_matcher.py src\services\comparison\region_compare_pipeline.py src\services\comparison\region_profile.py src\services\comparison\region_match_overrides.py src\services\comparison\region_viewer_package.py src\gui\region_match_dialog.py
- python -m pytest tests\unit\services\comparison\test_region_aware_compare.py tests\unit\services\comparison\test_region_profile.py tests\unit\services\comparison\test_region_match_overrides.py tests\unit\scripts\test_validate_multi_detail_region_compare.py -q
- python scripts\diagnose_region_detection.py --help
- python scripts\validate_multi_detail_region_compare.py --help

Required output:

1. Verdict:
   - Complete / Partially implemented / Diagnostic only / Not working
   - Confidence level
   - One paragraph explaining why
2. User-visible behavior assessment:
   - What is improved
   - What is not improved
   - Why the user may not perceive improvement
3. Findings ordered by severity:
   - CRITICAL / HIGH / MEDIUM / LOW
   - Evidence path and line/artifact field
   - Impact on real multi-detail comparison
   - Concrete fix
   - Test or validation needed
4. Gate assessment:
   - Which gates passed
   - Which gates are review-gated
   - Which gates are missing
   - Whether the current gates can falsely imply completion
5. Implementation gap map:
   - detection gaps
   - matching gaps
   - manual review UI gaps
   - region-local compare gaps
   - viewer/reporting gaps
   - pilot evidence gaps
6. Recommended next sprint:
   - 3 to 7 concrete tasks
   - acceptance criteria for each task
   - exact tests/artifacts required to prove completion

Do not accept "run completed", "tests passed", or "sidecar JSON exists" as proof of the feature. The proof must show that multiple detail frames are detected, same logical details are matched, ambiguous matches are reviewable, and approved matches produce region-local comparison output that the user can see.
```

## Reviewer Decision Standard

Use this standard when scoring the work:

- `Complete`: real multi-detail drawings produce multiple detected regions, correct same-detail matches, approved region-local comparisons, and user-visible region-level rows/viewer evidence.
- `Partially implemented`: diagnostics and guardrails exist, but the default workflow still falls back or stops at review gates.
- `Diagnostic only`: artifacts describe why the feature could not run, but the user-visible comparison is still global/legacy.
- `Not working`: detection/matching artifacts are missing, invalid, or misleading.

Based on the latest inspected run alone, the expected provisional verdict is
`Partially implemented` or `Diagnostic only`, not `Complete`, unless further
evidence proves a successful multi-region pilot case.
