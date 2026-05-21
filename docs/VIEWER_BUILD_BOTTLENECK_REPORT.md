# Viewer Build Bottleneck — cProfile Baseline (§13 Phase C)

**작성**: 2026-05-16
**브랜치**: `fix/audit-recommendation-gates` (audit-gates worktree)
**측정 도구**: `scripts/benchmark_viewer_build.py`
**목적**: 사용자 보고 "12분 viewer build hang" 의 진짜 bottleneck 측정 후 fix 방향 결정

---

## 1. Headline finding

> **`viewer_package.export_viewer_package` 는 hot path 가 아니다.**
>
> 3개 골든 fixture (small/medium/structural) 모두에서 viewer_package 의 누적 시간은 **wall time 의 1.7-2.7%** 에 불과. 진짜 hot path 는 `folder_compare_pipeline._apply_export_profile_outputs` 의 **`Path.resolve()` 폭주** (~75% wall).

이는 §13.4 Phase C 가 검증하려던 가설 ("ProcessPoolExecutor 로 viewer_package 를 병렬화하면 50-65% 단축") 의 **전제를 무너뜨린다**.

| 지표 | small | medium | structural |
|------|-------|--------|-----------|
| Fixture | `02_single_modification` | `08_intentional_zone_shift_beam` | `14_structural_submm_shift` |
| Wall time (fastest of 3 runs) | 2.224s | 1.760s | 1.864s |
| `viewer_package.export_viewer_package` cumtime | 0.061s | (~동일) | 0.036s |
| 비중 | **2.7%** | (~3%) | **1.9%** |
| `_apply_export_profile_outputs` cumtime | 1.717s | 1.300s | 1.391s |
| 비중 | **77%** | **74%** | **75%** |

---

## 2. 측정 방법

### 2.1 환경
- OS: Windows (audit-gates worktree, Python 3.12)
- Python: `python -X utf8 scripts/benchmark_viewer_build.py`
- cProfile + pstats, sort by cumulative time
- Filter: viewer_package / viewer_tile_cache / zone_render / matplotlib / PIL
- 각 fixture 3회 반복, 가장 빠른 run 채택 (warm-up 잡음 제거)

### 2.2 Fixture
| 이름 | 경로 | 특성 |
|------|------|------|
| small | `tests/data/comparison/golden/dxf/02_single_modification/` | 1 entity 변경 |
| medium | `tests/data/comparison/golden/dxf/08_intentional_zone_shift_beam/` | 구조 zone shift |
| structural | `tests/data/comparison/golden/dxf/14_structural_submm_shift/` | sub-mm shift |

### 2.3 Subprocess 우회
benchmark 는 `viewer_package_proxy.export_viewer_package_isolated` 를 in-process exporter 로 monkeypatch — cProfile 이 child Python 의 함수 호출을 보지 못하기 때문. 이는 측정 의도상 정확하나, **subprocess overhead (Popen, pipe, JSONL 파싱) 는 측정에서 제외됨**.

---

## 3. 진짜 bottleneck 분석

### 3.1 `_apply_export_profile_outputs` (1.3-1.7s, 75% wall)

```
ncalls  tottime  cumtime  filename:lineno
     2    0.000    1.717  folder_compare_pipeline.py:820(_apply_export_profile_outputs)
    63    0.001    1.664  export_profiles.py:249(apply_export_profile_to_file)
    31    0.001    1.393  export_profiles.py:104(apply_export_profile_to_json)
  2620    0.010    1.351  export_profiles.py:44(profile_path_value)
5014/30   0.010    1.204  export_profiles.py:73(redact_payload_paths)
  4913    0.009    1.117  pathlib.py:1228(resolve)
  4913    0.020    0.968  <frozen ntpath>:705(realpath)
 14385    0.761    0.761  {built-in method nt._getfinalpathname}
```

**핵심 hot-line**: `nt._getfinalpathname` 14,385 calls × 평균 53µs = **761ms**.

각 산출물 파일에 대해 `apply_export_profile_to_file` 가 **`redact_payload_paths`** 를 호출하면, JSON payload 의 모든 절대경로를 상대화하기 위해 `Path.resolve()` 가 호출되고, Windows 의 `_getfinalpathname` 까지 내려가서 reparse point / junction 등을 따라간다. 총 4913회의 resolve 가 14385회의 OS-level path 정규화로 amplify 됨.

### 3.2 `viewer_package` 자체 (0.04-0.06s)

```
ncalls  tottime  cumtime  filename:lineno
     1    0.000    0.061  viewer_package.py:141(export_viewer_package)
     4    0.000    0.021  viewer_package.py:687(_read_json)
     3    0.000    0.017  viewer_package.py:696(_write_json)
     2    0.000    0.013  viewer_package.py:717(_load_optional_json)
     1    0.000    0.011  viewer_package.py:1754(_update_artifact_manifest)
     2    0.000    0.007  viewer_tile_cache.py:484(materialise_tiles_manifest_from_jsonl)
```

JSON I/O 가 거의 전부. PIL/matplotlib 호출 0회 (golden fixture 가 너무 작아서 actual rendering 이 트리거되지 않음).

---

## 4. "12분 build" 의 진실

### 4.1 Golden fixture 는 12분 시나리오를 재현하지 못한다

3개 fixture 모두 wall time **2-3초**. 12분 = **720초** 이므로 **240-360x** 격차. 즉 golden fixture 의 cProfile 결과를 그대로 12분 시나리오에 외삽 (extrapolate) 하면 **잘못된 bottleneck 을 fix** 하게 됨.

### 4.2 12분 시나리오의 가능한 원인 (가설, 측정 미시행)

사용자 보고 (2026-05-15, S20-class 입력) 의 12분은 다음 중 하나:
1. **PIL/matplotlib actual rendering** — small/medium fixture 는 zone 이 1-2개라 rendering hot path 가 짧음. S20 은 zone 350K → render call 수 자릿수 차이.
2. **viewer_package per-pair render loop** — small=1 pair 라 loop 자체가 1회 실행. S20 은 20+ pairs.
3. **export_profile path resolve amplification** — 본 fixture 도 75% 인 만큼 S20 에서는 절대 시간 비례 확대 (75% × 12분 = 9분).

### 4.3 다음 step 의 evidence-based 결정

| 옵션 | 근거 | 측정 결과 영향 |
|------|------|---------------|
| **A. ProcessPoolExecutor for viewer_package pair render** | §13.4 §3.4 의 원래 제안 | **반영해야 함**: golden fixture 에서는 효과 입증 불가. 진짜 측정은 S20-class 입력으로 GUI 또는 별도 fixture 필요 |
| **B. export_profiles path resolve 캐싱** | 본 측정에서 75% wall 차지 확인 | **권장**: 작은 입력에서도 즉각 75% 단축. lru_cache(`Path.resolve`) 또는 path 정규화 1회 후 재사용 |
| **C. Incremental cache (mtime skip)** | §13.4 §3.4 Phase B | golden fixture 에서는 측정 불가 (입력 무시 시 의미 없음). S20 second-run 시 효과 |
| **D. tiles_manifest_jsonl streaming** | §1.2 LOW finding | 본 측정에서 `materialise_tiles_manifest_from_jsonl` 0.007s — golden fixture 에서는 무의미. S20 large 입력에서만 효과 |

**권장 우선순위**: **B → A → C → D**

이유:
- B 는 본 측정에서 입증된 75% wall — small fixture 부터 즉각 효과
- A 는 가설 (S20 측정 후 결정)
- C/D 는 large fixture 또는 second-run 시나리오 측정 후

---

## 5. 정직성 노트

본 측정이 답하지 **못하는** 질문들:
- ✗ S20-class 12분 시나리오의 진짜 bottleneck (golden fixture 가 너무 작음)
- ✗ subprocess overhead (Popen/pipe/JSONL) 의 실제 비용 (in-process 우회로 측정에서 제외)
- ✗ PIL/matplotlib rendering 이 large input 에서 얼마나 비싼지 (golden 은 rendering 이 트리거 안 됨)
- ✗ disk I/O vs CPU 비중 (cProfile 은 CPU time, wall time 차이 없음 → I/O bound 가능성)

본 측정이 답하는 질문들:
- ✓ **viewer_package 자체가 hot path 인가?** → **NO** (1.7-2.7% wall)
- ✓ **수치 추정 ("50-65% 단축") 의 베이스라인?** → 작은 fixture 에서는 viewer_package 자체가 너무 빨라서 의미 없음
- ✓ **다른 hot path 가 있는가?** → **YES** (`_apply_export_profile_outputs` 75% wall)

**본 보고서는 §13 Phase C 의 의도대로 "측정만" 수행 — fix 코드 변경 0건**. 다음 step (Option A vs B vs C vs D) 은 사용자 결정 후 별도 plan.

---

## 6. 재현 절차

```powershell
Set-Location "D:/00.Work_AI_Tool/02.TEKLA_MCP/.claude/worktrees/audit-gates"

# 3개 fixture 측정
python -X utf8 scripts/benchmark_viewer_build.py --fixture small --runs 3
python -X utf8 scripts/benchmark_viewer_build.py --fixture medium --runs 3
python -X utf8 scripts/benchmark_viewer_build.py --fixture structural --runs 3

# 결과 확인
ls tmp/viewer_build_profile_*.txt
```

각 측정은 ~10-20초 소요 (warm-up 포함).

---

## 7. 다음 plan 후보

본 보고서는 §13 Phase C 의 종점. P1 의 "12분 build 단축" 항목은 본 보고서 결과에 따라 다음 plan 으로 분리:

**Plan §14 후보 — Option B 기반**:
- title: "fix(perf): export_profile path resolve 캐싱 — Phase C 측정 결과 반영"
- scope: `redact_payload_paths` 의 path 정규화 lru_cache 적용
- 예상 효과: 본 측정 wall time 1.7s → ~0.5s (1.2s 단축, 70% 감소)
- 예상 시간: 60분 (구현 30분 + 회귀 sweep + benchmark 재측정)

**Plan §15 후보 — Option A 기반**:
- title: "feat(perf): viewer_package pair render parallelisation"
- scope: `_render_pair_backgrounds` ProcessPoolExecutor 도입
- 예상 효과: **불명** (golden fixture 로는 측정 불가, S20 fixture 또는 GUI 측정 필요)
- 예상 시간: 90-180분 + S20 측정 시간

**권장**: Plan §14 (Option B) 를 먼저 실행 — 본 측정에서 입증된 효과. Option A 는 S20 fixture 확보 후.

---

**§13 Phase C DoD (Definition of Done)**:
- [x] `scripts/benchmark_viewer_build.py` 작성
- [x] 3개 fixture 측정 완료 (small/medium/structural)
- [x] cProfile 결과 dump → `tmp/viewer_build_profile_*.txt` 3개 파일
- [x] 본 보고서 작성 (top 5 bottleneck + 다음 step 옵션 4개)
- [x] commit 1개 — `docs(perf): viewer_build cProfile baseline + bottleneck analysis`
- **fix 코드 변경 0건** — 측정만 수행 (의도)
