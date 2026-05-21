# Phase J Step 4 (K1) — 200-zone Golden Set Workflow

> 보고서 §회귀 위험 R3 ("정확도 5%+ 손실") 측정 근거를 만들기 위한
> 워크플로우. Phase I 16-zone v1 fixture를 200-zone v2로 확장 →
> Quality vs Speed 모드 정밀 비교 + 카테고리별 confusion matrix.

## 워크플로우 개요

```
1. extract_zones_for_labeling.py  ── 사용자의 review_state.json 트리에서
       │                              200 zone CSV 추출 (stratified)
       ▼
   tools/labeling/zones_to_label_v1.csv   (expected_category 컬럼 BLANK)
       │
2. label_zones_cli.py             ── 인터랙티브 CLI 라벨러
       │                              (사용자 수동 작업 ~50분 ~ 2시간)
       ▼
   같은 CSV 파일 (expected_category 채워짐)
       │
3. build_golden_set_v2.py         ── CSV → JSON 변환 + balance 옵션
       │
       ▼
   tools/golden_zones_v2.json
       │
4. verify_embedding_backends.py --golden-set v2  ── Quality vs Speed
       │                                            정밀 비교 + 카테고리별
       ▼                                            recall + confusion matrix
   bench_real_v2.json
```

## 도구별 사용법

### 1단계 — Zone 추출

```powershell
python tools/extract_zones_for_labeling.py `
    --root "D:\path\to\drawing_compare_runs" `
    --output tools/labeling/zones_to_label_v1.csv `
    --max-zones 200 `
    --seed 42 `
    --include-statuses confirmed,needs_review
```

**인자**:
| 인자 | 기본값 | 설명 |
|---|---|---|
| `--root` | (필수) | `change_zones.json` 파일들이 들어있는 루트 디렉토리 (재귀 검색) |
| `--output` | `tools/labeling/zones_to_label_v1.csv` | 출력 CSV 경로 |
| `--max-zones` | 200 | 최대 샘플 수 (0=제한 없음) |
| `--seed` | 42 | 재현성 위한 랜덤 시드 |
| `--include-statuses` | `confirmed,needs_review` | 포함할 review_state status (또는 `all`) |

**Stratified sampling 정책**:
1. **Status 우선순위**: confirmed > needs_review > review_required > ignored > false_positive
   - confirmed zone은 사용자가 검증한 것이므로 가장 신뢰할 만한 학습 시그널
2. **Drawing 균형**: 같은 status 내에서 drawing_number별로 round-robin
   - 한 도면에 zone이 100개 있어도 다른 도면들과 균형있게 샘플링
3. **재현성**: `--seed`로 결정적 결과

**출력 CSV 컬럼**:
```
zone_id, pair_id, drawing_number, source_a_filename, source_b_filename,
change_type, raw_count, layer, entity_type, review_status,
expected_category   ← 사용자가 채워 넣을 컬럼 (BLANK)
```

### 2단계 — 라벨링 (사용자 수동 작업)

#### 옵션 A — Excel/스프레드시트
CSV를 Excel에서 열고 `expected_category` 컬럼만 채우면 됩니다. 허용 값:
- `structural_member` — 구조 부재 변경 (보, 기둥, 슬래브, 벽체)
- `dimension` — 치수 변경
- `text_label` — 텍스트/주기
- `grid` — 그리드 라인
- `layout` — 부재 위치 이동
- `detail_drawing` — 디테일/단면
- `note` — 주석
- `unknown` — 분류 미정

#### 옵션 B — CLI 라벨러 (권장)

```powershell
python tools/label_zones_cli.py --csv tools/labeling/zones_to_label_v1.csv
```

per-zone 화면:
```
[12 / 200]   labelled so far: 11
  drawing : S20-0002
  pair    : p3
  zone_id : z42
  files   : S20-0002_REV0.dwg  →  S20-0002_REV1.dwg
  layer   : BEAM
  entity  : MTEXT
  change  : modified, count=3
  status  : needs_review

  [1] structural_member  [2] dimension      [3] text_label
  [4] grid               [5] layout         [6] detail_drawing
  [7] note               [8] unknown
  [s] skip   [b] back     [q] quit (saves progress)
  >
```

**기능**:
- 매 입력마다 atomic CSV write — Ctrl-C / 정전 시에도 진행 보존
- 이미 라벨된 zone은 다음 실행 시 자동 스킵 (resume)
- `b` (back)로 직전 zone 다시 라벨링
- `q` (quit)로 진행 보존 + 종료
- 종료 시 카테고리별 분포 히스토그램 표시

**진행 상태만 확인**:
```powershell
python tools/label_zones_cli.py --csv tools/labeling/zones_to_label_v1.csv --show-distribution-only
```

### 3단계 — Golden set JSON 생성

```powershell
python tools/build_golden_set_v2.py `
    --input tools/labeling/zones_to_label_v1.csv `
    --output tools/golden_zones_v2.json `
    --version v2 `
    --description "200-zone golden set from S20 series + ..." `
    --balance
```

**인자**:
| 인자 | 기본값 | 설명 |
|---|---|---|
| `--input` | (필수) | 라벨된 CSV 경로 |
| `--output` | `tools/golden_zones_v2.json` | 출력 JSON 경로 |
| `--version` | `v2` | JSON 내 schema version |
| `--description` | 자동 생성 | JSON 내 description 필드 |
| `--balance` | (off) | 카테고리별 개수를 가장 작은 카테고리에 맞춰 균형 |
| `--seed` | 42 | balance 시 random sub-sample seed |

**Balance 옵션 권장**: 카테고리별 개수가 크게 다르면 micro-average accuracy가 큰 카테고리에 dominated 됨. `--balance`로 모든 카테고리를 동일 개수로 맞추면 per-category recall이 더 공정하게 비교됨.

### 4단계 — 정밀 평가

```powershell
python tools/verify_embedding_backends.py `
    --backend both `
    --golden-set v2 `
    --output bench_real_v2.json
```

`--backend both` 시 출력:
```
Backend: llama_cpp_qwen3_embedding (truncate_dim=None)
  Cold start: 3247 ms
  Per-zone p50: 18 ms / p95: 32 ms
  Golden accuracy: 168 / 200 (84.0%)
  Per-category recall:
    structural_member    32/40  (80%)
    dimension            22/25  (88%)
    text_label           18/25  (72%)
    grid                 24/25  (96%)
    layout               17/25  (68%)
    detail_drawing       21/25  (84%)
    note                 18/20  (90%)
    unknown              16/15  (107%)
  Batch-16 encode: 187 ms

Backend: onnx_mxbai_large (truncate_dim=None)
  Cold start: 198 ms
  ...
```

JSON 출력 (`bench_real_v2.json`)에는 `confusion_matrix` 필드도 포함:
```json
{
  "results": [{
    "backend_id": "llama_cpp_qwen3_embedding",
    "golden_accuracy_pct": 84.0,
    "per_category_accuracy": { ... },
    "confusion_matrix": {
      "structural_member": {"structural_member": 32, "dimension": 5, ...},
      ...
    }
  }]
}
```

## 카테고리당 권장 목표

| 카테고리 | 최소 | 권장 |
|---|---:|---:|
| structural_member | 30 | 40 |
| dimension | 25 | 30 |
| text_label | 20 | 25 |
| grid | 20 | 25 |
| layout | 20 | 25 |
| detail_drawing | 20 | 25 |
| note | 15 | 20 |
| unknown | 10 | 15 |
| **합계** | **160** | **205** |

200-zone full set이면 카테고리별 평균 25 zone — 통계적으로 의미있는 per-category recall 측정 가능.

## 도구 매핑

| 파일 | 역할 |
|---|---|
| [extract_zones_for_labeling.py](../tools/extract_zones_for_labeling.py) | review_state.json + change_zones.json → 라벨링 후보 CSV |
| [label_zones_cli.py](../tools/label_zones_cli.py) | 인터랙티브 CLI 라벨러 (atomic save, resume, distribution) |
| [build_golden_set_v2.py](../tools/build_golden_set_v2.py) | CSV → golden_zones_v2.json (balance 옵션) |
| [verify_embedding_backends.py](../tools/verify_embedding_backends.py) | `--golden-set v2` 옵션 + confusion matrix + per-category recall |
| [test_k1_golden_tools.py](../tests/unit/tools/test_k1_golden_tools.py) | 27 단위 테스트 (extract / build / label / verify resolution) |

## 위험 + 완화책

| # | 위험 | 완화책 |
|---|---|---|
| K1-R1 | 라벨링 부담 (200 zone × 15초 = 50분) | label_zones_cli atomic save + resume → 분할 작업 가능 |
| K1-R2 | text_snippet 누락 (production zone에 없음) | build_golden_set_v2가 layer + entity + change_type + count로 합성 |
| K1-R3 | 사용자 라벨 일관성 (같은 zone이 다르게 라벨될 수 있음) | --include-statuses confirmed로 검증된 결과만 활용 |
| K1-R4 | 카테고리 불균형 (structural_member 100개 vs unknown 5개) | --balance 옵션으로 per-category equity 강제 |
| K1-R5 | 한 도면의 zone이 압도적으로 많음 | extract 시 drawing_number-stratified round-robin |

## 다음 단계 (K1 완료 후)

- **자동 라벨 학습**: 200-zone v2로 사용자 fine-tune (CachedMultipleNegativesRankingLoss)
- **continuous evaluation**: CI에 verify --golden-set v2 추가하여 회귀 감지
- **Phase L (Stage-3 LLM 정확도)**: golden set으로 LLM의 should_invoke 임계값 튜닝
- **K2 (500+ zone)**: K1을 운영하며 점진적 확장

---

## 회귀 통계 (Phase J Step 4 (K1) 도구 완료 시점)

- 1510 / 1511 통과 (1 skip on missing sentence_transformers branch)
- K1 신규 테스트: 27건 (`test_k1_golden_tools.py`)
- K1 신규 코드: ~1,100 LOC
  - tools/extract_zones_for_labeling.py (~300)
  - tools/label_zones_cli.py (~250)
  - tools/build_golden_set_v2.py (~250)
  - tools/verify_embedding_backends.py 확장 (~50)
- K1 신규 문서: 이 파일 (~250 lines)

**미완료**: 사용자 라벨링 작업 (review_state.json 디렉토리 + ~1-2일).
도구는 모두 준비됨 — 사용자가 본인의 review_state.json 위치를 알려주면
`tools/extract_zones_for_labeling.py --root <path>`로 즉시 시작.
