# RULES — 핵심 제약 (경량화)

> ⚠️ 이 목표(러너 DWG 온램프 + 폴더 배치)에 직결되는 조항만.

## 절대 규칙 (위배 시 작업 중단)
- **compare/변환 재구현 금지**: 폴더 배치 = `FolderComparePipeline` 폴더 스캔 / `BatchCompareJob` 그대로. DWG 변환 = 기존 `dwg_converter`/auto_convert 경로 그대로. 러너는 **입력 라우팅 + 출력 그룹핑만**. 새 비교/변환 로직 0.
- **정책 게이트 준수**: DWG "완전/네이티브 완전지원" 미주장. ODA를 "필수"로 표기 금지. `cad_policy_gate` 그린 유지(token-free).
- **침묵 다운그레이드 금지**: DWG 변환 불가/ODA 부재 시 **fail-loud**(사전변환 안내). 조용히 빈 결과/단일파일 폴백 금지 ([[oda_dual_path_slim_gap]] 교훈).
- **단일쌍 비퇴행**: PR#56 단일 DXF 쌍 동작·산출물 불변(기존 경로 무변경, 분기만 추가).
- **정답 미조작·스키마 보존**: csv 스켈레톤 = 기존 스키마·사실만(PR#56 규칙 승계).

## 설계/코드 제약
- 경량: argparse 입력 분기(파일 vs 폴더, dxf vs dwg) + 파이프라인 호출 + 포맷. 무거운 customer-evidence 게이트 의존 금지.
- 신규 테스트는 결정적·헤드리스(real ODA 불요 — 변환은 mock/배선 단언). per-PR 목록 유지(silent-inert 금지).
- 한 반복 = 한 단계.

## 우선순위 (충돌 시)
1. 정직성(재구현 안 함·침묵 다운그레이드 안 함) > 2. 정책 준수 > 3. 무마찰(실폴더/DWG 그대로) > 4. 결정성 > 5. 단순성

## 검증 연결
- 재구현 금지·라우팅 = T-PB1/T-PB2. 정책·dogfood = T-PB5. 비퇴행 = T-PB3.
