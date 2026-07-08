# AI 비전 도면 리뷰 — 고도화 기획서

- 작성일: 2026-07-01
- 상태: Phase 0 완료(골든 검증) · Phase 1 착수(호스트-Claude 비전 실증 완료) · 실도면은 측정된 블로커 2건
- 관련 메모리: `ai-agent-diff-mcp-rewire`, `golden-accuracy-baseline`, `oda-dual-path-slim-gap`, `dxf-objects-bloat`

---

## 1. 북극성

> 구조 도면 리비전 2장을 넣으면, AI가 **사람 검토자처럼 "무엇이·어디서·왜 중요하게 바뀌었는지"를
> 정확(완전·무환각)하고 재현 가능하게 판독한 리뷰 산출물**을 자동 생성한다.

## 2. 설계 원칙 (타협 불가 4개)

1. **위치·완전성 = 결정적 엔진.** 비전에 "전부 찾아라"를 시키지 않는다(환각·누락 차단).
2. **판독·서술·의미 = 비전.** 엔진이 짚어준 변경 구역 크롭만 본다(강점 발휘, 약점 회피).
3. **결정적 코어.** 같은 입력 → 같은 diff. 비전 출력은 캐시 + 검증으로 재현성 확보.
4. **실데이터 측정.** 골든 통과 ≠ 실도면 통과. 비전 정확도는 엔진과 **별도로** 측정한다.

## 3. 아키텍처

```
 두 도면(DXF/DWG)
      │
      ▼
 [결정적 엔진]  DwgDiffer.compare()  ── 정밀·완전·재현가능한 변경 위치/유형
      │  (added/deleted/modified + 좌표)
      ▼
 [구역화]  공간 클러스터링 → 변경 구역 bbox
      │
      ▼
 [렌더]  DxfRenderer  ── 구역별 before/after/overlay 이미지 (헤드리스)
      │
      ▼
 [비전 판독]  Claude(호스트, 현재) / Gemini·Claude API(서버측, 차후)
      │  ── 구조화 verdict: 무엇이/어떻게/유의성/confidence
      ▼
 [산출물]  변경 리뷰 리포트 · 구름마크 · 배치 요약
```

핵심: **비전은 엔진을 대체하지 않고 보강한다.** 비전을 정확하게 쓰려면 변경 구역만 크롭해서
줘야 하는데, 어디를 크롭할지는 엔진만 안다(부트스트랩 역설) → 엔진을 못 버린다.

## 4. 현재 구축 상태 (Phase 0 + 도구 인벤토리)

| 도구 | 위치 | 상태 |
|------|------|------|
| `compare_drawings` (MCP) | `07.Dwg_diff/mcp_server.py` | ✅ 실엔진 shell-out 재배선 |
| `compare_drawings_cli.py` | `DrawingCompareWorkbench/scripts/` | ✅ 헤드리스 diff JSON |
| `render_change_regions` (MCP) | `07.Dwg_diff/mcp_server.py` | ✅ 구역 before/after/overlay 렌더 |
| `render_change_regions_cli.py` | `DrawingCompareWorkbench/scripts/` | ✅ 오버레이(🔴삭제 🔵추가 ⚫불변) |
| `classify_changes` (MCP) | `07.Dwg_diff/mcp_server.py` | ✅ 엔티티 changes[] 소비 |
| 비전 판독 | 호스트 Claude | 🟡 Phase 1 실증 완료, 도구화 대기 |

- MCP 런타임 = 시스템 Python312(`D:\00.Work_AI_Tool\.mcp.json`, bare `python`). ezdxf/matplotlib/pymupdf/cv2/PIL 보유.
- **주의: 새 도구 반영엔 dwg-diff-mcp 서버 재시작 필수.**

## 5. 측정된 실도면 블로커 (골든 성공은 착시였음 — 2026-07-01 실측)

실 쌍 `240111_P5 복합동_PSRC,HMB 상세도.dxf` ↔ `_r1.dxf`(각 ~63.8MB)로 측정:

### 블로커 A — 토큰 한도 fail-closed
- 증상: `compare()` → `pipeline_status: failed`, `CAD_TOKEN_LIMIT_EXCEEDED: 2500001 > 2500000`.
- 원인: 정식 파이프라인의 2.5M 토큰 상한. cleaned DXF가 여전히 상한 초과(ODA OBJECTS bloat 잔재).
- 성격: **정직한 거부(버그 아님)**. 단, 초기 CLI가 이를 "ok/0-changes"로 위장 → **수정 완료**
  (이제 `status:error, COMPARE_IMPORT_FAILED`).
- 해결책(Phase 2): **slim-before-budget** — 토큰 상한 검사 전에 미사용 OBJECTS/프록시를 절단
  (메모리 `dxf-objects-bloat`: 65.7→3.7MB, 파싱 10× 선례 있음). 상한을 무작정 올리면 블로커 B로 전이.

### 블로커 B — 전체 시트 렌더 OOM
- 증상: `DxfRenderer.render_with_transform` → `MemoryError: bad allocation`.
- 원인: 693k mm 시트를 dpi100 풀캔버스로 그리려다 메모리 폭발(`dxf_renderer.py` 주석의 기존 경고).
- 성격: 초기 CLI가 조용히 진행 → **수정 완료**(이제 `status:error, RENDER_TOO_LARGE`).
- 해결책(Phase 2): **구역별 tight 렌더** — 전체를 그리지 않고 각 변경 구역만 set_xlim/ylim로
  좁게 렌더. 대형 시트에서도 고해상 크롭 확보(sub-pixel 문제 동시 해결).

> 교훈: "검증 안 된 토대 위에 층을 쌓지 않는다." 실측이 이 두 블로커를 조기에 드러냄.

## 6. 로드맵

### Phase 1 — 비전 판독 (착수, 핵심)
- 목표: 크롭 이미지 → "무엇이 어떻게 바뀌었나" 구조화 출력.
- 현재 경로(키 불필요): **호스트 Claude 비전**이 `render_change_regions` 산출 이미지를 보고 판독.
- 산출물: 아래 §7 verdict 스키마 + 프로토콜. (도구화 = Phase 1b)
- **실증 완료**: 골든 02에서 호스트 Claude가 정확 판독(§7.3).

### Phase 1b — 서버측 비전 도구 (키 확보 후)
- 목표: 헤드리스/자동화/Gemini용. 클라이언트 비전이 없어도 동작.
- 산출물: `analyze_change_with_vision` MCP 도구 — 제공자 플러그블(env `VISION_PROVIDER=claude|gemini`),
  크롭을 비전 API로 전송 → §7 스키마 반환.
- 선결: `anthropic`(또는 google-genai) SDK 설치 + API 키. **현재 둘 다 없음 → 미배선(정직 게이트).**
- 리스크: 기밀(도면이 외부 API로 나감) · 호출당 과금 · 비결정성(캐시로 완화).

### Phase 2 — 실도면 정확도 (블로커 A·B 해소)
- ① slim-before-budget(블로커 A) ② 구역별 tight 렌더(블로커 B) ③ 재원점 정합 적용(before/after 크롭 좌표 정렬).
- DoD: 실 P5 쌍에서 변경 구역 크롭이 정확히 그 변경을 담고, 비전이 판독 가능.

### Phase 3 — 신뢰성·측정 ("정확한"의 근거)
- ① 비전 자가검증(적대적 재질문·다수결·confidence 게이팅) ② 캐시(이미지 해시 키 → 결정성)
  ③ 평가 하네스(골든에 비전-라벨 truth 추가, 비전 precision/recall을 엔진과 분리 측정).
- DoD: 비전 레이어 자체의 측정 정확도 수치 존재(현재 엔진만 p=0.706/r=0.857, 비전은 미측정).

### Phase 4 — 사람이 받는 산출물
- ① 구름마크 리뷰(`compare_and_mark` 존재 + 비전 주석 결합) ② 리뷰 리포트(변경표+심각도+이미지+서술)
  ③ 폴더 대 폴더 배치 비교.

### Phase 5 — 제품화
- ① PDF/래스터 입력(벡터 없는 스캔도면 = 비전이 유일해) ② 기밀 옵션(로컬 비전 vs 클라우드)
  ③ 외부 사용자 dry-run(프로젝트 P0 연결).

## 7. Phase 1 상세 — 비전 판독 프로토콜

### 7.1 호출 순서 (호스트 Claude)
1. `render_change_regions(a, b)` 호출 → regions[] + 이미지 경로.
2. 각 region에 대해 `overlay_png`(우선) + `before_png`/`after_png`를 본다.
3. 아래 스키마로 verdict를 만든다.
4. 엔진의 `change_types`(delete/add/modified)와 교차검증한다.

### 7.2 verdict 스키마
```json
{
  "region_id": 0,
  "change_kind": "geometry_shift | member_added | member_removed | dimension_change | text_edit | other",
  "element": "판독한 부재/객체 (예: 수평 빔, 치수선, 주석)",
  "description": "무엇이 어떻게 바뀌었는지 사람 언어로",
  "evidence": "overlay/before/after에서 근거가 된 시각 단서",
  "magnitude_estimate": "소/중/대 또는 대략 수치",
  "significance": "구조적 유의성 판단 + 정밀확인 권장",
  "confidence": 0.0,
  "engine_cross_check": { "change_types": {}, "note": "엔진 결과와의 일치/재해석" }
}
```

### 7.3 실증 증거 — 골든 02 (호스트 Claude 판독, 2026-07-01)
엔진: `{deleted:1, added:1}` (location 500,402.5). 렌더: overlay에 회색 수평선 바로 아래 빨간 수평선.
호스트 Claude verdict:
```json
{
  "region_id": 0,
  "change_kind": "geometry_shift",
  "element": "수평 선분(내부 빔 추정)",
  "description": "구역 중앙 수평 선분이 미세 상향 이동. before는 교차점이 중심보다 아래, after는 중심에 근접.",
  "evidence": "overlay의 빨간 수평선(이동 전)이 회색선(불변/이동 후) 바로 아래 — 평행 이동 시그니처.",
  "magnitude_estimate": "소(~수 mm)",
  "significance": "빔 위치 변경이면 검토 필요. 미세 이동이라 도면 정정 수준일 수도. 엔진 좌표로 정밀확인 권장.",
  "confidence": 0.82,
  "engine_cross_check": { "change_types": {"deleted":1,"added":1},
                          "note": "엔진의 delete+add 페어를 '단일 이동'으로 재해석 — 엔진 FP(이동 분리) 개선" }
}
```
→ **비전이 값어치를 함**: raw 크롭에선 미세했으나 overlay로 판독 가능했고, 엔진의 delete+add를
"이동"으로 재해석해 알려진 오탐 패턴을 교정했다.

## 8. 측정 백본 ("정확한"을 수치로)

- 엔진(위치/완전성): 골든 15쌍 micro p=0.706 / r=0.857 / noise_fp=0 (측정됨, CI floor 0.70/0.85).
- **비전(판독): 미측정.** Phase 3에서 비전-라벨 truth로 별도 측정 필요. 비전 없이 "정확"은 주장일 뿐.

## 9. 시퀀싱 & 즉시 다음 한 수

```
Phase 1(호스트 비전 실증·완료) → Phase 2(블로커 A·B 해소; 실도면의 진짜 시험대)
   → Phase 3(측정으로 "정확" 잠금) → Phase 4(산출물) → Phase 5(제품화)
Phase 1b(서버측 API)는 키 확보 시 병렬.
```
- 즉시 후보: **블로커 A(slim-before-budget)** — 실도면서 비전이 볼 게 생기려면 이게 먼저.
- 그 다음: 블로커 B(tight 렌더) → 실 P5 쌍 end-to-end 비전 리뷰.
