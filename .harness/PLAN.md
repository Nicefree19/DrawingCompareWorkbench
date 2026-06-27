# PLAN — 이행 로드맵

> 작은 단위·의존성. 한 반복 = 한 단계. verify-then-fix-or-drop.

## 단계 (순서대로)

### S1. 최소 수정 훅 + fixture 가능성 확정 (선결)  (복잡도: 낮)
- 무엇을: ① 폴더+DWG 변환을 어디서 고칠지 최소 blast-radius 훅 확정 — (a) 러너가 폴더 DWG를 사전 변환해 임시 DXF 폴더로 파이프라인 호출, vs (b) 파이프라인 폴더 경로가 per-file 변환. 공유 GUI 경로 비파괴 기준으로 택일. ② 골든 02 DXF→AC1032 DWG 변환 1회 실행해 산출 크기/헤더 확인(커밋 가능성). ③ 실 AC1032 폴더 입력이 현재 실패함을 재현(red).
- 산출: 훅 결정 메모 + 변환된 DWG 샘플 + red 재현.
- 검증: 실측(현재 실패 + 변환 산출)
- 의존: 없음

### S2. 커밋 fixture 생성 (DF2)  (복잡도: 낮)
- 무엇을: 골든 02 `before.dxf`/`after.dxf` → AC1032 DWG 변환, `tests/data/comparison/golden/dxf/02_single_modification/dwg/{before,after}.dwg` 커밋. 헤더 AC1032·크기 확인. 생성 절차를 주석/스크립트로 기록(재현성).
- 산출: 커밋된 DWG 쌍.
- 검증: T-DF2
- 의존: S1 →

### S3. 폴더-DWG 변환 수정 (DF1)  (복잡도: 중)
- 무엇을: S1 결정 훅에 per-file 변환 배선. 폴더 입력의 각 DWG를 `auto_convert_unsupported_dwg`(기존)로 DXF 변환 후 compare. 단일 파일 경로·DXF 경로 불변.
- 산출: 수정 + red→green.
- 검증: T-DF1
- 의존: S1,S2 →

### S4. 실-ODA e2e 테스트 (DF3/DF4)  (복잡도: 중)
- 무엇을: `@skipif(not installed)` e2e — 단일 DWG 쌍 + DWG 폴더 둘 다 실 `run_pilot_spotcheck`→BEAM 검출 단언. 기존 mock 단위테스트 보존. black/isort clean. per-PR 목록에 추가.
- 산출: e2e 테스트(클린).
- 검증: T-DF3, T-DF4
- 의존: S2,S3 →

### S5. 비퇴행 + 정책 + 골든 floor (DF5)  (복잡도: 낮)
- 무엇을: 골든 정확도 floor(measure_golden_accuracy_baseline)·CAD 회귀 통과 확인, `cad_policy_gate` 그린(DWG 커밋·문구), dogfood lint, per-PR 갱신 확인.
- 검증: T-DF5
- 의존: S2~S4 →

## 리스크 & 대응
| 리스크 | 영향 | 대응 |
|--------|------|------|
| 파이프라인 폴더 경로 수정이 공유 GUI 경로 파괴 | 상 | S1서 최소 훅 택일. 가능하면 러너-레벨(공유 비파괴). 파이프라인 수정 시 기존 폴더 테스트 보존. |
| 변환 재구현(중복) | 상 | 기존 `auto_convert_unsupported_dwg` per-file 호출만. 새 변환 로직 0. |
| 커밋 DWG가 비결정 바이트(ODA 타임스탬프) | 중 | 테스트는 바이트 동등 아닌 **검출 결과** 단언. fixture는 1회 생성·커밋. |
| e2e가 CI서 skip(inert) | 중 | skipif는 정직한 외부 dep 게이트(기존 골든 e2e 패턴). **로컬 ODA서 비-skip 실행**이 진짜 증거. silent 아님(skip 사유 명시). |
| DWG 커밋이 정책 게이트 위반 | 중 | 테스트 데이터 .dwg는 위반 아님. S5서 gate 확인. |
| AC1032 외 버전이라 native가 읽어 변환 미발화 | 중 | fixture를 **AC1032**로(native 미지원→변환 강제). S1서 헤더 확인. |

## 변경 이력
- 2026-06-27 생성: PR#58 폴더+DWG 실버그(에이전트 라이브 재현) 수정 + mock-only→실-ODA 증명. 회귀 우선 수정.
