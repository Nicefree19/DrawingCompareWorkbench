# RULES — 핵심 제약 (경량화)

> ⚠️ 이 목표(골든 채점 entity_type synonym 수정)에 직결되는 조항만.

## 절대 규칙 (위배 시 작업 중단)
- **Honesty linchpin**: 엔진이 07 변경을 실제로 (500,400)에 검출함을 **S1에서 예측 덤프로 입증**한 뒤에만 진행. 검출이 없으면 synonym 매칭은 분식 → **즉시 중단**.
- **검출/매칭 엔진 무변경**: 수정은 **채점 레이어**(accuracy_metrics / measure 정규화)에만. 검출·zone·매처 로직 0 변경. truth.json 0 변경.
- **무차별 완화 금지**: entity_type 호환은 정의된 블록-속성 family 세트로 한정. 전체 필터 제거/우회 금지. 위치 거리 게이트는 그대로 병존.
- **only-07 변화**: 수정 후 전체 골든서 **07만** 개선, 다른 fixture per-pair tp/fp/fn·noise_fp 불변임을 증명.

## 설계/코드 제약
- 경량: synonym 세트 + 비교 함수 1곳. 새 채점 파이프라인 금지.
- 신규 테스트 결정적. per-PR 목록 추가(silent-inert 금지).
- floor 상향은 선택·보수적(다른 fixture 변동 대비). 측정 개선은 리포트로.
- 한 반복 = 한 단계.

## 우선순위 (충돌 시)
1. 정직성(엔진 실검출 입증·검출 무변경) > 2. only-07·noise 무회귀 > 3. 정책/dogfood > 4. 결정성 > 5. 단순성

## 검증 연결
- 검출 입증 = S1 실측. synonym·과매칭 = T-SC1/T-SC3. 비퇴행·noise = T-SC3/T-SC5.
