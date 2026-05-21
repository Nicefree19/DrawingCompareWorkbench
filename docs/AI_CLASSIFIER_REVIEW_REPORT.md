# AI Classifier Code Review Report (2026-05-07)

> Scope: 8 commits `cc42d992^..HEAD` on branch `review-prompt-202605`
> implementing Phase J (J1+J2+K1) + Phase K2 + Phase L (L1+L2+L3).
> 24 files, ~7,100 LOC, ~140 tests.

## Summary

3-tier AI classifier cascade (heuristic → embedding → LLM+RAG) shipped
successfully — with **strong cascade safety** (every tier abstains
via `None` / `""`, never raises into public API) and thorough happy-
path test coverage (1554/1555 passing).

Independent code-verification-agent surfaced **6 critical/important
bugs**, of which **4 are fixed in this commit**. Remaining 2 noted
as future work (lower-impact, not blocking).

**Verdict**: Approve with the included fixes. Architecture is sound;
fixes address real production issues.

---

## Bugs Found + Fixed (4 of 6)

### 🔴 Critical — Fixed

#### Bug #2 — `clear_llm_dispatcher_cache()` not called after dialog save

**File**: `src/gui/drawing_compare_workbench.py:4472`

**Issue**: After user clicks OK in AI settings dialog, only the
embedding dispatcher cache was cleared. LLM + KDS RAG dispatchers
remained cached with old config — every LLM/RAG config change was
silently ignored until process restart.

**Fix**: Added `clear_llm_dispatcher_cache()` import + call alongside
existing embedding cache clear.

**Impact**: HIGH — affects every Stage-3 LLM mode change attempt.

---

#### Bug #1 — `OllamaExaoneLlmBackend.probe_available()` ignored configured host

**File**: `src/services/comparison/ai_classifier/llm_backends/ollama_exaone.py:202`

**Issue**: `probe_available()` was a `@classmethod` that hardcoded
`DEFAULT_HOST` and `DEFAULT_MODEL`, regardless of user's
`llm_host` / `llm_model` config. Users on remote Ollama (e.g.
`http://10.0.0.5:11434`) saw incorrect probe status — green when
localhost responded, red when localhost didn't even though the
remote host had the model.

**Fix**:
1. `probe_available(host=DEFAULT_HOST, model_name=DEFAULT_MODEL)` —
   classmethod accepts optional host + model parameters
2. New `probe_with_instance_config()` instance method threads
   `self._host` + `self.model_name` into the classmethod
3. Dialog's `_probe_llm_one` now reads host/model from
   `self._current_config` and passes them through

**Impact**: HIGH — required for any non-localhost Ollama deployment.

---

### 🟡 Important — Fixed

#### Issue #5 — `should_invoke` returns True for "hybrid" / "error" / "disabled" classifications

**File**: `src/services/comparison/ai_classifier/llm_classifier.py:215`

**Issue**: Original guard `if classifier_used != "embedding": return True`
matched too broadly. If a `"hybrid"` result (already through Stage-3)
were ever fed back into the cascade, it would re-trigger LLM →
re-classification loop. Also wasted LLM calls for `"error"` / `"disabled"`
zones.

**Fix**: Explicit allowlist:
- `"heuristic"` → invoke (Stage-2 abstained, LLM may help)
- `"embedding"` → gate on confidence threshold
- Everything else (`"hybrid"`, `"disabled"`, `"error"`, future) → skip

**Impact**: MEDIUM — defensive fix against latent re-cascade bugs.

---

#### Issue #8 — Silent save failure (no user feedback)

**File**: `src/gui/ai_settings_dialog.py:763`

**Issue**: When `save_ai_config()` raises (read-only `%LOCALAPPDATA%`,
disk full, etc.), the dialog logged the exception and returned without
accepting — but the user only saw "OK does nothing" with no
explanation.

**Fix**: Added `QMessageBox.critical` with the exception message and
recovery hints (check %LOCALAPPDATA% permissions, check disk space).

**Impact**: MEDIUM — UX bug, not data loss.

---

### ✅ Fixed in Phase L4 (Issue #6)

#### Issue #6 — `llm_host` / `llm_model` not persisted to ai_config.json (FIXED)

**Files**:
- `src/services/comparison/ai_classifier/config_io.py:78` (PERSISTED_FIELDS)
- `src/services/comparison/ai_classifier/config_io.py:223` (validation)
- `src/gui/ai_settings_dialog.py:255` (QLineEdit widgets)

**Fix**:
1. `_PERSISTED_FIELDS` extended with `llm_host` and `llm_model`
2. `_validate_payload` rejects malformed values (host scheme check,
   model charset, length caps)
3. Dialog adds 2 QLineEdit widgets — gated on Ollama backend
   selection AND use_llm checked (Stub backend ignores them)
4. `editingFinished` signal triggers probe re-check with the user's
   typed host/model
5. `_build_config_from_ui` threads values through both branches
   (preserves on Off-mode for re-enable)

**Tests**: 5 new config_io tests + 6 new dialog tests = 11 regression
cases. Custom Ollama deployments (e.g. `http://10.0.0.5:11434` with
`llama3.2:3b`) now survive Workbench restart.

### ✅ Fixed in Phase L5 follow-up commit

#### Issue #3 — `_extract_first_json` brace scanner edge case (FIXED)

**File**: `src/services/comparison/ai_classifier/llm_backends/ollama_exaone.py:133`

**Issue**: hand-rolled depth counter mismatched braces appearing
inside string values (e.g. Korean rationale `"보의 {단면} 변경"`).

**Fix**: replaced with `json.JSONDecoder().raw_decode()` walking each
`{` candidate position. The real JSON tokenizer handles string
escaping + nested braces correctly. 4 regression tests added.

#### Issue #4 — Thread safety (FIXED)

**Files**:
- `src/services/comparison/ai_classifier/kds_rag/local_json.py:118`
- `src/services/comparison/ai_classifier/llm_classifier.py:135`

**Fix**: added `threading.Lock` with double-checked-locking pattern
(matches `AbstractEmbeddingBackend.warmup` from Phase H 2nd-review)
to both `LocalJsonKdsRagClient._ensure_loaded()` and
`LlmClassifierDispatcher._get_kds_rag_client()`. 2 regression tests
added that verify exactly 1 file read + 1 client instance under
8 concurrent threads.

---

## What Looks Good

- **Cascade safety model is genuinely strong** — every tier abstains
  via `None` / `""` rather than raising. `AbstractLlmBackend.classify`
  catches `BaseException`, making it impossible for backend bugs to
  propagate to `public_api.classify_zones`.
- **Singleton cache design** with per-config tuple keys — clean,
  avoids repeated model loads, properly invalidates on config change
  (after Bug #2 fix).
- **Atomic save pattern** in `save_ai_config()` — write `.tmp`, then
  `Path.replace()`. Mirrors manifest pattern from Phase H. Prevents
  partial-write corruption.
- **`_validate_payload`** — thorough field-by-field validation with
  specific error messages.
- **Schema versioning** — v1 → v2 bump preserved backward compat;
  loader fills missing v2 fields from `auto_mode()` base.
- **Test coverage** — happy paths thoroughly covered. Toy backends
  enable deterministic dispatcher tests without real models.

---

## Architectural Observations (not bugs, future work)

### Three registries could share a generic meta-pattern

`BACKEND_REGISTRY` (embedding) + `LLM_BACKEND_REGISTRY` + `KDS_RAG_REGISTRY`
all follow identical `dict[str, Callable]` patterns with
`register_*` / `get_*` / `available_*` functions. ~120 lines of
duplicated boilerplate. A `BackendRegistry[T]` generic class would
deduplicate. Not urgent but worth tracking.

### `AiClassifierConfig` will need to split

18 fields across 4 logical groups (enabled flag + embedding 5 +
LLM 6 + RAG 4). At 25+ fields this becomes painful. Consider
splitting into `AiEmbeddingConfig` / `AiLlmConfig` / `AiKdsRagConfig`
nested under a top-level `AiClassifierConfig` container. Phase
boundary natural.

### Dialog cascade dependency could use a banner

Embedding Off → LLM Off → RAG Off cascade is correct but visually
subtle. A one-line banner ("LLM 캐스케이드는 Stage-2 임베딩이
켜져 있어야 활성화됩니다") would eliminate support questions.

### Phase doc cross-references incomplete

Each phase doc (`AI_EMBEDDING_PHASE_I.md`, `AI_LLM_PHASE_J2.md`,
`AI_K1_GOLDEN_SET.md`, `AI_KDS_RAG_PHASE_K2.md`) is updated
incrementally but not consistently cross-linked. E.g. L3 added
KDS RAG dialog notes to K2 doc but not to J2 doc (which describes
the LLM cascade that L3 hooks into).

### `_extract_first_json` brace scanner has edge case

Nested braces inside string values (e.g. `{"r": "보의 {단면} 변경"}`)
break the depth-counting scanner. Currently mitigated by Ollama's
`format: json` mode forcing clean JSON. Will become a real bug if
a future backend omits that flag. Future fix: use
`json.JSONDecoder().raw_decode()`.

---

## Test Coverage Gaps

- **No end-to-end real-cascade test** — all cascade tests use
  mocked dispatchers. Recommend `tools/verify_classifier_cascade.py`
  smoke harness with toy backends across all 3 tiers.
- **CI runs on Linux**, production is Windows — `LOCALAPPDATA` path
  resolution divergence not caught. Acceptable for now (documented
  in workflow comments) but blind spot.
- **No real Ollama smoke** — only mocked HTTP. By design (no Ollama
  in CI), but the K1 verify_real workflow could be stretched to
  exercise this against a self-hosted Ollama mirror.

---

## Suggested Next Phase

**L4 — `llm_host` / `llm_model` persistence + dialog widgets**:
Issue #6 fix. Adds host text input + model name combo to the dialog's
LLM section. Required for any user with non-default Ollama
deployment. ~150 LOC + 5 tests.

**M1 — End-to-end operational guide**: Workbench launch → setup
dialog → run comparison → AI cascade verification → expected output.
Live screenshots. Critical for first paying customer onboarding.
