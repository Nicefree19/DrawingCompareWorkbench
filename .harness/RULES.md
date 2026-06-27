# RULES — 핵심 제약 (경량화)

> ⚠️ 이 목표(폴더-DWG 변환 수정 + 실-ODA 증명)에 직결되는 조항만.

## 절대 규칙 (위배 시 작업 중단)
- **변환 재구현 금지**: 폴더 내 DWG 변환은 기존 `auto_convert_unsupported_dwg`(per-file) 그대로 호출. 새 변환 로직 0.
- **최소 blast-radius**: 공유 파이프라인/GUI 폴더 경로를 불필요하게 건드리지 않는다. 가능하면 러너-레벨 수정. 파이프라인 수정이 불가피하면 기존 폴더-compare 테스트 전량 보존.
- **실증명 우선(mock 보존)**: mock 단위테스트는 유지하되, **실 AC1032 DWG fixture로 비-skip 로컬 실행**해 end-to-end 증명. skip-only로 "통과" 주장 금지.
- **단일 DWG·DXF 비퇴행**: 현재 작동하는 단일 DWG 쌍·모든 DXF 경로 동작 불변.
- **정책 준수**: DWG "완전지원" 미주장, ODA "필수" 미표기, `cad_policy_gate` 그린.

## 설계/코드 제약
- 경량: 폴더 입력 분기서 per-file 변환 + 기존 파이프라인 호출. 무거운 customer-evidence 의존 금지.
- 커밋 DWG fixture는 AC1032(native 미지원→변환 강제), 작게(<100KB/파일). 생성 절차 기록(재현성).
- e2e는 `@skipif(not installed)` + 명시적 skip 사유. per-PR 목록 추가(로컬 실행 증거 STATUS 기록).
- 한 반복 = 한 단계.

## 우선순위 (충돌 시)
1. 정직성(실증명·재구현 안 함) > 2. 비퇴행(공유 경로·단일 DWG) > 3. 정책 준수 > 4. 결정성 > 5. 단순성

## 검증 연결
- 변환 수정·실증명 = T-DF1/T-DF3. 비퇴행 = T-DF4/T-DF5. dogfood·정책·골든floor = T-DF5.
