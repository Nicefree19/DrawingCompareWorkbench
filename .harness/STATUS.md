# STATUS — 실시간 진행 추적

> 매 반복 ⑥ 기록 스텝에서 즉시 갱신한다. 상태: ⬜대기 / 🔄진행 / ✅완료 / 🚧블록

## 현재 포커스
- **지금 무엇을, 왜**: 🚧 BLOCK — 안전 수정 2종으로 recall 0.714→0.786 달성·검증 완료. DoD(≥0.90) 잔여 3 FN(04/07/11)은 서로 상충하는 깊은 위험 변경 → 사람 결정 필요.
- **마지막 갱신**: 2026-06-26 / 회차 3 종료(블록 보고)

## 근인 (S1 확정)
- 기본 경로 = **canonical ComparePipeline**(legacy DxfComparator.find_near_matches 아님). `_distance_threshold_for` 미호출 확인.
- **07/10 공통**: 변경 위치를 bbox **centroid**로 보고 → truth의 entity **anchor(geometry.insert)** 와 불일치(07 tol 1mm서 5mm, 10 tol 50mm서 51.5mm).
- **10 추가**: canonical 옵션이 `search_radius_mm=near_match_radius(10mm)`만 전달, `text_near_match_radius(50mm)` 미연결 → 30mm 이동 텍스트 분할.

## 단계 현황
| 단계 | 상태 | 다음 할 일 | 검증(T) | 메모 |
|------|------|-----------|---------|------|
| S1 측정 고정+FN 덤프 | ✅ | — | T1,T2 | 근인 확정(위) |
| S2 anchor 위치 수정 | ✅ | — | T2 | _entity_anchor 도입. recall 0.714→0.786, 10 recall 회복. 07은 미해결(블록 anchor≠ATTRIB) |
| S3 text near-match 반경 연결 | 🔄 | 엔진 type별 반경 + 옵션 연결 | T3 | 10 분할 병합(pred2→1) 목적 |
| S3 text near-match 반경 연결 | ✅ | — | T3 | fixture10 pred 2→1, 완전 해결 |
| S4 fixture 07·04·11 FN | 🚧 | 사람 결정: 위험/범위 (아래) | T2,T4 | 깨끗한 수정 아님·상충 |
| S5 통합 재측정·비퇴행 | ✅(부분) | recall 0.90 제외 전부 충족 | T1,T5,T6,T7 | recall 0.786<0.90 |

## 검증 로그 (증거)
- [~] T1 recall: 최종 `AGGREGATE recall=0.786` (baseline 0.714) — DoD 0.90 **미달**
- [~] T2 fixture07: `r=0.000` — block_reference(cat=text) vs truth attrib 매처 타입거부(모델링 이슈)
- [x] T3 fixture10: 최종 `pred=1 r=1.000 fp=0` — 완전 해결
- [~] T4 fixture04·11: 04 r=0.5(과병합), 11 r=0.5(block-local 좌표누수) — 미해결
- [x] T5 noise_fp: `noise_fp=0`, precision 0.647(≥0.556) — 유지/상승
- [x] T6 comparison 유닛: at-risk 8파일+native/realset 56 serial 그린; 5 신규 회귀테스트 그린. (-n auto 14 fail은 전부 native-AV/MemoryError/타이밍 환경 artifact — serial 재현 0)
- [x] T7 policy gate: `passed` exit 0

## 블록/이슈 (🚧 사람 결정 필요)
DoD recall≥0.90엔 04/07/11 중 **2개** 수정 필요. 셋 다 깨끗한 메커니즘-수정이 아니며 방향이 상충:
- **07**(ATTRIB): 검출·위치 정확하나 엔진=block_reference / truth=attrib → 매처 타입거부. **재타이핑은 zone/overlay 소비자 오염 위험**(모델링 결정).
- **04**(LINE 과병합): 서로 다른 추가/삭제 선을 1 modified로 병합. 선 병합을 *줄이면* 텍스트 병합(S3 성과)·다른 fixture 회귀 위험.
- **11**(block-local 누수): 예측 1건 (10,0) — re-origin/expand_blocks 좌표버그(legacy_block_local 계열, 위험).
권고: 별도 신중 세션에서 1개씩(특히 11 좌표누수는 독립 버그). 무리한 동시 튜닝은 RULES(fit-to-test 금지)·dead-end 위반.
