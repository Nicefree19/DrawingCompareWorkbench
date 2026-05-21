# AI 변경 의미 자동 분류 — 로컬 OSS 모델 기반 설계 명세

**상태**: 설계 단계 (구현 후순위)
**범위**: Drawing Compare Workbench의 변경 zone에 의미 라벨/심각도/검토 우선순위 자동 부여
**제약**: 클라우드 API 사용 금지, 100% 로컬 OSS 모델, 사용자 PC에서 실행

---

## 1. 목표

검토자가 9-100개의 변경 zone을 한 도면에서 마주칠 때, 각 zone에 대해 자동으로:

1. **변경 카테고리** — 부재 변경 / 치수 변경 / 텍스트 변경 / 그리드 변경 / 레이아웃 변경 / 기타
2. **심각도 등급** — 임계 / 보통 / 사소 (구조 안전 영향도 기반)
3. **권장 액션** — 즉시 검토 / 일반 검토 / 시각만 확인 / 자동 confirm 가능
4. **요약 문장** — "보 단면 변경 5건 (KDS 14 31 04 5.3 휨강도 영향)" 한국어 1줄

수동 라벨링 시간을 zone당 30초 → 5초로 단축 (검토 효율 6배).

---

## 2. 기술 후보 비교

| 옵션 | 장점 | 단점 | 모델 크기 | RAM | 권장도 |
|---|---|---|---|---|---|
| **A. Sentence-Transformers (BGE-M3)** | 한국어 최강 임베딩, 1024차원, 휴리스틱 분류와 결합 | 진정한 LLM 추론 X (룰 기반 + 임베딩) | ~570MB | 1.5GB | ⭐⭐⭐⭐⭐ |
| **B. Llama.cpp + Qwen2-7B-Instruct (한국어 모델)** | 진짜 LLM, 자연어 요약 가능 | 추론 시간 1-3초/zone, GPU 권장 | ~4GB Q4 | 6GB | ⭐⭐⭐ |
| **C. Ollama + EXAONE-3.5-7.8B (한국어 특화 LG AI)** | 한국어 최고 품질, KDS/KCS 이해도 높음 | 첫 실행 다운로드 ~5GB | ~5GB Q4 | 8GB | ⭐⭐⭐⭐ |
| **D. ONNX Runtime + small classification head** | 1ms 추론, CPU OK | 학습 데이터 필요 (수동 라벨 100건+) | ~50MB | 200MB | ⭐⭐⭐ |
| **E. Hybrid: A + B 캐스케이드** | 임베딩 빠른 분류 + LLM 모호한 케이스만 정밀 분석 | 복잡도 ↑ | A+B | A+B | ⭐⭐⭐⭐⭐ |

**추천: 옵션 E (Hybrid)** — Sentence-Transformers로 1차 분류 (10ms/zone), 신뢰도 < 0.7인 케이스만 Qwen2/EXAONE으로 2차 분석 (1-3s/zone).

---

## 3. 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│ Workbench (drawing_compare_workbench.py)                 │
│  ↓ 비교 후 변경 zone 17개                                  │
│  ↓ _request_ai_classification_v2(zones)  [신규]          │
└─────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────┐
│ ChangeClassifier (src/services/comparison/ai_classifier.py) │
│                                                            │
│  Stage 1: Embedding-based (instant, 모든 zone)             │
│  ├─ encode(zone_metadata + nearby_text)                    │
│  ├─ cosine_similarity(zone_emb, prototype_embeddings)      │
│  ├─ if max_score >= 0.7 → 분류 확정 + 종료                  │
│  └─ else → Stage 2로 escalate                              │
│                                                            │
│  Stage 2: LLM-based (지연 OK, escalated만)                  │
│  ├─ build_prompt(zone, nearby_pdf_text, kds_excerpts)      │
│  ├─ ollama_client.generate(prompt) [or llama.cpp]          │
│  ├─ parse_structured_output(JSON)                          │
│  └─ 캐시 결과                                                │
└─────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────┐
│ KDS/KCS Knowledge Source                                  │
│  ├─ kcsc-rag-mcp (이미 운영 중) — 기준 검색 + RAG          │
│  └─ Local KDS index (sentence-transformers 임베딩)        │
└─────────────────────────────────────────────────────────┘
```

---

## 4. 모듈 설계

### 4.1 신규 파일

```
src/services/comparison/ai_classifier/
├── __init__.py                  # 공개 API
├── embedding_classifier.py      # Stage 1: 임베딩 기반
├── llm_classifier.py            # Stage 2: 로컬 LLM 호출
├── prompt_templates.py          # LLM 프롬프트 템플릿 (Korean)
├── prototype_corpus.py          # 사전 정의된 카테고리 프로토타입
├── classification_cache.py      # 결과 캐시 (zone hash → label)
└── runtime_config.py            # 모델 경로, GPU 사용 여부, 임계값
```

### 4.2 핵심 데이터 클래스

```python
from dataclasses import dataclass
from enum import Enum
from typing import Optional

class ChangeCategory(str, Enum):
    STRUCTURAL_MEMBER = "structural_member"      # 보, 기둥, 슬래브
    DIMENSION = "dimension"                      # 치수 변경
    TEXT_LABEL = "text_label"                    # 텍스트/주기
    GRID = "grid"                                # 그리드 라인
    LAYOUT = "layout"                            # 부재 위치 이동
    DETAIL_DRAWING = "detail_drawing"            # 디테일/단면
    NOTE = "note"                                # 주석
    UNKNOWN = "unknown"

class Severity(str, Enum):
    CRITICAL = "critical"     # 즉시 검토 (구조 안전 영향)
    NORMAL = "normal"         # 일반 검토
    MINOR = "minor"           # 시각 확인만

@dataclass(frozen=True)
class ChangeClassification:
    zone_id: str
    category: ChangeCategory
    severity: Severity
    confidence: float                  # 0.0-1.0
    suggested_action: str              # "confirm" | "review" | "ignore"
    summary_ko: str                    # "보 단면 변경 5건 (KDS 14 31 04)"
    kds_references: list[str]          # ["KDS 14 31 04 5.3", ...]
    classifier_used: str               # "embedding" | "llm" | "hybrid"
    elapsed_ms: float
    raw_evidence: dict                 # 디버깅용 원본 데이터
```

### 4.3 공개 API

```python
def classify_zones(
    zones: list[dict],                # overlay 정보
    pdf_text_snippets: dict[str, str], # zone_id → 주변 텍스트
    *,
    runtime_config: Optional[RuntimeConfig] = None,
    use_llm_fallback: bool = True,
    cache_dir: Optional[Path] = None,
) -> list[ChangeClassification]:
    """주어진 zone들을 분류한다.

    워크플로우:
        1. 캐시 확인 (zone hash 매칭)
        2. 임베딩 분류 시도
        3. 신뢰도 < threshold → LLM 호출 (use_llm_fallback=True 시)
        4. 결과 캐시 + 반환

    Args:
        zones: workbench의 _active_overlays_by_zone.values()와 호환되는 dict
        pdf_text_snippets: PyMuPDF page.get_text("text", clip=zone_bbox)로 추출
        runtime_config: 모델 경로/GPU/임계값 지정 (기본값: 환경변수 + ~/.tekla_mcp/ai_config.json)
        use_llm_fallback: False면 임베딩만 사용 (속도 우선)
        cache_dir: 결과 캐시 위치 (기본: %LOCALAPPDATA%/DrawingCompareWorkbench/ai_cache)

    Returns:
        len(zones)와 동일 길이의 ChangeClassification 리스트.
    """
```

---

## 5. Stage 1: 임베딩 분류 (BGE-M3 한국어)

### 5.1 모델 선택

- **BGE-M3** (BAAI/bge-m3) — 한국어 최강 다국어 임베딩
- 차원: 1024
- 모델 크기: 568MB
- 추론 속도: ~10ms/문장 (CPU), ~2ms (GPU)
- 라이선스: MIT

### 5.2 프로토타입 코퍼스 정의

```python
# src/services/comparison/ai_classifier/prototype_corpus.py
PROTOTYPES = {
    ChangeCategory.STRUCTURAL_MEMBER: [
        "보 단면 H400×200×8×13에서 H450×200×9×14로 변경",
        "기둥 강관 □400×400×16 추가",
        "슬래브 두께 200mm에서 250mm로 증가",
        "보 부재기호 G3 → G3' 변경",
    ],
    ChangeCategory.DIMENSION: [
        "치수 8000mm에서 8500mm로 변경",
        "스팬 6000 → 7000 수정",
        "베이스 플레이트 두께 25t → 28t",
    ],
    ChangeCategory.TEXT_LABEL: [
        "주기 추가: '시공 시 X-Ray 검사 필수'",
        "도면 번호 S20-0002 → S20-0002A 변경",
        "재질 표기 SS400 → SM490 수정",
    ],
    ChangeCategory.GRID: [
        "그리드 X3 위치 변경",
        "Y축 그리드 간격 조정",
        "그리드 명칭 X1A → X2 수정",
    ],
    # ... etc
}
```

각 프로토타입을 BGE-M3로 임베딩 → `prototype_embeddings.npy` (사전 계산, 패키지 포함).

### 5.3 추론 알고리즘

```python
def classify_embedding(zone_evidence: str) -> ChangeClassification:
    # 1. zone evidence 임베딩
    zone_emb = encoder.encode(zone_evidence)  # (1024,)

    # 2. 모든 프로토타입과 코사인 유사도
    sims = cosine_similarity(zone_emb, prototype_embeddings)  # (N_proto,)

    # 3. 카테고리별 최대 유사도 → top-3
    by_category = group_by_category(sims, prototype_metadata)
    top_category, top_score = max(by_category.items(), key=lambda kv: kv[1])

    # 4. 신뢰도 = top_score
    if top_score < 0.7:
        # 모호 → LLM에게 escalate
        return None

    # 5. 심각도는 카테고리에 매핑
    severity = SEVERITY_BY_CATEGORY[top_category]

    return ChangeClassification(
        category=top_category,
        severity=severity,
        confidence=top_score,
        suggested_action=ACTION_BY_SEVERITY[severity],
        summary_ko=f"{KOREAN_LABEL[top_category]} ({top_score:.0%} 신뢰도)",
        classifier_used="embedding",
        ...
    )
```

---

## 6. Stage 2: 로컬 LLM (Ollama 권장)

### 6.1 Ollama 통합 이유

- **간단**: `ollama pull qwen2:7b` → 즉시 사용
- **REST API**: HTTP localhost:11434, 어떤 언어든 호출
- **모델 관리**: 다운로드/캐시/업데이트 자동
- **GPU 자동 감지**: NVIDIA/AMD/Apple Silicon 자동 활용

### 6.2 모델 후보 (한국어 + 구조 도메인)

| 모델 | 크기 (Q4) | RAM | 한국어 | 추천도 |
|---|---|---|---|---|
| `qwen2:7b-instruct` | 4.4GB | 6GB | ⭐⭐⭐⭐ | 권장 (균형) |
| `gemma2:9b-instruct` | 5.5GB | 8GB | ⭐⭐⭐ | 영어/코딩 우수 |
| `exaone3.5:7.8b` | 4.8GB | 6GB | ⭐⭐⭐⭐⭐ | LG AI 한국어 특화 (최우선) |
| `llama3.1:8b` | 4.7GB | 6GB | ⭐⭐⭐ | 범용 |
| `solar:10.7b` | 6.5GB | 9GB | ⭐⭐⭐⭐ | Upstage 한국어 |

### 6.3 프롬프트 템플릿

```python
SYSTEM_PROMPT_KO = """
당신은 한국 건축/구조 도면 검토 보조 AI입니다. 두 도면 사이의 변경 영역을
분석하여 의미를 분류하고, 검토 우선순위를 결정합니다.

응답은 반드시 다음 JSON 형식으로:
{
  "category": "structural_member|dimension|text_label|grid|layout|detail_drawing|note|unknown",
  "severity": "critical|normal|minor",
  "summary_ko": "한 줄 한국어 요약 (예: '보 단면 변경 5건')",
  "kds_references": ["KDS 14 31 04 5.3", ...],
  "rationale_ko": "분류 근거 1-2문장"
}
"""

USER_PROMPT_TEMPLATE = """
[변경 영역 정보]
- 위치: 도면 페이지 {page}, bbox=[{x0}, {y0}, {x1}, {y1}]
- 레이어: {layer}
- 영역 내 텍스트: "{text_snippet}"

[비교 결과 메타데이터]
- 변경 유형: {change_type}
- 변경 픽셀 수: {raw_change_count}
- 심각도(휴리스틱): {heuristic_severity}

[참고 KDS 발췌]
{kds_excerpts}

위 변경 영역을 분류하고 JSON으로 응답하세요.
"""
```

### 6.4 호출 패턴

```python
import requests

def classify_llm(zone_evidence: dict, kds_excerpts: list[str]) -> ChangeClassification:
    prompt = USER_PROMPT_TEMPLATE.format(**zone_evidence, kds_excerpts="\n".join(kds_excerpts))
    response = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": runtime_config.llm_model,    # "exaone3.5:7.8b"
            "prompt": prompt,
            "system": SYSTEM_PROMPT_KO,
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 200},
        },
        timeout=10,
    )
    raw = response.json()["response"]
    parsed = json.loads(raw)

    return ChangeClassification(
        category=ChangeCategory(parsed["category"]),
        severity=Severity(parsed["severity"]),
        confidence=0.85,  # LLM 결과는 일반적으로 신뢰도 높게 책정
        summary_ko=parsed["summary_ko"],
        kds_references=parsed.get("kds_references", []),
        classifier_used="llm",
        ...
    )
```

---

## 7. 캐시 전략

```
%LOCALAPPDATA%/DrawingCompareWorkbench/ai_cache/
├── embeddings/
│   ├── bge_m3_v1_0.npy       # 프로토타입 임베딩 (사전 계산)
│   └── zones_<run_uuid>.npy  # 비교별 zone 임베딩 캐시
├── classifications/
│   └── <zone_hash>.json      # zone evidence hash → classification
└── llm_responses/
    └── <prompt_hash>.json    # LLM 호출 결과 (재실행 회피)
```

**Cache key**: `sha256(zone_id + change_type + raw_count + bbox + layer + text_snippet)` — 동일 evidence면 LLM 재호출 없음.

**TTL**: 무제한. KDS RAG 결과 변경 시에만 invalidate.

---

## 8. 의존성

```toml
# pyproject.toml 추가 (선택적 extras)
[project.optional-dependencies]
ai-classification = [
    "sentence-transformers>=3.0.0",
    "torch>=2.0.0",            # CPU only OK
    "numpy>=1.22",
    "ollama>=0.3.0",           # Python client (선택)
]
```

설치는 사용자 옵션:
```bash
pip install -e ".[ai-classification]"
```

미설치 시 workbench는 grace fully degrade — AI 분류 비활성화, 휴리스틱만 사용.

---

## 9. 사용자 설정

`%LOCALAPPDATA%/DrawingCompareWorkbench/ai_config.json`:

```json
{
  "enabled": true,
  "embedding_model": "BAAI/bge-m3",
  "llm_provider": "ollama",
  "llm_model": "exaone3.5:7.8b",
  "llm_host": "http://localhost:11434",
  "embedding_threshold": 0.7,
  "llm_timeout_s": 10,
  "use_gpu": "auto",
  "kds_rag_mcp_endpoint": "http://localhost:8765"
}
```

GUI 설정 다이얼로그에서 편집 가능.

---

## 10. 성능 예상

| 시나리오 | Stage 1만 | Hybrid | LLM only |
|---|---|---|---|
| 17개 zone 분류 | 0.2s | ~3s | 30s |
| 100개 zone 분류 | 1s | ~15s | 3분 |
| 500개 zone 분류 | 5s | ~60s | 15분 |

기본값: Hybrid, 신뢰도 ≥ 0.7면 임베딩만 사용 (대부분 cases).

---

## 11. 배포 모델

### 11.1 첫 실행 onboarding
- AI 분류 toggle: 기본 OFF
- 사용자가 ON → "AI 모델 다운로드 (~570MB BGE-M3, 시간 5분)" 다이얼로그
- 다운로드 후 자동 활성

### 11.2 Ollama 의존
- LLM fallback 사용 시 Ollama 로컬 설치 필요
- Onboarding 단계에서 Ollama 미설치 감지 시 안내:
  - "Ollama 설치 안내" 링크 (https://ollama.com/download)
  - "임베딩만으로 사용" 대체 옵션

### 11.3 오프라인 운영
- 모델 한 번 다운로드 후 인터넷 불필요 (보안 환경 OK)
- 모델 파일 시스템 위치: `%LOCALAPPDATA%/DrawingCompareWorkbench/models/`

---

## 12. 단계별 구현 로드맵

| 단계 | 작업 | 추정 |
|---|---|---|
| **1주차** | embedding_classifier.py + prototype_corpus.py + 단위 테스트 | 3일 |
| **2주차** | classification_cache.py + workbench 통합 (right-panel AI 라벨 표시) | 3일 |
| **3주차** | llm_classifier.py + Ollama 호출 + 프롬프트 튜닝 | 4일 |
| **4주차** | KDS RAG 연동 + GUI 설정 다이얼로그 + onboarding | 3일 |
| **5주차** | E2E 테스트 + 사용자 acceptance + 문서 | 3일 |

총 16일 (2-3개월 캘린더, 다른 우선순위 병행 시).

---

## 13. 측정 지표

배포 후 추적할 KPI:

1. **분류 정확도**: 사용자가 AI 라벨을 그대로 confirm한 비율 (목표 ≥ 70%)
2. **검토 시간 단축**: zone당 평균 검토 시간 (목표 30s → 5s)
3. **LLM fallback 비율**: 임베딩만으로 충분한 케이스 비율 (목표 ≥ 80%)
4. **사용자 토글률**: AI 분류 ON 유지율 (목표 ≥ 90%)

---

## 14. 위험 요소

| # | 위험 | 대응 |
|---|---|---|
| R1 | 한국 구조 도면 도메인 데이터 부족 → 분류 부정확 | 사전 정의 프로토타입 50개 시작 → 사용자 confirm 데이터로 fine-tune |
| R2 | LLM 추론 시간 1-3초 → 100개 zone 시 너무 느림 | Hybrid 캐스케이드 + 결과 캐시 + 비동기 백그라운드 분류 |
| R3 | 사용자 PC RAM 부족 (8GB 이하) | 임베딩만 모드 (1.5GB) 자동 fallback |
| R4 | Ollama 미설치 환경 | grace degrade — 임베딩만으로도 70% 정확도 확보 가능 |
| R5 | 모델 다운로드 (570MB-5GB) 사용자 부담 | 점진적 다운로드 + 일시정지/재개 + 회사 LAN 미러링 안내 |
| R6 | 잘못된 KDS 인용 (hallucination) | LLM 응답에서 KDS 번호 정규식 검증 + RAG 결과와 cross-check |

---

## 15. 다음 액션 (구현 시작 시)

1. `pyproject.toml`에 `ai-classification` extras 추가
2. `src/services/comparison/ai_classifier/__init__.py` 골격 생성
3. `tests/unit/services/comparison/ai_classifier/test_embedding_classifier.py` — TDD 시작
4. BGE-M3 모델 첫 다운로드 + 프로토타입 50개 임베딩 사전 계산
5. workbench 우측 패널에 "AI 분류" 토글 + 라벨 표시 위치 설계
6. KDS RAG MCP 연동 검증 (이미 운영 중인 `kcsc-rag-mcp` 활용)

---

**결론**: 클라우드 API 의존 없이 OSS 모델만으로 구현 가능. Hybrid 접근(임베딩 + LLM fallback)이 최적의 정확도/속도/비용 균형. 5주 구현, 사용자 PC당 한 번 580MB-5.5GB 다운로드 후 영구 오프라인 운영. 모든 코드 + 모델 가중치 OSS 라이선스 (MIT/Apache/Llama2 community).
