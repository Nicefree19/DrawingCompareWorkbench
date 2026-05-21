# 기술 사양서 (TechSpec): 평면 뷰어 간섭 표시 가시성 개선

**버전**: v2.8.0
**작성일**: 2025-12-09
**작성자**: TEKLA_MCP Team
**상태**: 검토 중 (Review)

---

## 1. 개요 (Executive Summary)

### 1.1 문제 정의

현재 평면 뷰어(`ResultViewerWidget`)에서 간섭 타입 표시의 가시성이 현저히 낮음:

1. **마커 크기 문제**: `radius = 250.0`으로 설정되어 있으나, 건물 스케일(수만 mm) 대비 매우 작음
2. **줌 레벨 의존성**: 초기 fitInView 상태에서 마커가 거의 점으로 보임
3. **TYPE2 강조 부족**: 간섭 발생(TYPE2)이 다른 타입과 시각적 구별이 어려움
4. **정보 부족**: 현재는 간섭량 숫자만 표시, 부재기호/단면 정보 미표시

### 1.2 개선 목표

| 항목 | 현재 상태 | 목표 상태 |
|------|----------|----------|
| 마커 가시성 | 줌인 필요 | 기본 축척에서 육안 식별 가능 |
| TYPE2 구분 | 색상만 다름 | 펄스 + 크기 + 외곽선 강조 |
| 정보 표시 | 간섭량만 | 부재기호 + 단면명 + 간섭량/Down량 |
| 줌 반응 | 고정 크기 | 스케일 독립적 (Cosmetic) |

### 1.3 영향 범위

- **파일**: `gui/result_viewer_widget.py`
- **연관 모듈**: `gui/theme.py`, `models.py` (읽기 전용)
- **테스트**: `tests/unit/composite_beam_interference/test_result_viewer.py` (신규)

---

## 2. 현재 구현 분석 (As-Is)

### 2.1 NodeItem 클래스 구조

```python
# gui/result_viewer_widget.py:103-173

class NodeItem(QGraphicsItem):
    def __init__(self, node_result: NodeResult):
        self.radius = 250.0  # ← 고정 크기 (문제점)

    def paint(self, painter, option, widget):
        # 색상 결정
        color = COLOR_TYPE3
        if self.node_result.has_interference:
            color = COLOR_TYPE2  # 빨강
        elif self.node_result.overall_type.value == "TYPE1":
            color = COLOR_TYPE1  # 녹색

        # 형상: 다이아몬드(TYPE2) vs 원형(기타)
        if self.node_result.has_interference:
            path = diamond_shape  # 다이아몬드
        else:
            path = circle_shape  # 원형

        # 텍스트: 간섭량만 표시
        if self.node_result.has_interference:
            text = f"{max_amount:.0f}"  # 숫자만
```

### 2.2 현재 가시성 문제 원인

| 문제 | 원인 | 코드 위치 |
|------|------|----------|
| 마커 너무 작음 | `radius = 250.0` (건물 스케일 대비 0.5%~1%) | line 111 |
| 줌아웃 시 소실 | QPen width=4 (고정 픽셀) | line 144 |
| TYPE2 구별 어려움 | 색상만 다름, 크기 동일 | line 129 |
| 정보 부족 | `pair_results`에서 부재명 미활용 | line 156 |

### 2.3 사용 가능한 데이터

`NodeResult`와 `PairResult`에서 다음 정보 접근 가능:

```python
# NodeResult (line 467-480)
- node_id: int
- connected_beams: List[str]  # 부재명 리스트 ★
- pair_results: List[PairResult]

# PairResult (line 431-464)
- beam_a_name: str  # 부재기호 ★
- beam_b_name: str  # 부재기호 ★
- interference_amount: float  # 간섭량
- upper_beam_name: str  # 상부보
- lower_beam_name: str  # 하부보

# IntegratedBeam (line 363-424) - set_export_data()로 전달
- name: str  # 부재기호
- section_name: str  # "H-400x200x8x13" ★
- section_height: float
- section_width: float
```

---

## 3. 개선 설계 (To-Be)

### 3.1 마커 크기 시스템 (Scale-Independent)

#### 3.1.1 Cosmetic 렌더링 방식

```python
# 기존: 월드 좌표계 기준 (줌에 따라 크기 변동)
self.radius = 250.0  # mm 단위

# 개선: 화면 좌표계 기준 (줌 무관하게 일정한 픽셀 크기)
class NodeItem(QGraphicsItem):
    # 기본 화면 픽셀 크기
    BASE_SCREEN_RADIUS = 24  # px (TYPE3, UNKNOWN)
    TYPE1_SCREEN_RADIUS = 20  # px (정렬)
    TYPE2_SCREEN_RADIUS = 32  # px (간섭) ★ 가장 큼
    TYPE4_SCREEN_RADIUS = 28  # px (상부-하부 간섭)

    def paint(self, painter, option, widget):
        # 현재 뷰 변환에서 스케일 팩터 추출
        view = self.scene().views()[0] if self.scene().views() else None
        if view:
            transform = view.transform()
            scale_factor = transform.m11()  # X축 스케일
            screen_radius = self.get_screen_radius() / scale_factor
        else:
            screen_radius = self.radius  # 폴백
```

#### 3.1.2 타입별 크기 차등화

```python
def get_screen_radius(self) -> float:
    """타입에 따른 화면 픽셀 크기 반환"""
    if self.node_result.has_interference:
        return self.TYPE2_SCREEN_RADIUS  # 32px ★ 가장 큼
    elif self.node_result.overall_type == InterferenceType.TYPE1:
        return self.TYPE1_SCREEN_RADIUS  # 20px
    elif self.node_result.overall_type == InterferenceType.TYPE4:
        return self.TYPE4_SCREEN_RADIUS  # 28px
    else:
        return self.BASE_SCREEN_RADIUS  # 24px
```

### 3.2 TYPE2 시각적 강조

#### 3.2.1 다중 레이어 렌더링

```
┌─────────────────────────────────────────────┐
│  TYPE2 노드 렌더링 레이어 (안쪽 → 바깥쪽)   │
├─────────────────────────────────────────────┤
│ Layer 4: Pulse Animation (기존)             │
│   - 확장하는 링 애니메이션                   │
│   - 1.5초 주기                              │
├─────────────────────────────────────────────┤
│ Layer 3: Glow Effect (신규)                 │
│   - 빨간색 외곽 글로우                       │
│   - 반경: 마커의 1.8배                      │
│   - 알파 그라디언트                          │
├─────────────────────────────────────────────┤
│ Layer 2: Outline Ring (신규)                │
│   - 두꺼운 흰색 외곽선 (4px cosmetic)        │
│   - 마커 형상과 동일                         │
├─────────────────────────────────────────────┤
│ Layer 1: Fill Shape (기존 개선)             │
│   - 다이아몬드 형상                          │
│   - 빨간색 (#FF453A) 채우기                  │
│   - 불투명도 증가 (180 → 220)               │
├─────────────────────────────────────────────┤
│ Layer 0: Info Label (신규)                  │
│   - 부재기호 + 단면 + 간섭량 텍스트          │
│   - 마커 아래에 위치                         │
└─────────────────────────────────────────────┘
```

#### 3.2.2 심각도별 색상 그라디언트

```python
# InterferenceSeverity 기반 색상
SEVERITY_COLORS = {
    InterferenceSeverity.NOTICE: QColor("#FFD60A"),    # 노랑 (< 10mm)
    InterferenceSeverity.WARNING: QColor("#FF9F0A"),   # 주황 (10-50mm)
    InterferenceSeverity.CRITICAL: QColor("#FF453A"),  # 빨강 (≥ 50mm)
}
```

### 3.3 정보 라벨 시스템

#### 3.3.1 라벨 컴포넌트 설계

```
┌────────────────────────────────────────────────────────┐
│                    INFO LABEL 레이아웃                  │
├────────────────────────────────────────────────────────┤
│                                                        │
│    ┌─────────────────────────────────────────────┐    │
│    │  [◆] ← TYPE2 마커 (다이아몬드)              │    │
│    └─────────────────────────────────────────────┘    │
│                         │                              │
│                         ▼                              │
│    ┌─────────────────────────────────────────────┐    │
│    │ ┌─────────────────────────────────────────┐ │    │
│    │ │  2PG1 ↔ 3PG2                            │ │ ← 부재기호 (Line 1)
│    │ │  H-400×200  ↔  H-500×200                │ │ ← 단면명 (Line 2)
│    │ │  ⚠ 45mm / 35mm (간섭/잔여)              │ │ ← 간섭량/Down량 (Line 3)
│    │ └─────────────────────────────────────────┘ │    │
│    │            (둥근 모서리 배경)               │    │
│    └─────────────────────────────────────────────┘    │
│                                                        │
└────────────────────────────────────────────────────────┘
```

**라벨 Line 3 형식**: `⚠ {간섭량}mm / {Down량}mm (간섭/잔여)`
- **간섭량** (`interference_amount`): 상부보가 하부보 영역에 침범한 양
- **Down량** (`down_amount`): 하부보 영역 중 상부보에 침범되지 않은 잔여 영역

#### 3.3.2 라벨 정보 구성

```python
class InfoLabel:
    """노드 상세 정보 라벨"""

    def format_beam_names(self, pair: PairResult, beams: Dict[str, IntegratedBeam]) -> str:
        """부재기호 포맷팅"""
        beam_a = beams.get(pair.beam_a_name)
        beam_b = beams.get(pair.beam_b_name)

        name_a = pair.beam_a_name  # "2PG1"
        name_b = pair.beam_b_name  # "3PG2"

        return f"{name_a} ↔ {name_b}"

    def format_section_info(self, pair: PairResult, beams: Dict[str, IntegratedBeam]) -> str:
        """단면 정보 포맷팅"""
        beam_a = beams.get(pair.beam_a_name)
        beam_b = beams.get(pair.beam_b_name)

        section_a = beam_a.section_name if beam_a else "?"
        section_b = beam_b.section_name if beam_b else "?"

        # "H-400x200x8x13" → "H-400×200" (간략화)
        short_a = self._shorten_section(section_a)
        short_b = self._shorten_section(section_b)

        return f"{short_a} ↔ {short_b}"

    def _shorten_section(self, full_name: str) -> str:
        """단면명 간략화: H-400x200x8x13 → H-400×200"""
        if not full_name or full_name == "?":
            return "?"

        parts = full_name.replace("×", "x").split("x")
        if len(parts) >= 2:
            return f"{parts[0]}×{parts[1]}"
        return full_name
```

### 3.4 줌 레벨별 표시 전략

#### 3.4.1 LOD (Level of Detail) 시스템

```python
class NodeItem(QGraphicsItem):
    """줌 레벨에 따른 상세 표시 조절"""

    # LOD 임계값 (scale_factor 기준)
    LOD_FULL = 0.01     # 모든 정보 표시 (줌인 많이)
    LOD_MEDIUM = 0.005  # 마커 + 간섭량만
    LOD_MINIMAL = 0.001 # 마커만

    def paint(self, painter, option, widget):
        scale = self._get_current_scale()

        # 마커는 항상 그림
        self._draw_marker(painter, scale)

        # 줌 레벨에 따라 라벨 표시
        if scale >= self.LOD_FULL:
            self._draw_full_label(painter, scale)  # 부재기호 + 단면 + 간섭량
        elif scale >= self.LOD_MEDIUM:
            self._draw_amount_only(painter, scale)  # 간섭량만
        # LOD_MINIMAL: 마커만 (라벨 없음)
```

#### 3.4.2 줌 레벨별 표시 항목

| 줌 레벨 | scale_factor | 마커 | 간섭량 | 부재기호 | 단면명 |
|---------|-------------|------|-------|---------|-------|
| 축소 (전체 보기) | < 0.001 | ✓ (Cosmetic) | ✗ | ✗ | ✗ |
| 중간 | 0.001 ~ 0.005 | ✓ | ✓ | ✗ | ✗ |
| 확대 | 0.005 ~ 0.01 | ✓ | ✓ | ✓ | ✗ |
| 최대 확대 | ≥ 0.01 | ✓ | ✓ | ✓ | ✓ |

---

## 4. 상세 구현 사양

### 4.1 NodeItem 클래스 개선

```python
# gui/result_viewer_widget.py

class NodeItem(QGraphicsItem):
    """노드 시각화 아이템 (v2.8 개선)"""

    # ═══════════════════════════════════════════════════════════
    # 상수 정의
    # ═══════════════════════════════════════════════════════════

    # 화면 픽셀 기준 크기 (Cosmetic)
    RADIUS_TYPE1 = 18      # 정렬 (작게)
    RADIUS_TYPE2 = 32      # 간섭 (가장 크게) ★
    RADIUS_TYPE3 = 20      # 간섭 없음
    RADIUS_TYPE4 = 26      # 상부-하부 간섭
    RADIUS_UNKNOWN = 16    # 데이터 부족 (가장 작게)

    # LOD 임계값
    LOD_FULL_DETAIL = 0.008    # 모든 정보
    LOD_MEDIUM_DETAIL = 0.003  # 간섭량만
    LOD_MINIMAL = 0.001        # 마커만

    # 글로우 설정
    GLOW_RADIUS_MULTIPLIER = 2.0
    GLOW_ALPHA_BASE = 60

    def __init__(
        self,
        node_result: NodeResult,
        integrated_beams: Optional[Dict[str, IntegratedBeam]] = None
    ):
        super().__init__()
        self.node_result = node_result
        self.integrated_beams = integrated_beams or {}

        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.ItemIsSelectable)

        # 캐싱
        self._cached_radius = None
        self._cached_color = None

    def boundingRect(self) -> QRectF:
        """바운딩 박스 (라벨 영역 포함)"""
        r = self._get_max_visual_radius()
        # 라벨 높이 고려
        label_height = 80 if self.node_result.has_interference else 0
        return QRectF(-r, -r, r * 2, r * 2 + label_height).adjusted(-20, -20, 20, 20)

    def _get_screen_radius(self) -> float:
        """타입별 화면 픽셀 반경"""
        if self.node_result.has_interference:
            return self.RADIUS_TYPE2

        match self.node_result.overall_type:
            case InterferenceType.TYPE1:
                return self.RADIUS_TYPE1
            case InterferenceType.TYPE3:
                return self.RADIUS_TYPE3
            case InterferenceType.TYPE4:
                return self.RADIUS_TYPE4
            case _:
                return self.RADIUS_UNKNOWN

    def _get_current_scale(self) -> float:
        """현재 뷰 스케일 팩터 반환"""
        if not self.scene() or not self.scene().views():
            return 1.0
        view = self.scene().views()[0]
        return view.transform().m11()

    def _world_radius(self) -> float:
        """월드 좌표계 반경 (Cosmetic 렌더링용)"""
        scale = self._get_current_scale()
        if scale <= 0:
            scale = 0.001
        return self._get_screen_radius() / scale

    def paint(self, painter: QPainter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)

        scale = self._get_current_scale()
        world_r = self._world_radius()
        color = self._get_type_color()

        # ── Layer 1: 글로우 효과 (TYPE2만) ──
        if self.node_result.has_interference:
            self._draw_glow(painter, world_r, color)

        # ── Layer 2: 흰색 외곽선 (TYPE2만) ──
        if self.node_result.has_interference:
            self._draw_outline(painter, world_r)

        # ── Layer 3: 메인 마커 ──
        self._draw_marker(painter, world_r, color)

        # ── Layer 4: 정보 라벨 (줌 레벨에 따라) ──
        if self.node_result.has_interference:
            self._draw_info_label(painter, world_r, scale)

    def _get_type_color(self) -> QColor:
        """타입별 색상 반환"""
        if self.node_result.has_interference:
            # 심각도별 색상
            max_severity = self._get_max_severity()
            return SEVERITY_COLORS.get(max_severity, COLOR_TYPE2)

        match self.node_result.overall_type:
            case InterferenceType.TYPE1:
                return COLOR_TYPE1
            case InterferenceType.TYPE4:
                return QColor("#9b59b6")  # 보라
            case _:
                return COLOR_TYPE3

    def _get_max_severity(self) -> InterferenceSeverity:
        """최대 심각도 반환"""
        max_sev = InterferenceSeverity.NONE
        for pair in self.node_result.pair_results:
            if pair.severity.value > max_sev.value:
                max_sev = pair.severity
        return max_sev

    def _draw_glow(self, painter: QPainter, radius: float, color: QColor):
        """글로우 효과 그리기"""
        glow_r = radius * self.GLOW_RADIUS_MULTIPLIER
        gradient = QRadialGradient(QPointF(0, 0), glow_r)
        gradient.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), self.GLOW_ALPHA_BASE))
        gradient.setColorAt(0.5, QColor(color.red(), color.green(), color.blue(), self.GLOW_ALPHA_BASE // 2))
        gradient.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(QPointF(0, 0), glow_r, glow_r)

    def _draw_outline(self, painter: QPainter, radius: float):
        """흰색 외곽선 그리기 (TYPE2 강조)"""
        outline_r = radius * 1.15
        pen = QPen(QColor("white"), 3)
        pen.setCosmetic(True)  # 줌 무관 일정 두께
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        if self.node_result.has_interference:
            # 다이아몬드 외곽
            path = QPainterPath()
            path.moveTo(0, -outline_r)
            path.lineTo(outline_r, 0)
            path.lineTo(0, outline_r)
            path.lineTo(-outline_r, 0)
            path.closeSubpath()
            painter.drawPath(path)

    def _draw_marker(self, painter: QPainter, radius: float, color: QColor):
        """메인 마커 그리기"""
        # 외곽선
        pen = QPen(color, 2)
        pen.setCosmetic(True)
        painter.setPen(pen)

        # 채우기 (반투명)
        fill_color = QColor(color.red(), color.green(), color.blue(), 200)
        painter.setBrush(QBrush(fill_color))

        if self.node_result.has_interference:
            # 다이아몬드
            path = QPainterPath()
            path.moveTo(0, -radius)
            path.lineTo(radius, 0)
            path.lineTo(0, radius)
            path.lineTo(-radius, 0)
            path.closeSubpath()
            painter.drawPath(path)
        else:
            # 원형
            painter.drawEllipse(QPointF(0, 0), radius, radius)

    def _draw_info_label(self, painter: QPainter, radius: float, scale: float):
        """정보 라벨 그리기 (줌 레벨에 따라)"""
        if not self.node_result.pair_results:
            return

        # 첫 번째 간섭 쌍 정보
        pair = self._get_worst_pair()
        if not pair:
            return

        # ── 텍스트 구성 ──
        lines = []

        if scale >= self.LOD_FULL_DETAIL:
            # 부재기호
            lines.append(f"{pair.beam_a_name} ↔ {pair.beam_b_name}")
            # 단면명
            section_line = self._format_sections(pair)
            if section_line:
                lines.append(section_line)
            # 간섭량 / Down량
            lines.append(f"⚠ {pair.interference_amount:.0f} / {pair.down_amount:.0f} (간섭/잔여)")

        elif scale >= self.LOD_MEDIUM_DETAIL:
            # 간섭량 / Down량 (간략)
            lines.append(f"{pair.interference_amount:.0f} / {pair.down_amount:.0f}")

        else:
            # LOD_MINIMAL: 라벨 없음
            return

        if not lines:
            return

        # ── 라벨 렌더링 ──
        font_size = max(8, int(12 / scale / 100))  # 스케일에 따른 폰트 크기
        font = QFont("Segoe UI", font_size)
        font.setBold(True)
        painter.setFont(font)

        fm = painter.fontMetrics()
        line_height = fm.height()
        max_width = max(fm.horizontalAdvance(line) for line in lines)

        # 라벨 위치 (마커 아래)
        label_x = -max_width / 2
        label_y = radius + line_height * 0.5

        # 배경
        bg_rect = QRectF(
            label_x - 10 / scale,
            label_y - line_height * 0.3,
            max_width + 20 / scale,
            line_height * len(lines) + 10 / scale
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 220))
        corner_r = 6 / scale
        painter.drawRoundedRect(bg_rect, corner_r, corner_r)

        # 텍스트
        painter.setPen(QColor("white"))
        for i, line in enumerate(lines):
            painter.drawText(
                QPointF(label_x, label_y + line_height * (i + 0.8)),
                line
            )

    def _get_worst_pair(self) -> Optional[PairResult]:
        """최대 간섭량 쌍 반환"""
        worst = None
        max_amount = -1
        for pair in self.node_result.pair_results:
            if pair.interference_amount > max_amount:
                max_amount = pair.interference_amount
                worst = pair
        return worst

    def _format_sections(self, pair: PairResult) -> str:
        """단면명 포맷팅"""
        beam_a = self.integrated_beams.get(pair.beam_a_name)
        beam_b = self.integrated_beams.get(pair.beam_b_name)

        def shorten(name: str) -> str:
            if not name:
                return "?"
            parts = name.replace("×", "x").split("x")
            if len(parts) >= 2:
                return f"{parts[0]}×{parts[1]}"
            return name[:15]

        sec_a = shorten(beam_a.section_name) if beam_a else "?"
        sec_b = shorten(beam_b.section_name) if beam_b else "?"

        return f"{sec_a} ↔ {sec_b}"
```

### 4.2 ResultViewerWidget 수정

```python
# gui/result_viewer_widget.py

class ResultViewerWidget(QWidget):
    def update_data(
        self,
        node_results: List[NodeResult],
        mgt_model: Optional[MGTModel] = None,
        integrated_beams: Optional[Dict[str, IntegratedBeam]] = None  # ★ 추가
    ):
        """데이터 업데이트"""
        self.scene.clear()
        self._last_node_results = node_results
        self.mgt_model = mgt_model
        self._integrated_beams_for_display = integrated_beams or self._integrated_beams

        # ... (기존 min/max 계산)

        for nr in node_results:
            x, y, z = nr.node_coord

            # ★ integrated_beams 전달
            item = NodeItem(nr, self._integrated_beams_for_display)
            item.setPos(x, -y)

            if nr.has_interference:
                pulse = PulseItem(COLOR_TYPE2, radius=250.0, parent=item)

            self.scene.addItem(item)
```

### 4.3 PulseItem 개선 (선택적)

```python
class PulseItem(QGraphicsObject):
    """개선된 펄스 애니메이션 (TYPE2 강조)"""

    def __init__(self, color: QColor, radius: float = 150.0, parent=None):
        super().__init__(parent)
        self.base_radius = radius
        self.color = color
        self._factor = 0.0

        # 더 빠른 펄스
        self.anim = QPropertyAnimation(self, b"factor")
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setDuration(1000)  # 1초 (기존 1.5초)
        self.anim.setLoopCount(-1)
        self.anim.start()

    def paint(self, painter, option, widget):
        # Cosmetic 펜으로 줌 무관 일정 두께
        pen = QPen(
            QColor(self.color.red(), self.color.green(), self.color.blue(),
                   int(255 * (1.0 - self._factor))),
            3
        )
        pen.setCosmetic(True)  # ★ 줌 무관

        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        # 부모 NodeItem에서 world_radius 참조
        if self.parentItem():
            parent_radius = getattr(self.parentItem(), '_world_radius', lambda: 250.0)()
            r = parent_radius * (1.0 + self._factor * 0.8)
        else:
            r = self.base_radius * (0.5 + self._factor)

        painter.drawEllipse(QPointF(0, 0), r, r)
```

---

## 5. 작업 계획 (Implementation Plan)

### 5.1 단계별 작업 분류

#### Phase 1: 마커 Cosmetic 렌더링 (우선순위: 높음)

| Task ID | 작업 내용 | 예상 시간 | 의존성 |
|---------|----------|----------|-------|
| T1.1 | `NodeItem` 상수 정의 (RADIUS_*, LOD_*) | 15분 | 없음 |
| T1.2 | `_get_current_scale()` 메서드 구현 | 20분 | T1.1 |
| T1.3 | `_world_radius()` 메서드 구현 | 15분 | T1.2 |
| T1.4 | `_get_screen_radius()` 타입별 크기 반환 | 15분 | T1.1 |
| T1.5 | `paint()` 메서드에 Cosmetic 펜 적용 | 30분 | T1.3 |

**Phase 1 소요 시간**: 약 1.5시간

#### Phase 2: TYPE2 시각적 강조 (우선순위: 높음)

| Task ID | 작업 내용 | 예상 시간 | 의존성 |
|---------|----------|----------|-------|
| T2.1 | `_draw_glow()` 글로우 효과 구현 | 30분 | T1.5 |
| T2.2 | `_draw_outline()` 흰색 외곽선 구현 | 20분 | T1.5 |
| T2.3 | `_get_type_color()` 심각도별 색상 | 20분 | T1.1 |
| T2.4 | `paint()` 레이어 순서 적용 | 30분 | T2.1, T2.2, T2.3 |
| T2.5 | `PulseItem` Cosmetic 펜 적용 | 20분 | T1.5 |

**Phase 2 소요 시간**: 약 2시간

#### Phase 3: 정보 라벨 시스템 (우선순위: 중간)

| Task ID | 작업 내용 | 예상 시간 | 의존성 |
|---------|----------|----------|-------|
| T3.1 | `NodeItem.__init__()` integrated_beams 파라미터 추가 | 15분 | 없음 |
| T3.2 | `_get_worst_pair()` 최대 간섭 쌍 반환 | 20분 | T3.1 |
| T3.3 | `_format_sections()` 단면명 포맷팅 | 25분 | T3.1 |
| T3.4 | `_draw_info_label()` 라벨 렌더링 구현 | 45분 | T3.2, T3.3 |
| T3.5 | LOD 시스템 적용 (줌 레벨별 표시) | 30분 | T3.4, T1.2 |
| T3.6 | `ResultViewerWidget.update_data()` integrated_beams 전달 | 15분 | T3.1 |

**Phase 3 소요 시간**: 약 2.5시간

#### Phase 4: 테스트 및 검증 (우선순위: 높음)

| Task ID | 작업 내용 | 예상 시간 | 의존성 |
|---------|----------|----------|-------|
| T4.1 | 단위 테스트 작성 (`test_result_viewer.py`) | 45분 | T3.6 |
| T4.2 | 시각적 검증 (수동 테스트) | 30분 | T3.6 |
| T4.3 | 성능 테스트 (100+ 노드 렌더링) | 20분 | T3.6 |
| T4.4 | Edge case 처리 (empty data, missing sections) | 30분 | T4.1 |

**Phase 4 소요 시간**: 약 2시간

### 5.2 총 예상 소요 시간

| Phase | 내용 | 시간 |
|-------|------|------|
| Phase 1 | Cosmetic 렌더링 | 1.5h |
| Phase 2 | TYPE2 강조 | 2.0h |
| Phase 3 | 정보 라벨 | 2.5h |
| Phase 4 | 테스트/검증 | 2.0h |
| **합계** | | **8.0h** |

### 5.3 작업 순서 권장

```
Phase 1 (T1.1 → T1.5) ─┐
                       ├─→ Phase 2 (T2.1 → T2.5)
Phase 3 (T3.1 → T3.3) ─┘
                               │
                               ▼
                       Phase 3 (T3.4 → T3.6)
                               │
                               ▼
                       Phase 4 (T4.1 → T4.4)
```

---

## 6. 리스크 및 대응 방안

### 6.1 식별된 리스크

| ID | 리스크 | 발생 확률 | 영향도 | 대응 방안 |
|----|-------|----------|-------|----------|
| R1 | 렌더링 성능 저하 (100+ 노드) | 중 | 높 | LOD 시스템으로 라벨 최적화, 캐싱 활용 |
| R2 | Cosmetic 펜 Qt 버전 호환성 | 낮 | 중 | PySide6 6.x 이상 확인, 폴백 로직 |
| R3 | 라벨 겹침 (노드 밀집 시) | 높 | 중 | LOD로 축소 시 라벨 숨김, 클러스터링 고려 |
| R4 | integrated_beams 누락 | 중 | 낮 | 폴백: "?" 표시, 부재명만 표시 |

### 6.2 폴백 전략

```python
# R4 대응: IntegratedBeam 누락 시
def _format_sections(self, pair: PairResult) -> str:
    beam_a = self.integrated_beams.get(pair.beam_a_name)
    beam_b = self.integrated_beams.get(pair.beam_b_name)

    # 둘 다 없으면 단면 정보 생략
    if not beam_a and not beam_b:
        return ""  # 부재기호만 표시

    sec_a = beam_a.section_name if beam_a else "?"
    sec_b = beam_b.section_name if beam_b else "?"

    return f"{sec_a} ↔ {sec_b}"
```

---

## 7. 테스트 계획

### 7.1 단위 테스트

```python
# tests/unit/composite_beam_interference/test_result_viewer.py

import pytest
from PySide6.QtWidgets import QApplication
from gui.result_viewer_widget import NodeItem, ResultViewerWidget
from src.services.composite_beam_interference.models import NodeResult, PairResult, InterferenceType

@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app

class TestNodeItem:
    def test_screen_radius_type2_largest(self, qapp):
        """TYPE2 마커가 가장 크게 표시되는지 확인"""
        node_result = create_node_result(has_interference=True)
        item = NodeItem(node_result)

        assert item._get_screen_radius() == NodeItem.RADIUS_TYPE2
        assert item._get_screen_radius() > NodeItem.RADIUS_TYPE1
        assert item._get_screen_radius() > NodeItem.RADIUS_TYPE3

    def test_info_label_with_sections(self, qapp):
        """단면 정보가 라벨에 포함되는지 확인"""
        # ... 테스트 구현

    def test_lod_minimal_no_label(self, qapp):
        """최소 줌 레벨에서 라벨이 표시되지 않는지 확인"""
        # ... 테스트 구현

class TestResultViewerWidget:
    def test_integrated_beams_passed_to_items(self, qapp):
        """integrated_beams가 NodeItem에 전달되는지 확인"""
        # ... 테스트 구현
```

### 7.2 시각적 검증 체크리스트

- [ ] 초기 fitInView 상태에서 TYPE2 마커가 육안으로 보이는지
- [ ] 줌아웃 상태에서 마커 크기가 유지되는지 (Cosmetic)
- [ ] TYPE2가 TYPE1/TYPE3보다 크게 표시되는지
- [ ] 글로우 효과가 TYPE2에만 적용되는지
- [ ] 펄스 애니메이션이 줌 무관하게 동작하는지
- [ ] 줌인 시 부재기호/단면명이 표시되는지
- [ ] 간섭량 숫자가 정확히 표시되는지

---

## 8. 버전 및 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| v2.8.0 | 2025-12-09 | 초안 작성 |

---

## 9. 참고 자료

- `gui/result_viewer_widget.py` - 현재 구현
- `gui/theme.py` - NanoColors 정의
- `src/services/composite_beam_interference/models.py` - 데이터 모델
- Qt Documentation: [QPen::setCosmetic()](https://doc.qt.io/qt-6/qpen.html#setCosmetic)
- Qt Documentation: [QRadialGradient](https://doc.qt.io/qt-6/qradialgradient.html)
