# 외부 코드 리뷰 요청 — Drawing Viewer V2 / Phase B+C + Composite Beam Refinement

> 이 문서는 **외부 시니어 리뷰어(또는 GPT/Codex CLI)** 에게 그대로 복사해서 전달하는 자기완결형 프롬프트입니다.
> 저장소를 처음 본다는 가정으로 모든 맥락이 들어 있습니다.

---

## 0. 리뷰어를 위한 안내

당신은 **Python(PySide6 / Qt Quick / QML) + 한국 건설 BIM 도메인** 경험이 있는 시니어 리뷰어입니다.
이 프로젝트(`Tekla MCP` / GitHub: `Nicefree19/MGT-Tools`)는 **MIDAS Gen MGT 파일 ↔ Tekla Structures BIM ↔ DXF/PDF 도면**을 연계하는 한국 구조설계용 통합 플랫폼입니다.

**언어 정책**: UI/문서/주석은 한국어가 1차, 영어가 2차. 식별자(클래스/메서드/상수)는 영어. 답변은 한국어로 부탁드리되 코드/식별자/파일경로는 원문 유지해 주세요.

**도메인 용어 빠른 참조**
- *MGT*: MIDAS Gen 구조해석 입력 파일(EUC-KR 인코딩 다수)
- *Tekla Structures*: 트림블 BIM 저작도구. 본 프로젝트는 pythonnet으로 .NET API 호출
- *Drawing Compare Workbench*: 두 시점의 도면(PDF/DXF) 차이를 검토자가 시각적으로 확인/확인무시/오탐 분류하는 데스크탑 GUI
- *Cloud mark / Change zone*: 변경 구역을 둘러싼 빨간 구름선 표식(검측 산출물)
- *HMB*: Heaviest Member Beam(합성보 간섭 검토 입력)

---

## 1. 변경 컨텍스트

- **저장소**: `https://github.com/Nicefree19/MGT-Tools`
- **브랜치**: `codex/drawing-viewer-v2-review` (push-friendly 변형, 베이스는 `codex/drawing-viewer-v2-review-pushable`)
- **검토 대상 커밋 8개** (오래된 → 최신):

  ```
  d1bd42dd  fix(mgt-dxf): localize HMB effective geometry to node ends   ← 베이스
  18fa7c59  chore(repo): ignore out/ acceptance smoke artifacts
  59e0a4cb  feat(drawing-compare): Phase B+C reviewer UX upgrades
  43bff815  feat(drawing-compare): implement _export_confirmed_cloud_marks_v2 handler
  19c17c16  feat(composite-beam): expose floor match metadata in load plan API
  c7922f9e  test(drawing-compare): unit tests for confirmed cloud export module
  88d98013  docs(collab): add external code review request prompt for drawing viewer v2  ← 이 문서
  c25f4d82  feat(drawing-compare): add menu bar + Ctrl+N to escape collapsed input mode
  ```

  > 참고: 이 브랜치는 본 작업을 동료 / 외부 리뷰어가 빠르게 가져갈 수 있게 push-friendly 베이스(`codex/drawing-viewer-v2-review-pushable`) 위에 cherry-pick으로 재작성된 것입니다. 트리 상태는 원본 작업 브랜치와 동일하지만 SHA는 새로 매겨졌습니다.

- **확인 명령**:
  ```bash
  git fetch origin
  git checkout codex/drawing-viewer-v2-review
  git log --oneline codex/drawing-viewer-v2-review-pushable..HEAD     # 8개 커밋 확인
  git diff codex/drawing-viewer-v2-review-pushable...HEAD --stat       # 누적 diff 통계
  git diff codex/drawing-viewer-v2-review-pushable...HEAD              # 누적 diff
  ```

- **변경 통계 요약**: 약 +2,150 / -27 (12 파일 + 1 menu bar 추가)
  - `.gitignore` (+3)
  - `scripts/workbench_acceptance_smoke.py` (+359, 신규)
  - `scripts/workbench_preview_probe.py` (+110, 신규)
  - `src/gui/assets/drawing_compare/DrawingGpuViewport.qml` (+33)
  - `src/gui/drawing_compare_workbench.py` (+855, **두 차례 추가**: Phase B+C 720줄 + menu bar/Ctrl+N 137줄)
  - `src/services/comparison/confirmed_cloud_export.py` (+331, 신규)
  - `src/services/load_plan_editor.py` (+30/-7)
  - `tests/unit/services/comparison/test_confirmed_cloud_export.py` (+143, 신규)
  - `tests/unit/services/comparison/test_korean_workbench_ux.py` (+6)
  - `tests/unit/services/comparison/test_workbench_phase_b.py` (+119, 신규)
  - `tests/unit/services/comparison/test_workbench_phase_c.py` (+162, 신규)
  - `tests/unit/ui/test_composite_beam_load_plan_api.py` (+15)

---

## 2. 변경 의도 (요약)

### 2.1 Drawing Compare Workbench — Phase B (UI 컨트롤)

도면 비교 검토자(reviewer) UX 개선:

- **B1 오버레이 투명도 슬라이더**
  - 0.3 ~ 1.0 사이 클램프
  - QML `overlayOpacityScale` 속성으로 클라우드/포커스/라벨 오버레이 베이스 투명도(0.45 / 0.85 / 0.92)에 **곱연산** 적용
  - 위젯 폴백 경로 존재 (`GpuDrawingViewport.set_overlay_opacity_scale`)
- **B2 컴팩트 모드 토글**: 헤더 영역 visibility on/off + 버튼 라벨 동기화
- **B3 미리보기 품질 프리셋**: `PREVIEW_QUALITY_PRESETS` 3-tier
  - 보통 (80 DPI / max-edge ~px)
  - 고화질 (120 DPI) — `PREVIEW_QUALITY_DEFAULT_INDEX = 1`
  - 초고화질 (200 DPI)
- **부수 픽스**: 배경 이미지 항상 렌더링 (이전엔 `useTiles ? "" : imageSource` 게이팅 → sparse 타일 그리드에서 빈 화면 발생)

### 2.2 Drawing Compare Workbench — Phase C (사용성 폴리싱)

- **C1**: 검토 상태 필터 드롭다운 + 영역 진행률 라벨
- **C2**: 줌 슬라이더 ↔ 뷰포트 `zoomFactor` 양방향 동기화
- **C3**: 비교 프리셋 `COMPARE_PRESETS` 4종
  - `(label, quality_index, recursive)` 튜플
  - 표준 / 빠른 / 정밀 / 전체 폴더
  - `COMPARE_PRESET_DEFAULT_INDEX = 0` (표준=단일 폴더, 고화질)
- **C4**: 영역 메모(zone memo) 영속화

### 2.3 Confirmed-only Cloud Mark Export (신규 모듈)

검토자가 **확인(confirmed)** 처리한 변경 구역만 골라 깨끗한 PNG/DXF로 내보내는 신규 파이프라인.

- **모듈**: `src/services/comparison/confirmed_cloud_export.py`
  - 공개 API: `export_confirmed_cloud_marks(pair_id, after_image_path, overlays, review_records, output_dir, is_pdf_pair) -> ConfirmedCloudExportResult`
  - 출력: `<run>/artifacts/confirmed_clouds/<pair_id>_confirmed.png` + 메타
  - 색상: 빨간 `(220, 38, 38)` 클라우드 + 흰 라벨
- **GUI 와이어링**: 워크벤치에 두 버튼 추가
  - "확인된 변경 구름마크 추출 (현재 도면)"
  - "전체 도면 일괄 추출"
  - 핸들러: `DrawingCompareWorkbenchV2._export_confirmed_cloud_marks_v2(all_pairs: bool)` — 결과 폴더 자동 열기 + 한국어 결과 다이얼로그
- **테스트**: `test_confirmed_cloud_export.py` 5개 시나리오 (정상 / 빈 오버레이 / 미확인 / PDF / DXF)

> ⚠️ 커밋 분할 주의: `59e0a4cb`(feat: Phase B+C)에 버튼 정의가 들어갔지만, 동시 편집 중이던 워킹카피 영향으로 핸들러 본체가 빠짐. `43bff815`(fix-forward)에서 핸들러를 보강했습니다. 두 커밋을 같이 보면 정상이고, 단일 커밋만 체크아웃하면 `AttributeError` 위험이 있습니다.

### 2.4 Composite Beam Load Plan API 보강

`src/services/load_plan_editor.py` `build_load_plan_payload()` 확장:

- `requested_floor` 입력 strip + 문자열 강제
- 응답에 **`floor_match_status` 4-state enum** 추가
  - `no_floor_loads` — MGT에 floor-load 블록 자체가 없음
  - `all` — 사용자가 층을 요청하지 않음(=전체 반환)
  - `matched` — 요청 층에 블록 존재
  - `no_blocks_for_requested_floor` — 카탈로그엔 있지만 블록은 없음
- `available_floors` 배열을 root에 노출 (UI 드롭다운용; 기존 `floors`와 **동일 데이터**)
- root 레벨 `total_blocks` / `filtered_blocks` 추가 (기존 `summary` 안에도 존재 → 중복 노출)
- `summary`에 `available_floor_count` 추가
- 회귀 테스트: 카탈로그엔 있고 블록은 0인 케이스 추가

### 2.5 컴팩트 모드 탈출 경로 (Phase B B2 후속, 커밋 `c25f4d82`)

Phase B B2가 비교 후 입력 영역을 **자동 접기** 처리하면서 새로운 비교를 시작할 파일 픽커 버튼이 사라지는 문제 발생 → 다음 세 가지 탈출 경로 추가:

- **메뉴바**: `_build_menu_bar_v2()` — `파일` 메뉴 (단축키 `Ctrl+N` / `Ctrl+1` / `Ctrl+2`)
- **`Ctrl+N` / `N` 글로벌 단축키**: `_start_new_compare_v2(pick="")` 호출 → 입력 영역 펼침 + 선택 시 source picker 자동 오픈
- **컴팩트 토글 버튼 라벨 동적 변경**: 접힌 상태에서 `📁 ▼ 새 파일/폴더 선택하려면 클릭 (또는 Ctrl+N)` + `primary` QSS 속성으로 강조 (style.unpolish/polish로 강제 재스타일)
- **상태 라벨 hint 자동 추가**: 자동 접힘 시점에 안내 문구 append

### 2.6 운영/CI 보조

- `out/` 디렉토리를 `.gitignore`에 추가 (smoke 산출물)
- `scripts/workbench_acceptance_smoke.py` (오프스크린 Qt, 360줄): 사전계산된 검증 결과를 워크벤치에 로드해 위젯 상태(라벨 텍스트, 디테일 패널, 완료 게이트)를 어서션
- `scripts/workbench_preview_probe.py` (110줄): 빈 스크린샷 진단 — QML root의 `imageSource`/`hasBackground`/`statusText` 읽기

---

## 3. 정밀 리뷰 요청 항목

### 3.1 Drawing Compare Workbench (Phase B + C)

- [ ] **QML opacity 곱연산 cascading dimming**: 각 오버레이 베이스(0.45 / 0.85 / 0.92)에 `overlayOpacityScale`을 곱하는 방식이 라벨 가독성을 해치지 않는가? (예: scale=0.3일 때 라벨 effective opacity = 0.92 × 0.3 = 0.276 → 흰 글씨가 안 보일 위험)
- [ ] **opacity 클램프 [0.3, 1.0] 범위**: 한국 검토자가 0.3 미만(거의 투명)을 원할 가능성. UX 결정의 근거가 충분한가?
- [ ] **`useTiles` gating 제거 성능 영향**: 타일 그리드가 가득 찬 시나리오에서 배경 PNG가 불필요하게 큰 이미지로 렌더링돼 GPU 메모리 / paint time이 증가하지 않는가?
- [ ] **`set_overlay_opacity_scale` fallback widget 경로**: 예외를 silently swallow → 테스트가 실패를 가리지 않는가? 로그/메트릭 기록 권장?
- [ ] **`COMPARE_PRESETS` ↔ `PREVIEW_QUALITY_PRESETS` 결합도**: 프리셋 변경 시 quality combo box를 mutate함. preset 자체와 quality slider를 동시 조작하면 race가 있는가?
- [ ] **`drawing_compare_workbench.py` (4500+ 줄)**: 단일 파일 비대화. C4 메모 영속화 / Phase B 컨트롤 / 클라우드 export 핸들러를 분리할 시점인가?
- [ ] **테스트 누락**: B1/B2/B3, C1~C4 단위 테스트는 존재하지만, **QML 곱연산 결과의 실제 픽셀 값 검증은 부재**. 통합 테스트 가치 있는가?

### 3.2 Confirmed Cloud Export 모듈

- [ ] **`_resolve_pixel_bbox` 좌표계 변환**: PDF / DXF / 회전된 페이지에서의 정확성. 단위 테스트가 충분히 커버하는가?
- [ ] **PIL `Image.new("RGB", ...)` + 알파 클라우드 그리기**: RGBA 합성 누락 가능성? (DEFAULT_CLOUD_COLOR가 alpha=255인데 RGB 이미지에서 의미 있는가?)
- [ ] **에러 처리**: 워크벤치 핸들러가 `Exception`을 광범위하게 catch — silent swallow vs Telemetry. 사용자가 어떤 실패를 어떻게 알 수 있는가?
- [ ] **파일 충돌**: 같은 `pair_id`를 두 번 export하면 덮어쓰기 — 의도된 동작인가? (검토자가 실수로 재실행 시 이전 산출물 손실)
- [ ] **`QDesktopServices.openUrl(...)` 자동 폴더 열기**: 사용자 동의 없이 OS 파일 매니저가 뜸 — 헤드리스 CI / 원격 데스크톱에서 부작용?

### 3.3 Composite Beam Load Plan API

- [ ] **`floors` vs `available_floors` 중복**: 응답에 사실상 같은 데이터가 두 키로 노출. 미래 drift 위험. 한 쪽 deprecate 또는 doc-comment?
- [ ] **`floor_match_status="all"` 의미 충돌**: `requested_floor == ""` (미지정) 일 때 자동 `"all"`로 매핑. 사용자가 명시적으로 "전체" 옵션을 선택한 경우와 구별 안 됨. 프런트엔드가 두 케이스를 구별할 수 있어야 하는가?
- [ ] **`total_blocks` / `filtered_blocks` 이중 노출** (root + summary): 의도된 호환성 레이어인가, 정리 대상 부채인가?
- [ ] **하위 호환성**: 새 필드가 외부에 공개된 OpenAPI/Swagger 스펙에 영향을 주는가? (저장소 내 스펙 파일 확인 권장)
- [ ] **테스트 범위**: enum 4개 중 `no_blocks_for_requested_floor`만 회귀 추가. `all` / `matched` / `no_floor_loads` 케이스 추가가 필요한가?

### 3.4 진단 스크립트 / 운영

- [ ] `workbench_acceptance_smoke.py`가 의존하는 `out/acceptance_smoke/results/` 사전 산출물의 생성 절차는 README/CONTRIBUTING에 문서화되어 있는가? (현재는 docstring에만 명시)
- [ ] `sys.stdout.reconfigure(encoding="utf-8")` Windows cp949 콘솔 처리 — 다른 스크립트들과 일관성 있는가? (공통 유틸로 추출?)
- [ ] CI에서 이 스모크/프로브가 실행되는가? GitHub Actions 워크플로우 누락 가능성 점검.

### 3.5 횡단 항목

- [ ] **커밋 분할 품질**: 5개 커밋이 논리적으로 잘 분리되었는가? 특히 `9d083255`(fix-forward)는 squash하는 게 깔끔한가?
- [ ] **보안/시크릿 누출**: diff에 API 키 / 사용자 경로 / 비밀번호가 들어가지 않았는가?
- [ ] **로깅 PII**: 새 로그가 사용자 파일 경로 / 도면명을 그대로 출력해 PII 노출 위험이 있는가?
- [ ] **퍼포먼스 회귀**: 100K 요소 MGT, 대형 DXF에서 변경이 영향을 주는가?
- [ ] **단일 파일 비대화**: `drawing_compare_workbench.py` 720줄 추가 → 총 4500+ 줄. 모듈 분리 권고가 있는가?
- [ ] **i18n / 한국어 하드코딩**: 사용자 노출 문자열이 모두 한국어 하드코딩. i18n 레이어가 있는가, 단일 언어가 정책인가?
- [ ] **타입 힌트 / 런타임 검증**: 새 enum, 새 모듈에서 타입 어노테이션 일관성?

---

## 4. 답변 형식 (이 형식 그대로 부탁드립니다)

```
## 종합 의견 (Severity 기반)
- 🔴 Critical: <머지 차단 — 데이터/보안/회귀 위험>
- 🟠 High: <머지 전 수정 권장>
- 🟡 Medium: <후속 PR로도 수용 가능>
- 🟢 Nit: <취향/스타일/문서 보강>

## 영역별 상세 피드백

### 3.1 Drawing Compare Workbench (Phase B + C)
- [경로:줄] 코멘트 + 제안 패치(있으면)
- ...

### 3.2 Confirmed Cloud Export
- ...

### 3.3 Composite Beam Load Plan
- ...

### 3.4 진단 스크립트 / 운영
- ...

### 3.5 횡단
- ...

## 권장 후속 작업 (체크리스트)
- [ ] ...

## 칭찬할 점
(좋은 패턴/결정이 있으면 짧게)

## 신뢰도
이 리뷰의 자기 신뢰도(0~100%)와 그 근거. 추가로 봐야 할 파일/테스트가 있다면 명시.
```

---

## 5. 참고 자료

- **커밋 컨벤션**: `type(scope): description`
  - 타입: `feat`, `fix`, `refactor`, `chore`, `docs`, `test`, `perf`, `style`
  - 자주 쓰는 스코프: `drawing-compare`, `composite-beam`, `mgt-dxf`, `tekla`, `web`, `repo`
- **README**: 저장소 루트 `README.md`
- **협업 문서**: `docs/collab/STATUS.md`, `docs/collab/DECISIONS.md`, `docs/collab/REVIEWS.md`
- **연관 직전 커밋**:
  - `502a1de9 fix(mgt-dxf): localize HMB effective geometry to node ends` (브랜치 베이스)
  - `0722838c feat(drawing-compare): customer-grade UX upgrade + sharable audit + perf telemetry` (Phase A 격)
  - `ae0981ba feat(mgt-dxf): consolidate core frame drawing model`
  - `f061ed05 feat(web): streamline core frame review workflow`
- **테스트 실행**:
  ```bash
  pytest tests/unit/services/comparison/test_workbench_phase_b.py -v
  pytest tests/unit/services/comparison/test_workbench_phase_c.py -v
  pytest tests/unit/services/comparison/test_confirmed_cloud_export.py -v
  pytest tests/unit/ui/test_composite_beam_load_plan_api.py -v
  # 헤드리스 GUI 스모크 (사전 산출물 필요)
  python scripts/workbench_acceptance_smoke.py
  ```

---

> **부탁드립니다**: 답변은 한국어로 부탁드리되, 코드/식별자/파일 경로는 원문 유지해 주세요. 변경량이 +2,006줄로 큰 편이니 4단계 severity로 분류해 우선순위를 명확히 짚어 주시면 큰 도움이 됩니다.
