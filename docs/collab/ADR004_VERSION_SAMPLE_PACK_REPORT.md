# ADR-004 Version Sample Pack Report

Date: 2026-06-02

## Scope

This report records a local version-stratified DWG/DXF sample pack generated
from existing CAD files on this workstation. It is validation evidence only:
product code still must not bundle or automatically invoke ODA.

## Local Converter

- Converter: `C:\Program Files\ODA\ODAFileConverter 26.10.0\ODAFileConverter.exe`
- Use: local sample generation only
- Product behavior: unchanged; DWG native support remains fail-closed unless an
  explicit user-converter path or converted DXF baseline is supplied.

## Outputs

- Sample pack: `out/adr004_version_samples_20260602_104044`
- Manifest: `out/adr004_version_samples_20260602_104044/manifest.json`
- Import smoke: `out/adr004_version_samples_20260602_104044/import_smoke_summary.json`
- Compare smoke: `out/adr004_version_samples_20260602_104044/compare_smoke_summary.json`
- Repeatable validation JSON: `out/adr004_version_samples_20260602_104044/validation_summary_v2.json`
- Repeatable validation Markdown: `out/adr004_version_samples_20260602_104044/validation_summary_v2.md`

The pack contains 10 DWG copies and 10 generated DXFs across AC1018, AC1021,
AC1024, AC1027, and AC1032.

## Version Mapping Used

| DWG code | DXF output option | DXF `$ACADVER` verified |
| --- | --- | --- |
| AC1018 | ACAD2004 | AC1018 |
| AC1021 | ACAD2007 | AC1021 |
| AC1024 | ACAD2010 | AC1024 |
| AC1027 | ACAD2013 | AC1027 |
| AC1032 | ACAD2018 | AC1032 |

## Selected Samples

| Version | Pair kind | Source |
| --- | --- | --- |
| AC1018 | duplicated import baseline | `D:\서울대에코폼PSRC관련 자료\01.도면 (건축 & 구조)\구조\S50-베이스플레이트.dwg` |
| AC1021 | duplicated import baseline | `D:\서울대에코폼PSRC관련 자료\01.도면 (건축 & 구조)\구조\150402 TSC 일람표.dwg` |
| AC1024 | likely revision pair | `D:\04. 작성도면\230203_P5+P6 복합동 HMB+PC보 일람표.dwg` -> `_Rev.01_skY.dwg` |
| AC1027 | likely revision pair, small | `D:\04. 작성도면\230908_P5 ... AFD_PSRC 관련 상세.dwg` -> `D:\04. 작성도면\230920_P5 ... 치수수정.dwg` |
| AC1032 | confirmed revision pair | `D:\도면 비교\240111_P5 복합동_PSRC,HMB 상세도.dwg` -> `_r1.dwg` |

## Import Smoke Result

Import smoke reads generated DXFs directly with raised validation limits
(`max_entities=500000`, `max_dxf_tokens=25000000`).

| Version | Result | Entity counts | Notes |
| --- | --- | ---: | --- |
| AC1018 | partial | 10233 / 10233 | Unsupported entities and unresolved XREF warnings only |
| AC1021 | partial | 3311 / 3311 | Unsupported entities and unresolved XREF warnings only |
| AC1024 | partial | 120129 / 113443 | Requires higher entity limit than default 100000 |
| AC1027 | partial | 54746 / 55234 | Importable; compare smoke timed out |
| AC1032 | timeout | - | Raw ODA DXF timed out at 240s; existing registered DXF in `D:\도면 비교\dxf_registered` remains the usable baseline |

## Compare Smoke Result

Compare smoke uses `ComparePipeline` with explicit `dwg_backend_mode="user_converter"`.

| Version | Result | Diff summary |
| --- | --- | --- |
| AC1018 | partial | added 0, removed 0, modified 0, unchanged 10233 |
| AC1021 | partial | added 0, removed 0, modified 0, unchanged 3311 |
| AC1024 | failed | Default entity limit exceeded before compare |
| AC1027 | timeout | Compare exceeded 180s |
| AC1032 | timeout | Raw ODA DXF exceeded 180s; registered DXF baseline previously compared successfully |

## Repeatable Validation Runner

Added `scripts/validate_adr004_version_sample_pack.py` so the pack can be
checked repeatedly without invoking any DWG converter. The runner validates
manifest structure, DWG headers, DXF `$ACADVER`, import smoke, and compare smoke
through subprocess workers with timeout capture.

Command used:

```powershell
python scripts\validate_adr004_version_sample_pack.py out\adr004_version_samples_20260602_104044 --json-report out\adr004_version_samples_20260602_104044\validation_summary_v2.json --md-report out\adr004_version_samples_20260602_104044\validation_summary_v2.md
```

Result:

| Area | Result |
| --- | --- |
| Overall | partial |
| Manifest/header validation | 0 errors, 0 `$ACADVER` mismatches |
| Import smoke | 8 partial, 2 timeout |
| Compare smoke | 2 partial, 2 skipped by size, 1 timeout |

Per-version summary:

| Version | Header | Import | Compare |
| --- | --- | --- | --- |
| AC1018 | ok | partial 10233 / 10233 entities | partial, 0 changes |
| AC1021 | ok | partial 3311 / 3311 entities | partial, 0 changes |
| AC1024 | ok | partial 120129 / 113443 entities | timeout at 180s |
| AC1027 | ok | partial 54746 / 55234 entities | skipped by 50 MiB compare-size envelope |
| AC1032 | ok | timeout / timeout | skipped by 50 MiB compare-size envelope |

## Conclusions

1. Version-stratified local sample generation is now complete for AC1018,
   AC1021, AC1024, AC1027, and AC1032.
2. Generated DXF headers match the intended version families.
3. AC1018, AC1021, AC1024, and AC1027 generated DXFs are importable with
   validation limits.
4. AC1024 and larger samples show that converted-DXF baselines need explicit
   high-entity validation settings before compare-level metrics are meaningful.
5. AC1032 raw ODA conversion is not the best baseline for the current importer;
   the existing `D:\도면 비교\dxf_registered` prepared DXFs remain the usable
   AC1032 compare baseline.

6. The repeatable runner confirms the pack is structurally valid, but compare
   recall gates need smaller AC1024+ confirmed pairs or a dedicated large-CAD
   compare budget.

## Next Work

Use this pack for import coverage gates first. For compare-recall gates, either
choose smaller confirmed revision pairs or run compare with a dedicated large
CAD budget and timeout envelope.
