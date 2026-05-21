# AI Classifier Documentation Index

> Master navigation for the 3-tier AI classifier cascade
> (heuristic → embedding → LLM+RAG) shipped across Phases H/I/J/K/L.
> Use this doc as the **single entry point** when onboarding to the
> classifier subsystem or hunting down a specific design decision.

## Quick links

| Aspect | Doc | Status |
|---|---|---|
| Original 3-tier design | [AI_CHANGE_CLASSIFICATION_DESIGN.md](AI_CHANGE_CLASSIFICATION_DESIGN.md) | ✅ V1 reference |
| Embedding model selection (Qwen3 vs mxbai) | [AI_EMBEDDING_PLAN_V2.md](AI_EMBEDDING_PLAN_V2.md) | ✅ V2 plan |
| Phase I — dual backend + Matryoshka | [AI_EMBEDDING_PHASE_I.md](AI_EMBEDDING_PHASE_I.md) | ✅ shipped |
| Phase J Step 5 — Stage-3 LLM cascade | [AI_LLM_PHASE_J2.md](AI_LLM_PHASE_J2.md) | ✅ shipped |
| Phase J Step 4 — golden-set tooling (K1) | [AI_K1_GOLDEN_SET.md](AI_K1_GOLDEN_SET.md) | ✅ shipped |
| Phase K2 — KDS RAG integration | [AI_KDS_RAG_PHASE_K2.md](AI_KDS_RAG_PHASE_K2.md) | ✅ shipped |
| Code review + L4/L5 follow-up fixes | [AI_CLASSIFIER_REVIEW_REPORT.md](AI_CLASSIFIER_REVIEW_REPORT.md) | ✅ 6/6 issues closed |

## 3-tier cascade overview

```
                  ┌──────────────────────────────────────┐
                  │  zone (text + layer + change_type)   │
                  └─────────────────┬────────────────────┘
                                    ▼
       ┌────────────────────────────────────────────────────┐
       │ Stage 1 — keyword heuristic (always, < 1 ms/zone)  │
       │ [heuristic_classifier.py]                          │
       └─────────────────┬──────────────────────────────────┘
                         ▼  ChangeClassification(baseline)
       ┌────────────────────────────────────────────────────┐
       │ Stage 2 — embedding cosine (use_embedding=True)    │
       │ [embedding_classifier.py + backends/]              │
       │ Quality: Qwen3-Embedding-0.6B-GGUF (639 MB)        │
       │ Speed: mxbai-embed-large-v1 ONNX (670 MB)          │
       │ Auto: filesystem-availability bootstrap             │
       └─────────────────┬──────────────────────────────────┘
                         ▼  Optional[ChangeClassification]
                         │  None → keep Stage-1
                         │  classifier_used="embedding"
       ┌────────────────────────────────────────────────────┐
       │ Stage 3 — LLM (use_llm=True AND should_invoke())   │
       │ [llm_classifier.py + llm_backends/]                │
       │ Stub: deterministic test (always available)         │
       │ Ollama EXAONE: real LLM (1-3s/zone)                 │
       │ KDS RAG context (use_kds_rag=True): kds_rag/        │
       └─────────────────┬──────────────────────────────────┘
                         ▼  Optional[ChangeClassification]
                         │  classifier_used="hybrid"
                         ▼
                  Final classification
```

## Module map

### Core classifier package
`src/services/comparison/ai_classifier/`

| File | Phase | Role |
|---|---|---|
| `__init__.py` | All | Public API exports |
| `schema.py` | H | `AiClassifierConfig` + `ChangeClassification` + `ChangeCategory` enum |
| `public_api.py` | H/J/K | `classify_zones()` 3-tier cascade entry point |
| `heuristic_classifier.py` | H | Stage-1 keyword matcher |
| `embedding_classifier.py` | H/I | Stage-2 dispatcher + manifest cache |
| `llm_classifier.py` | J2/L4 | Stage-3 dispatcher + KDS RAG hook |
| `normalizer.py` | H | Text canonicalization for embedding |
| `manifest.py` | H | Embedding cache invalidation manifest |
| `prototype_corpus.py` | H | 51 seed phrases × 8 categories |
| `config_io.py` | J1/L1/L3/L4 | `ai_config.json` v2 persistence |

### Embedding backends
`src/services/comparison/ai_classifier/backends/`

| File | Phase | Role |
|---|---|---|
| `base.py` | H | `EmbeddingBackend` Protocol + `AbstractEmbeddingBackend` ABC |
| `__init__.py` | H | `BACKEND_REGISTRY` + `register_backend()` |
| `llama_cpp_qwen3_embedding.py` | H | Quality backend (Qwen3-Embedding GGUF) |
| `onnx_mxbai_large.py` | I | Speed backend (mxbai ONNX) |

### LLM backends
`src/services/comparison/ai_classifier/llm_backends/`

| File | Phase | Role |
|---|---|---|
| `base.py` | J2 | `LlmBackend` Protocol + `AbstractLlmBackend` ABC |
| `__init__.py` | J2 | `LLM_BACKEND_REGISTRY` + `register_llm_backend()` |
| `stub_llm.py` | J2 | Deterministic stub (always available) |
| `ollama_exaone.py` | J2/L1/L4 | Ollama HTTP client + JSON-mode prompt |

### KDS RAG clients
`src/services/comparison/ai_classifier/kds_rag/`

| File | Phase | Role |
|---|---|---|
| `base.py` | K2 | `KdsRagClient` Protocol + `AbstractKdsRagClient` ABC |
| `__init__.py` | K2 | `KDS_RAG_REGISTRY` + `register_kds_rag_client()` |
| `stub.py` | K2 | No-op (always returns "") |
| `local_json.py` | K2/L5 | File-based keyword retrieval |

### GUI
`src/gui/`

| File | Phase | Role |
|---|---|---|
| `ai_settings_dialog.py` | J1/L1/L3/L4 | Modal QDialog — embedding + LLM + KDS RAG sections |
| `drawing_compare_workbench.py` | J1/L1 | Menu integration + dispatcher cache invalidation |

### Tools
`tools/`

| File | Phase | Role |
|---|---|---|
| `verify_embedding_backends.py` | I/K1 | Bench harness with confusion matrix |
| `extract_zones_for_labeling.py` | K1 | review_state.json → labeling CSV |
| `label_zones_cli.py` | K1 | Interactive labeler (atomic save, resume) |
| `build_golden_set_v2.py` | K1 | CSV → golden_zones_v2.json |
| `golden_zones_v1.json` | I | 16-zone built-in fixture |

### CI
`.github/workflows/`

| File | Phase | Role |
|---|---|---|
| `ai-classifier.yml` | L2 | Linux runner, Python 3.10/3.12 matrix, ~3 min |

## Configuration reference

### `AiClassifierConfig` (18 fields persisted in `ai_config.json` v2)

```python
AiClassifierConfig(
    enabled=True,
    # ---- Stage-1 always on ----
    # ---- Stage-2 (embedding) ----
    use_embedding=True,
    embedding_backend_id="auto",         # "auto" | "llama_cpp_qwen3_embedding" | "onnx_mxbai_large"
    embedding_output_dim=None,           # None | 128 | 256 | 512 | 768 | 1024 (Matryoshka)
    embedding_threshold=0.7,             # cosine accept threshold
    # ---- Stage-3 (LLM) ----
    use_llm=False,
    llm_backend_id="ollama_exaone",      # "stub_llm" | "ollama_exaone"
    llm_host="http://localhost:11434",   # Ollama endpoint
    llm_model="exaone3.5:7.8b",          # Ollama model name
    llm_invoke_below_confidence=0.85,    # skip LLM when Stage-2 conf ≥ this
    llm_top_k_candidates=3,              # candidates passed to LLM
    llm_timeout_s=10.0,                  # per-zone wall-clock cap
    # ---- KDS RAG (Stage-3 enrichment) ----
    use_kds_rag=False,
    kds_rag_client_id="stub_kds",        # "stub_kds" | "local_json_kds"
    kds_rag_top_k=3,                     # # of clauses to inject
    kds_rag_timeout_s=5.0,               # per-zone retrieval cap
)
```

### Persistence

- File: `%LOCALAPPDATA%\DrawingCompareWorkbench\ai_config.json`
  (Windows) or `~/.config/DrawingCompareWorkbench/ai_config.json`
  (Linux/macOS)
- Schema: `v2` (backward-compatible with v1)
- Atomic write: temp + rename
- Validation: every field bounded; corrupt file → backup to `.bak`
  + fall back to `auto_mode()` defaults

## GUI workflow

```
[설정] → [🤖 AI 분류기 설정...]   (Ctrl+Shift+A)
   ┌──────────────────────────────────────────────┐
   │ Stage-2 임베딩 모드: Auto/Quality/Speed/Off   │ ← J1
   │   ├─ Cosine 임계값                            │
   │   └─ Matryoshka 출력 차원                     │
   ├──────────────────────────────────────────────┤
   │ ☐ Stage-3 LLM 캐스케이드 사용                  │ ← L1
   │   ├─ LLM 백엔드: Stub / Ollama EXAONE-3.5     │
   │   ├─ Ollama 호스트 (텍스트 입력)              │ ← L4
   │   ├─ Ollama 모델 (텍스트 입력)                │ ← L4
   │   ├─ LLM 호출 임계값                          │
   │   ├─ 후보 카테고리 수 (Top-K)                 │
   │   └─ 타임아웃                                 │
   ├──────────────────────────────────────────────┤
   │ ☐ KDS RAG 사용                                │ ← L3
   │   ├─ RAG 클라이언트: Stub / Local JSON        │
   │   ├─ 주입 조항 수 (Top-K)                     │
   │   └─ RAG 타임아웃                             │
   └──────────────────────────────────────────────┘
   [🧪 테스트 인코드 실행]   [OK] [Cancel]
```

**Cascade dependency** (enforced by dialog):
- Embedding **Off** → LLM auto-off + RAG auto-off
- LLM **Off** → RAG auto-off (RAG only fires inside LLM stage)
- LLM backend = **Stub** → host/model widgets disabled (Stub ignores them)

## Phase chronology

| Phase | Date | Commit | Scope |
|---|---|---|---|
| H Stage-2 W2 | Earlier | `6ccf19fe` | Concrete Qwen3 backend + 51 seed corpus + cosine dispatcher |
| I | Earlier | `a0da276d` | Dual-backend (Quality + Speed) + Matryoshka |
| J1 | 2026-05-06 | `f2027eed` | GUI mode selector + ai_config.json schema v1 |
| J2 | 2026-05-06 | `a4cf30e5` | Stage-3 LLM cascade (Stub + Ollama) |
| K1 | 2026-05-06 | `734bd1b6` | Golden-set tooling (4 CLI scripts) |
| L1 | 2026-05-06 | `60a5ba42` | LLM mode in dialog (schema v1 → v2) |
| K2 | 2026-05-06 | `d67dc16d` | KDS RAG integration |
| L2 | 2026-05-06 | `77f21638` | CI workflow |
| L3 | 2026-05-06 | `e01eeff1` | KDS RAG section in dialog |
| Review fixes | 2026-05-07 | `39c8aa4b` | Bugs #1, #2, #5, #8 |
| L5 | 2026-05-07 | `4467198b` | Issues #3, #4 (JSON parser + thread safety) |
| L4 | 2026-05-07 | `90a0c894` | Issue #6 (Ollama endpoint persistence) |

## Test inventory

| Suite | Tests | Notes |
|---|---:|---|
| `test_ai_classifier_heuristic.py` | ~30 | Stage-1 layer/keyword matching |
| `test_ai_classifier_normalizer.py` | ~25 | Canonicalization regex chain + idempotency |
| `test_ai_classifier_backends.py` | ~25 | Backend protocol + manifest fingerprint |
| `test_ai_classifier_embedding.py` | ~25 | Stage-2 dispatcher + cascade |
| `test_ai_classifier_llm.py` | ~30 | Stage-3 dispatcher + Ollama mock + thread safety |
| `test_ai_classifier_kds_rag.py` | 18 | KDS RAG protocol + dispatcher integration |
| `test_ai_config_io.py` | 30 | Schema v1↔v2 round-trip + validation |
| `test_workbench_ai_prepare.py` | ~10 | Workbench prepare_async + status polling |
| `test_ai_settings_dialog.py` | 38 | Dialog state + persistence + cascade gating |
| `test_k1_golden_tools.py` | 27 | Extract / label / build CLI tools |
| `test_verify_embedding_backends.py` | ~15 | Bench harness golden-set resolution |
| **Total** | **~270+** | All passing as of L4 commit |

## How to use the cascade end-to-end

### 1. Heuristic only (default — no setup required)

```python
from src.services.comparison.ai_classifier import (
    AiClassifierConfig, classify_zones,
)

cfg = AiClassifierConfig.heuristic_only()
results = classify_zones(zones, config=cfg)
```

### 2. Heuristic + Embedding (Quality mode requires Qwen GGUF)

Download `Qwen3-Embedding-0.6B-Q8_0.gguf` (639 MB) into
`%LOCALAPPDATA%\DrawingCompareWorkbench\ai_models\`, then:

```python
cfg = AiClassifierConfig.quality_mode()  # auto-detects Qwen
results = classify_zones(zones, config=cfg)
# results[i].classifier_used == "embedding" (when confident)
```

### 3. Full 3-tier hybrid (Heuristic + Embedding + LLM with KDS RAG)

```python
cfg = AiClassifierConfig.hybrid_mode()  # ships with stub_llm
# OR for real Ollama:
cfg = AiClassifierConfig(
    enabled=True,
    use_embedding=True, embedding_backend_id="auto",
    use_llm=True, llm_backend_id="ollama_exaone",
    llm_host="http://localhost:11434", llm_model="exaone3.5:7.8b",
    use_kds_rag=True, kds_rag_client_id="local_json_kds",
)
results = classify_zones(zones, config=cfg)
# results[i].classifier_used == "hybrid"
# results[i].raw_evidence["llm_rationale_ko"] = LLM explanation
```

### 4. From the Workbench GUI

1. Open Workbench, hit `Ctrl+Shift+A`
2. Pick mode in the AI settings dialog
3. Hit **🧪 테스트 인코드 실행** to verify the cascade works
4. Click OK — config persists to `ai_config.json` + dispatcher
   re-warms automatically

## Risk register (still open)

| # | Topic | Status |
|---|---|---|
| Architectural M3 | 3 backend registries could share generic `BackendRegistry[T]` (~120 LOC dedup) | Future Phase M3 |
| Architectural M4 | `AiClassifierConfig` will need split into nested configs at 25+ fields (currently 18) | Future Phase M4 |
| Test gap | No real-cascade end-to-end test (all use mocked dispatchers) | Future Phase M+ |
| CI gap | Linux runner only — Windows-specific path resolution not covered | Accepted limitation |
| User work | Real Ollama smoke (mock-only in tests) | User responsibility (`ollama pull`) |
| User work | K1 golden-set labelling (200 zones) | Awaiting `review_state.json` directory |

## Outstanding user actions

1. **Download Qwen GGUF** for Quality mode — required for Phase I bench
2. **Provide `review_state.json` directory** — required for K1 actual labelling
3. **Install Ollama + pull EXAONE-3.5-7.8B** — required for J2 end-to-end (mock tests pass without)
