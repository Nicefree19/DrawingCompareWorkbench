# PLAN — 이행 로드맵

> 작은 단위·의존성. 한 반복 = 한 단계. verify-then-fix-or-drop.

## 단계 (순서대로)

### S1. 패킷 소스 실측 + 가이드 베이스 확정 (선결)  (복잡도: 낮)
- 무엇을: v0.9.2 패킷의 `사용가이드.md`/`스팟체크_기록양식.md`/`.bat` 전문을 읽어 버전관리 소스 베이스 확정. app-dir 구조(`DrawingCompareWorkbench.exe`+`_internal/`) 확인. 샘플 쌍(골든02) 경로 확정. `cad_policy_gate` 금지문구(패킷 텍스트 대비) 확인.
- 산출: 가이드 베이스 + 조립 항목 목록.
- 검증: 실측
- 의존: 없음

### S2. 버전관리 가이드 소스 (PK2)  (복잡도: 낮)
- 무엇을: `docs/pilot_packet/사용가이드.md` 생성 — v0.9.2 가이드 기반 + **자동 `pilot_spotcheck.md`(비교 완료 시 결과폴더에 자동 생성)+작성·반송** 섹션으로 빈 수기 양식 대체. 정직 고지 유지.
- 산출: 가이드 소스.
- 검증: T-PK2(부분)
- 의존: S1 →

### S3. 조립 스크립트 (PK1/PK3)  (복잡도: 중)
- 무엇을: `scripts/build_pilot_packet.py` — argparse(app-dir, -o, --version, --zip). app-dir 검증(exe 존재)→패킷 디렉터리: app 복사 + `.bat`(인라인, 버전) + `docs/pilot_packet/사용가이드.md` 복사 + 샘플 쌍(골든02 before/after.dxf)→`샘플도면/` + 매니페스트(버전·git sha·내용). --zip 시 zip.
- 산출: 스크립트.
- 검증: T-PK1, T-PK3
- 의존: S2 →

### S4. 결정적 테스트 (PK4)  (복잡도: 중)
- 무엇을: `test_build_pilot_packet.py` — tmp stub app-dir(가짜 exe)→`build_pilot_packet` 호출→`.bat`/가이드/`샘플도면`/`app` 존재·매니페스트 단언. black/isort clean. per-PR 추가.
- 산출: 테스트.
- 검증: T-PK4
- 의존: S3 →

### S5. vapor 리다이렉트 + gate (PK5)  (복잡도: 낮)
- 무엇을: `CUSTOMER_PILOT_*` 런북의 "미구현" 배너를 `build_pilot_packet.py` 실 producer로 리다이렉트(명령 명시). `cad_policy_gate` 그린·dogfood·per-PR 확인.
- 검증: T-PK5
- 의존: S2~S4 →

## 리스크 & 대응
| 리스크 | 영향 | 대응 |
|--------|------|------|
| exe 빌드까지 끌어들여 범위 폭발 | 상 | app-dir를 **입력**으로. 빌드는 범위 밖·문서로 안내. stub로 테스트. |
| 패킷 텍스트가 정책 게이트 위반(DWG 완전지원 등) | 중 | v0.9.2 가이드 문구 보존(이미 통과)·자동시트 섹션만 추가. S5 gate. |
| 거대 app 복사로 테스트 느림 | 중 | 테스트는 stub app-dir(가짜 exe 1개)만. 실 복사는 빌드머신서. |
| `release/` gitignore라 소스 유실 | 상 | 소스는 `docs/pilot_packet/`(추적). 패킷 산출물만 release/(ignore). |
| 샘플 쌍 라이선스/기밀 | 낮 | 합성 골든 DXF만(기밀 아님). |

## 변경 이력
- 2026-06-27 생성: 냉철 리뷰 release-distribution 0.28 후속. v0.9.2 패킷 stale(6/11)+조립 스크립트 부재 → 재현 빌더 + 자동시트 가이드. exe 빌드는 범위 밖.
