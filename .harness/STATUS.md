# STATUS — 실시간 진행 추적

> 매 반복 ⑥ 즉시 갱신. 상태: ⬜대기 / 🔄진행 / ✅완료 / 🚧블록

## 현재 포커스
- **지금 무엇을, 왜**: ✅ Phase 2 완료 — C1~C5 전부 충족. 세션 신뢰성 테스트가 per-PR 강제됨.
- **마지막 갱신**: 2026-06-26 / Phase 2 종료

## 단계 현황
| 단계 | 상태 | 결과 | 검증(T) | 메모 |
|------|------|-----------|---------|------|
| S1 결정성 검증 | ✅ | 결정적 5종 53 pass×2(9.3/5.3s)·GUI 4 pass×2(offscreen) | T4 | 전부 결정적 |
| S2 per-PR 목록 추가 (C1) | ✅ | 5 결정적 테스트를 main pytest step에 추가 | T1 | |
| S3 GUI 테스트 처리 (C3) | ✅ | **별도 offscreen step**(gating, continue-on-error 아님)로 격리 — AV 귀속 명확, silent-skip 없음 | T3 | 결정적이나 격리 |
| S4 check_ci_gate 메타-가드 (C2) | ✅ | 6 critical 테스트 파일 존재 단언 추가(기존 패턴) + 누락-시뮬 테스트 | T2 | |
| S5 통합 비퇴행 (C5) | ✅ | gate passed·기존 step(golden/diff/policy) 보존·YAML 유효 | T5 | |

## 검증 로그 (증거)
- [x] T1 워크플로 신규 테스트: 6 파일 매치(grep)
- [x] T2 메타-가드: test_policy_gate_flags_dropped_reliability_test_in_ci pass(누락→violation)
- [x] T3 GUI: 결정적(offscreen 2x) → 별도 gating step로 포함(격리·사유 주석). silent-skip 없음
- [x] T4 결정성: 결정적 5종 2회 연속 53 pass
- [x] T5: `cad_policy_gate passed`·기존 3 step 보존·YAML valid. test_cad_policy_gate 12 pass

## 블록/이슈
- 없음. 정직성: GUI 테스트는 결정적이나 AV-prone 카테고리라 **별도 step 격리**(향후 CI서 flaky 판명 시 명시 사유로 처리 — silent 아님).
