# STATUS — 실시간 진행 추적

> 매 반복 ⑥ 즉시 갱신. 상태: ⬜대기 / 🔄진행 / ✅완료 / 🚧블록

## 현재 포커스
- **지금 무엇을, 왜**: S1~S5 전부 완료. 클로저 게이트 → 종료 판정
- **마지막 갱신**: 2026-06-27 / Phase 2 종료(5회차)
- **HARNESS_GATE**: tests=PASS · DoD 5/5 · 검출 무변경·only-07·noise_fp=0 · 측정 recall 0.786→0.857

## 단계 현황
| 단계 | 상태 | 다음 할 일 | 검증(T) | 메모 |
|------|------|-----------|---------|------|
| S1 honesty linchpin + 필터 위치 + red | ✅ | (완료) | 실측 | **엔진 07 실검출 입증**(pred block_reference @(500,400) dist0.000, blocked_by_type). 필터=`accuracy_metrics.py:288`. layer 비차단(require_layer_match 기본F) |
| S2 synonym 수정 (SC1) | ✅ | (완료) | T-SC1✅ | `_entity_types_compatible` + `accuracy_metrics.py:288` 교체. 07 tp0→1(probe 실측) |
| S3 단위테스트 (SC4) | ✅ | (완료) | T-SC1✅,T-SC4✅ | 20 passed(기존16+synonym4). family만 호환·거리게이트 병존 단언 |
| S4 전체 골든 measure 전/후 (SC2/SC3) | ✅ | (완료) | T-SC2✅,T-SC3✅ | 07 r0→1. AGG tp11→12·fp6→5·fn3→2·r0.786→0.857·noise0. delta=정확히07만(구조+실측) |
| S5 비퇴행+dogfood+floor (SC5) | ✅ | (완료) | T-SC5✅ | per-PR L77 추가·floor exit0·gate·dogfood·기존+importer 테스트 통과. floor 상향 보류(브리틀 회피) |

## 검증 로그 (증거)
- [x] T-SC1 synonym 매칭: `_entity_types_compatible` family만 호환(block_reference↔attrib True, line↔attrib False, text↔attrib False), 거리게이트 병존. 단위테스트 PASS.
- [x] T-SC2 07 recall 0→1: 골든 measure `07 ... tp=1 fp=0 fn=0 r=1.000`(이전 r=0). AGGREGATE recall 0.786→**0.857**.
- [x] T-SC3 only-07 + noise: 골든 truth 중 07만 entity_type=ATTRIB(블록family) → 구조적 only-07. 실측 delta=정확히 07(tp11→12·fp6→5·fn3→2), 14쌍 불변. **noise_fp=0**.
- [x] T-SC4 결정적: `pytest test_accuracy_metrics.py` **20 passed ×2회**(기존16+synonym4).
- [x] T-SC5 dogfood+정책+floor: black/isort clean·`CAD policy gate passed`·골든 floor exit0(r0.857·noise0)·per-PR 추가·importer 테스트 14 passed·검출 src 무변경(채점만).

## 블록/이슈
- 없음. Phase 2 완결. **honesty linchpin 통과**(엔진 07 거리0.000 실검출 입증 후 진행). 검출 엔진 무변경 — 채점 어휘갭만 수정.

## HARNESS_GATE
- tests=PASS | closure: DoD 5/5 met · TEST 0 unmet · steps 🔄0/🚧0 · pytest 20×2 + importer14 · 검증기 무약화(신규 테스트만) · 검출 무변경·only-07·noise_fp=0 · 골든 floor exit0
