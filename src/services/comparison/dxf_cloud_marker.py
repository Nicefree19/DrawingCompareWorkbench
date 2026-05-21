"""DXF 구름마크(Revision Cloud) 생성기

Sprint 9 Phase 4: DxfCloudMarker
비교 결과를 바탕으로 변경점에 구름마크를 추가한 DXF 파일을 생성합니다.

기능:
    - 변경 타입별 레이어 생성 (ADDED, DELETED, MODIFIED)
    - ezdxf.revcloud를 사용한 구름마크 생성
    - AutoCAD 완벽 호환
"""

import logging
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ezdxf 임포트
try:
    import ezdxf
    from ezdxf import revcloud
    from ezdxf.math import Vec2

    EZDXF_AVAILABLE = True
except ImportError:
    EZDXF_AVAILABLE = False
    logger.warning("ezdxf가 설치되지 않았습니다")

from .dxf_comparator import DxfChange, DxfChangeType


class DxfCloudMarker:
    """DXF 변경점 구름마크 생성기

    비교 결과를 받아 변경점에 구름마크를 추가한 새 DXF 파일을 생성합니다.

    사용 예시:
        marker = DxfCloudMarker()
        output_path = marker.create_marked_dxf(
            base_dxf_path="new.dxf",
            changes=comparison_result.changes,
            output_path="marked_output.dxf"
        )
    """

    # 변경 타입별 레이어 이름
    LAYER_NAMES = {
        DxfChangeType.ADDED: "CLOUD_ADDED",
        DxfChangeType.DELETED: "CLOUD_DELETED",
        DxfChangeType.MODIFIED: "CLOUD_MODIFIED",
    }

    # Phase P (RV-20260508-014) — AIA 표준 색상 매핑 (cyan/green/magenta).
    # revision_marker SSoT 와 일치. 기존 red(1)/orange(30) 은 LEGACY 로
    # 별도 보존. 사용자 친숙성 목표: AutoCAD/Revit 표준 표기.
    COLORS = {
        DxfChangeType.ADDED: 3,    # ACI green
        DxfChangeType.DELETED: 6,  # ACI magenta (was red 1)
        DxfChangeType.MODIFIED: 4, # ACI cyan (was orange 30) — AIA 표준
    }
    COLORS_LEGACY = {
        DxfChangeType.ADDED: 3,
        DxfChangeType.DELETED: 1,   # red
        DxfChangeType.MODIFIED: 30, # orange
    }

    # 변경 타입별 한글 라벨
    LABELS = {
        DxfChangeType.ADDED: "추가",
        DxfChangeType.DELETED: "삭제",
        DxfChangeType.MODIFIED: "수정",
    }

    def __init__(
        self,
        segment_length: float = 15.0,
        margin: float = 30.0,
        add_labels: bool = True,
        label_height: float = 10.0,
        max_segments_per_cloud: int = 32,
    ):
        """
        Args:
            segment_length: 구름마크 호 세그먼트 길이 (mm)
            margin: 바운딩 박스 여백 (mm)
            add_labels: 라벨 텍스트 추가 여부
            label_height: 라벨 텍스트 높이 (mm)
        """
        if not EZDXF_AVAILABLE:
            raise ImportError("ezdxf가 필요합니다: pip install ezdxf")

        self.segment_length = segment_length
        self.margin = margin
        self.add_labels = add_labels
        self.label_height = label_height
        self.max_segments_per_cloud = max(4, int(max_segments_per_cloud))

    def create_marked_dxf(
        self,
        base_dxf_path: Path,
        changes: List[DxfChange],
        output_path: Path,
    ) -> Path:
        """구름마크가 추가된 새 DXF 파일 생성

        Args:
            base_dxf_path: 기준 DXF 파일 (new 파일 권장)
            changes: 변경점 목록
            output_path: 출력 DXF 파일 경로

        Returns:
            생성된 DXF 파일 경로
        """
        base_dxf_path = Path(base_dxf_path)
        output_path = Path(output_path)

        if not base_dxf_path.exists():
            raise FileNotFoundError(f"기준 DXF 파일을 찾을 수 없습니다: {base_dxf_path}")

        logger.info(f"구름마크 DXF 생성 시작: {len(changes)}개 변경점")

        # DXF 파일 열기
        doc = ezdxf.readfile(str(base_dxf_path))
        msp = doc.modelspace()

        # 레이어 생성
        self._create_layers(doc)

        # 변경점별 구름마크 추가
        added_count = 0
        for change in changes:
            if change.location is None:
                continue

            try:
                self._add_cloud_for_change(msp, change)
                added_count += 1
            except Exception as e:
                logger.warning(f"구름마크 추가 실패: {e}")

        # DXF 저장
        doc.saveas(str(output_path))

        logger.info(f"구름마크 DXF 생성 완료: {output_path} ({added_count}개 마크)")

        return output_path

    def create_marked_dxf_from_zones(
        self,
        base_dxf_path: Path,
        zones: Iterable[Any],
        output_path: Path,
        *,
        use_old_bbox: bool = False,
    ) -> Path:
        """Create a DXF with one revision cloud per grouped change zone."""
        base_dxf_path = Path(base_dxf_path)
        output_path = Path(output_path)

        if not base_dxf_path.exists():
            raise FileNotFoundError(f"Base DXF file not found: {base_dxf_path}")

        doc = ezdxf.readfile(str(base_dxf_path))
        msp = doc.modelspace()
        self._create_layers(doc)

        added_count = 0
        for zone in zones:
            bbox = getattr(zone, "old_bbox", None) if use_old_bbox else getattr(zone, "bbox", None)
            if bbox is None:
                bbox = getattr(zone, "bbox", None)
            if bbox is None:
                continue
            change_type = self._zone_dxf_change_type(getattr(zone, "change_type", "modified"))
            label = getattr(zone, "label", None) or getattr(zone, "zone_id", "")
            try:
                self._add_cloud_for_bbox(msp, bbox, change_type, str(label))
                added_count += 1
            except Exception as e:
                logger.warning(f"Zone revision cloud failed: {e}")

        doc.saveas(str(output_path))
        logger.info(f"Zone cloud DXF created: {output_path} ({added_count} zones)")
        return output_path

    def _create_layers(self, doc: "ezdxf.Drawing"):
        """변경 타입별 레이어 생성. Phase P (RV-20260508-014) — lineweight
        AIA 표준 0.50mm (50/100 mm) 적용."""
        from .revision_marker import LINEWEIGHT_REVCLOUD_MM
        for change_type, layer_name in self.LAYER_NAMES.items():
            color = self.COLORS.get(change_type, 7)  # 기본: 흰색

            if layer_name not in doc.layers:
                doc.layers.add(
                    layer_name,
                    color=color,
                    linetype="CONTINUOUS",
                    lineweight=LINEWEIGHT_REVCLOUD_MM,
                )
                logger.debug(f"레이어 생성: {layer_name} (색상: {color}, lw={LINEWEIGHT_REVCLOUD_MM})")

    def _add_cloud_for_change(self, msp, change: DxfChange):
        """단일 변경점에 대한 구름마크 추가"""
        # 바운딩 박스 계산
        bbox = self._get_bounding_box(change)
        if bbox is None:
            return

        min_pt, max_pt = bbox

        # 폴리곤 정점 (사각형)
        polygon = [
            (min_pt[0], min_pt[1]),  # 좌하
            (max_pt[0], min_pt[1]),  # 우하
            (max_pt[0], max_pt[1]),  # 우상
            (min_pt[0], max_pt[1]),  # 좌상
        ]

        # 레이어 및 색상
        layer_name = self.LAYER_NAMES.get(change.change_type, "CLOUD_CHANGES")
        color = self.COLORS.get(change.change_type, 7)

        # 구름마크 추가
        try:
            revcloud.add_entity(
                msp,
                polygon,
                segment_length=self._segment_length_for_polygon(polygon),
                calligraphy=True,
                dxfattribs={
                    "layer": layer_name,
                    "color": color,
                },
            )
        except Exception as e:
            logger.warning(f"revcloud.add_entity 실패: {e}")
            # Fallback: 일반 폴리라인으로 대체
            self._add_fallback_polyline(msp, polygon, layer_name, color)

        # 라벨 추가
        if self.add_labels:
            self._add_label(msp, change, min_pt, max_pt, layer_name, color)

    def _add_cloud_for_bbox(
        self,
        msp,
        bbox: Tuple[float, float, float, float],
        change_type: DxfChangeType,
        label_text: str,
    ) -> None:
        min_x, min_y, max_x, max_y = [float(value) for value in bbox]
        min_pt = (min_x - self.margin, min_y - self.margin)
        max_pt = (max_x + self.margin, max_y + self.margin)
        polygon = [
            (min_pt[0], min_pt[1]),
            (max_pt[0], min_pt[1]),
            (max_pt[0], max_pt[1]),
            (min_pt[0], max_pt[1]),
        ]
        layer_name = self.LAYER_NAMES.get(change_type, "CLOUD_MODIFIED")
        color = self.COLORS.get(change_type, 7)
        try:
            revcloud.add_entity(
                msp,
                polygon,
                segment_length=self._segment_length_for_polygon(polygon),
                calligraphy=True,
                dxfattribs={"layer": layer_name, "color": color},
            )
        except Exception as e:
            logger.warning(f"revcloud.add_entity failed for zone: {e}")
            self._add_fallback_polyline(msp, polygon, layer_name, color)
        if self.add_labels:
            self._add_zone_label(msp, label_text, min_pt, max_pt, layer_name, color)

    def _segment_length_for_polygon(self, polygon: List[Tuple[float, float]]) -> float:
        """Scale cloud density for large bboxes to keep marked DXF generation bounded."""

        if len(polygon) < 2:
            return self.segment_length
        perimeter = 0.0
        points = list(polygon)
        for index, point in enumerate(points):
            next_point = points[(index + 1) % len(points)]
            perimeter += math.hypot(next_point[0] - point[0], next_point[1] - point[1])
        if perimeter <= 0:
            return self.segment_length
        return max(float(self.segment_length), perimeter / float(self.max_segments_per_cloud))

    def _add_zone_label(
        self,
        msp,
        label_text: str,
        min_pt: Tuple[float, float],
        max_pt: Tuple[float, float],
        layer_name: str,
        color: int,
    ) -> None:
        label_x = (min_pt[0] + max_pt[0]) / 2
        label_y = max_pt[1] + 5
        msp.add_text(
            label_text,
            dxfattribs={
                "layer": layer_name,
                "color": color,
                "height": self.label_height,
                "insert": (label_x, label_y),
            },
        )

    def _zone_dxf_change_type(self, value: Any) -> DxfChangeType:
        text = str(value).lower()
        if text == DxfChangeType.ADDED.value:
            return DxfChangeType.ADDED
        if text == DxfChangeType.DELETED.value:
            return DxfChangeType.DELETED
        return DxfChangeType.MODIFIED

    def _get_bounding_box(
        self, change: DxfChange
    ) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
        """변경점의 바운딩 박스 계산

        Returns:
            ((min_x, min_y), (max_x, max_y)) 또는 None
        """
        data = change.old_data or change.new_data
        if not data:
            # location만 있는 경우
            if change.location:
                x, y = change.location
                return ((x - self.margin, y - self.margin), (x + self.margin, y + self.margin))
            return None

        entity_type = change.entity_type

        if entity_type == "LINE":
            start = data.get("start", (0, 0))
            end = data.get("end", (0, 0))
            return (
                (min(start[0], end[0]) - self.margin, min(start[1], end[1]) - self.margin),
                (max(start[0], end[0]) + self.margin, max(start[1], end[1]) + self.margin),
            )

        elif entity_type in ("CIRCLE", "ARC"):
            center = data.get("center", (0, 0))
            radius = data.get("radius", 50)
            return (
                (center[0] - radius - self.margin, center[1] - radius - self.margin),
                (center[0] + radius + self.margin, center[1] + radius + self.margin),
            )

        elif entity_type in ("TEXT", "MTEXT", "DIMENSION"):
            pos = data.get("position") or data.get("defpoint", (0, 0))
            # 텍스트는 위치 + 고정 크기
            text_width = 100  # 추정값
            text_height = 20
            return (
                (pos[0] - self.margin, pos[1] - self.margin),
                (pos[0] + text_width + self.margin, pos[1] + text_height + self.margin),
            )

        elif entity_type == "INSERT":
            pos = data.get("insert_point", (0, 0))
            # 블록은 위치 + 고정 크기
            return (
                (pos[0] - self.margin, pos[1] - self.margin),
                (pos[0] + 100 + self.margin, pos[1] + 100 + self.margin),
            )

        elif entity_type in ("LWPOLYLINE", "POLYLINE"):
            points = data.get("points", [])
            if not points:
                return None
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            return (
                (min(xs) - self.margin, min(ys) - self.margin),
                (max(xs) + self.margin, max(ys) + self.margin),
            )

        # 기타: location 사용
        if change.location:
            x, y = change.location
            return ((x - self.margin, y - self.margin), (x + self.margin, y + self.margin))

        return None

    def _add_label(
        self,
        msp,
        change: DxfChange,
        min_pt: Tuple[float, float],
        max_pt: Tuple[float, float],
        layer_name: str,
        color: int,
    ):
        """구름마크 위에 라벨 텍스트 추가"""
        label_text = self.LABELS.get(change.change_type, "?")

        # 라벨 위치: 구름마크 상단 중앙
        label_x = (min_pt[0] + max_pt[0]) / 2
        label_y = max_pt[1] + 5  # 약간 위

        msp.add_text(
            label_text,
            dxfattribs={
                "layer": layer_name,
                "color": color,
                "height": self.label_height,
                "insert": (label_x, label_y),
            },
        )

    def _add_fallback_polyline(
        self, msp, polygon: List[Tuple[float, float]], layer_name: str, color: int
    ):
        """구름마크 대신 일반 폴리라인으로 대체"""
        msp.add_lwpolyline(
            polygon,
            close=True,
            dxfattribs={
                "layer": layer_name,
                "color": color,
            },
        )
