# ADR-004 Phase 0-B Baseline Corpus Plan

Date: 2026-06-02

## Scope

This plan prepares the version-stratified baseline corpus pack for ADR-004.
It does not implement a native DWG parser, does not add converter invocation,
and does not claim AC1032 native support. AC1018+ DWG remains on the
user-provided converted-DXF fallback workflow until later gates are satisfied.

## Source Evidence

- Inventory source: `out/adr004_phase0_corpus_inventory.json`
- Corpus report: `docs/collab/AC1032_NATIVE_PHASE0_CORPUS_REPORT.md`
- Sampled DWG count: 79
- Version distribution: AC1018=7, AC1021=4, AC1024=54, AC1027=3, AC1032=11
- Baseline-ready root count: 1
- Ready root: `D:\도면 비교`
- Repo `tests` root: no tracked DWG samples

## Version Candidate Matrix

| Version | Candidate status | Selected DWG candidate(s) | Pair confidence | Converted-DXF baseline | Phase 0-B action |
| --- | --- | --- | --- | --- | --- |
| AC1018 | conversion_required | `D:\서울대에코폼PSRC관련 자료\01.도면 (건축 & 구조)\구조\150402 구조평면도(지하층).dwg` | single representative; no revision pair found in inventory | missing | Request a matching revision DWG if available, plus converted DXF before/after baseline. |
| AC1021 | conversion_required | `D:\서울대에코폼PSRC관련 자료\01.도면 (건축 & 구조)\구조\150402 구조평면도(지상층).dwg` | single representative; no revision pair found in inventory | missing | Request a matching revision DWG if available, plus converted DXF before/after baseline. |
| AC1024 | conversion_required | `D:\04. 작성도면\230203_P5+P6 복합동 HMB+PC보 일람표.dwg`; `D:\04. 작성도면\230203_P5+P6 복합동 HMB+PC보 일람표_Rev.01_skY.dwg` | likely revision pair by filename | missing | Request converted DXF pair under a registered before/after layout. |
| AC1027 | conversion_required | `D:\04. 작성도면\231020_P5 복합동_구조평면도(10.20  발송용 for 삼우 AFC R1.0)_REV1.dwg`; `D:\04. 작성도면\231020_P5 복합동_구조평면도(10.20 발송용 for 삼우 AFC R1.0)_REV3.dwg` | likely revision pair by filename and issue date | missing | Request converted DXF pair under a registered before/after layout. |
| AC1032 | baseline_ready | `D:\도면 비교\240111_P5 복합동_PSRC,HMB 상세도.dwg`; `D:\도면 비교\240111_P5 복합동_PSRC,HMB 상세도_r1.dwg` | confirmed revision pair | ready via `dxf_registered/before` and `dxf_registered/after` | Use as the first baseline-ready AC1032 pack; keep native claim blocked. |

## `D:\04. 작성도면` Full Scan Result

The Phase 0-A run capped this root at 50 samples. Phase 0-B reran the root with
`--max-dwg-samples 5000` and wrote `out/adr004_phase0b_d04_full_inventory.json`.

| Field | Value |
| --- | ---: |
| DWG files sampled | 302 |
| Unsupported DWG files | 302 |
| AC1021 | 1 |
| AC1024 | 236 |
| AC1027 | 34 |
| AC1032 | 26 |
| Corrupted / unreadable | 5 |
| Converted-DXF fallback-ready roots | 0 |

This confirms that `D:\04. 작성도면` is a strong AC1024/AC1027 source, but not a
baseline-ready source until converted DXF before/after pairs are supplied.

## Captured Baseline Compare Summary

Baseline-ready candidate: AC1032, `D:\도면 비교`.

| Field | Value |
| --- | --- |
| Summary file | `D:\도면 비교\codex_validation_20260601_1930\dxf_registered_utf8\direct_compare_summary.json` |
| Status | completed |
| Source A | `D:\도면 비교\dxf_registered\before` |
| Source B | `D:\도면 비교\dxf_registered\after` |
| Completed pairs | 1 |
| Failed pairs | 0 |
| Total changes | 33 |
| Review zone count | 1 |
| Elapsed | 83.092 s |

The corresponding original-DWG direct summary remains a failure/unsupported
path and is not evidence of native AC1032 support.

## User Conversion Request List

Use `dxf_registered/before` and `dxf_registered/after` as the preferred layout
for compare baselines. Keep the original DWGs in place so provenance can be
recorded next to the effective converted DXF inputs.

Detailed request packet:

- `docs/collab/ADR004_PHASE0B_DXF_CONVERSION_REQUEST.md`
- `out/adr004_phase0b_dxf_conversion_request_manifest.json`

| Version | Request | Suggested layout |
| --- | --- | --- |
| AC1018 | Provide a matching before/after revision pair for `150402 구조평면도(지하층).dwg`, or identify the corresponding revision file if it already exists. Convert both files to DXF. | `<AC1018 work folder>\dxf_registered\before\*.dxf` and `<AC1018 work folder>\dxf_registered\after\*.dxf` |
| AC1021 | Provide a matching before/after revision pair for `150402 구조평면도(지상층).dwg`, or identify the corresponding revision file if it already exists. Convert both files to DXF. | `<AC1021 work folder>\dxf_registered\before\*.dxf` and `<AC1021 work folder>\dxf_registered\after\*.dxf` |
| AC1024 | Convert the likely revision pair `230203_P5+P6 복합동 HMB+PC보 일람표.dwg` and `230203_P5+P6 복합동 HMB+PC보 일람표_Rev.01_skY.dwg` to DXF. | `D:\04. 작성도면\dxf_registered\before\*.dxf` and `D:\04. 작성도면\dxf_registered\after\*.dxf` |
| AC1027 | Convert `231020_P5 복합동_구조평면도(10.20  발송용 for 삼우 AFC R1.0)_REV1.dwg` and `231020_P5 복합동_구조평면도(10.20 발송용 for 삼우 AFC R1.0)_REV3.dwg` to DXF. | `D:\04. 작성도면\dxf_registered\before\*.dxf` and `D:\04. 작성도면\dxf_registered\after\*.dxf` |

## Full/Stratified Scan Follow-up

Completed for `D:\04. 작성도면` in Phase 0-B using `--max-dwg-samples 5000`.
Remaining follow-up is not discovery; it is converted-DXF baseline creation for
the selected AC1024 and AC1027 pairs.

## Phase 0-C Entry Conditions

Phase 0-C should not begin native-reader candidate evaluation until these inputs
exist:

1. At least one confirmed before/after DWG pair for each target version:
   AC1018, AC1021, AC1024, AC1027, and AC1032.
2. Matching user-provided converted DXF before/after files for every selected
   pair.
3. A captured converted-DXF compare summary for every selected pair.
4. Metrics defined against those summaries: compare recall, false-positive
   delta, entity coverage, unsupported entity count, runtime, and memory.
5. Clean-room parser/provenance approval remains recorded before any native
   implementation work starts.
