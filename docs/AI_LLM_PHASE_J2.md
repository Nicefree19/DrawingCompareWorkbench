# Phase J Step 5 (J2) — Stage-3 LLM Cascade

> 3-tier classifier completed: Heuristic → Embedding → LLM.
> Phase J Step 3 (J1) wired the GUI mode selector for Stage-2.
> J2 adds the LLM tier on top — confident Stage-2 results skip
> the LLM round-trip, ambiguous ones escalate for disambiguation.

## 3-Tier Cascade Flow

```
                  ┌──────────────────────────────────────┐
                  │  zone (text + layer + change_type)   │
                  └─────────────────┬────────────────────┘
                                    ▼
       ┌────────────────────────────────────────────────────┐
       │ Stage 1 — keyword heuristic (always, < 1 ms/zone)  │
       │ classify_zone_heuristic()                          │
       └─────────────────┬──────────────────────────────────┘
                         ▼  ChangeClassification (baseline)
                         │
                         │  use_embedding=True?
                         ▼
       ┌────────────────────────────────────────────────────┐
       │ Stage 2 — embedding cosine (Qwen GGUF or mxbai     │
       │ ONNX or auto). Per-CATEGORY margin gate; abstain   │
       │ on tight margin. Stashes top-K candidates in       │
       │ raw_evidence for Stage-3 hand-off.                 │
       └─────────────────┬──────────────────────────────────┘
                         ▼  Optional[ChangeClassification]
                         │  None → keep Stage-1
                         │  classifier_used = "embedding"
                         │
                         │  use_llm=True AND should_invoke()?
                         ▼  (should_invoke: stage2 abstained OR
                         │   confidence < llm_invoke_below_confidence)
       ┌────────────────────────────────────────────────────┐
       │ Stage 3 — LLM (Stub or Ollama EXAONE).             │
       │ Picks best of top-K candidates + Korean rationale. │
       │ Closed-set: never returns category outside cands.  │
       │ Abstains (None) on timeout / parse error / out-of- │
       │ candidates pick. Caller keeps Stage-2 on abstain.  │
       └─────────────────┬──────────────────────────────────┘
                         ▼  Optional[ChangeClassification]
                         │  classifier_used = "hybrid"
                         │
                         ▼
                  Final classification
```

## Backends Shipped in J2

| Backend ID | When to use | Cold start | Per-zone latency | Deps |
|---|---|---:|---:|---|
| `stub_llm` | Tests, dev, "use LLM stage but offline" | 0 ms | <1 ms | none |
| `ollama_exaone` | Production with Ollama running | first POST ~ 200 ms (HTTP) | 1-3 s | `requests`, Ollama daemon, `ollama pull exaone3.5:7.8b` |

Both register at import time via the `LLM_BACKEND_REGISTRY` (mirrors
the embedding backends pattern). `available_llm_backends()` reports
which are reachable without invoking probe_available().

## Configuration

```python
from src.services.comparison.ai_classifier import AiClassifierConfig

# Full hybrid mode — heuristic + embedding (auto) + LLM (stub by default)
cfg = AiClassifierConfig.hybrid_mode()

# Or build manually
cfg = AiClassifierConfig(
    enabled=True,
    use_embedding=True,
    use_llm=True,
    embedding_backend_id="auto",
    llm_backend_id="ollama_exaone",     # "stub_llm" for testing
    llm_host="http://localhost:11434",
    llm_model="exaone3.5:7.8b",
    llm_timeout_s=10.0,
    llm_top_k_candidates=3,             # candidates to send to LLM
    llm_invoke_below_confidence=0.85,   # invoke when Stage-2 < this
)
```

| Field | Default | Purpose |
|---|---|---|
| `use_llm` | `False` | Enable Stage-3 LLM cascade |
| `llm_backend_id` | `"ollama_exaone"` | Registry key |
| `llm_host` | `"http://localhost:11434"` | Ollama endpoint |
| `llm_model` | `"exaone3.5:7.8b"` | Model name (Ollama only) |
| `llm_timeout_s` | `10.0` | Per-zone wall-clock cap |
| `llm_top_k_candidates` | `3` | Stage-2 → Stage-3 candidate set size |
| `llm_invoke_below_confidence` | `0.85` | Skip LLM when Stage-2 confidence ≥ this |

## Should-Invoke Policy (LLM gate)

`LlmClassifierDispatcher.should_invoke(stage2_result)` returns True when:

1. **Stage-2 abstained** — `classifier_used != "embedding"` (Stage-1
   heuristic passed through). LLM may add value via richer
   disambiguation.
2. **Stage-2 confidence too low** — `confidence < llm_invoke_below_confidence`.
   Default threshold 0.85 means highly-confident embedding hits skip
   the LLM round-trip.

This gate is critical for production: without it every zone pays the
1-3 s LLM round-trip and the workbench grinds on large folders. With
the gate, only ambiguous zones (typically 10-30%) escalate.

## Ollama Setup (사용자 후속 작업)

J2 ships the `ollama_exaone` backend implementation but DOES NOT
require Ollama installed in this iteration — `stub_llm` is the
default in `hybrid_mode()` so the cascade works out-of-the-box for
testing. To switch to real Ollama:

```powershell
# 1. Install Ollama (Windows .msi from https://ollama.com/download)
# 2. Start the daemon (auto-runs on Windows)
ollama serve   # or just launch the app

# 3. Pull the EXAONE model (one-time, ~5 GB)
ollama pull exaone3.5:7.8b

# 4. Verify
ollama list   # should show exaone3.5:7.8b

# 5. In ai_settings_dialog (Phase J Step 3), set llm_backend_id =
#    "ollama_exaone" — or edit ai_config.json directly:
#    "llm_backend_id": "ollama_exaone"
```

Verify the cascade works end-to-end:
```powershell
python -c "
from src.services.comparison.ai_classifier import (
    AiClassifierConfig, classify_zones,
)
cfg = AiClassifierConfig(
    enabled=True, use_embedding=False, use_llm=True,
    llm_backend_id='ollama_exaone',
)
out = classify_zones([{
    'zone_id': 'test', 'text_snippet': '보 단면 H400×200×8×13 변경',
    'layer': 'BEAM', 'change_type': 'modified',
}], config=cfg)
print(out[0].category.value, '|', out[0].classifier_used,
      '|', out[0].raw_evidence.get('llm_rationale_ko'))
"
```

Expected output: `structural_member | hybrid | <LLM rationale>`

## Prompt Template

```
당신은 한국 구조 도면 변경 분류 전문가입니다.

다음 도면 변경 영역을 분류 카테고리 중 하나로 분류하세요.

[변경 영역 설명]
{evidence}

[후보 카테고리 (이 중 하나만 선택)]
  - structural_member (구조 부재 변경)
  - dimension (치수 변경)
  - ... (top-K from Stage-2)

[참고 자료 (선택)]
{kds_context}   ← KDS RAG 결과 (Phase K)

응답은 반드시 다음 JSON 형식으로만 출력하세요. 다른 텍스트는 포함하지 마세요.
{"category": "<후보 ID 정확히>", "confidence": <0.0-1.0>, "rationale": "<한 문장 한국어 설명>"}
```

JSON parsing tolerates:
- Strict JSON-only responses (most common with `format=json` flag)
- Markdown-fenced JSON (```json ... ```)
- Prose-wrapped JSON (the parser scans for the first balanced `{...}`)

Safety: the response category MUST be in the candidate list AND in
the ChangeCategory enum. Anything else → abstain (None).

## Test Coverage

24 J2 tests in `test_ai_classifier_llm.py`:

| Group | Tests |
|---|---:|
| StubLlmBackend protocol | 4 |
| AbstractLlmBackend safety | 1 |
| Ollama HTTP (mocked) | 6 |
| Dispatcher should_invoke gate | 3 |
| Dispatcher classify_zone | 2 |
| Public API cascade | 4 |
| Singleton dispatcher cache | 2 |
| Schema hybrid_mode | 1 |
| (other) | 1 |

Plus regression: 1460 / 1461 across all comparison-services tests.

## Module Mapping

| 파일 | 역할 |
|---|---|
| [llm_backends/base.py](../src/services/comparison/ai_classifier/llm_backends/base.py) | `LlmBackend` Protocol + `AbstractLlmBackend` ABC + `LlmClassificationResult` dataclass |
| [llm_backends/__init__.py](../src/services/comparison/ai_classifier/llm_backends/__init__.py) | Registry + auto-import of concrete backends |
| [llm_backends/stub_llm.py](../src/services/comparison/ai_classifier/llm_backends/stub_llm.py) | Deterministic stub — picks first candidate |
| [llm_backends/ollama_exaone.py](../src/services/comparison/ai_classifier/llm_backends/ollama_exaone.py) | Ollama HTTP client + JSON-mode prompt |
| [llm_classifier.py](../src/services/comparison/ai_classifier/llm_classifier.py) | `LlmClassifierDispatcher` + `should_invoke` + cascade hook |
| [public_api.py](../src/services/comparison/ai_classifier/public_api.py) | `_apply_stage3` + `_extract_candidates` cascade integration |
| [schema.py](../src/services/comparison/ai_classifier/schema.py) | `AiClassifierConfig.hybrid_mode()` + LLM fields |
| [test_ai_classifier_llm.py](../tests/unit/services/comparison/test_ai_classifier_llm.py) | 24 tests |

## Phase K Hooks (이번 phase 외)

`LlmBackend.classify(... kds_context: str)` parameter is wired
end-to-end but the dispatcher always passes `""` for now. Phase K
will add a KDS RAG layer that:
1. Extracts a candidate KDS clause query from the canonicalised
   zone evidence
2. Calls the existing `kcsc-rag-mcp` server (already integrated
   per CLAUDE.md MCP servers section)
3. Stuffs the top-K clause text into `kds_context` for the LLM
4. The LLM cites clauses in `LlmClassificationResult.kds_references`

No changes to the LLM cascade architecture needed — `kds_context`
is already a pass-through parameter from the dispatcher to the
backend.

## Risks + Mitigations

| # | 위험 | 완화책 |
|---|---|---|
| 1 | LLM round-trip blocks Workbench (1-3 s/zone) | `should_invoke` gate skips confident Stage-2 zones; only ambiguous (10-30%) escalate |
| 2 | LLM picks category outside candidates (hallucination) | `_validate_response_category` rejects out-of-list answers → abstain |
| 3 | LLM returns malformed JSON | `_extract_first_json` tolerates markdown fences + prose wrapping; failure → abstain |
| 4 | Ollama not running / model not pulled | `probe_available` HTTP check + warmup raises `LlmBackendUnavailableError` with installation hint |
| 5 | LLM timeout under load | `llm_timeout_s` per-zone cap + abstain on timeout (Stage-2 result kept) |
| 6 | Cost amplification on enterprise folders | `llm_top_k_candidates=3` keeps prompt size bounded; per-zone gate via `should_invoke` |

## Phase L Candidates (J2 이후)

- ✅ **L1 — AI 설정 다이얼로그에 LLM 섹션** (완료) — use_llm 체크박스 +
  llm_backend_id combo + invoke threshold + top-K + timeout. Schema
  v1 → v2 bump (LLM 필드 5개 persist).
- KDS RAG integration (Phase K) — fill `kds_context` parameter
- LLM-side caching (zone hash → result, 24h TTL)
- Streaming response display in workbench (rationale appears as
  LLM generates)
- Multi-LLM ensemble (vote between Ollama EXAONE + GPT-4 + ...)
- Fine-tuned LLM on TEKLA-MCP review_state.json corpus (Phase K1
  golden-set as training data)

---

## 회귀 통계 (Phase J Step 5 (J2) 완료 시점)

- 1460 / 1461 통과 (1 skip — sentence_transformers 부분 설치 환경
  분기 미도달)
- J2 신규 테스트: 24건 (test_ai_classifier_llm.py)
- J2 신규 코드: ~1,200 LOC
  - llm_backends/base.py + __init__.py + stub_llm.py + ollama_exaone.py
  - llm_classifier.py
  - public_api.py 확장 (_apply_stage3 + _extract_candidates)
  - schema.py 필드 7개 추가 + hybrid_mode classmethod
  - embedding_classifier.py 1줄 추가 (top_categories)
- J2 신규 문서: 이 파일 (~250 lines)
