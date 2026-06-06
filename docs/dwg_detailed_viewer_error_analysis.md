# 캐드도면 상세비교 모듈 오류 원인 분석

## 개요
DWG 상세 비교 뷰어(`_open_detailed_viewer`) 실행 시 발생할 수 있는 오류 원인을 분석합니다.

## 오류 발생 지점 분석

### 1. DWG → DXF 변환 단계
**위치**: `drawing_compare_tab.py::_open_detailed_viewer` (DWG→DXF 변환 블록)

**잠재적 오류**:
- `ODAConverterNotFoundError`: ODA File Converter 미설치
- `DWGConversionError`: 변환 실패 (파일 손상, 권한 문제 등)
- `subprocess.CalledProcessError`: 변환 프로세스 실행 실패

**확인 방법**:
```python
# 로그 확인
logger.info("DWG→DXF 변환 완료 (%.2fs)", convert_time)
```

**해결 방안**:
- ODA File Converter 설치 확인
- 변환 실패 시 상세 오류 메시지 표시
- 임시 파일 경로 권한 확인

### 2. DXF 렌더링 단계
**위치**: `drawing_compare_tab.py::_open_detailed_viewer` (DxfRenderer 렌더 블록)

**잠재적 오류**:
- `ImportError`: ezdxf, matplotlib 미설치
- `FileNotFoundError`: DXF 파일 경로 문제
- `MemoryError`: 대용량 파일 렌더링 시 메모리 부족
- `ValueError`: DXF 파일 형식 오류

**확인 방법**:
```python
# 로그 확인
logger.info("DxfRenderer 상세 렌더링 완료 (render=%.2fs, max_edge_px=%s)", render_time, max_edge_px)
```

**해결 방안**:
- 라이브러리 설치 확인 (`pip install ezdxf matplotlib`)
- 파일 크기 확인 및 메모리 체크
- max_edge_px 조정으로 메모리 사용량 제한

### 3. 파일 경로 및 메타데이터 처리
**위치**: `drawing_compare_tab.py::_open_detailed_viewer` (changes_data 생성 블록)

**잠재적 오류**:
- `KeyError`: metadata 키 누락
- `AttributeError`: change 객체 속성 누락
- `TypeError`: 좌표 값 타입 오류

**확인 방법**:
```python
# change_type 정규화 로그 확인
change_type = normalize_change_type(meta.get("change_type"))
```

**해결 방안**:
- change_type 정규화 함수 적용 (완료)
- 메타데이터 기본값 처리 강화
- 좌표 값 검증 추가

### 4. 뷰어 다이얼로그 생성
**위치**: `drawing_compare_tab.py::_open_detailed_viewer` (viewer.load_comparison 호출)

**잠재적 오류**:
- `AttributeError`: 뷰어 객체 속성 누락
- `TypeError`: 이미지/변환 데이터 형식 오류
- `QWidget` 관련 오류: Qt 초기화 문제

**확인 방법**:
```python
# 뷰어 로드 로그 확인
viewer.load_comparison(...)
```

**해결 방안**:
- 뷰어 초기화 검증
- 이미지 데이터 형식 확인
- 변환 데이터 유효성 검사

### 5. 좌표 변환/정합 문제
**위치**: `drawing_comparison_viewer.py::_cad_to_rect`

**잠재적 오류**:
- `KeyError`: transform 키 누락 (`img_width`, `scale_x` 등)
- `ZeroDivisionError`: 도면 범위가 0으로 계산되는 경우
- 좌표 단위 불일치: DWG 단위가 mm가 아닐 경우 스케일 왜곡

**확인 방법**:
```python
logger.debug(f"transform: {self._old_transform}")
logger.debug(f"transform: {self._new_transform}")
```

**해결 방안**:
- 변환 정보 None/키 누락 시 fallback 처리 확인
- 도면 단위 점검 (DWG 단위 설정)
- 극단적으로 큰/작은 범위는 max_edge_px 조정으로 완화

### 6. 이미지 변환/픽스맵 변환 실패
**위치**: `drawing_comparison_viewer.py::_numpy_to_pixmap`

**잠재적 오류**:
- `TypeError`: numpy dtype이 uint8이 아닐 경우 QImage 변환 실패
- `ValueError`: 채널 수가 예상과 다른 경우 (2채널/5채널 등)

**확인 방법**:
```python
logger.debug(f"img dtype={img.dtype}, shape={img.shape}")
```

**해결 방안**:
- 렌더러에서 uint8 보장
- 채널 수가 3/4가 아닌 경우 사전 변환

### 7. 캐시/품질 설정 관련 문제
**위치**: `drawing_compare_tab.py::_get_dwg_render_max_edge_px`, 캐시 LRU

**잠재적 오류**:
- 해상도 과도 축소로 상세 확인 불가
- 캐시된 이미지가 의도보다 오래 유지되는 경우
- 단일 이미지가 200MB 초과 시 OOM 위험

**확인 방법**:
```python
logger.info("상세 뷰 렌더 캐시 상태: old=%s new=%s max_edge_px=%s", ...)
logger.warning("단일 이미지 캐시 스킵: %.1fMB > 제한=%dMB", ...)
```

**해결 방안**:
- 상세 뷰 품질 옵션(빠름/보통/고품질) 조정
- 캐시 크기 조정 또는 수동 캐시 초기화
- 단일 이미지 200MB 초과 시 캐시 스킵 (자동 적용)

### 8. DPI 제한 관련 문제
**위치**: `dxf_renderer.py::_cap_dpi`

**잠재적 오류**:
- 극소 도면 (< 0.1 inch) 렌더링 시 DPI 폭주
- 입력 DPI가 10 미만인 경우 렌더링 품질 저하
- 과도한 DPI로 인한 메모리 부족

**확인 방법**:
```python
logger.warning("DXF 도면 크기가 매우 작음: %.4f x %.4f inches, 안전 DPI(%d) 적용", ...)
logger.info("DXF 렌더링 DPI 제한 적용: %.2f -> %.2f (max_edge_px=%s)", ...)
```

**해결 방안**:
- MIN_SAFE_DPI (10) / MAX_SAFE_DPI (300) 자동 적용
- 극소 도면은 MIN_SAFE_DPI로 강제 렌더링
- 최종 반환값에도 MIN_SAFE_DPI 강제 적용

### 9. change_type 정규화 문제
**위치**: `drawing_compare_tab.py::normalize_change_type`, `drawing_comparison_viewer.py::_draw_annotations`

**잠재적 오류**:
- Enum 객체, 대문자, 점 표기법 등 다양한 형식 혼용
- 알 수 없는 change_type이 "추가/수정"으로 잘못 표시

**확인 방법**:
```python
logger.warning("Unknown change_type detected: %r -> %r, using 'unknown'", raw_type, normalized)
```

**해결 방안**:
- `normalize_change_type()` 함수로 모든 형식 정규화
- 유효 타입: added, deleted, modified, unknown
- "unknown"은 회색 구름마크로 별도 표시 (빨간색과 구분)

## 오류 진단 체크리스트

### 필수 확인 사항
1. **라이브러리 설치 확인**
   ```bash
   pip install ezdxf matplotlib
   ```

2. **ODA File Converter 설치 확인**
   - 기본 경로: `C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe`
   - 환경변수: `ODA_CONVERTER_PATH`

3. **파일 경로 확인**
   - Old/New 파일 경로 유효성
   - 읽기 권한 확인
   - 파일 크기 확인 (10MB+ 대용량 파일)

4. **메모리 확인**
   - 대용량 파일 렌더링 시 메모리 사용량
   - max_edge_px 설정 확인
   - 상세 뷰 품질 옵션 확인 (자동/빠름/보통/고품질)

### 로그 확인 포인트
1. **변환 시간 로그**
   ```
   DWG→DXF 변환 완료 (X.XXs)
   ```

2. **렌더링 시간 로그**
   ```
   DxfRenderer 상세 렌더링 완료 (render=X.XXs, max_edge_px=XXXX)
   ```

3. **캐시 상태 로그**
   ```
   상세 뷰 렌더 캐시 상태: old=True new=True max_edge_px=XXXX
   ```

4. **예외 로그**
   ```
   Failed to open detailed viewer
   ```

5. **DWG/DXF 비교 타이밍**
   ```
   DWG/DXF 비교 타이밍: convert=... load=... extract_old=...
   ```

## 개선 사항 (완료)

### 1. change_type 정규화 개선 ✅
- `normalize_change_type()` 함수 추가
- enum 객체/대문자/문자열/점 표기법 모두 호환
- None/빈 문자열 → "unknown" 안전 처리
- 유효 타입 검증: added, deleted, modified, unknown
- "unknown"은 회색 구름마크로 별도 표시

### 2. 렌더링 성능 개선 ✅
- max_edge_px 기반 해상도 제한
- 렌더 캐시 LRU 구현 (OrderedDict 기반)
- 파일 크기 기반 자동 해상도 조정
- 상세 뷰 품질 옵션/프로젝트 설정 저장 연동

### 3. 오류 처리 강화 ✅
- ImportError 명시적 처리
- 일반 Exception 포괄 처리
- 사용자 친화적 오류 메시지

### 4. 캐시 메모리 관리 ✅ (Sprint 14)
- 캐시 메모리 제한: 200MB (기본값)
- 캐시 항목 수 제한: 4개 (LRU)
- **단일 이미지 200MB 초과 시 캐시 스킵** (OOM 방지)
- 캐시 키 정밀도 개선: `Path.resolve()` + `st_mtime_ns` + `st_size` + `max_edge_px`
- 품질 변경 시 전체 캐시 클리어 (이미지 + transform)

### 5. DPI 제한 강화 ✅ (Sprint 14)
- MIN_SAFE_DPI: 10 (최소 품질 보장)
- MAX_SAFE_DPI: 300 (메모리 보호)
- 극소 도면 (< 0.1 inch) 감지 및 안전 DPI 적용
- 음수/비정상 입력 절대값 처리
- 최종 반환값에 MIN_SAFE_DPI 강제 적용

### 6. 테스트 커버리지 ✅ (Sprint 14)
- `test_dxf_renderer_cap_dpi.py`: 18개 경계값 테스트
- `test_drawing_compare_cache.py`: 10개 캐시 로직 테스트
- `test_normalize_change_type.py`: 19개 정규화 테스트
- `test_drawing_comparison_viewer_behaviors.py`: 28개 동작 테스트 (신규)

### 7. unknown change_type Old/New 뷰 지원 ✅ (Sprint 14 - LOW #1)
- unknown 타입이 Old 좌표를 가지면 Old 뷰에 표시
- old_cad_x/old_cad_y가 유효한 숫자이고 new와 다르면 Old 뷰 선택
- None/문자열 등 무효한 좌표는 New 뷰로 fallback
- `isinstance()` 검증으로 타입 안전성 강화
- 삭제된 것으로 추정되는 불확실한 변경사항 누락 방지

### 8. normalize_change_type 공용 유틸리티 ✅ (Sprint 14 - LOW #2)
- 중복 코드 제거: `drawing_compare_tab.py`, `drawing_comparison_viewer.py`
- 공용 모듈: `src/gui/unified_load_module/utils/change_type_utils.py`
- 단일 수정 지점으로 유지보수성 향상
- 테스트 Qt 의존성 제거 (독립 실행 가능)

### 9. 좌표 검증 및 메모리 관리 강화 ✅ (Sprint 14 - MEDIUM #1, #2)
- **MEDIUM #1**: `_safe_coord()` 헬퍼 함수로 None/비숫자 좌표 안전 처리
- **MEDIUM #1**: `has_location` 검증 강화 (키 존재 + 숫자 타입 확인)
- **MEDIUM #2**: `viewer.exec()` 후 200MB 초과 이미지 메모리 해제
- 좌표 변환 시 TypeError 방지
- 대용량 DWG 사용 시 OOM 위험 감소

### 10. 동작 테스트 추가 ✅ (Sprint 14 - LOW #3)
- `TestUnknownChangeTypeViewSelection`: 8개 (Old/New 뷰 선택 로직)
- `TestViewerMemoryRelease`: 5개 (200MB 초과 메모리 해제)
- `TestSafeCoordHelper`: 6개 (좌표 안전 변환)
- `TestHasLocationValidation`: 9개 (위치 유효성 검증)
- 총 57개 GUI 테스트 (0.64s)

## 다음 단계

실제 오류 발생 시 다음 정보를 제공해주시면 정확한 원인 분석이 가능합니다:

1. **오류 발생 단계**
   - 비교 실행 → 상세 비교 버튼 클릭 → Visual Diff 선택 시
   - 또는 비교 완료 직후 등

2. **파일 타입/조합**
   - .dwg/.dxf vs .pdf vs 이미지
   - old/new 각각 확장자

3. **에러 메시지/스택트레이스 전문**
   - 전체 오류 메시지
   - 스택트레이스

4. **로그 파일**
   - `error_YYYYMMDD.log` 또는 `*.log`
   - 오류 시각 전후 50~100줄

이 정보를 제공해주시면 코드 흐름 기준으로 바로 원인 분석 + 해결안까지 정리해드리겠습니다.

---

## 부록: 경량 뷰어(V2)에서 대형 DWG가 "상대 위치 모드"로 보이는 이유 (정직한 폴백)

**증상**: 변경이 매우 많은 대형 DWG(예: 변경 ~20,000 / 구역 ~150)를 경량 뷰어로 열면
실제 CAD 배경 없이 `상대 위치 모드 · raster preview`(워터마크: "실배경 없음 — 변경구역
위치만 추정 표시")로 표시되고, 구역 선택 시 마커만 보인다.

**원인 (버그 아님, 의도된 정직 폴백)**: 경량 뷰어는 렌더된 전체 래스터 PNG(`before_image`/
`after_image`)와 world transform이 있으면 `_load_lightweight_raster_preview_v2`
(`src/gui/drawing_compare_workbench.py`)가 `load_raster_image`로 **실 배경**을 띄우고
fidelity를 `exact_world_render`로 둔다(이때 구역 선택 확대는 PDF와 동일하게 실배경 위에서
매칭됨). 그러나 대형 도면은 전체 래스터 렌더가 **pending/실패**(시간·픽셀 예산 초과)하는 경우가
있고, 이때 PNG가 없어 `load_raster_image`가 None을 받아 `relative_only`로 **정직하게** 폴백한다.
`relative_only`는 [render_modes.py](../src/services/comparison/render_modes.py)의 계약상
**절대 실배경으로 표시되지 않으며** 항상 워터마크로 사용자에게 추정 표시임을 알린다(silent
misinformation 방지).

**차단 아님**: 중소형 DXF/DWG는 전체 래스터가 완성되어 실배경 + 구역 확대 매칭이 정상 동작한다
(회귀 테스트 `tests/unit/gui/test_lightweight_raster_preview.py`가 이 경로를 고정).

**별도 후속(범위 외)**: 대형 도면의 전체 래스터 배경을 항상 완성시키는 성능/타일링 개선은
신뢰성 로드맵의 별도 P0 항목으로 다룬다. 현재 동작(정직한 relative_only 폴백)은 안전하다.

---

## 2026-06-06 — DWG 구역 선택 시 "포커싱·변경부위 파악 안 됨" 다층 근본원인 (실측)

사용자: "PDF는 원하는 결과인데 DWG는 여전히 뷰어 포커싱·변경부위 파악이 안 됨." 라이브 실행
(`compare_20260606_152453` = pair_89cd07e28a3d8e19, `compare_20260606_153048` =
pair_707b29e82365e060)의 `viewer/viewer_perf.jsonl` + zone_crops/zone_vector 산출물 + 전체
배경 PNG를 실측해 **경량 전용 모드(QtQuick 머신)에서 DWG 구역의 선명/정합 경로가 여러 층에서
동시에 실패**함을 확인.

1. **전체 래스터 확대 = mush (지배적 증상)**: 전체 배경 PNG는 8000×1414 px. 경량 뷰어가 전체
   배경을 구역으로 확대하면 fallback 렌더러가 8000px 텍스처를 다운샘플 → 확대 시 흐림. (PDF는
   줌 시 고DPI 재렌더 `_maybe_schedule_pdf_rerender`라 선명. DWG 래스터는 재렌더 없음.)

2. **`cad-background-image-crop` 구역 crop은 실제로 선명하지만 경량에 표시 안 됨**: 8000px 배경
   → 구역(~10,000mm/50,557mm) crop ≈ 1:1(1600px). 즉 crop은 선명. 그러나
   `_on_zone_crop_render_finished_v2`의 crop 적재 블록이 `if not DRAWING_COMPARE_LIGHTWEIGHT_
   VIEWER_ONLY`로 **레거시(숨김) 뷰포트에만** 적재 → 경량 전용 모드에서 비가시. **→ 본 커밋
   7a6678c가 해결**(`_apply_zone_crop_to_lightweight_v2`로 경량에 surface).

3. **before/after 좌표계 불일치 → 한쪽 crop이 blank(백지)**: 실측 전체배경 world_bbox —
   before=(353044,206619,403601,215556), after=(481392,-109331,531949,-100393) **완전 분리**.
   변경 zone bbox가 after 좌표라 before-bg 밖 → `_render_background_image_crop`이
   `outside_background_bounds` → `_write_blank_crop`(백지). pair_707b…의 8개 zone **전부**
   outside_background_bounds. 그래서 surface 시 **per-side blank guard 필수**(zone world_window가
   해당 면 전체배경 bbox와 안 겹치면 그 면은 relative_only로 정직 폴백, 백지 패널 금지) — 7a6678c
   에 포함. **단, 좌표계 불일치 자체는 별개 미해결**(개정 DWG가 다른 datum으로 재원점화? 또는
   정합/페어링 이슈; P0-2b 정합 영역). added(b_only) 변경은 after 한 면만으로 충분하나, modified의
   before↔after 대조는 깨짐.

4. **선명 SVG 벡터 렌더는 성공하지만 레거시 뷰포트로만 라우팅**: `_start_zone_vector_render_v2`가
   source_b(after)에서 zone SVG를 렌더(실측 `compare_20260606_153048/viewer/zone_vector/*.svg`
   3건, **status=ok, entity_count=1500, 60,000mm 윈도, 1.1MB 진짜 벡터**). 그러나
   `_apply_zone_vector_to_qml_v2`가 `preview_after_v2`/`preview_before_v2`(레거시)에만 push →
   경량 전용 모드에서 무한줌 벡터가 비가시. **미해결**(후속: 경량에 벡터/고해상 래스터화 surface).

5. **경량 네이티브 벡터(scene-pack zone-focus) 빌드 자체가 스킵됨**: 로그
   `request_zone(...): no source path, skipping worker submit`. ViewerSession V3 manifest의
   per-side `state.source_path`가 비어 `request_zone`이 zone 마이크로팩을 안 만들고 →
   `push_zone_focus_pack`(경량 네이티브 무한줌 벡터) 미실행 → 전체도 scene-pack 대신 raster 폴백
   (1번의 mush 뿌리). **미해결**(후속: ViewerSession source_path 배선 — 단, 대형 DWG는 의도적
   raster 폴백일 가능성 있어 성능/메모리 검토 필요).

**본 커밋(7a6678c) 범위**: 2번(crop을 경량에 surface) + 3번(blank guard)만. 즉 **after 면 선명
crop은 해결**(지배적 added 케이스). 3·4·5의 잔여(좌표계 불일치, SVG 경량 라우팅, scene-pack
배선)는 **후속 별도 작업**. 라이브 육안 검증 필요(헤드리스 재현 불가 — [[zone_zoom_surrogate_
rootcause]] 교훈).

### 후속 (2026-06-06 동일 세션)

**5번 해결 (commit f05376d)** — 경량 네이티브 무한줌 벡터(zone-focus 마이크로팩):
디스플레이 경로(`_on_viewer_session_zone_evidence_v2`→`_apply_zone_evidence_to_lightweight_v2`
→`push_zone_focus_pack`)는 **이미 배선돼 있었으나** `request_zone`이 ViewerSession state의
`source_path` 부재로 네이티브 빌드를 스킵했음. V3 manifest가 (a) source_path를 `<redacted>`로
가리고 (b) state를 패키지 pair_uuid로 키잉 → 멀티페어 워크벤치가 자기 pair 해시로 요청하면
미스. `ViewerSession.ensure_pair_source(pair_id, side, source_path)` 추가 + `_request_zone_focus_v2`
가 워크벤치가 이미 복구한 로컬 경로(`_repair_viewer_pair_source_paths_v2`)를 주입(non-PDF·usable
한정). **crop(2번)과 합성**: crop=즉시 첫 페인트, 네이티브 벡터=완료 시 상위 레이어(무한줌). 읽기
불가 소스(raw .dwg 등)는 `render_zone_focus`에서 graceful skip→evidence 없음→no-op(crop 유지),
크래시 없음. 4번(SVG 경량 라우팅)은 5번 네이티브 벡터가 상위호환이라 **사실상 대체**.

**3번 = 정합 범위 한계로 재특정 (미해결, 별도 대형 항목)**: V3 manifest 실측 —
`before_world_bbox == after_world_bbox == shared_world_bbox`(둘의 **합집합**), `alignment_*_to_shared
== identity`. 그러나 실제 콘텐츠는 before=(353k,206k 영역) vs after=(481k,-109k 영역)로 **약
178,000mm 떨어진 서로 다른 사분면**. P0-2b 정합 estimator는 **50mm 내 작은 변환만 복원**
([[p0_2b_visual_alignment]])하므로 이 대규모 오프셋은 범위 밖 → identity 유지 → before/after 분리.
즉 before↔after 대조에는 **coarse 전역 정합(대규모 오프셋 복원) 단계**가 필요 — 별도 알고리즘 작업.
(현재 P5_154kv_POT_BEARING 쌍. 두 도면이 진짜 다른 원점인지 추출/정규화 버그인지는 소스파일
실측 필요.) added(b_only)는 after 한 면으로 충분하므로 2+5로 지배적 케이스는 커버됨.

