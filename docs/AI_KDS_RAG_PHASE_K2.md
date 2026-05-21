# Phase K2 — KDS RAG Integration

> Stage-3 LLM (J2) `kds_context` 파라미터에 한국 건설기준 (KDS / KCS)
> 조항 텍스트를 자동 주입. LLM이 카테고리 선택 시 표준 조항을 인용
> 가능 → 검토자가 근거 추적 가능.

## 데이터 흐름

```
Stage 1 (heuristic)
   ↓
Stage 2 (embedding cosine)
   ↓ raw_evidence["top_categories"] = [(cat, score), ...]
   ↓
Stage 3 trigger (cfg.use_llm + should_invoke)
   │
   ├── KDS RAG retrieve (cfg.use_kds_rag)
   │      │
   │      ├── stub_kds          → "" (no-op, default)
   │      └── local_json_kds    → "[KDS XX YY ZZ §S] title: text\n..."
   │
   ↓ kds_context injected into LLM prompt
   ↓
LLM classify(zone_evidence, candidates, kds_context=...)
   ↓
ChangeClassification(classifier_used="hybrid")
   raw_evidence["kds_rag_client"] = "stub_kds" | "local_json_kds" | "none"
   raw_evidence["kds_rag_context_chars"] = N (0 when no RAG)
```

## Clients Shipped in K2

| client_id | When to use | Deps | I/O |
|---|---|---|---|
| `stub_kds` | Default (RAG off) | none | always returns "" |
| `local_json_kds` | Offline production deployment | none (json stdlib) | reads `kds_clauses.json` from AppData |

Future:
- `http_kds` — POST to a configurable HTTP endpoint
- `mcp_kds` — wrapper around the kcsc-rag-mcp server (only when running
  behind an MCP-aware client; not used in production runtime)

## Configuration

### Enable KDS RAG via AiClassifierConfig

```python
from src.services.comparison.ai_classifier import AiClassifierConfig

cfg = AiClassifierConfig(
    enabled=True,
    use_embedding=True,
    use_llm=True,
    llm_backend_id="ollama_exaone",
    use_kds_rag=True,                       # opt-in (default False)
    kds_rag_client_id="local_json_kds",     # or "stub_kds"
    kds_rag_top_k=3,                        # # of clauses to inject
    kds_rag_timeout_s=5.0,                  # per-zone wall-clock cap
)
```

### Schema field defaults

| Field | Default | Purpose |
|---|---|---|
| `use_kds_rag` | `False` | Master enable for RAG layer |
| `kds_rag_client_id` | `"stub_kds"` | Registry key for the RAG client |
| `kds_rag_top_k` | `3` | # of clauses to inject |
| `kds_rag_timeout_s` | `5.0` | Per-zone wall-clock cap |

## Local-JSON KDS clauses file

The `local_json_kds` client reads from `kds_clauses.json` in any of:

1. `%LOCALAPPDATA%\DrawingCompareWorkbench\kds_clauses.json` (production)
2. `./data/kds_clauses.json` (development)
3. `<project_root>/data/kds_clauses.json` (fallback)

### File schema

```json
{
  "version": "v1",
  "clauses": [
    {
      "code": "KDS 24 24 00",
      "section": "5.3",
      "title": "휨강도",
      "category_hints": ["structural_member", "dimension"],
      "keywords": ["보", "휨", "단면"],
      "text": "보의 휨모멘트 단면강도는 ϕMn으로 계산한다..."
    }
  ]
}
```

| Field | Required | Purpose |
|---|---|---|
| `code` | optional | KDS clause code (e.g. "KDS 24 24 00") |
| `section` | optional | Sub-section (e.g. "5.3") |
| `title` | optional | Korean title |
| `category_hints` | optional | When set, clause only fires for these candidate categories. Empty/missing = applies to all. |
| `keywords` | required | Korean keywords for substring scoring |
| `text` | **required** | Korean clause body — gets injected into LLM prompt |

### Retrieval algorithm

1. **Filter by category hint**: clauses with `category_hints` ⊉ candidates → skip. Hint-less clauses always pass.
2. **Score by keyword overlap**: per clause, sum `len(kw)` for each keyword whose lowercase form appears in the canonicalised zone evidence.
3. **Rank descending**, take top-K.
4. **Format**: `[code §section] title: text` (per-clause text truncated to 400 chars).

Intentionally simple — no embedding, no BM25, no external dep. Deterministic + sub-millisecond per call. Phase L+ may upgrade.

## LLM Prompt impact

When `use_kds_rag=True` and the client returns non-empty context, the
Ollama prompt template's `[참고 자료 (선택)]` section gets the
retrieved clauses verbatim:

```
[참고 자료 (선택)]
[KDS 24 24 00 §5.3] 휨강도: 보의 휨모멘트 단면강도는 ϕMn으로 계산한다...
[KDS 14 30 00 §4.1] 치수 표기: 치수는 mm 단위로 표기한다...
```

The LLM is instructed to cite these in its `rationale` when relevant.
Phase L+ may add a `kds_references` parser to surface the cited clause
codes in `LlmClassificationResult.kds_references` (currently always `[]`).

## Cascade Safety

- **RAG client unavailable** → `get_kds_rag_client` falls back to stub
  (warning log) → empty context → LLM proceeds without RAG
- **Retrieval crash** → caught in dispatcher → empty context (cascade
  never crashes from RAG layer)
- **Empty context** → no degradation; LLM classifies with prompt
  template's `[참고 자료]` showing `(없음)` (the OllamaExaoneLlmBackend
  default)
- **Timeout exceeded** → up to client implementation; current local_json
  is sub-ms so timeout doesn't trigger; future http_kds will respect
  `kds_rag_timeout_s`

## Module Mapping

| 파일 | 역할 |
|---|---|
| [kds_rag/base.py](../src/services/comparison/ai_classifier/kds_rag/base.py) | `KdsRagClient` Protocol + `AbstractKdsRagClient` ABC (catch-all retrieve safety) |
| [kds_rag/__init__.py](../src/services/comparison/ai_classifier/kds_rag/__init__.py) | Registry + auto-import |
| [kds_rag/stub.py](../src/services/comparison/ai_classifier/kds_rag/stub.py) | No-op stub — always returns "" |
| [kds_rag/local_json.py](../src/services/comparison/ai_classifier/kds_rag/local_json.py) | Local file client with keyword scoring + category hint filter |
| [llm_classifier.py](../src/services/comparison/ai_classifier/llm_classifier.py) | Dispatcher reads `cfg.use_kds_rag` + lazy-instantiates client + threads context into LLM prompt |
| [schema.py](../src/services/comparison/ai_classifier/schema.py) | 4 new K2 fields: use_kds_rag + kds_rag_client_id + kds_rag_top_k + kds_rag_timeout_s |
| [test_ai_classifier_kds_rag.py](../tests/unit/services/comparison/test_ai_classifier_kds_rag.py) | 18 tests (protocol + 2 clients + dispatcher integration + cache key) |

## Test Coverage (K2 신규 테스트 18건)

| Group | Tests |
|---|---:|
| Stub client (always-empty) | 4 |
| Local-JSON client (file resolution + scoring + format + truncation) | 8 |
| Dispatcher integration (off/stub/local_json/cache) | 6 |
| Schema to_dict | (covered above) |

회귀 통계: 274 passed / 1 skipped (was 256 before K2, +18).

## Phase L+ Hooks

- ✅ **Phase L3 GUI** (완료) — AI 설정 다이얼로그에 KDS RAG 섹션 추가:
  use_kds_rag 체크 + client_id combo (Stub/LocalJSON) + top_k + timeout +
  probe indicator. Embedding 모드 Off → LLM Off → RAG Off cascade.
  config_io.py schema v2 persist (use_kds_rag, kds_rag_client_id,
  kds_rag_top_k, kds_rag_timeout_s 4개 필드).
- **Phase L4 HTTP client**: `http_kds` for self-hosted RAG service
- **Phase L5 Embedding-based**: replace keyword scoring with embedding cosine when corpus grows beyond ~100 clauses
- **kds_references parser**: scan LLM rationale for cited clause codes, populate `LlmClassificationResult.kds_references` so the workbench detail panel can link to source

## Risks + Mitigations

| # | 위험 | 완화책 |
|---|---|---|
| K2-R1 | KDS clauses 파일 없음 → RAG 무용 | stub_kds default + local_json graceful empty return |
| K2-R2 | 파일 손상 → 모든 RAG 호출 실패 | json.JSONDecodeError 캐치 + 빈 결과 + warning log |
| K2-R3 | 한국어 keyword 토큰화 부정확 | 단순 substring (lowercase) → 한국어 명사는 substring 잘 동작; 형태소 분석은 Phase L+ |
| K2-R4 | 카테고리 힌트 누락 시 전 카테고리 매칭 → noise | 의도된 fallback (clauses에 hints 추가하면 scope 좁아짐) |
| K2-R5 | LLM이 RAG context 무시 | prompt template은 `[참고 자료 (선택)]` 라벨 — model이 인용 책임 (rationale에 명시 권장) |
