# ADR-004 Compact Compare Candidates Report

Date: 2026-06-02

## Scope

This report records the next ADR-004 baseline step after the first
version-stratified sample pack exposed large-DXF compare limits. It is local
evidence only. Product code still does not invoke ODA or claim native AC1018+
DWG support.

## Artifacts

- Candidate selection JSON: `out/adr004_compact_compare_candidate_selection.json`
- Candidate selection Markdown: `out/adr004_compact_compare_candidate_selection.md`
- Compact generated-DXF pack: `out/adr004_compact_compare_samples_20260602_131140`
- Compact validation JSON: `out/adr004_compact_compare_samples_20260602_131140/validation_summary.json`
- Compact validation Markdown: `out/adr004_compact_compare_samples_20260602_131140/validation_summary.md`
- AC1032 registered-DXF baseline: `out/adr004_ac1032_registered_baseline_20260602_132007`
- AC1032 validation JSON: `out/adr004_ac1032_registered_baseline_20260602_132007/validation_summary.json`

## Candidate Scan

Added `scripts/select_adr004_compact_compare_candidates.py` to rank likely
before/after DWG pairs by header version, filename similarity, revision/date
signals, and combined DWG size. The script does not read geometry and does not
run a converter.

| Version | Samples scanned | Candidate count |
| --- | ---: | ---: |
| AC1018 | 7 | 0 |
| AC1021 | 5 | 0 |
| AC1024 | 241 | 8 |
| AC1027 | 36 | 8 |
| AC1032 | 31 | 8 |

## Compact Validation Results

Validation command:

```powershell
python scripts\validate_adr004_version_sample_pack.py out\adr004_compact_compare_samples_20260602_131140 --json-report out\adr004_compact_compare_samples_20260602_131140\validation_summary.json --md-report out\adr004_compact_compare_samples_20260602_131140\validation_summary.md --skip-compare-over-dxf-mb 90
```

Result: `partial`

| Version | Header | Import | Compare |
| --- | --- | --- | --- |
| AC1024 | ok | partial 6590 / 6792 entities | partial; added 342, removed 140, modified 19, unchanged 6431 |
| AC1027 | ok | partial 21988 / 48727 entities | partial; added 26741, removed 2, modified 0, unchanged 21986 |
| AC1032 raw generated DXF | ok | partial 170123 / 173441 entities | timeout at 180s |

## AC1032 Registered Baseline

The raw generated AC1032 DXF pair is not an efficient compare baseline because
it expands to more than 170k imported entities per side. The existing prepared
registered-DXF baseline remains the correct AC1032 compare oracle.

Validation command:

```powershell
python scripts\validate_adr004_version_sample_pack.py out\adr004_ac1032_registered_baseline_20260602_132007 --json-report out\adr004_ac1032_registered_baseline_20260602_132007\validation_summary.json --md-report out\adr004_ac1032_registered_baseline_20260602_132007\validation_summary.md --skip-compare-over-dxf-mb 130 --compare-timeout-seconds 240
```

Result: `ok`

| Version | Header | Import | Compare |
| --- | --- | --- | --- |
| AC1032 registered DXF | ok | partial 7197 / 7195 entities | partial; added 37, removed 39, modified 207, unchanged 6951 |

## Conclusions

1. AC1024 and AC1027 now have repeatable compact compare baselines.
2. AC1032 compare baseline should use the existing prepared registered DXF, not
   raw ODA-generated DXF.
3. AC1018 and AC1021 still need real before/after revision pairs; current
   evidence is import-only duplicated baseline coverage.
4. Phase 0-C can define baseline metrics for AC1024, AC1027, and AC1032 now,
   while AC1018/AC1021 remain corpus gaps for compare-recall metrics.
