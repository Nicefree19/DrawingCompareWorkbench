# AI Classifier — End-to-End Test Plan

> Comprehensive test plan covering the 3-tier AI classifier
> (heuristic → embedding → LLM with KDS RAG) integrated into the
> Drawing Compare Workbench. Includes the **Phase N integration**
> (the workbench cascade wiring discovered during self-review).
>
> Last updated: 2026-05-07 (post Phase N).

## Critical context — Phase N integration finding

**Background**: From Phase H (2026-04ish) through Phase L4
(2026-05-07), the 3-tier AI classifier cascade was built in
isolation and unit-tested at ~270+ tests. All tests passed.

**Finding (independent verification)**: The cascade was wired into:
- `_run_test_encode` in the AI settings dialog (synthetic single-zone
  test button)
- `_kickoff_ai_prepare_v2` warm-up path (only loads dispatchers, doesn't
  classify real zones)

But **NOT** into the workbench's actual zone classification flow —
that called `zone_classifier.classify_zone` (heuristic-only). End
users could enable Quality / LLM / RAG modes in the dialog but saw
zero behavioural change.

**Phase N fix**: New `zone_classifier_adapter.py` bridges the schemas
(`ChangeClassification` ↔ `ZoneCategoryResult`) and the workbench's
`_compute_zone_categories_for_pair_v2` now routes through
`classify_zone_with_cascade(zone, cfg)` — heuristic-only users get
the same fast path; users with `use_embedding`/`use_llm` enabled get
real cascade results displayed in the existing UI.

**Lesson for E2E**: Unit tests in isolation are insufficient. The
test plan below is structured around **integration paths**, not
modules.

## Scope

### In scope
- Workbench → adapter → `ai_classifier.classify_zones` → 3-tier cascade
- Schema bridge (`ChangeClassification` → `ZoneCategoryResult` UI shape)
- AI settings dialog → `ai_config.json` persistence → next-classification effect
- Cascade safety (heuristic always available, abstain on backend failure)
- KDS RAG retrieval into LLM prompt
- CI smoke (no models, no Ollama, no real KDS)

### Out of scope (explicit)
- Tekla Structures integration
- DWG/DXF/PDF parser correctness (covered by separate test suites)
- Workbench's other UI features (review state, export, viewer)
- Composite beam, MGT DXF (separate feature areas)

## Test environment matrix

| Category | Models | Ollama | KDS | Runs in CI? |
|---|:---:|:---:|:---:|:---:|
| **Cat A** Smoke | ❌ | ❌ | ❌ | ✅ Yes |
| **Cat B** Heuristic-only | ❌ | ❌ | ❌ | ✅ Yes |
| **Cat C** Quality embedding | Qwen GGUF | ❌ | ❌ | ❌ |
| **Cat D** Speed embedding | mxbai ONNX | ❌ | ❌ | ❌ (env conflict) |
| **Cat E** Hybrid (Qwen + Stub LLM) | Qwen | ❌ (stub) | ❌ | ❌ |
| **Cat F** Hybrid (Qwen + Ollama) | Qwen | EXAONE | ❌ | ❌ |
| **Cat G** RAG enriched | Qwen | EXAONE | local_json | ❌ |
| **Cat H** Workbench integration | varies | varies | varies | ❌ (manual) |
| **Cat I** User PoC scenario | full | full | full | ❌ (manual) |

## Cat A — Smoke (CI, no setup)

### Goal
Verify the Phase H/I/J/K/L code compiles, imports, and the cascade
contract holds with toy backends.

### Tests (existing, in CI)
- `tests/unit/services/comparison/test_ai_classifier_*.py` (~150 tests)
- `tests/unit/services/comparison/test_ai_config_io.py` (30 tests)
- `tests/unit/gui/test_ai_settings_dialog.py` (38 tests)
- `tests/unit/gui/test_workbench_ai_prepare.py` (~10 tests)
- `tests/unit/services/comparison/test_zone_classifier_adapter.py` (**13 tests, Phase N**)

### Acceptance
- All 270+ tests pass on `python 3.10` and `python 3.12`
- `verify_embedding_backends.py --backend both --golden-set v1`
  exits 0 (both backends skip cleanly when models absent)
- `.github/workflows/ai-classifier.yml` job goes green within 5 minutes

### Status
✅ Currently passing. CI green.

## Cat B — Heuristic-only (no models)

### Goal
Verify the workbench's default path (no AI enabled) works
end-to-end with real comparison data.

### Setup
1. `python start_drawing_compare_workbench.py`
2. No model files, no Ollama, no KDS clauses

### Test scenarios

**B1**: Workbench launch + first comparison
- Pick two folders containing 2-5 DWG/DXF pairs
- Wait for comparison to finish
- Click on a drawing pair in the left panel
- Verify zone list populates with categories (`category` column shows
  Korean labels: 구조 부재 변경 / 그리드 변경 / 치수 주석 변경 / 등)
- Verify zone detail panel shows rationale (no `[Stage-N]` marker
  → confirms heuristic path)

**B2**: Settings dialog default state
- Open `[설정] → [🤖 AI 분류기 설정...]` (Ctrl+Shift+A)
- Verify Embedding mode = "Auto", LLM checkbox unchecked, RAG
  checkbox unchecked
- Status indicator should show ✓/✗ for the available models
- Click "🧪 테스트 인코드" → result appears in 1 second
  (heuristic since no models)

**B3**: Cascade banner visible
- Open the dialog
- Verify the cascade dependency banner (light blue) is visible
  near the top: "💡 3단계 캐스케이드: 임베딩 모드 → LLM
  캐스케이드 → KDS RAG 순서로 활성화 가능…"

### Acceptance
- Workbench launches without errors
- Comparison produces zones with category labels
- Dialog opens, all sections are visible, defaults reasonable
- Status label shows "휴리스틱 분류만 사용" (or model-missing warning)

## Cat C — Quality embedding (Qwen GGUF)

### Goal
Verify Stage-2 embedding with the real Qwen3-Embedding-0.6B model
produces correct classifications + sensible latency.

### Setup
1. Download `Qwen3-Embedding-0.6B-Q8_0.gguf` (~639 MB) into
   `%LOCALAPPDATA%\DrawingCompareWorkbench\ai_models\`
2. `pip install llama-cpp-python` (if missing)
3. Launch workbench

### Test scenarios

**C1**: Cold-start latency
- Start workbench, wait for AI prepare
- Measure time from launch → "✓ AI 준비 완료" status label
- **Expected**: < 5 seconds total (Qwen GGUF mmap + first encode)

**C2**: Bench harness
- `python tools/verify_embedding_backends.py --backend quality
  --golden-set v1 --output bench_quality.json`
- Open `bench_quality.json`
- **Expected**:
  - cold_start_ms < 5000
  - per_zone_ms_p50 < 50
  - per_zone_ms_p95 < 100
  - golden_accuracy_pct ≥ 75% on the 16-zone v1 fixture

**C3**: Workbench classification with Quality mode
- Open dialog, select Mode = "Quality (Qwen3-Embedding)"
- Click OK
- Open a drawing pair → verify zone detail panel shows
  `[Stage-2 임베딩]` marker in rationale
- Manually inspect 5-10 zones — categories should be more accurate
  than heuristic (e.g. zones with no obvious layer pattern get
  classified by content)

**C4**: Switching back to Off
- Open dialog, select Mode = "Off"
- OK
- Re-select drawing pair → categories should re-compute (cache
  invalidated by Phase N fix in `_show_ai_settings_dialog_v2`)
- No tier marker in rationale (heuristic only)

### Acceptance
- bench_quality.json shows latency targets met
- Quality mode visibly produces better-categorised zones
- Settings change immediately affects next pair selection (no stale
  cache — Phase N fix verified)

## Cat D — Speed embedding (mxbai ONNX)

### Goal
Verify Phase I Speed mode works end-to-end with the real mxbai
ONNX model.

### Setup
**Critical**: Requires a clean venv because the project's global
Python env has `tokenizers` ↔ `transformers` ↔ `peft` version
conflicts (documented in earlier review).

```powershell
python -m venv .venv-speed
.\.venv-speed\Scripts\activate
pip install -r requirements.txt sentence-transformers onnxruntime optimum
huggingface-cli download mixedbread-ai/mxbai-embed-large-v1 \
  --local-dir "$env:LOCALAPPDATA\DrawingCompareWorkbench\ai_models\onnx_mxbai_large" \
  --include "*.json" "tokenizer.*" "onnx/*"
```

### Test scenarios

**D1**: Cold-start latency
- **Expected**: < 500 ms (ONNX is much faster than GGUF)

**D2**: Bench harness
- `python tools/verify_embedding_backends.py --backend speed
  --golden-set v1 --output bench_speed.json`
- **Expected**:
  - cold_start_ms < 500
  - per_zone_ms_p50 < 10
  - golden_accuracy_pct ≥ 65% (ONNX is faster but slightly less
    accurate than GGUF)

**D3**: Matryoshka truncation
- `--truncate-dim 512`
- Compare accuracy delta vs native (1024 dim)
- **Expected**: < 5 percentage point drop

### Acceptance
- bench_speed.json hits latency targets
- Truncation accuracy delta < 5pp

## Cat E — Hybrid (Qwen + Stub LLM, no Ollama)

### Goal
Verify Phase J2 cascade — Stage-2 embedding feeds candidates to
Stage-3 LLM, even with the stub backend.

### Setup
- Cat C complete (Qwen installed)
- Stub LLM is built-in, no extra setup

### Test scenarios

**E1**: Settings dialog hybrid mode
- Open dialog
- Mode = Quality, check "Stage-3 LLM 캐스케이드 사용", backend
  = "Stub", invoke threshold = 0.85
- OK
- Open drawing pair → verify some zones show `[Stage-3 LLM]`
  marker (the ambiguous ones — Stage-2 confidence < 0.85)
- Confident zones should show `[Stage-2 임베딩]` (LLM skipped)

**E2**: should_invoke gate verification
- Use `tools/verify_embedding_backends.py` with a custom hybrid
  config (manual edit) to count how many zones triggered LLM
- **Expected**: ~10-30% of zones (most should be confident
  enough to skip LLM)

### Acceptance
- Mixed `[Stage-2]` / `[Stage-3]` markers visible in zone list
- LLM call count is bounded (not 100%)

## Cat F — Hybrid with real Ollama EXAONE

### Goal
Validate end-to-end LLM enrichment with real Korean LLM.

### Setup
```powershell
# Install Ollama from https://ollama.com/download
ollama serve  # or just launch the app on Windows
ollama pull exaone3.5:7.8b   # ~5 GB, one-time
```

### Test scenarios

**F1**: Probe in dialog
- Open dialog, select LLM backend = "Ollama EXAONE-3.5"
- Status indicator should turn green (✓)
- Click "🧪 테스트 인코드" → expect Korean rationale from EXAONE

**F2**: Custom host (Phase L4)
- Type `http://localhost:11434` in host field (default)
- Type `exaone3.5:7.8b` in model field
- OK + verify saved in `%LOCALAPPDATA%\DrawingCompareWorkbench\ai_config.json`
- Re-open dialog → values preserved

**F3**: Real comparison with LLM enrichment
- Open a real DWG pair
- For 5 zones, verify rationale has natural Korean explanations
  (not the stub's "(stub) 첫 번째 후보…" boilerplate)

### Acceptance
- Probe green, test encode shows real Korean rationale
- Custom host/model persists across Workbench restart
- Per-zone latency: 1-3 seconds (Ollama round-trip)

## Cat G — RAG enriched (KDS clauses)

### Goal
Validate Phase K2 KDS RAG injection into LLM prompt.

### Setup
1. Cat F complete (Ollama working)
2. Create `%LOCALAPPDATA%\DrawingCompareWorkbench\kds_clauses.json`
   with 10-20 real KDS clauses (sample format in
   `docs/AI_KDS_RAG_PHASE_K2.md`)

### Test scenarios

**G1**: Dialog probe
- Open dialog, check "KDS RAG 사용", client = "Local JSON"
- Status indicator → green ✓ (file found)
- OK

**G2**: Real run with RAG
- Compare two real DWG drawings
- Open a structural-member zone → rationale should cite KDS clause
  codes (e.g. "[KDS 14 24 00 §5.3] 휨강도 …")

### Acceptance
- LLM rationale contains KDS clause codes
- `raw_evidence["kds_rag_context_chars"] > 0` in zone metadata

## Cat H — Workbench integration scenarios

### Goal
Verify the integration paths the verification agent flagged as
"untested" — the Phase N regression must not recur.

### Test scenarios

**H1**: Settings change → next-pair re-classification (Phase N fix)
- Open pair A → verify `[Stage-1]` markers (default heuristic mode)
- Open settings → enable Quality → OK
- Re-open same pair A → verify `[Stage-2]` markers (cache cleared,
  re-classified)
- This catches the Phase N regression and verifies the cache
  invalidation fix at line 4488 of drawing_compare_workbench.py

**H2**: Background warm-up timeout (UX gap noted by reviewer)
- Place an oversized fake Qwen GGUF (>10 GB so mmap is slow)
- Launch workbench
- Watch `lbl_status_v2` — should NOT freeze indefinitely
- After 30 seconds, status should either succeed or show error
- **Known gap**: current `_poll_ai_prepare_v2` stops polling after
  60 polls (30s) without surfacing a timeout error. Test will FAIL
  here — this is a documented-but-unfixed UX issue.

**H3**: text_snippet missing (always missing in production)
- Run a real comparison (overlays don't carry text_snippet)
- Verify the cascade does not crash when zone evidence has only
  layer + entity_type + change_type
- The `_zone_evidence_text` helper concatenates whatever fields
  are present, so empty text_snippet is graceful

**H4**: Stale cache scenario (verified by Phase N tests)
- Already covered by integration test
  `test_workbench_overlay_shape_classified_via_cascade`
- Manual verification: select pair → change config → re-select
  pair → categories must reflect new config

### Acceptance
- H1, H3, H4 pass
- H2 is documented as known limitation (not blocking)

## Cat I — Full PoC scenario (first paying customer)

### Goal
Demonstrate end-to-end value delivery: real customer drawings,
full 3-tier cascade, Korean LLM rationale, KDS clause citations.

### Setup
- All of Cat C + F + G complete
- Have 1-2 real customer drawing pairs ready
- Have customer's KDS clauses in JSON format

### Scenarios

**I1**: Time to first AI-classified zone
- Cold start workbench
- Pick customer drawings
- Wait for comparison + AI prepare
- Click first pair → measure time to category appears
- **Target**: < 30 seconds total (cold start + comparison + classify)

**I2**: Reviewer workflow
- Reviewer opens 10 zones in sequence
- Records: agreement rate with AI category, time per zone, perceived
  value of LLM rationale
- **Target**: 80%+ agreement, 30 seconds avg per zone, perceived
  positive value (subjective)

**I3**: Settings change agility
- Reviewer wants to switch from Quality to Speed mid-session
- Open dialog → switch → OK → next pair
- **Target**: < 5 seconds for the switch + new mode applies
  immediately

### Acceptance
- I1, I2, I3 all hit targets
- No crashes, no UI freezes longer than 1 second
- Reviewer subjectively reports: "this saves time"

## Failure recovery (per tier)

| Tier failure | Symptom | Expected behaviour |
|---|---|---|
| Qwen GGUF missing | "✗ AI 모델 미설치" status | Heuristic fallback, no crash |
| Qwen probe OK but encode fails | Background warmup error | Status shows error, heuristic fallback |
| mxbai ONNX missing | Same as Qwen | Same |
| Ollama not running | Status indicator red | LLM cascade abstains, Stage-2 result kept |
| Ollama model not pulled | Same as not running | Same |
| KDS file missing | Status indicator red | RAG returns empty context, LLM still fires |
| Cascade exception | Caught + logged | Heuristic fallback (Phase N adapter safety) |

## Acceptance criteria (overall)

To declare the AI classifier "ready to ship to first paying customer":

| Cat | Requirement |
|---|---|
| A | All 270+ unit tests + 13 integration tests pass in CI |
| B | Workbench launches + heuristic comparison works |
| C | Quality mode bench: ≥75% accuracy on 16-zone v1 |
| D | Speed mode bench: ≥65% accuracy + truncation delta <5pp |
| E | Hybrid mode visibly mixes Stage-2 / Stage-3 markers |
| F | Real Ollama produces natural Korean rationale |
| G | RAG injects KDS clause codes into LLM prompt |
| H | H1, H3, H4 pass (H2 documented as known limit) |
| I | I1, I2, I3 hit subjective + numeric targets |

## Automation harness

Most categories require user setup (model files, Ollama, KDS data).
What we CAN automate:

- **Cat A**: ✅ already automated (`ai-classifier.yml` workflow)
- **Cat B + B3**: 새로 자동화 가능 (`tools/run_e2e_smoke.py`,
  Phase N has the integration test that proves cascade IS wired)
- **Cat C**: bench harness exists (`verify_embedding_backends.py`),
  user runs after downloading model
- **Cat D-G**: orchestration script could be added but each requires
  user to install upstream deps (Ollama, KDS data)
- **Cat H**: H1, H3, H4 are unit-testable; the new
  `test_zone_classifier_adapter.py` covers them
- **Cat I**: by definition manual + qualitative

## Outstanding gaps

| # | Gap | Workaround / next step |
|---|---|---|
| 1 | Cat D requires venv (env conflict in default) | Document venv setup in user guide |
| 2 | H2 timeout UX (30s+ silent hang) | Phase O: add explicit timeout error + retry button |
| 3 | No Win11 CI (Linux only) | Accept; manual smoke on Windows |
| 4 | Real Ollama smoke not in CI | User responsibility; document `ollama pull` |
| 5 | KDS clauses fixture not shipped | Provide template `kds_clauses.example.json` |

## Review history

- 2026-05-07: Initial plan after Phase N integration discovery (this doc)
