# AI Embedding Classifier — Plan V2 (Qwen3-**Embedding**-0.6B-GGUF)

> **⚠️ Model disambiguation (3rd-review fix)**. There are TWO public
> 0.6B Qwen3 GGUF artefacts on HuggingFace:
> 1. ``Qwen/Qwen3-0.6B-GGUF`` — **causal LM** (text generation).
>    Returns **token-level** embeddings via llama-cpp-python; sequence
>    embedding is NOT guaranteed and would require manual pooling
>    (mean / last-token) with no quality acceptance from upstream.
> 2. ``Qwen/Qwen3-Embedding-0.6B-GGUF`` — **dedicated embedding model**.
>    Instruction-aware retrieval, 32–1024 variable output dim, official
>    llama.cpp recipe in the model card.
>
> **Plan V2 targets #2 — `Qwen/Qwen3-Embedding-0.6B-GGUF`.** Every
> reference below ("Qwen3-Embedding-0.6B-GGUF", "Qwen3 backend") refers to the
> Embedding variant. The Week-2 backend implementation will validate
> the model name on first load and refuse to start with the causal
> variant. If the upstream Embedding GGUF stops being published, the
> fallback is **NOT** the causal variant — it's KR-SBERT ONNX INT8
> per § Selected stack.

**Status**: ACTIVE · supersedes `docs/AI_CHANGE_CLASSIFICATION_DESIGN.md`
(V1, BGE-M3 + EXAONE Hybrid) for the **embedding tier model choice**.
The V1 doc's overall architecture (Stage-1 heuristic + Stage-2
embedding + Stage-3 LLM, KDS RAG, hard constraints) remains valid;
this V2 doc replaces only the model + runtime selection based on the
external research report (`TEKLA_MCP Drawing Compare Workbench
임베딩 모델 선정 보고서.md`, 2026-05).

**TL;DR** — `Qwen/Qwen3-Embedding-0.6B` (GGUF Q8_0 ~0.65 GB or
Q4_K_M ~0.4 GB) loaded via `llama-cpp-python` in-process is the
single combination that satisfies every hard constraint (offline,
≤ 800 MB, Apache-2.0, Korean+English, Windows-native, no Docker /
no separate server). KR-SBERT ONNX INT8 stays as a license-pending
backup for ultra-fast cold-start scenarios.

---

## 1. Why V2 changes the model choice

V1 picked **BGE-M3** as the Stage-2 embedding model (top KLUE-STS
score, MIT licence). However:

| Constraint | BGE-M3 | Qwen3-Embedding-0.6B-GGUF |
|---|---|---|
| ≤ 800 MB single-model footprint | ❌ 2.27 GB FP32 weights | ✅ Q8_0 ~0.65 GB / Q4_K_M ~0.4 GB |
| Apache-2.0 commercial-friendly | ✅ MIT | ✅ Apache-2.0 |
| Korean + English mixed text | ✅ multilingual | ✅ 100+ languages, instruction-aware |
| In-process (no server) | ✅ ONNX | ✅ llama.cpp embedding API |
| Windows native | ✅ | ✅ (CPU + optional CUDA) |
| Offline after first download | ✅ | ✅ |

**BGE-M3 fails the size limit** — that's the disqualifier. The same
size limit also rules out KURE-v1 (2.27 GB), multilingual-e5-large
(1.12 GB weights), Qwen3-4B/8B (2.5–4.7 GB Q4), and PIXIE-Spell-v1.5
(F32). Qwen3-Embedding-0.6B is the only model that lands ≤ 800 MB while still
being multilingual + commercially licensed.

---

## 2. Selected stack

### Primary path
```
Qwen/Qwen3-Embedding-0.6B
  ├── GGUF quantisation:  Q8_0 (default) | Q4_K_M (memory-tight machines)
  ├── Runtime:            llama-cpp-python ≥ 0.3.22, in-process
  ├── Embedding dim:      1024
  ├── Languages:          100+ (instruction-aware)
  ├── Licence:            Apache-2.0
  └── Footprint:          0.4 GB (Q4_K_M) → 0.65 GB (Q8_0)
```

### Fallback path (license review pending)
```
snunlp/KR-SBERT-V40K-klueNLI-augSTS
  ├── Runtime:            sentence-transformers + onnxruntime (INT8)
  ├── Embedding dim:      768
  ├── Footprint:          468 MB
  ├── Cold-start:         ≤ 200 ms (BERT encoder, much lighter than decoder)
  └── ONNX INT8 speedup:  up to 3.08× vs PyTorch (ST docs benchmark)
```

### Out (size / license)
- BGE-M3, KURE-v1, multilingual-e5-large-instruct (size)
- Qwen3-Embedding-4B/8B (size)
- BAAI/bge-multilingual-gemma2 (Gemma licence + 37 GB)
- mxbai-embed-large-v1 (no Korean benchmark evidence)
- EXAONE / HyperCLOVA / Cohere v4 OSS (no public open checkpoint)

---

## 3. Architecture refinement

V1's three-stage cascade stays:
```
Stage 1: heuristic_classifier (already shipped, b9c90dd8)
   └─ 100% of zones, < 1 ms each → category + severity

Stage 2: embedding_classifier (NEW — this plan)
   └─ Re-classify zones with confidence < 0.7 from Stage 1
   └─ Qwen3-Embedding-0.6B-GGUF + cosine to 50-prototype corpus
   └─ Replaces Stage 1's category if cosine top-1 > threshold

Stage 3: LLM_classifier (FUTURE — separate plan)
   └─ Ollama-hosted LLM for ambiguous Stage-2 cases (margin < δ)
   └─ Adds Korean summary + KDS reference
```

V1 picked EXAONE-3.5-7.8B for Stage 3. V2 keeps that recommendation
(unaffected by the embedding model choice) but it's a separate
follow-up plan; this V2 only ships Stage 2.

---

## 4. New module structure

```
src/services/comparison/ai_classifier/
├── __init__.py                       # existing, public re-export
├── schema.py                         # existing, ChangeClassification
├── public_api.py                     # existing, classify_zones()
├── heuristic_classifier.py           # existing, Stage-1
├── normalizer.py                     # NEW — domain text canonicalisation
├── prototype_corpus.py               # NEW — 50-prototype seed corpus
├── backends/                         # NEW
│   ├── __init__.py
│   ├── base.py                       # EmbeddingBackend protocol
│   ├── llama_cpp_qwen3_embedding.py  # Qwen/Qwen3-Embedding-0.6B-GGUF backend
│   └── onnx_kr_sbert.py              # KR-SBERT ONNX INT8 backend
├── embedding_classifier.py           # NEW — Stage-2 dispatcher
└── manifest.py                       # NEW — model SHA + version pinning
```

### `EmbeddingBackend` protocol
```python
class EmbeddingBackend(Protocol):
    """Unified embedding API. Both Qwen3 and KR-SBERT must implement."""

    backend_id: str          # "llama_cpp_qwen3" | "onnx_kr_sbert"
    model_sha256: str        # Model file hash; pinned in manifest
    embedding_dim: int       # 1024 (Qwen3) / 768 (KR-SBERT)

    def encode(
        self,
        texts: Sequence[str],
        *,
        normalize: bool = True,
    ) -> np.ndarray:
        """Returns (n_texts, embedding_dim) float32 array."""

    def warmup(self) -> None:
        """Dummy single-text encode to amortise cold start."""

    def is_ready(self) -> bool:
        """True after first encode + warmup completes."""
```

This protocol ensures that swapping Qwen3 ↔ KR-SBERT requires zero
code change in `embedding_classifier.py` — only a config flip.

### Domain text normaliser
The report identifies a critical risk: **structural section
markers** (`H400×200×8×13`, `□400×400`) and **dimension values**
(`5500mm`, `5500`) dominate cosine similarity. Without
normalisation, "구조부재 변경" zones with H-beam text and "치수 변경"
zones with mm text get conflated.

`normalizer.py` defines `canonicalize_zone_text(raw)` which:
- Replaces H-beam tokens: `H400×200×8×13` → `H_BEAM_400_200_8_13`
- Replaces square tubes: `□400×400` → `SQR_TUBE_400_400`
- Replaces dimensions: `5500mm` → `DIM_5500`, `5500.5mm` → `DIM_5500_5`
- Replaces grid IDs: `GRID A-1` → `GRID_A_1`, `Y2'` → `GRID_Y2_PRIME`
- Replaces detail callouts: `DET-03` → `DETAIL_03`
- Strips multiple whitespace, normalises NFC Unicode

Each zone gets TWO embeddings:
- `raw_emb` — embedding of original text (preserves nuance)
- `norm_emb` — embedding of canonicalised text (preserves category)
The classifier uses weighted average: `0.4 * raw + 0.6 * norm`.

### Prototype corpus
50 seed examples (5-7 per category × 8 categories), each with:
- canonical Korean phrasing for that change category
- representative section/dimension/grid markers
- pre-computed embedding (saved as `.npy` alongside the seed JSON)

```json
{
  "version": "v2.0",
  "embedding_backend_sha": "abc...",   // ties prototypes to backend
  "normalizer_version": "v1",
  "categories": {
    "structural_member": [
      "보 단면 H400×200×8×13에서 H450×200×9×14로 변경",
      "기둥 강관 □400×400×16 추가",
      "..."
    ],
    "dimension": ["치수 8000mm에서 8500mm로 변경", ...],
    ...
  }
}
```

### Manifest (model + corpus pinning)
Single source of truth so a model upgrade doesn't silently break
the prototype space:
```json
{
  "embedding_backend": "llama_cpp_qwen3",
  "model_file": "Qwen3-Embedding-0.6B-Q8_0.gguf",
  "model_sha256": "...",
  "embedding_dim": 1024,
  "prototype_corpus_version": "v2.0",
  "normalizer_version": "v1",
  "computed_at_utc": "..."
}
```
Any field change → automatic prototype recompute on next launch.

---

## 5. 5-week roadmap (revised from V1)

The report's roadmap is **better than V1's** because it front-loads
text normalisation (the actual accuracy lever) instead of model
download. Adopted verbatim:

### Week 1 — Backend abstraction + normalizer
- `EmbeddingBackend` protocol + 2 backend skeletons (Qwen3 stub +
  KR-SBERT stub)
- `normalizer.py` regex chain + 30-test suite for invariants:
  same input → same hash → same canonical form
- 50-prototype JSON + reference embedding `.npy` (pre-computed at
  build time — first user run doesn't pay this cost)
- **Deliverable**: `pytest tests/.../test_normalizer.py` passes

### Week 2 — Qwen3-Embedding-0.6B-GGUF integration
- Wire `llama_cpp_qwen3.py` to actual GGUF file
- `embedding_classifier.py`: `zone_text → encode → cosine(top50) → top1`
- Startup preload (background thread, non-blocking)
- Evidence hash cache (zone hash → category)
- **Deliverable**: second-inference latency < 50 ms; CPU/GPU
  fallback self-test passes on Win 11

### Week 3 — Precision regression
- Q4_K_M vs Q8_0 comparison on 200-zone golden set
- Confusion matrix per category
- Low-margin abstain mechanism: surface "top-2 candidates + scores"
  in UI when margin < 0.05
- **Deliverable**: `golden_eval.json` + release-blocking thresholds
  (e.g. macro-F1 ≥ 0.70)

### Week 4 — Fallback backend
- KR-SBERT ONNX INT8 backend (only if legal clears the licence)
- `backend_registry` config switch — workbench operator can pin
  the backend per session
- ONNX/OpenVINO export pipeline so encoder swaps stay cheap
- **Deliverable**: workbench setting "AI backend: [Qwen3 | KR-SBERT |
  Disabled]"

### Week 5 — Domain adaptation decision
- Try prototype expansion + hard negatives + category-specific
  prompt rewrites BEFORE any LoRA work
- Only if accuracy still under threshold, run sentence-transformers
  trainer with `CachedMultipleNegativesRankingLoss` on labelled data
- **Deliverable**: go/no-go memo for finetune phase

---

## 6. Hard constraints — re-verified

From the original CLAUDE.md / V1 design + report's verification:

| # | Constraint | Status (V2) |
|---|---|---|
| 1 | No cloud API | ✅ llama.cpp + Ollama both local |
| 2 | Offline after first download | ✅ GGUF file is self-contained |
| 3 | No Docker / WSL / separate server | ✅ in-process Python |
| 4 | Hardware: i7 16 GB RAM, RTX 3060 (or CPU only) | ✅ Q4_K_M runs on 4 GB RAM machine |
| 5 | Licence: MIT / Apache / BSD / Llama2-community | ✅ Apache-2.0 (Qwen3) |
| 6 | Model footprint ≤ 800 MB | ✅ Q8_0 0.65 GB |
| 7 | Korean + English handling | ✅ 100+ language coverage |

---

## 7. Five risks + mitigations (from report § 회귀 위험)

### R1 — Numeric/section markers dominate cosine similarity
Section codes like `H400×200×8×13` and dimensions like `5500mm`
overweight the embedding away from category semantics.
**Mitigation**: dual-encoding (raw + normalised) with weighted
average. Documented in §4 Domain text normaliser.

### R2 — Decoder embedding cold load
Qwen3-Embedding-0.6B-GGUF is small but decoder-style; first encode can take
1-3 seconds even with the model loaded.
**Mitigation**: app-startup preload on background thread + dummy
warm-up call before user sees the workbench.

### R3 — Windows GPU variability
ONNX DirectML is in sustained-engineering mode (WinML is the new
recommendation). llama.cpp Windows CUDA wheels have ABI variability.
**Mitigation**: CPU baseline must always work; GPU is opt-in
acceleration only. Pin wheel hashes per release.

### R4 — Unclear-licence model accidentally adopted
KR-SBERT and ko-sroberta-multitask have no explicit commercial
licence statement on their HuggingFace cards.
**Mitigation**: `LICENSE_MANIFEST.json` in build pipeline; only
licence-cleared models register in the default backend list.

### R5 — Model upgrade silently breaks prototype space
Upgrading the embedding model invalidates pre-computed prototypes,
threshold values, and confusion patterns — but won't crash; it
just classifies wrong.
**Mitigation**: Manifest (§4) ties prototype version to backend
SHA. Any field change forces a prototype recompute on next launch.

---

## 8. Skeleton code that lands in this commit

This commit ships only the **structural skeleton** so the next
session (Week 1 deliverable) can fill it in without architectural
rework:

- `src/services/comparison/ai_classifier/normalizer.py` — full
  implementation (no model dependency, no cold start)
- `src/services/comparison/ai_classifier/backends/base.py` — Protocol
  definition + ABC fallback
- `src/services/comparison/ai_classifier/backends/__init__.py` —
  registry stub
- `src/services/comparison/ai_classifier/manifest.py` — schema
  dataclass + load/save helpers
- `tests/unit/services/comparison/test_normalizer.py` — invariant
  tests for the regex chain (no model needed)
- `tests/unit/services/comparison/test_embedding_backend_protocol.py`
  — protocol-conformance tests against a fake backend

The actual Qwen3 backend (with `llama_cpp_qwen3.py`) lands in
**Week 2** because it requires the GGUF file download + CI infra.

---

## 9. Open questions for review

1. **GGUF file hosting**: should the GGUF live in our git LFS, a
   private artefact bucket, or be downloaded on first launch from
   HuggingFace? Trade-off: bundle size vs first-run latency vs
   corporate network restrictions.
2. **Q8_0 vs Q4_K_M default**: the report recommends Q8_0 (~0.65
   GB) but Q4_K_M (~0.4 GB) trades quality for half the footprint.
   Need a one-evening A/B on real customer text to decide.
3. **CUDA wheel pinning**: llama-cpp-python Windows CUDA wheel is
   currently `0.3.22` per report. Should we wheel-mirror or rely on
   the upstream pip resolver?
4. **License clearance for KR-SBERT**: the fallback is blocked
   until our legal team confirms the licence terms. Owner?
5. **EmbeddingGemma-300m** (referenced as `google/embeddinggemma-300m`
   in report): hadn't been considered in V1; report doesn't deeply
   evaluate but it's worth a 2-hour spike before locking Qwen3.

---

## 10. References

Source: `C:\Users\user\Downloads\TEKLA_MCP Drawing Compare Workbench
임베딩 모델 선정 보고서.md` (2026-05, external deep-research
output, ~211 lines).

Key cited model cards:
- https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
- https://huggingface.co/Qwen/Qwen3-Embedding-4B-GGUF
- https://huggingface.co/BAAI/bge-m3
- https://huggingface.co/snunlp/KR-SBERT-V40K-klueNLI-augSTS
- https://huggingface.co/mixedbread-ai/mxbai-embed-large-v1
- https://huggingface.co/google/embeddinggemma-300m
- https://github.com/ggerganov/llama.cpp (embedding mode)
- https://github.com/abetlen/llama-cpp-python

V1 design doc (now superseded for model choice only):
- `docs/AI_CHANGE_CLASSIFICATION_DESIGN.md`

---

## Verdict

V2 = adopt Qwen3-Embedding-0.6B GGUF + llama-cpp-python as the
Stage-2 embedding backend, with KR-SBERT ONNX INT8 reserved as
backup pending legal review. Roadmap front-loads text normalisation
(the actual accuracy lever) over model download. Five-week plan,
weekly deliverables independently shippable.

**Next commit (this one)**: skeleton modules + tests so Week 1 can
start without architectural rework.
**Subsequent commits**: Week 1 → Week 5 deliverables, one commit
per week, each tied to a measurable acceptance criterion.

---

## Phase I 완료 노트 (2026-05)

V2 plan §우선순위와 §로드맵의 다음 항목을 모두 출시:

- **Top 1 (quality)**: `Qwen/Qwen3-Embedding-0.6B-GGUF` Q8_0 — 출시
  완료 (commit `6ccf19fe`, Phase H Stage-2 Week-2).
- **Top 2 (speed)**: `mixedbread-ai/mxbai-embed-large-v1` ONNX —
  Phase I 출시. Apache-2.0, 670 MB, 1024D MRL → 512D 절단 default.
- **자동 모드**: `embedding_backend_id="auto"` — 파일시스템 가용성
  bootstrap. Qwen GGUF가 `ai_models/`에 있으면 Qwen, 없으면 mxbai
  ONNX, 둘 다 없으면 abstain (Stage-1 휴리스틱 단독 동작).
- **Matryoshka**: backend-side truncate (slice → re-normalise — 보고서
  §양자화와 압축 권장 순서). `AbstractEmbeddingBackend.encode(...,
  truncate_dim=N)` 시그니처에서 base class가 슬라이스 + L2 재정규화.
- **Workbench prepare_async wiring**: `QTimer.singleShot(800,
  _kickoff_ai_prepare_v2)` after zone-render prewarm(500 ms).
  daemon thread가 GGUF / ONNX warm-up. `lbl_status_v2`에 진행 상태
  3-state 표시 ("AI 분류기 준비 중…" / "✓ AI 준비 완료
  (backend, NNNms)" / "⚠ AI 모델 미설치 — 휴리스틱 분류만 사용").
- **verify_embedding_backends.py**: cold-start + per-zone p50/p95
  + 16-zone golden-set accuracy 측정. quality / speed / both 모드.
  모델 미설치 시 graceful skip.

회귀: 1437 passed / 1 skipped (Phase H Stage-1 + Stage-2 Week-2 +
Phase I 합계).

후속 (Phase J 후보):
- 200-500 zone golden-set 확장 (보고서 §회귀 위험)
- KR-SBERT ONNX 추가 (라이선스 클리어 후)
- Stage-3 LLM enrichment (Ollama EXAONE-3.5-7.8B)
- web client (Transformers.js + WebGPU; 보고서 §WebGPU/WASM)
