# Multi-Detail Region Compare Pilot Report

Status: pending real pilot data.

This report is the R9 evidence target for
`docs/collab/MULTI_DETAIL_REGION_COMPARE_AGENT_ROADMAP.md`. The validation
harness is available at `scripts/validate_multi_detail_region_compare.py`.

## Pilot Command

```powershell
python scripts\validate_multi_detail_region_compare.py --input <pilot-manifest.json> --output build\multi-detail-pilot
```

Use `--collect-only` when the manifest points to existing compare run
directories instead of raw before/after source paths.

## Manifest Shape

```json
{
  "runs": [
    {
      "case_id": "case-001",
      "output_dir": "D:/path/to/existing/run",
      "expected_region_count": 12,
      "expected_match_count": 6,
      "review_evidence": {
        "reviewed_region_matches": 6,
        "correct_region_matches": 6,
        "global_false_positive_count": 20,
        "region_local_false_positive_count": 6
      },
      "screenshots": [
        "D:/path/to/case-001-before-after.png"
      ]
    }
  ],
  "pairs": [
    {
      "case_id": "case-002",
      "source_a": "D:/path/to/before",
      "source_b": "D:/path/to/after",
      "recursive": false,
      "expected_region_count": 10,
      "expected_match_count": 5
    }
  ]
}
```

## Acceptance Gates

| Gate | Required |
| --- | --- |
| Region detection | At least 80 percent of expected regions detected |
| Whole-modelspace fallback | Below 10 percent |
| User-approved match accuracy | At least 95 percent |
| False positive reduction | At least 50 percent versus global compare |
| Screenshot evidence | At least three existing viewer screenshot files |

## Current Result

No real 10-20 pair pilot set has been supplied yet, so R9 acceptance is not
evaluable. R10 default enablement must remain blocked until the generated pilot
summary reports all acceptance rows as `passed`.

## R10 Enablement And Rollback

Default region-local primary compare is guarded by pilot evidence.

- `DRAWING_COMPARE_REGION_LOCAL_DEFAULT=pilot_passed` allows default enablement
  only when `DRAWING_COMPARE_REGION_PILOT_SUMMARY` points to a pilot summary
  whose `overall_status` and all acceptance rows are `passed`.
- `DRAWING_COMPARE_REGION_LOCAL_DEFAULT=off` rolls the default path back to
  sidecar/review-only behavior.
- `DRAWING_COMPARE_AUTO_REGION_COMPARE=1` remains an explicit operator opt-in
  for diagnostic or controlled runs.

Even after pilot pass, the pipeline keeps single-detail drawings on global
compare and keeps ambiguous, unmatched, or whole-modelspace fallback cases
review-gated. Region-local default execution is only allowed for high-confidence
multi-detail detections with approved one-to-one region matches.
