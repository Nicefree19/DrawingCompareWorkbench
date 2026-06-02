# ADR-004 Phase 0-B DXF Conversion Request

Date: 2026-06-02

## Scope

This is a user conversion request packet for Phase 0-B baseline creation. It
does not invoke a converter, does not add converter automation, and does not
claim native support for AC1024, AC1027, or AC1032.

## Existing DXF Check

No same-stem DXF files were found for the selected AC1024/AC1027 candidates.
No `dxf_registered/before` and `dxf_registered/after` fallback layout exists
under `D:\04. 작성도면` yet.

## Requested Baseline Layout

Prefer a dedicated folder so these baselines do not mix with active drawing
work:

```text
D:\04. 작성도면\adr004_phase0b_converted_dxf\
  ac1024\
    before\
      230203_P5+P6 복합동 HMB+PC보 일람표.dxf
    after\
      230203_P5+P6 복합동 HMB+PC보 일람표_Rev.01_skY.dxf
  ac1027\
    before\
      231020_P5 복합동_구조평면도(10.20  발송용 for 삼우 AFC R1.0)_REV1.dxf
    after\
      231020_P5 복합동_구조평면도(10.20 발송용 for 삼우 AFC R1.0)_REV3.dxf
```

The current fallback resolver recognizes `dxf_registered/before` and
`dxf_registered/after` at a selected work root. The dedicated layout above is a
request packet layout; after conversion, either run comparison directly on those
before/after DXF folders or move/copy the selected pair into a
`dxf_registered/before` and `dxf_registered/after` layout for fallback testing.

## AC1024 Request

| Role | DWG source | Size bytes | Same-stem DXF exists |
| --- | --- | ---: | --- |
| before | `D:\04. 작성도면\230203_P5+P6 복합동 HMB+PC보 일람표.dwg` | 2722504 | no |
| after | `D:\04. 작성도면\230203_P5+P6 복합동 HMB+PC보 일람표_Rev.01_skY.dwg` | 2195992 | no |

Requested output:

| Role | DXF target |
| --- | --- |
| before | `D:\04. 작성도면\adr004_phase0b_converted_dxf\ac1024\before\230203_P5+P6 복합동 HMB+PC보 일람표.dxf` |
| after | `D:\04. 작성도면\adr004_phase0b_converted_dxf\ac1024\after\230203_P5+P6 복합동 HMB+PC보 일람표_Rev.01_skY.dxf` |

## AC1027 Request

| Role | DWG source | Size bytes | Same-stem DXF exists |
| --- | --- | ---: | --- |
| before | `D:\04. 작성도면\231020_P5 복합동_구조평면도(10.20  발송용 for 삼우 AFC R1.0)_REV1.dwg` | 14544187 | no |
| after | `D:\04. 작성도면\231020_P5 복합동_구조평면도(10.20 발송용 for 삼우 AFC R1.0)_REV3.dwg` | 15595171 | no |

Requested output:

| Role | DXF target |
| --- | --- |
| before | `D:\04. 작성도면\adr004_phase0b_converted_dxf\ac1027\before\231020_P5 복합동_구조평면도(10.20  발송용 for 삼우 AFC R1.0)_REV1.dxf` |
| after | `D:\04. 작성도면\adr004_phase0b_converted_dxf\ac1027\after\231020_P5 복합동_구조평면도(10.20 발송용 for 삼우 AFC R1.0)_REV3.dxf` |

## After Conversion

Once the DXFs exist, run direct DXF compare for each version and capture:

1. `direct_compare_summary.json`
2. completed/failed pair counts
3. total change count
4. review zone count
5. elapsed time
6. output path provenance

Those summaries become the Phase 0-C baseline inputs. They are not native DWG
support evidence.

