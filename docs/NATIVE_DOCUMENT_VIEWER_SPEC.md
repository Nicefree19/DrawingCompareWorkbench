# 네이티브 도면 뷰어 기술 스펙 (Native Document Viewer Technical Specification)

**Version**: 1.0.0
**Date**: 2026-01-02
**Author**: TEKLA_MCP Team
**Status**: Implementation In Progress

---

## 1. 개요 (Overview)

### 1.1 목적
기존 이미지 렌더링 기반 도면 비교 시스템을 확장하여, 실제 PDF/DWG/DXF 파일을 네이티브하게 표시하고 변경점에 구름마크(Cloud Mark)를 오버레이하는 상세 검토 뷰어 구현.

### 1.2 핵심 요구사항
- **Windows 전용** 개발 (크로스플랫폼 고려 불필요)
- **DWG 변환 지원**: ODA File Converter 연동 (배포 시 라이선스 재검토 필요)
- **읽기 전용 어노테이션** (향후 편집 기능 확장 가능한 아키텍처)
- **2패널 동기화 뷰**: Old/New 도면 동시 표시, 줌/팬 동기화
- **대용량 도면 지원**: 타일 기반 렌더링, LOD (Level of Detail)
- **멀티 도면 비교**: 도면 리스트에서 선택하여 상세 뷰어 표시

---

## 2. 시스템 아키텍처

### 2.1 컴포넌트 다이어그램

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        NativeViewerDialog                               │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                    DocumentListPanel (Left)                       │  │
│  │  ┌────────────────────────────────────────────────────────────┐  │  │
│  │  │ 📁 비교 도면 목록                                           │  │  │
│  │  │ ├── Drawing_A_old.pdf ↔ Drawing_A_new.pdf  [변경: 15건]   │  │  │
│  │  │ ├── Drawing_B_old.dxf ↔ Drawing_B_new.dxf  [변경: 8건]    │  │  │
│  │  │ └── Drawing_C_old.dwg ↔ Drawing_C_new.dwg  [변경: 23건]   │  │  │
│  │  └────────────────────────────────────────────────────────────┘  │  │
│  │  ┌────────────────────────────────────────────────────────────┐  │  │
│  │  │ 📊 변경점 목록 (선택된 도면)                                │  │  │
│  │  │ ├── #1 영역 A3 - 치수 변경 (12.5m → 13.0m)                │  │  │
│  │  │ ├── #2 영역 B1 - 부재 추가                                 │  │  │
│  │  │ └── #3 영역 C2 - 텍스트 수정                               │  │  │
│  │  └────────────────────────────────────────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                   SyncedDualPanelViewer (Right)                   │  │
│  │  ┌─────────────────────────┐ ┌─────────────────────────┐         │  │
│  │  │   OldDocumentPanel      │ │   NewDocumentPanel      │         │  │
│  │  │  ┌───────────────────┐  │ │  ┌───────────────────┐  │         │  │
│  │  │  │ Native Document   │  │ │  │ Native Document   │  │         │  │
│  │  │  │ Layer (PDF/DXF)   │  │ │  │ Layer (PDF/DXF)   │  │         │  │
│  │  │  ├───────────────────┤  │ │  ├───────────────────┤  │         │  │
│  │  │  │ CloudMark Overlay │  │ │  │ CloudMark Overlay │  │         │  │
│  │  │  │ (Blue: Deleted)   │  │ │  │ (Red: Added)      │  │         │  │
│  │  │  └───────────────────┘  │ │  └───────────────────┘  │         │  │
│  │  └─────────────────────────┘ └─────────────────────────┘         │  │
│  │                                                                   │  │
│  │  [🔍 줌+] [🔍 줌-] [🔄 리셋] [⬅️ 이전] [➡️ 다음] [📐 Fit] [🔲 Max] │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 클래스 다이어그램

```
┌─────────────────────────────────┐
│      NativeViewerDialog         │
│  (QDialog)                      │
├─────────────────────────────────┤
│ - document_list_panel           │
│ - dual_panel_viewer             │
│ - comparison_results            │
├─────────────────────────────────┤
│ + load_comparison_results()     │
│ + on_document_selected()        │
│ + toggle_maximize()             │
│ + fit_to_screen()               │
└─────────────────────────────────┘
            │
            ├────────────────────────────────────────┐
            ▼                                        ▼
┌─────────────────────────────────┐  ┌─────────────────────────────────┐
│     DocumentListPanel           │  │    SyncedDualPanelViewer        │
│  (QWidget)                      │  │  (QWidget)                      │
├─────────────────────────────────┤  ├─────────────────────────────────┤
│ - document_tree                 │  │ - old_panel: NativeDocumentPanel│
│ - change_list                   │  │ - new_panel: NativeDocumentPanel│
│ - comparison_data               │  │ - sync_enabled: bool            │
├─────────────────────────────────┤  ├─────────────────────────────────┤
│ + set_comparison_results()      │  │ + load_documents()              │
│ + on_document_clicked()         │  │ + sync_zoom_pan()               │
│ + on_change_clicked()           │  │ + navigate_to_change()          │
│ signal: document_selected       │  │ + set_cloud_marks()             │
│ signal: change_selected         │  │ signal: zoom_changed            │
└─────────────────────────────────┘  └─────────────────────────────────┘
                                                │
                                                ▼
                                 ┌─────────────────────────────────┐
                                 │     NativeDocumentPanel         │
                                 │  (QGraphicsView)                │
                                 ├─────────────────────────────────┤
                                 │ - scene: QGraphicsScene         │
                                 │ - document_item: QGraphicsItem  │
                                 │ - cloud_overlay: CloudMarkLayer │
                                 │ - tile_cache: TileCache         │
                                 ├─────────────────────────────────┤
                                 │ + load_pdf()                    │
                                 │ + load_dxf()                    │
                                 │ + set_zoom()                    │
                                 │ + set_pan()                     │
                                 │ + add_cloud_marks()             │
                                 │ + render_visible_tiles()        │
                                 │ signal: view_changed            │
                                 └─────────────────────────────────┘
                                                │
                            ┌───────────────────┼───────────────────┐
                            ▼                   ▼                   ▼
               ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
               │  PDFRenderer     │ │  DXFRenderer     │ │ CloudMarkLayer   │
               │  (PyMuPDF)       │ │  (ezdxf)         │ │ (QGraphicsItem)  │
               ├──────────────────┤ ├──────────────────┤ ├──────────────────┤
               │ + render_page()  │ │ + render_doc()   │ │ + add_mark()     │
               │ + get_tile()     │ │ + get_tile()     │ │ + clear_marks()  │
               │ + get_bounds()   │ │ + get_bounds()   │ │ + highlight()    │
               └──────────────────┘ └──────────────────┘ └──────────────────┘
```

---

## 3. 기술 스택

### 3.1 핵심 라이브러리

| 컴포넌트 | 라이브러리 | 버전 | 용도 |
|----------|-----------|------|------|
| GUI Framework | PySide6 | 6.4+ | Qt 위젯, QGraphicsView |
| PDF 렌더링 | PyMuPDF (fitz) | 1.23+ | PDF 네이티브 렌더링 |
| DXF 렌더링 | ezdxf | 1.0+ | DXF 파싱 및 렌더링 |
| DWG 변환 | ODA File Converter | 25.1+ | DWG → DXF 변환 (외부 도구) |
| 이미지 처리 | Pillow | 10.0+ | 타일 캐싱, 이미지 변환 |
| 좌표 계산 | NumPy | 1.24+ | 좌표 변환, 행렬 연산 |

### 3.2 ODA File Converter 연동

```python
# DWG → DXF 변환 흐름
class DWGConverter:
    """ODA File Converter를 사용한 DWG 변환"""

    ODA_CONVERTER_PATH = r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe"

    def convert_dwg_to_dxf(self, dwg_path: Path, output_dir: Path) -> Path:
        """
        DWG 파일을 DXF로 변환

        Args:
            dwg_path: 입력 DWG 파일 경로
            output_dir: 출력 디렉토리

        Returns:
            변환된 DXF 파일 경로
        """
        # ODA File Converter 명령줄 실행
        # ODAFileConverter "InputFolder" "OutputFolder" ACAD2018 DXF 0 1
        pass
```

**배포 시 고려사항**:
- ODA File Converter는 무료이나 재배포 시 ODA 라이선스 검토 필요
- 대안: 사용자가 직접 ODA 설치하도록 안내 (첫 실행 시 설치 가이드 표시)
- 최악의 경우: DWG 미지원, DXF만 지원 (사용자가 미리 변환)

---

## 4. 핵심 알고리즘

### 4.1 좌표 변환 시스템

```python
class CoordinateTransformer:
    """도면 좌표 ↔ 화면 좌표 변환"""

    def __init__(self, doc_bounds: QRectF, view_size: QSize):
        self.doc_bounds = doc_bounds  # 문서 좌표계 범위
        self.view_size = view_size     # 뷰포트 픽셀 크기
        self.zoom = 1.0
        self.pan = QPointF(0, 0)

    def doc_to_view(self, doc_point: QPointF) -> QPointF:
        """문서 좌표 → 뷰포트 좌표"""
        # 1. 문서 좌표를 정규화 (0~1)
        norm_x = (doc_point.x() - self.doc_bounds.left()) / self.doc_bounds.width()
        norm_y = (doc_point.y() - self.doc_bounds.top()) / self.doc_bounds.height()

        # 2. 뷰포트 크기에 맞게 스케일링
        view_x = norm_x * self.view_size.width() * self.zoom + self.pan.x()
        view_y = norm_y * self.view_size.height() * self.zoom + self.pan.y()

        return QPointF(view_x, view_y)

    def view_to_doc(self, view_point: QPointF) -> QPointF:
        """뷰포트 좌표 → 문서 좌표 (역변환)"""
        # 역순으로 계산
        pass
```

### 4.2 타일 기반 렌더링 (대용량 도면)

```python
class TileCache:
    """타일 기반 렌더링 캐시"""

    TILE_SIZE = 512  # 픽셀
    MAX_CACHE_SIZE = 100  # 최대 캐시 타일 수

    def __init__(self, renderer: DocumentRenderer):
        self.renderer = renderer
        self.cache: Dict[Tuple[int, int, int], QPixmap] = {}  # (x, y, zoom_level) → tile
        self.lru_order: List[Tuple[int, int, int]] = []

    def get_tile(self, tile_x: int, tile_y: int, zoom_level: int) -> QPixmap:
        """타일 가져오기 (캐시 히트 또는 렌더링)"""
        key = (tile_x, tile_y, zoom_level)

        if key in self.cache:
            # LRU 업데이트
            self.lru_order.remove(key)
            self.lru_order.append(key)
            return self.cache[key]

        # 캐시 미스 → 렌더링
        tile = self.renderer.render_tile(tile_x, tile_y, zoom_level, self.TILE_SIZE)
        self._add_to_cache(key, tile)
        return tile

    def _add_to_cache(self, key: Tuple, tile: QPixmap):
        """캐시에 타일 추가 (LRU 정책)"""
        if len(self.cache) >= self.MAX_CACHE_SIZE:
            # 가장 오래된 타일 제거
            oldest = self.lru_order.pop(0)
            del self.cache[oldest]

        self.cache[key] = tile
        self.lru_order.append(key)
```

### 4.3 구름마크 오버레이

```python
class CloudMarkItem(QGraphicsPathItem):
    """구름마크 그래픽 아이템"""

    def __init__(self, bounds: QRectF, change_type: str, change_id: int):
        super().__init__()

        self.change_id = change_id
        self.change_type = change_type  # "added", "deleted", "modified"

        # 구름 모양 경로 생성
        path = self._create_cloud_path(bounds)
        self.setPath(path)

        # 색상 설정
        colors = {
            "added": QColor(255, 0, 0, 100),      # 빨강 (반투명)
            "deleted": QColor(0, 0, 255, 100),    # 파랑 (반투명)
            "modified": QColor(255, 165, 0, 100)  # 주황 (반투명)
        }
        color = colors.get(change_type, QColor(128, 128, 128, 100))

        self.setPen(QPen(color.darker(), 2))
        self.setBrush(QBrush(color))

        # 호버 효과 활성화
        self.setAcceptHoverEvents(True)

    def _create_cloud_path(self, bounds: QRectF) -> QPainterPath:
        """구름 모양 경로 생성 (물결 테두리)"""
        path = QPainterPath()

        # 물결 무늬 테두리 생성 (구름 효과)
        wave_count = max(4, int(bounds.width() / 20))
        wave_height = min(10, bounds.height() / 4)

        # 상단 물결
        path.moveTo(bounds.left(), bounds.top() + wave_height)
        for i in range(wave_count):
            x1 = bounds.left() + (i + 0.5) * bounds.width() / wave_count
            x2 = bounds.left() + (i + 1) * bounds.width() / wave_count
            path.quadTo(x1, bounds.top(), x2, bounds.top() + wave_height)

        # 우측, 하단, 좌측도 유사하게 처리
        # ... (생략)

        path.closeSubpath()
        return path
```

---

## 5. 동기화 메커니즘

### 5.1 줌/팬 동기화

```python
class SyncedDualPanelViewer(QWidget):
    """동기화된 2패널 뷰어"""

    def __init__(self):
        super().__init__()
        self.old_panel = NativeDocumentPanel()
        self.new_panel = NativeDocumentPanel()
        self.sync_enabled = True

        # 시그널 연결
        self.old_panel.view_changed.connect(self._on_old_view_changed)
        self.new_panel.view_changed.connect(self._on_new_view_changed)

    def _on_old_view_changed(self, zoom: float, pan: QPointF):
        """Old 패널 변경 시 New 패널 동기화"""
        if self.sync_enabled:
            self.new_panel.blockSignals(True)
            self.new_panel.set_zoom(zoom)
            self.new_panel.set_pan(pan)
            self.new_panel.blockSignals(False)

    def _on_new_view_changed(self, zoom: float, pan: QPointF):
        """New 패널 변경 시 Old 패널 동기화"""
        if self.sync_enabled:
            self.old_panel.blockSignals(True)
            self.old_panel.set_zoom(zoom)
            self.old_panel.set_pan(pan)
            self.old_panel.blockSignals(False)
```

---

## 6. 파일 구조

```
src/gui/unified_load_module/
├── dialogs/
│   ├── native_viewer_dialog.py          # 메인 네이티브 뷰어 다이얼로그
│   └── drawing_comparison_viewer.py     # 기존 이미지 기반 뷰어 (유지)
│
├── components/
│   ├── native_document_panel.py         # 네이티브 문서 패널 (QGraphicsView)
│   ├── synced_dual_panel_viewer.py      # 동기화 2패널 뷰어
│   ├── document_list_panel.py           # 도면/변경점 목록 패널
│   └── cloud_mark_layer.py              # 구름마크 오버레이 레이어
│
├── renderers/
│   ├── pdf_renderer.py                  # PDF 렌더러 (PyMuPDF)
│   ├── dxf_renderer.py                  # DXF 렌더러 (ezdxf)
│   └── tile_cache.py                    # 타일 캐시 시스템
│
└── converters/
    └── dwg_converter.py                 # DWG → DXF 변환기 (ODA)
```

---

## 7. 구현 우선순위

| 단계 | 작업 | 예상 시간 | 의존성 |
|------|------|----------|--------|
| 1 | NativeDocumentPanel 기본 구조 | 2h | - |
| 2 | PDF 렌더러 (PyMuPDF) | 2h | 1 |
| 3 | DXF 렌더러 (ezdxf 확장) | 3h | 1 |
| 4 | CloudMarkLayer 구현 | 2h | 1 |
| 5 | SyncedDualPanelViewer | 2h | 1, 4 |
| 6 | DocumentListPanel | 2h | - |
| 7 | NativeViewerDialog 통합 | 2h | 1-6 |
| 8 | DWG 변환기 (ODA 연동) | 2h | 3 |
| 9 | 타일 기반 렌더링 | 3h | 2, 3 |
| 10 | 기존 시스템 통합 | 2h | 7 |

**총 예상 시간**: 22시간

---

## 8. 확장성 고려사항

### 8.1 향후 편집 기능 확장을 위한 설계
- `CloudMarkItem`에 `setEditable(bool)` 메서드 예약
- 마크 이동/크기조정 이벤트 핸들러 인터페이스 정의
- 코멘트 추가를 위한 `AnnotationData` 데이터 모델 설계

### 8.2 DWG 네이티브 지원 확장
- ODA Viewer SDK 도입 시 `DWGRenderer` 클래스 추가
- 렌더러 팩토리 패턴으로 파일 타입별 자동 선택

---

## 9. 테스트 계획

| 테스트 유형 | 대상 | 검증 항목 |
|------------|------|----------|
| 단위 테스트 | CoordinateTransformer | 좌표 변환 정확성 |
| 단위 테스트 | TileCache | LRU 캐시 동작 |
| 통합 테스트 | PDF 렌더링 | 페이지 렌더링 품질 |
| 통합 테스트 | DXF 렌더링 | 도면 정확성 |
| E2E 테스트 | 전체 워크플로우 | 비교 → 상세 뷰어 → 네비게이션 |
| 성능 테스트 | 대용량 도면 | 100MB DXF, 1000페이지 PDF |

---

*Document generated by TEKLA_MCP Team*
