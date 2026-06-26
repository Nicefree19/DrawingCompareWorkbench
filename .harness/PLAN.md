# PLAN — 이행 로드맵

> DoD까지 단계별. 작은 단위·의존성 표기. 한 반복 = 한 단계. 시간추정 대신 복잡도.
> 순서 원칙: **clean(비-모놀리스) 먼저, 모놀리스-제약(라인-실링) 나중.**

## 단계 (순서대로)

### S1. release env-check를 진짜 게이트로 (D1)  (복잡도: 낮)
- 무엇을: `release_environment_check.py`에 `--strict`(또는 항상) 모드 — REQUIRED(`runtime_modules`) 중 미가용이면 nonzero 반환. `release_drawing_compare_workbench.py`가 PyInstaller 단계 **전에** 이 게이트를 호출, 실패 시 빌드 halt.
- 산출: 게이트 함수 + 릴리스 스크립트 배선. 단위테스트(누락 시뮬→nonzero).
- 검증: T1
- 의존: 없음

### S2. bundle-presence 테스트 + spec assets 명시 (D2)  (복잡도: 낮)
- 무엇을: `test_build_spec_bundling.py`에 datas 존재 단언 추가 — spec의 `datas`가 `src`(QML assets 포함)·`scripts`(render_viewer_package_subprocess.py)를 싣는지 텍스트/경로 레벨로. 누락 시 spec에 명시적 assets datas 추가.
- 산출: 테스트 케이스(+spec edit 필요시).
- 검증: T2
- 의존: 없음 (S1과 독립)

### S3. zone-failure 표면화 (D3)  (복잡도: 높 — 모놀리스 라인-실링)
- 무엇을: `_on_full_zone_tree_overlay_failed_v2`/`_plan_failed_v2`(L7311/7611)·`_on_zone_crop_render_error_v2`(L11223)가 status(`lbl_status_v2`) 또는 FailureBadge에 신호. **라인-실링 비증가**: 기존 log-only 라인을 status-갱신으로 대체하거나 satellite 헬퍼로 추출.
- 산출: 표면화 배선 + offscreen 테스트.
- 검증: T3, T5(라인-실링)
- 의존: 없음 (단 라인-실링 전략 선결)

### S4. zero-change 명시 (D4)  (복잡도: 중 — 모놀리스 라인-실링)
- 무엇을: 변경 0건 시 요약/상태에 "변경 없음 · 파일 일치" 명시. 가능하면 순수 포맷 헬퍼(`workbench_summary_format.py` satellite)로 — 모놀리스 라인 비증가.
- 산출: 명시 메시지 + 테스트.
- 검증: T4, T5
- 의존: 없음

### S5. 통합 비퇴행 게이트 (D5)  (복잡도: 중)
- 무엇을: at-risk comparison 유닛 + 신규 테스트 + cad_policy_gate(라인-실링 포함) 동시 그린.
- 검증: T5, T6
- 의존: S1~S4 →

## 리스크 & 대응
| 리스크 | 영향 | 대응 |
|--------|------|------|
| D3/D4가 모놀리스 라인 증가 → 라인-실링 게이트 trip | 상 | 기존 라인 대체 또는 satellite 추출(net-neutral). 매 반복 `cad_policy_gate` 확인. 불가 시 해당 타겟 보류·보고. |
| D3 GUI 테스트 flaky(PySide6 AV) | 중 | offscreen+콜백 직접 호출(이벤트루프 없이), 시그널 단언만. |
| 에이전트 주장 추가 과장 | 중 | 각 단계 ①에서 verify-then-fix-**or-drop**(R1/R2처럼). |
| D1 게이트가 dev 환경서 오작동(필수모듈 정의 과넓음) | 중 | runtime_modules 그대로 사용(이미 REQUIRED 정의). dev엔 다 있으니 그린. |

## 변경 이력
- 2026-06-26 생성: 3각도 감사 + 재검증 기반. R1/R2/G2/G4 드롭. D1/D2 clean·D3/D4 모놀리스-제약.
