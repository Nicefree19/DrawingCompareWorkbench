# ADR-004 Phase 0 DWG Corpus Report

- Generated: `2026-06-02T00:30:20.709871`
- Schema version: `2`
- DWG sample limit per root: `50`
- Unique sampled DWG files: `79`
- Unsupported sampled DWG files: `79`
- Converted-DXF fallback-ready roots: `1`

## Guardrails

- This report is corpus/readiness inventory only.
- It does not implement a native DWG parser.
- AC1032 native support remains unclaimed; AC1018+ DWG still uses user-provided converted DXF.
- No ODA/GPL/AGPL converter or library path is introduced by this Phase 0 report.

## Root Summary

| Root | Exists | DWG sampled | Versions | Unsupported | Fallback ready | Fallback kind | Fallback counts | Gap |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `D:\00.Work_AI_Tool\DrawingCompareWorkbench` | Yes | 2 | AC1032=2 | 2 | No | - | - | missing converted-DXF baseline |
| `D:\00.Work_AI_Tool\DrawingCompareWorkbench\tests` | Yes | 0 | - | 0 | No | - | - | no DWG samples |
| `D:\도면 비교` | Yes | 2 | AC1032=2 | 2 | Yes | dxf_registered/before_after_dirs | after_dxf_count=1, before_dxf_count=1 | - |
| `D:\00.Work_AI_Tool\07.Dwg_diff\도면비교` | Yes | 4 | AC1024=1, AC1032=3 | 4 | No | - | - | missing converted-DXF baseline |
| `D:\04. 작성도면` | Yes | 50+ | AC1024=47, AC1027=1, AC1032=2 | 50 | No | - | - | sample limit reached, missing converted-DXF baseline |
| `D:\241217_CUB동 PSRC+HMB+PC 견적용 도면` | Yes | 6 | AC1024=2, AC1027=2, AC1032=2 | 6 | No | - | - | missing converted-DXF baseline |
| `D:\P5복합동 골조변경에 따른 CASE 관련 자료` | Yes | 2 | AC1024=2 | 2 | No | - | - | missing converted-DXF baseline |
| `D:\복합동 2차 용역 관련 자료` | Yes | 2 | AC1024=2 | 2 | No | - | - | missing converted-DXF baseline |
| `D:\서울대에코폼PSRC관련 자료` | Yes | 11 | AC1018=7, AC1021=4 | 11 | No | - | - | missing converted-DXF baseline |

## Version Distribution

| Version | Count | Phase 0 target |
| --- | --- | --- |
| AC1018 | 7 | Yes |
| AC1021 | 4 | Yes |
| AC1024 | 54 | Yes |
| AC1027 | 3 | Yes |
| AC1032 | 11 | Yes |

## Converted-DXF Fallback Readiness

| Root | Ready | Reason | Effective before | Effective after | Top candidate |
| --- | --- | --- | --- | --- | --- |
| `D:\00.Work_AI_Tool\DrawingCompareWorkbench` | No | - | `D:\00.Work_AI_Tool\DrawingCompareWorkbench` | `D:\00.Work_AI_Tool\DrawingCompareWorkbench` | - |
| `D:\도면 비교` | Yes | unsupported_dwg_folder_with_converted_dxf_dirs | `D:\도면 비교\dxf_registered\before` | `D:\도면 비교\dxf_registered\after` | dxf_registered/before_after_dirs score=9001 |
| `D:\00.Work_AI_Tool\07.Dwg_diff\도면비교` | No | - | `D:\00.Work_AI_Tool\07.Dwg_diff\도면비교` | `D:\00.Work_AI_Tool\07.Dwg_diff\도면비교` | - |
| `D:\04. 작성도면` | No | - | `D:\04. 작성도면` | `D:\04. 작성도면` | - |
| `D:\241217_CUB동 PSRC+HMB+PC 견적용 도면` | No | - | `D:\241217_CUB동 PSRC+HMB+PC 견적용 도면` | `D:\241217_CUB동 PSRC+HMB+PC 견적용 도면` | - |
| `D:\P5복합동 골조변경에 따른 CASE 관련 자료` | No | - | `D:\P5복합동 골조변경에 따른 CASE 관련 자료` | `D:\P5복합동 골조변경에 따른 CASE 관련 자료` | - |
| `D:\복합동 2차 용역 관련 자료` | No | - | `D:\복합동 2차 용역 관련 자료` | `D:\복합동 2차 용역 관련 자료` | - |
| `D:\서울대에코폼PSRC관련 자료` | No | - | `D:\서울대에코폼PSRC관련 자료` | `D:\서울대에코폼PSRC관련 자료` | - |

## Unsupported DWG Sample Summary

| Path | Version/code | Release | Error |
| --- | --- | --- | --- |
| `D:\00.Work_AI_Tool\DrawingCompareWorkbench\build\tmp-structural-probe\blocked_ac1032_probe.dwg` | AC1032 | AutoCAD 2018+ | - |
| `D:\00.Work_AI_Tool\DrawingCompareWorkbench\build\structural-fixture-evaluation\unsupported_dwg_fail_closed\inputs\blocked_ac1032.dwg` | AC1032 | AutoCAD 2018+ | - |
| `D:\도면 비교\240111_P5 복합동_PSRC,HMB 상세도.dwg` | AC1032 | AutoCAD 2018+ | - |
| `D:\도면 비교\240111_P5 복합동_PSRC,HMB 상세도_r1.dwg` | AC1032 | AutoCAD 2018+ | - |
| `D:\00.Work_AI_Tool\07.Dwg_diff\도면비교\1.dwg` | AC1032 | AutoCAD 2018+ | - |
| `D:\00.Work_AI_Tool\07.Dwg_diff\도면비교\2.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\00.Work_AI_Tool\07.Dwg_diff\도면비교\250217_P3 복합동 기준_PLEG 단면 설계 부재일람표.dwg` | AC1032 | AutoCAD 2018+ | - |
| `D:\00.Work_AI_Tool\07.Dwg_diff\도면비교\250219_P3 복합동 기준_PLEG 단면 설계 부재일람표및 상세도.dwg` | AC1032 | AutoCAD 2018+ | - |
| `D:\04. 작성도면\(230630_SEN)_P5 복합동_HMB+PC보 일람표_검토중.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\(지수판검토, PSRC기둥+PTW벽체, 230830) P5 복합동 (1,2층 구조평면도).dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\(지수판검토, PSRC기둥+PTW벽체, 230830) P5 복합동 (1,2층 구조평면도)_센구조회신.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\(코어위치 이동 반영)[구조기호 변경 체크용] 251124_P5 복합동_구조평면도_센수정_전층(참고용).dwg` | AC1032 | AutoCAD 2018+ | - |
| `D:\04. 작성도면\(코어위치 이동 반영)[구조기호 변경 체크용] 251124_P5 복합동_구조평면도_센수정_주심도&1F.dwg` | AC1032 | AutoCAD 2018+ | - |
| `D:\04. 작성도면\20231024_[P5복합동] 구조평면도_REV19_센구조 comment.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\20231024_[P5복합동] 구조평면도_REV19_센구조 comment_REV1.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\220907_P3 복합동 모듈 F-PSRC HMB Mock-Up 도면_BASE 앵커 타입 변경.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230117_T.FAB 복합동_구조계획도면_SEN.dwg` | AC1027 | AutoCAD 2013/2014/2015/2016/2017 | - |
| `D:\04. 작성도면\230120_T.FAB 복합동_구조계획도면_SEN.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230202_P5+P6 복합동 PSRC 일람표_배근작업중.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230203_P5+P6 복합동 HMB+PC보 일람표.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230203_P5+P6 복합동 HMB+PC보 일람표_Rev.01_skY.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230214_P5+P6 복합동 7F 평면도 작업.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230215_P5+P6 복합동 7F 구조평면도.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230215_P5+P6 복합동 PC보+HMB 일람표.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230215_P5+P6 복합동 구조평면도.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230215_구조일람표_P5+P6 복합동.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230221_HMB 상세리스트 & 참고도면.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230403_F-PSRC 주각부 상세도(참고도면).dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230407_P5 복합동 구조평면도_취합완료_skY_검토전_rev.01(5,6F수정).dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230417_P5 복합동 수조 SLAB 설치 상세도.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230522_P5 복합동 HMB+PC 일람표 양식.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230526_P56 복합동 구조평면도(센작성).dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230526_P56 복합동 구조평면도_REV1.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230529_P56 복합동 구조평면도_REV2.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230530_P5 복합동 HMB 일람표.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230530_P5 복합동 PSRC , HMB 상세도_작성중.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230612_P5 복합동_HMB 단면 및 배근 정보.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230612_P5 복합동_HMB 단면 정보.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230612_P5 복합동_PSRC,HMB 상세도(작업중) (2).dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230612_P5 복합동_PSRC,HMB 상세도(작업중).dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230623_P5 복합동_구조평면도_[FIZ실 우선 송부].dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230626_P5 복합동_PSRC 절주 도면.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230626_P5 복합동_PSRC,HMB 상세도(작업중).dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230628_P5 복합동 HMB 단부 보강 상세 실시 설계 단면.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230629_P5 복합동_PSRC,HMB 상세도_작성중.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230629_P5 복합동_PSRC,HMB 상세도_작성중_recover.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230630_P5 복합동_PSRC,HMB 상세도.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230707_P5 복합동 HMB 단부 보강 상세 실시 설계 단면.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230710_P5 복합동_수조내부 PSRC+PC보 상세.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |
| `D:\04. 작성도면\230714_P5 복합동 HMB 단부 보강 상세 실시 설계 단면_REV1.dwg` | AC1024 | AutoCAD 2010/2011/2012 | - |

> Unsupported sample list truncated to 50 rows.

## Corpus Gaps

| Scope | Root | Gap | Detail |
| --- | --- | --- | --- |
| root | `D:\00.Work_AI_Tool\DrawingCompareWorkbench` | unsupported_without_converted_dxf_baseline | unsupported_count=2; versions=AC1032=2 |
| root | `D:\00.Work_AI_Tool\DrawingCompareWorkbench\tests` | no_dwg_samples | - |
| root | `D:\00.Work_AI_Tool\07.Dwg_diff\도면비교` | unsupported_without_converted_dxf_baseline | unsupported_count=4; versions=AC1024=1, AC1032=3 |
| root | `D:\04. 작성도면` | sample_limit_reached | sample_limit=50 |
| root | `D:\04. 작성도면` | unsupported_without_converted_dxf_baseline | unsupported_count=50; versions=AC1024=47, AC1027=1, AC1032=2 |
| root | `D:\241217_CUB동 PSRC+HMB+PC 견적용 도면` | unsupported_without_converted_dxf_baseline | unsupported_count=6; versions=AC1024=2, AC1027=2, AC1032=2 |
| root | `D:\P5복합동 골조변경에 따른 CASE 관련 자료` | unsupported_without_converted_dxf_baseline | unsupported_count=2; versions=AC1024=2 |
| root | `D:\복합동 2차 용역 관련 자료` | unsupported_without_converted_dxf_baseline | unsupported_count=2; versions=AC1024=2 |
| root | `D:\서울대에코폼PSRC관련 자료` | unsupported_without_converted_dxf_baseline | unsupported_count=11; versions=AC1018=7, AC1021=4 |

## Next Phase 0-B Priorities

1. Collect version-stratified DWG plus converted-DXF pairs for AC1018, AC1021, AC1024, AC1027, and AC1032.
2. Capture converted-DXF baseline compare summaries for each sample pair before evaluating any native reader candidate.
3. Define recall, false-positive delta, entity coverage, runtime, and memory thresholds against the converted-DXF baseline.
4. Keep native DWG support claims blocked until version-specific corpus and clean-room parser gates are satisfied.
