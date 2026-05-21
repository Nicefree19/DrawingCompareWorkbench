# Phase I — 이중 백엔드 임베딩 (Quality + Speed Mode)

> 추천 모델, 설치 가이드, 모드 선택 의사결정 트리, 검증 절차.
> Phase H Stage-2 Week-2 (commit `6ccf19fe`)에서 만든 단일-백엔드
> 구조의 후속 — auto / quality / speed 3-mode 선택 + Matryoshka 절단
> + Workbench 백그라운드 워밍업.

## 모델 다운로드

두 백엔드 모두 **사전 다운로드**가 필요합니다 (offline 보장).
다운로드 후 `%LOCALAPPDATA%/DrawingCompareWorkbench/ai_models/` 아래
지정된 위치에 배치하세요.

### Quality Mode — Qwen3-Embedding-0.6B-GGUF (639 MB)

- **모델 카드**: https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF
- **권장 파일**: `Qwen3-Embedding-0.6B-Q8_0.gguf`
- **라이선스**: Apache-2.0
- **출력 차원**: 1024 (Matryoshka 32-1024)
- **배치 위치**:
  ```
  %LOCALAPPDATA%/DrawingCompareWorkbench/ai_models/Qwen3-Embedding-0.6B-Q8_0.gguf
  ```

### Speed Mode — mxbai-embed-large-v1 ONNX (670 MB)

- **모델 카드**: https://huggingface.co/mixedbread-ai/mxbai-embed-large-v1
- **권장 파일**: `onnx/model_quint8_avx2.onnx` + tokenizer
- **라이선스**: Apache-2.0
- **출력 차원**: 1024 (MRL → 512 default in speed_mode())
- **배치 위치**:
  ```
  %LOCALAPPDATA%/DrawingCompareWorkbench/ai_models/onnx_mxbai_large/
      ├── config.json                    (필수 marker)
      ├── tokenizer.json                  (필수 marker)
      ├── tokenizer_config.json
      ├── modules.json
      └── onnx/
          └── model_quint8_avx2.onnx     (또는 model_qint8_avx512.onnx 등)
  ```

ONNX 백엔드는 `sentence-transformers + onnxruntime + optimum` 세
패키지에 의존:
```bash
pip install sentence-transformers onnxruntime optimum
```

## 모드 선택 의사결정 트리

```
                     +---------------------+
                     |  AI 분류 사용 여부?  |
                     +---------------------+
                              | yes
                              v
                +----------------------------+
                |  cold-start latency 중요?   |
                +----------------------------+
                  no |                  | yes
                     v                  v
            +------------------+   +------------------+
            |  품질 우선?       |   |  speed_mode()     |
            +------------------+   +------------------+
              no |       | yes
                 v       v
        +-------------+ +-----------------+
        | auto_mode() | |  quality_mode() |
        +-------------+ +-----------------+
```

| 모드 | classmethod | backend_id | output_dim | cold start | 한국어 STS |
|---|---|---|---:|---:|---:|
| Quality | `AiClassifierConfig.quality_mode()` | `llama_cpp_qwen3_embedding` | 1024 (native) | 2-5초 | 0.7017 (MTEB ko) |
| Speed | `AiClassifierConfig.speed_mode()` | `onnx_mxbai_large` | 512 (Matryoshka) | 200-300 ms | ~0.62 (보고서 추정) |
| Auto | `AiClassifierConfig.auto_mode()` | `auto` (filesystem probe) | native | depends | depends |

**기본값**: Workbench 시작 시 `_load_ai_config_v2()`가
`%LOCALAPPDATA%\DrawingCompareWorkbench\ai_config.json`을 읽음. 파일이
없으면 `auto_mode()`. 사용자는 GUI 메뉴 **`[설정] → [🤖 AI 분류기 설정...]`**
(단축키 `Ctrl+Shift+A`)에서 모드를 직접 선택 가능 (Phase J Step 3
이후).

## verify_embedding_backends.py 사용법

벤치 하네스로 두 백엔드의 cold-start + per-zone latency + golden-set
정확도를 측정:

```bash
# 두 백엔드 모두 (모델 미설치 시 graceful skip)
python tools/verify_embedding_backends.py --backend both

# Quality 모드만 (Qwen GGUF 검증)
python tools/verify_embedding_backends.py --backend quality

# Speed 모드만 + Matryoshka 512 절단
python tools/verify_embedding_backends.py --backend speed --truncate-dim 512

# JSON 결과 출력 (CI 적재용)
python tools/verify_embedding_backends.py --backend both --output bench.json
```

출력 예시 (모델 모두 설치된 경우):
```
Backend: llama_cpp_qwen3_embedding (truncate_dim=None)
  Cold start: 3247 ms
  Per-zone p50: 18 ms / p95: 32 ms
  Golden accuracy: 14 / 16 (87.5%)
  Batch-16 encode: 187 ms

Backend: onnx_mxbai_large (truncate_dim=512)
  Cold start: 198 ms
  Per-zone p50: 4 ms / p95: 8 ms
  Golden accuracy: 12 / 16 (75.0%)
  Batch-16 encode: 29 ms
```

Golden zone-set은 첫 실행 시 `tools/golden_zones_v1.json`에 자동 생성
(16개 zone, 8 카테고리 × 2). 다른 fixture를 쓰려면 이 파일을 직접 수정.

## Matryoshka 차원 절단 (정확도 vs 메모리 trade-off)

mxbai와 Qwen 모두 **Matryoshka Representation Learning** 지원 — 1024D
벡터를 512 / 256 / 128로 슬라이스해도 의미 정보가 유지되도록 학습됨.

| 절단 차원 | 메모리 | 속도 | 정확도 영향 |
|---:|---:|---:|---|
| 1024 (native) | 100% | baseline | baseline |
| 512 | 50% | ~50% faster cosine | < 1pp 손실 (보고서) |
| 256 | 25% | ~75% faster | 2-5 pp 손실 (실측 필요) |
| 128 | 12.5% | ~87% faster | 5+ pp 손실 |

`speed_mode()`는 default 512. quality_mode()는 default native.
사용자 정의는 `embedding_output_dim` 필드:
```python
cfg = AiClassifierConfig(
    use_embedding=True,
    embedding_backend_id="onnx_mxbai_large",
    embedding_output_dim=256,  # 더 공격적 절단
)
```

**중요 — slice → normalize 순서**: 절단은 L2-normalise **이전**에
일어나야 unit-norm 출력. `AbstractEmbeddingBackend.encode()`가
이 순서를 강제 (subclass는 normalize=False로 raw 반환, base가 슬라이스
+ 재정규화).

## 5가지 위험 (보고서 §회귀 위험과 완화책 채택)

| # | 위험 | 완화책 |
|---|---|---|
| 1 | cold start > 200 ms | speed mode + workbench prepare_async() pre-warm |
| 2 | `H400×200×8×13` 같은 표기가 토크나이저마다 분절 | normalizer.canonicalize_zone_text()의 H_BEAM_RE / SQR_TUBE_RE / DIM_RE 정규화 |
| 3 | 카테고리 경계 모호성 | 카테고리당 다중 prototype + per-CATEGORY 마진 게이트 (top-1 vs top-2 카테고리 차이) |
| 4 | Windows GPU wheel 불안정 | "auto" mode에서 Qwen 실패 시 ONNX로 silent 전환 |
| 5 | 양자화 / 절단 후 5%+ 정확도 손실 | verify_embedding_backends.py로 baseline 1024 vs truncated 512 delta 측정 |

## 모듈 매핑

| 파일 | 역할 |
|---|---|
| [base.py](../src/services/comparison/ai_classifier/backends/base.py) | `EmbeddingBackend` Protocol + `AbstractEmbeddingBackend` ABC (truncate_dim handling) |
| [llama_cpp_qwen3_embedding.py](../src/services/comparison/ai_classifier/backends/llama_cpp_qwen3_embedding.py) | Quality backend (Phase H Stage-2 Week-2) |
| [onnx_mxbai_large.py](../src/services/comparison/ai_classifier/backends/onnx_mxbai_large.py) | **Speed backend (Phase I)** |
| [embedding_classifier.py](../src/services/comparison/ai_classifier/embedding_classifier.py) | Dispatcher with auto-select + Matryoshka |
| [schema.py](../src/services/comparison/ai_classifier/schema.py) | `AiClassifierConfig.{quality,speed,auto}_mode()` |
| [config_io.py](../src/services/comparison/ai_classifier/config_io.py) | **`ai_config.json` load/save (Phase J Step 3)** |
| [ai_settings_dialog.py](../src/gui/ai_settings_dialog.py) | **AI 모드 GUI 다이얼로그 (Phase J Step 3)** |
| [drawing_compare_workbench.py](../src/gui/drawing_compare_workbench.py) | `_kickoff_ai_prepare_v2` + `_poll_ai_prepare_v2` + `_show_ai_settings_dialog_v2` |
| [verify_embedding_backends.py](../tools/verify_embedding_backends.py) | Bench harness |
| [golden_zones_v1.json](../tools/golden_zones_v1.json) | 16-zone fixture (자동 생성) |

## Phase J Step 3 (J1) 완료 노트 (2026-05)

**목적**: Phase I의 `_load_ai_config_v2` 하드코딩(`auto_mode()`)을 사용자
설정 파일 + GUI 메뉴로 교체.

**주요 변경**:
- 신규: `config_io.py` — `default_ai_config_path()`, `load_ai_config()`,
  `save_ai_config()` (atomic temp+rename, schema_version="v1",
  validation, corrupt → .bak fallback)
- 신규: `ai_settings_dialog.py` — modal QDialog with mode combo
  (Auto/Quality/Speed/Off), threshold spinbox, output_dim combo,
  probe_available 상태 indicator, "테스트 인코드" 버튼
- 수정: `drawing_compare_workbench.py`
  - `_load_ai_config_v2`: `auto_mode()` 하드코딩 → `load_ai_config()` 호출
  - 신규 `_show_ai_settings_dialog_v2`: 다이얼로그 오픈 + 저장 후
    `clear_dispatcher_cache()` + `_kickoff_ai_prepare_v2()` 재호출
  - 메뉴: `[설정] → [🤖 AI 분류기 설정...]` (`Ctrl+Shift+A`)
- 신규 테스트: 31건 (config_io 17 + dialog 14)

**JSON 스키마 v1**:
```json
{
  "schema_version": "v1",
  "enabled": true,
  "use_embedding": true,
  "embedding_backend_id": "auto",
  "embedding_output_dim": null,
  "embedding_threshold": 0.7,
  "computed_at_utc": "2026-05-06T..."
}
```

LLM 필드(use_llm, llm_*) 미포함 — Phase J Step 5 (J2)에서 schema v2로 bump.

**검증 시나리오**:
1. ai_config.json 없음 → auto_mode() 동작 (기존과 동일 default)
2. 사용자가 메뉴 → Quality 선택 → ai_config.json 저장 → dispatcher 재로드
3. 손상된 ai_config.json → .bak 이동 + auto_mode() fallback
4. 모르는 backend_id → validation 실패 + auto_mode()
5. 다이얼로그 [테스트 인코드] → 1초 내 결과 표시 (모델 로드 후)

## 회귀 통계 (Phase I 완료 시점)

- 1437 / 1438 통과 (1 skip — sentence_transformers 미설치 환경에서
  ONNX 백엔드 model-dir-missing 분기 미도달 → 의도된 skip)
- 신규 테스트: I-1 (10) + I-2 (8) + I-4 (17) + I-5 (9) + I-6 (9) = 53건
- 신규 코드: ~1,300 LOC
- 신규 문서: ~250 lines (이 파일 + AI_EMBEDDING_PLAN_V2.md Phase I 노트)
