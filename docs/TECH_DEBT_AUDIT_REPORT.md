# 기술 부채 통합 감사 보고서 / Consolidated Tech-Debt Audit Report
## DrawingCompareWorkbench

**작성일 / Date:** 2026-06-16
**범위 / Scope:** gui-monolith, dwg-cad-pipeline, core-accuracy, test-health, build-deps-config
(comparison-sprawl, scripts-docs-bloat, perf-reliability 차원은 레이트리밋으로 후속 재실행)
**검증 원칙 / Verification discipline:** 모든 high-priority 주장은 adversarial 검증 또는 본 통합 단계에서 독립 재현됨. 반증(refuted)된 high-priority 주장은 없음. / Every high-priority claim was adversarially verified or independently reproduced. No high-priority claim was refuted.

---

## 1. 총평 / Executive Summary

DrawingCompareWorkbench는 기능적으로 풍부하지만 구조적으로 긴장 상태인 CAD/PDF 도면 비교 제품이다. 감사 결과 세 가지 지배적 테마가 드러난다:

1. **정직하지만 작동하지 않는 장치 (Honest-but-inert machinery)** — 존재하고 테스트되고 출하되었으나 기본 설정에서 아무 일도 하지 않음이 증명된 안전/품질 시스템들: zone 노이즈 필터(`min_changes_per_zone=1`로 게이트 도달 불가, ACC-4), pytest 300초 행 가드(`pytest-timeout` 미설치, TH-5), AC1032 "북극성" 네이티브 리더(유일한 per-PR CI 게이트에서 제외, 실샘플 테스트는 CI에서 전부 skip, TH-1).
2. **죽은 섬과 죽은 무게 (Dead islands & dead weight)** — line-add 동결된 모놀리스 안의 V1 QMainWindow(~960줄)와 ScanWorker는 완전히 죽었고(MONO-1/2), 모놀리스 자체는 14,857줄로 드리프트(프롬프트 13,692·AGENTS.md 14,105 둘 다 초과, MONO-9). 네이티브 AC1032 렌더 프로듀서, native_cad_importer/bridge, `apply_to_changes`는 모두 빌드·테스트되었으나 어디에도 배선되지 않음(DWG-1/6, ACC-8).
3. **정확도 정직성 격차 (Accuracy honesty gaps)** — 휴리스틱 엔진은 강제-"content" 폴백(ACC-1)과 과도하게 엄격한 layer/type near-match(ACC-2)로 거짓 양성을 생산하고, "AI 티어"는 이미 탐지된 zone에 라벨만 붙이므로 문서가 "이겨야 한다"고 명시한 F1=0.625 탐지 베이스라인을 수학적으로 움직일 수 없다(ACC-3).

동결은 순(net)-신규 모놀리스 줄은 막고 있으나 전체 드리프트는 막지 못한다. 가장 비용 대비 효과가 큰 작업은 동결의 **의도를 위배가 아니라 전진시키는** 삭제와 설정 수정이다.

---

## 2. 서브시스템별 분석 / Per-Subsystem Findings

### 2.1 GUI 모놀리스 / GUI Monolith

| ID | Title | Severity | Status |
|----|-------|----------|--------|
| MONO-1 | V1 class is fully dead (~960 lines) | HIGH | Confirmed (재현) |
| MONO-2 | ScanWorker dead, V1-only | MEDIUM | Verified confirmed |
| MONO-4 | V2 god-object (271 methods, 80 attrs) | HIGH | Confirmed |
| MONO-5 | 11+ god-methods >150 lines (max 265) | MEDIUM | Confirmed |
| MONO-8 | NanoColors duplicated | LOW | Confirmed (재현) |
| MONO-9 | Freeze baseline line counts stale | LOW | Confirmed (14,857) |

**핵심 증거 / Key evidence (재현됨):**
- `wc -l src/gui/drawing_compare_workbench.py` = **14,857** (프롬프트 13,692, AGENTS.md 14,105 둘 다 초과).
- Class 경계: `ScanWorker@L978`, `DrawingCompareWorkbench` (V1) `@L3015` (→L4478), `DrawingCompareWorkbenchV2@L4518`.
- `main()@L14849`는 `DrawingCompareWorkbenchV2()`만 인스턴스화. Repo-wide `DrawingCompareWorkbench\(` (V2 제외) = **0건**. 동적 인스턴스화 경로 없음.
- `ScanWorker`는 L978(def)/L3024/L3297에서만 참조 — 전부 V1 span 내부.

V2 god-object: `__init__`가 80개 인스턴스 attribute 설정, 가장 긴 메서드 `_load_lightweight_pdf_v2`는 265줄(L9778). 테스트는 method-rebinding hack(`test_workbench_ai_prepare.py`)으로만 구동 가능.

---

### 2.2 DWG/CAD 파이프라인 / DWG-CAD Pipeline

| ID | Title | Severity | Status |
|----|-------|----------|--------|
| DWG-1 | Native AC1032 render producer is dead-island | HIGH | Verified confirmed |
| DWG-2 | get_status() hardcodes AC1015, ignores opt-in | HIGH | Verified confirmed (재현) |
| DWG-3 | diagnose_dwg_file reports AC1032 blocked | MEDIUM | Confirmed |
| DWG-4 | AC1032 adapter bypassed when ODA installed | MEDIUM | Confirmed |
| DWG-6 | native_cad_importer/bridge dead-islands (~525 lines) | MEDIUM | Verified confirmed |
| DWG-7 | Viewer normalize ignores compare backend mode | MEDIUM | Confirmed |
| DWG-8 | Placeholder adapters fail-closed (positive control) | LOW | Non-issue |

**중요 범위 주의 / Important scoping:** DWG-2/DWG-3는 사용자에게 보이는 "지원 버전" **문자열**의 부정확성이며, AC1032가 실제로 읽히는지를 좌우하지 **않는다**(어댑터가 그것을 담당). `dwg_differ.py`는 `"dwg_supported_versions": ["AC1015"]`를 무조건 반환하고 `ac1032_native_opt_in` 참조가 0건이며, `compare_runtime_diagnostics.py:78`이 이를 한국어 에러 '내장 DWG 직접 지원 범위: {supported}'로 렌더.

DWG-1(재현): `build_native_scene_pack`는 src 소비자 0. 라이브 경로는 `viewer_session.py:48-54` → `scene_pack_builder.build_scene_pack`(ezdxf lazy import). `dwg_r2018_reader.py:2032`이 직접 'Diagnostic-only; not wired to product import' 명시.

---

### 2.3 핵심 정확도 엔진 / Core Accuracy Engine

| ID | Title | Severity | Status |
|----|-------|----------|--------|
| ACC-1 | Forced-content FP on near-matched identical pairs | HIGH | Confirmed (재현) |
| ACC-2 | Near-match requires exact layer+type (layer-move → 2 FPs) | HIGH | Confirmed (재현) |
| ACC-3 | AI tier cannot improve detection F1 (labels only) | HIGH | Verified confirmed |
| ACC-4 | Noise gate dead at default (min_changes_per_zone=1) | HIGH | Confirmed (재현) |
| ACC-5 | Priority uses English-only layer profiles | MEDIUM | Confirmed w/ caveat |
| ACC-6 | ~15 un-tuned magic-number tolerances | MEDIUM | Confirmed |
| ACC-7 | TEXT/DIM hash bundles position+content | MEDIUM | Confirmed |
| ACC-8 | apply_to_changes dead production code | LOW | Confirmed (재현) |
| ACC-9 | CAD marker bbox hardcoded 50×30 | MEDIUM | Confirmed |
| ACC-10 | Korean regex over-matches bare 보/벽 | MEDIUM | Plausible, unverified |
| ACC-12 | Re-origin dict-equality crutch | LOW | Confirmed |

**핵심 증거 / Key evidence (재현됨):**
- **ACC-1:** `dxf_comparator.py:2071-2074` — `if not categories: categories.append("content")` / `if not details: details.append("데이터 변경")`. `_is_significant_change`는 `categories == ["position"]`인 경우에만 드롭하므로 강제 `["content"]`는 통과 → MODIFIED 방출.
- **ACC-2:** rtree 경로 `dxf_comparator.py:1835-1838`와 linear 경로 `1936-1939` 모두 `entity_type`과 `layer` 정확 일치 강제. re-layer된 엔티티는 1 DELETED + 1 ADDED = 1 변경에 2 레코드.
- **ACC-3 (검증 confirmed):** `classify_zones`는 zone당 1개 `ChangeClassification` 반환 — 탐지를 추가/제거/병합하지 않음. baseline 리포트가 F1=0.625를 'AI 티어가 이겨야 할 기준'으로 프레이밍하나 분류만 하므로 수학적으로 P/R/F1을 움직일 수 없음.
- **ACC-4:** `change_zones.py:62` `min_changes_per_zone: int = 1`; 게이트 `L398-403`은 `len(zone_group) < 1` — 결코 참 아님.

---

### 2.4 테스트 건강성 / Test Health

| ID | Title | Severity | Status |
|----|-------|----------|--------|
| TH-1 | AC1032 reader excluded from PR gate, samples skip in CI | CRITICAL | Confirmed (재현) |
| TH-2 | zone_render_process main()/parser untested | HIGH | Confirmed |
| TH-3 | viewer subprocess success path 0% real coverage | HIGH | Confirmed |
| TH-4 | skipif(True) dead test as CRITICAL coverage | MEDIUM | Verified confirmed |
| TH-5 | pytest.ini timeout=300 inert (plugin missing) | HIGH | Verified confirmed (재현) |
| TH-6 | PR gate pytest 7 vs full-suite pytest 9 skew | MEDIUM | Confirmed |
| TH-7 | Manifest tests locked to D:/00.Work_AI_Tool path | MEDIUM | Confirmed |
| TH-9 | -n auto only in manual full-suite job | LOW | Confirmed |

**핵심 증거 / Key evidence (재현됨):**
- **TH-1:** `cad-format-regression.yml`(유일한 pull_request/push 게이트)은 12개 파일을 명시(L37-51)하며 `test_dwg_r2018_reader.py`/`test_dwg_native_ac1032_adapter.py`/`test_native_scene_pack_builder.py`를 **포함하지 않음**. `full-suite-health.yml`은 `workflow_dispatch`-only, nightly cron 주석 처리.
- **TH-5 (검증 confirmed):** `pytest.ini:64-65` `timeout=300`이 'pytest-timeout 사용 시' 주석 아래이나 플러그인 미설치. 경험적으로 inert 증명(timeout=1 + 3초 sleep 테스트가 3.6초에 PASS).
- **TH-4 (검증 confirmed):** `test_phase_c_audit_chain.py:331-357`는 `@skipif(True)` + body `raise AssertionError("unreachable")`이나 docstring은 CRITICAL 주장.

---

### 2.5 빌드·의존성·설정 / Build-Deps-Config

| ID | Title | Severity | Status |
|----|-------|----------|--------|
| BDC-1 | PyMuPDF in no requirements, only spec hiddenimports | HIGH | Confirmed (재현) |
| BDC-2 | All lint/type/security config has zero CI enforcement | HIGH | Confirmed |
| BDC-3 | Spec references non-existent hooks/ dir | MEDIUM | Verified confirmed |
| BDC-5 | pytest.ini shadows pyproject [tool.pytest] | MEDIUM | Confirmed |
| BDC-6 | Two DWG backend resolvers disagree on oda_converter | MEDIUM | Confirmed |
| BDC-7 | Spec hard-bundles optional scipy/skimage | MEDIUM | Verified confirmed |
| BDC-8 | 4 requirements files + pyproject duplicate & drift | MEDIUM | Confirmed |
| BDC-9 | CI Python 3.12 only; 3 inconsistent floors claimed | MEDIUM | Confirmed |
| BDC-10 | Whole scripts/ tree (51k lines) bundled in exe | LOW | Confirmed |
| BDC-11 | AI/ML deps excluded (benign-by-design) | LOW | Non-issue |

**핵심 증거 / Key evidence (재현됨):**
- **BDC-1:** requirements의 유일한 PyMuPDF 언급은 의도적 **제외 주석**; 15개 src 파일이 `import fitz`; `.spec`은 fitz를 hiddenimport + `collect_all('fitz')`. `pip install` 환경은 PyMuPDF 없음 → PDF-first 경로(ADR-001 Accepted) 런타임 실패.
- **BDC-3 (검증 confirmed):** `.spec:62` `hookspath=[str(ROOT/"hooks")]`인데 hooks/ 디렉터리 부재.
- **BDC-7 (검증 confirmed):** scipy/skimage는 try/except 안에서만 import되나 spec은 hiddenimport + `collect_all('scipy')`.

---

## 3. 우선순위 로드맵 / Prioritized Roadmap

순위 기준: **impact × confidence × (1/risk)**, 동결 준수.

### 3.1 Quick Wins (지금 안전하게)

1. **죽은 V1 + ScanWorker 삭제 (MONO-1/2)** — 삭제만, 런타임 무변, 동결 의도 전진.
2. **네이티브 리더 테스트를 PR 게이트에 추가 (TH-1)** — 기존 CI 강화, 신규 게이트 아님.
3. **노이즈 억제 기본값 수정 (ACC-4)** — `recommended()` 프리셋 기본 OR 게이트 `<=` 의미.
4. **pytest-timeout 설치 (TH-5)** — requirements-dev + full-suite pip install.
5. **AC1032 opt-in 시 dwg_supported_versions 정직화 (DWG-2)** — 진단 문자열만.
6. **apply_to_changes 삭제 (ACC-8) + NanoColors 통합 (MONO-8)**.
7. **누락 hooks/ 참조 수정 (BDC-3) + 죽은 skipif(True) 테스트 삭제 (TH-4)**.

### 3.2 Structural Refactors (계획·합의 필요)

1. **위성 시임으로 V2 god-object 분해 (MONO-4/5)** — 클래스에 추가 금지; 응집 state 클러스터를 신규 모듈로 추출.
2. **near-match 완화로 layer-move·split-edit 회복 (ACC-2/7)**.
3. **AI 티어 프레이밍 교정 + 탐지 튜닝 루프 (ACC-3/6)**.
4. **DWG 백엔드 해석·설정 표면 통합 (DWG-4, BDC-6)**.
5. **빌드/deps/config 통합 + lint/type CI 잡 (BDC-2/5/7/8)**.

### 3.3 Deferred (보류/재분류)

- **ACC-3는 탐지 버그가 아닌 문서-정직성 이슈** — 수정은 편집적(리포트) + UI abstention 공개.
- **DWG-2/3는 상태-문자열 부정확성** — 'opt-in 시 AC1032를 못 읽음'으로 과장 금지.
- **ACC-5/ACC-10은 한글-layer 처리 리팩터로 묶기**.
- **MONO-3/6/7은 MONO-1의 결과** — V1 삭제 후 기계적 해소.
- **DWG-8/BDC-11은 검증된 비이슈**.
- **ACC-12/DWG-7/BDC-10/TH-2/3/7/10** — 유효하나 저레버리지, 기회적 처리. 감사-게이트 테스트 추가 금지(동결 #2).

---

## 4. 정직성 노트 / Honesty Notes

- 본 감사의 **high-priority 주장 중 반증되거나 불확실로 판정된 것은 없음.**
- 두 건만 경미한 evidence 과장: **ACC-5**(priority_calculator Korean 매치 0건 → 실제 1건; 구조적 격차는 실재) 및 **DWG-6**(참조처 열거 오류 — dead-island 결론은 오히려 강화).
- **ACC-3와 DWG-2는 confirmed이나 범위 정확히**: 전자는 '문서 프레이밍 오류', 후자는 '상태 텍스트 stale'.
