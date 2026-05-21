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

