# -*- coding: utf-8 -*-
"""DXF 시각화 서비스

Phase 3 P3-6: DXF 시각화 UI 연결

DxfRenderer와 DxfOverlayRenderer를 통합하여 비교 결과 시각화를 제공합니다.

기능:
    - DXF 파일 렌더링 및 오버레이 생성
    - 색상 코드: 추가=녹색, 삭제=빨간색, 수정=주황색
    - 변경점 클릭 시 해당 위치로 이동 지원
    - HTML 리포트 내보내기

Author: TEKLA_MCP Team
Date: 2025-12-23
Sprint: Phase 3 P3-6
"""

import base64
import io
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# 의존성 검사
try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    logger.warning("OpenCV가 설치되지 않았습니다")

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from .dxf_comparator import DxfChange, DxfChangeType, DxfComparisonResult
from .dxf_renderer import DxfRenderer, RENDERER_AVAILABLE
from .dxf_overlay_renderer import DxfOverlayRenderer, CV2_AVAILABLE as OVERLAY_AVAILABLE


@dataclass
class ClickableRegion:
    """클릭 가능한 변경점 영역

    UI에서 변경점 클릭 시 해당 위치로 이동하기 위한 정보.

    Attributes:
        change_id: 변경 항목 인덱스
        pixel_x: 이미지 상 X 좌표 (픽셀)
        pixel_y: 이미지 상 Y 좌표 (픽셀)
        cad_x: CAD 좌표 X
        cad_y: CAD 좌표 Y
        change_type: 변경 타입
        entity_type: 엔티티 타입
        layer: 레이어 이름
        radius: 클릭 영역 반경 (픽셀)
    """
    change_id: int
    pixel_x: int
    pixel_y: int
    cad_x: float
    cad_y: float
    change_type: DxfChangeType
    entity_type: str
    layer: str
    radius: int = 15

    def contains_point(self, x: int, y: int) -> bool:
        """지정된 좌표가 이 영역 내에 있는지 확인"""
        distance = ((x - self.pixel_x) ** 2 + (y - self.pixel_y) ** 2) ** 0.5
        return distance <= self.radius

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
            "change_id": self.change_id,
            "pixel_x": self.pixel_x,
            "pixel_y": self.pixel_y,
            "cad_x": self.cad_x,
            "cad_y": self.cad_y,
            "change_type": self.change_type.value,
            "entity_type": self.entity_type,
            "layer": self.layer,
            "radius": self.radius,
        }


@dataclass
class ColorConfig:
    """색상 토글 설정 (Phase 3+ QW-2)

    변경 유형별 색상 및 표시 여부를 설정합니다.

    Attributes:
        added_color: 추가 항목 색상 (RGB 튜플, 0-255)
        deleted_color: 삭제 항목 색상 (RGB 튜플, 0-255)
        modified_color: 수정 항목 색상 (RGB 튜플, 0-255)
        show_added: 추가 항목 표시 여부
        show_deleted: 삭제 항목 표시 여부
        show_modified: 수정 항목 표시 여부

    Examples:
        >>> config = ColorConfig.get_default()
        >>> config.show_deleted = False  # 삭제 항목 숨기기
        >>> config = ColorConfig.get_colorblind_friendly()  # 색약 친화적
    """

    # 색상 설정 (RGB 튜플)
    added_color: Tuple[int, int, int] = (0, 255, 0)       # 녹색
    deleted_color: Tuple[int, int, int] = (255, 0, 0)     # 빨간색
    modified_color: Tuple[int, int, int] = (255, 165, 0)  # 주황색

    # 표시 토글
    show_added: bool = True
    show_deleted: bool = True
    show_modified: bool = True

    @classmethod
    def get_default(cls) -> "ColorConfig":
        """기본 색상 설정 반환"""
        return cls()

    @classmethod
    def get_colorblind_friendly(cls) -> "ColorConfig":
        """색약 친화적 색상 설정 반환

        파란색-주황색 계열로 변경하여 적록색약자도 구분 가능
        """
        return cls(
            added_color=(0, 114, 178),     # 파란색
            deleted_color=(213, 94, 0),    # 주황-빨강
            modified_color=(204, 121, 167), # 분홍
        )

    @classmethod
    def get_high_contrast(cls) -> "ColorConfig":
        """고대비 색상 설정 반환"""
        return cls(
            added_color=(0, 255, 0),       # 밝은 녹색
            deleted_color=(255, 0, 255),   # 마젠타
            modified_color=(255, 255, 0),  # 노란색
        )

    def get_color_for_type(self, change_type: DxfChangeType) -> Tuple[int, int, int]:
        """변경 유형에 대한 색상 반환"""
        color_map = {
            DxfChangeType.ADDED: self.added_color,
            DxfChangeType.DELETED: self.deleted_color,
            DxfChangeType.MODIFIED: self.modified_color,
        }
        return color_map.get(change_type, (128, 128, 128))  # 기본: 회색

    def get_hex_for_type(self, change_type: DxfChangeType) -> str:
        """변경 유형에 대한 Hex 색상 반환"""
        r, g, b = self.get_color_for_type(change_type)
        return f"#{r:02X}{g:02X}{b:02X}"

    def is_visible(self, change_type: DxfChangeType) -> bool:
        """변경 유형의 표시 여부 반환"""
        visibility_map = {
            DxfChangeType.ADDED: self.show_added,
            DxfChangeType.DELETED: self.show_deleted,
            DxfChangeType.MODIFIED: self.show_modified,
        }
        return visibility_map.get(change_type, True)

    def filter_changes(self, changes: List[DxfChange]) -> List[DxfChange]:
        """표시할 변경 항목만 필터링"""
        return [c for c in changes if self.is_visible(c.change_type)]

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "added_color": list(self.added_color),
            "deleted_color": list(self.deleted_color),
            "modified_color": list(self.modified_color),
            "show_added": self.show_added,
            "show_deleted": self.show_deleted,
            "show_modified": self.show_modified,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ColorConfig":
        """딕셔너리에서 생성"""
        return cls(
            added_color=tuple(data.get("added_color", [0, 255, 0])),
            deleted_color=tuple(data.get("deleted_color", [255, 0, 0])),
            modified_color=tuple(data.get("modified_color", [255, 165, 0])),
            show_added=data.get("show_added", True),
            show_deleted=data.get("show_deleted", True),
            show_modified=data.get("show_modified", True),
        )


@dataclass
class VisualizationResult:
    """시각화 결과

    Attributes:
        overlay_image: 오버레이된 이미지 (numpy 배열)
        side_by_side_image: 좌우 비교 이미지 (optional)
        clickable_regions: 클릭 가능한 영역 목록
        image_size: 이미지 크기 (width, height)
        extents: CAD 좌표 범위
        changes_count: 변경점 개수 (타입별)
    """
    overlay_image: Optional[np.ndarray] = None
    side_by_side_image: Optional[np.ndarray] = None
    clickable_regions: List[ClickableRegion] = field(default_factory=list)
    image_size: Tuple[int, int] = (0, 0)
    extents: Optional[Tuple[Tuple[float, float], Tuple[float, float]]] = None
    changes_count: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
            "image_size": self.image_size,
            "extents": self.extents,
            "changes_count": self.changes_count,
            "clickable_regions": [r.to_dict() for r in self.clickable_regions],
        }


class VisualizationService:
    """DXF 시각화 통합 서비스

    DxfRenderer와 DxfOverlayRenderer를 통합하여
    비교 결과 시각화 및 HTML 리포트 생성을 제공합니다.

    사용 예시:
        service = VisualizationService()
        result = service.create_overlay(
            dxf_path="drawing.dxf",
            changes=comparison_result.changes,
        )

        # HTML 리포트 생성
        service.export_html_report(
            old_path="old.dxf",
            new_path="new.dxf",
            comparison_result=result,
            output_path="report.html",
        )
    """

    # 변경 타입별 색상 (RGB)
    COLORS_RGB = {
        DxfChangeType.ADDED: (0, 255, 0),     # 녹색
        DxfChangeType.DELETED: (255, 0, 0),   # 빨간색
        DxfChangeType.MODIFIED: (255, 165, 0), # 주황색
    }

    # 변경 타입별 색상 (Hex)
    COLORS_HEX = {
        DxfChangeType.ADDED: "#00FF00",
        DxfChangeType.DELETED: "#FF0000",
        DxfChangeType.MODIFIED: "#FFA500",
    }

    def __init__(self, dpi: int = 150):
        """
        Args:
            dpi: 렌더링 해상도 (기본 150 DPI)
        """
        if not RENDERER_AVAILABLE:
            logger.warning(
                "DXF 렌더링 불가: ezdxf 또는 matplotlib 미설치"
            )
        if not OVERLAY_AVAILABLE:
            logger.warning("오버레이 렌더링 불가: OpenCV 미설치")

        self.dpi = dpi
        self._renderer: Optional[DxfRenderer] = None
        self._overlay_renderer: Optional[DxfOverlayRenderer] = None

    @property
    def renderer(self) -> DxfRenderer:
        """DxfRenderer 지연 초기화"""
        if self._renderer is None:
            self._renderer = DxfRenderer(dpi=self.dpi)
        return self._renderer

    @property
    def overlay_renderer(self) -> DxfOverlayRenderer:
        """DxfOverlayRenderer 지연 초기화"""
        if self._overlay_renderer is None:
            self._overlay_renderer = DxfOverlayRenderer()
        return self._overlay_renderer

    def create_overlay(
        self,
        dxf_path: Path,
        changes: List[DxfChange],
        background_color: str = "#FFFFFF",
        color_config: Optional[ColorConfig] = None,
    ) -> VisualizationResult:
        """DXF 파일에 변경점 오버레이 생성

        AC1: 비교 완료 후 오버레이 이미지 자동 생성
        AC2: 추가=녹색, 삭제=빨간색, 수정=주황색 표시
        QW-2: ColorConfig를 통한 색상/표시 토글 지원

        Args:
            dxf_path: DXF 파일 경로
            changes: 변경점 목록
            background_color: 배경색 (hex)
            color_config: 색상 토글 설정 (None이면 기본값 사용)

        Returns:
            VisualizationResult: 시각화 결과
        """
        dxf_path = Path(dxf_path)
        result = VisualizationResult()

        # ColorConfig 기본값 적용
        if color_config is None:
            color_config = ColorConfig.get_default()

        if not RENDERER_AVAILABLE:
            logger.error("DXF 렌더링 불가: 의존성 미설치")
            return result

        # 1. DXF 렌더링
        try:
            base_image = self.renderer.render(dxf_path, background_color=background_color)
            extents = self.renderer.get_extents(dxf_path)
        except Exception as e:
            logger.error(f"DXF 렌더링 실패: {e}")
            return result

        # 2. ColorConfig에 따라 표시할 변경점 필터링
        filtered_changes = color_config.filter_changes(changes)

        # 3. 오버레이 렌더링
        if OVERLAY_AVAILABLE and filtered_changes:
            try:
                overlay_image = self.overlay_renderer.render(
                    base_image=base_image,
                    changes=filtered_changes,
                    extents=extents,
                    color_config=color_config,  # 색상 설정 전달
                )
            except Exception as e:
                logger.warning(f"오버레이 렌더링 실패: {e}")
                overlay_image = base_image
        else:
            overlay_image = base_image

        result.overlay_image = overlay_image
        result.extents = extents
        result.image_size = (overlay_image.shape[1], overlay_image.shape[0])

        # 3. 클릭 가능 영역 계산
        result.clickable_regions = self._calculate_clickable_regions(
            changes=changes,
            extents=extents,
            image_size=result.image_size,
        )

        # 4. 변경점 통계
        result.changes_count = {
            "added": sum(1 for c in changes if c.change_type == DxfChangeType.ADDED),
            "deleted": sum(1 for c in changes if c.change_type == DxfChangeType.DELETED),
            "modified": sum(1 for c in changes if c.change_type == DxfChangeType.MODIFIED),
            "total": len(changes),
        }

        logger.info(
            f"오버레이 생성 완료: {result.image_size[0]}x{result.image_size[1]}, "
            f"변경점 {result.changes_count['total']}개"
        )

        return result

    def create_side_by_side(
        self,
        old_dxf_path: Path,
        new_dxf_path: Path,
        changes: List[DxfChange],
    ) -> VisualizationResult:
        """좌우 비교 이미지 생성

        Args:
            old_dxf_path: 이전 DXF 파일 경로
            new_dxf_path: 새 DXF 파일 경로
            changes: 변경점 목록

        Returns:
            VisualizationResult: 시각화 결과
        """
        result = VisualizationResult()

        if not RENDERER_AVAILABLE:
            logger.error("DXF 렌더링 불가")
            return result

        try:
            # 렌더링
            old_image = self.renderer.render(old_dxf_path)
            new_image = self.renderer.render(new_dxf_path)
            extents = self.renderer.get_extents(new_dxf_path)

            # 좌우 비교 이미지
            if OVERLAY_AVAILABLE:
                side_by_side = self.overlay_renderer.render_side_by_side(
                    img_a=old_image,
                    img_b=new_image,
                    changes=changes,
                    extents=extents,
                )
            else:
                # 단순 좌우 결합
                side_by_side = np.hstack([old_image, new_image])

            result.side_by_side_image = side_by_side
            result.extents = extents
            result.image_size = (side_by_side.shape[1], side_by_side.shape[0])

        except Exception as e:
            logger.error(f"좌우 비교 이미지 생성 실패: {e}")

        return result

    def _calculate_clickable_regions(
        self,
        changes: List[DxfChange],
        extents: Tuple[Tuple[float, float], Tuple[float, float]],
        image_size: Tuple[int, int],
    ) -> List[ClickableRegion]:
        """클릭 가능 영역 계산

        AC3: UI에서 변경점 클릭 시 해당 위치로 이동

        Args:
            changes: 변경점 목록
            extents: CAD 좌표 범위
            image_size: 이미지 크기 (width, height)

        Returns:
            클릭 가능 영역 목록
        """
        regions = []
        (min_x, min_y), (max_x, max_y) = extents
        cad_width = max_x - min_x
        cad_height = max_y - min_y
        img_width, img_height = image_size

        if cad_width == 0 or cad_height == 0:
            return regions

        for i, change in enumerate(changes):
            if change.location is None:
                continue

            cad_x, cad_y = change.location

            # CAD 좌표 → 픽셀 좌표 변환
            pixel_x = int((cad_x - min_x) / cad_width * img_width)
            # Y축 반전 (CAD는 Y가 위로, 이미지는 아래로)
            pixel_y = int((1 - (cad_y - min_y) / cad_height) * img_height)

            # 범위 제한
            pixel_x = max(0, min(pixel_x, img_width - 1))
            pixel_y = max(0, min(pixel_y, img_height - 1))

            regions.append(
                ClickableRegion(
                    change_id=i,
                    pixel_x=pixel_x,
                    pixel_y=pixel_y,
                    cad_x=cad_x,
                    cad_y=cad_y,
                    change_type=change.change_type,
                    entity_type=change.entity_type,
                    layer=change.layer,
                )
            )

        return regions

    def find_change_at_position(
        self,
        regions: List[ClickableRegion],
        x: int,
        y: int,
    ) -> Optional[ClickableRegion]:
        """지정된 픽셀 좌표에서 변경점 찾기

        Args:
            regions: 클릭 가능 영역 목록
            x: 클릭한 X 좌표 (픽셀)
            y: 클릭한 Y 좌표 (픽셀)

        Returns:
            해당 위치의 ClickableRegion 또는 None
        """
        for region in regions:
            if region.contains_point(x, y):
                return region
        return None

    def save_overlay_image(
        self,
        result: VisualizationResult,
        output_path: Path,
    ) -> Path:
        """오버레이 이미지 파일로 저장

        Args:
            result: VisualizationResult
            output_path: 출력 경로 (.png, .jpg)

        Returns:
            저장된 파일 경로
        """
        if result.overlay_image is None:
            raise ValueError("오버레이 이미지가 없습니다")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if CV2_AVAILABLE:
            # RGB → BGR
            img_bgr = cv2.cvtColor(result.overlay_image, cv2.COLOR_RGB2BGR)
            cv2.imwrite(str(output_path), img_bgr)
        elif PIL_AVAILABLE:
            img = Image.fromarray(result.overlay_image)
            img.save(str(output_path))
        else:
            raise ImportError("이미지 저장을 위해 OpenCV 또는 Pillow가 필요합니다")

        logger.info(f"오버레이 이미지 저장: {output_path}")
        return output_path

    def _image_to_base64(self, image: np.ndarray) -> str:
        """numpy 배열을 base64 문자열로 변환"""
        if PIL_AVAILABLE:
            img = Image.fromarray(image)
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            return base64.b64encode(buffer.getvalue()).decode("utf-8")
        elif CV2_AVAILABLE:
            img_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
            _, buffer = cv2.imencode(".png", img_bgr)
            return base64.b64encode(buffer).decode("utf-8")
        else:
            return ""

    def export_html_report(
        self,
        old_path: Path,
        new_path: Path,
        comparison_result: DxfComparisonResult,
        visualization_result: Optional[VisualizationResult],
        output_path: Path,
        title: str = "DXF 비교 리포트",
    ) -> Path:
        """HTML 리포트 내보내기

        AC4: HTML 리포트 내보내기 기능

        Args:
            old_path: 이전 DXF 파일 경로
            new_path: 새 DXF 파일 경로
            comparison_result: 비교 결과
            visualization_result: 시각화 결과 (optional)
            output_path: 출력 HTML 경로
            title: 리포트 제목

        Returns:
            저장된 HTML 파일 경로
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # 이미지 base64 인코딩
        image_base64 = ""
        if visualization_result and visualization_result.overlay_image is not None:
            image_base64 = self._image_to_base64(visualization_result.overlay_image)

        # 변경점 목록 HTML 생성
        changes_html = self._generate_changes_table_html(comparison_result.changes)

        # 통계 HTML 생성
        stats_html = self._generate_stats_html(comparison_result)

        # 레이어 통계 HTML (P3-4)
        layer_stats_html = self._generate_layer_stats_html(comparison_result)

        # 전체 HTML 조합
        html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        :root {{
            --color-added: #00FF00;
            --color-deleted: #FF0000;
            --color-modified: #FFA500;
            --bg-primary: #1a1a2e;
            --bg-secondary: #16213e;
            --text-primary: #eaeaea;
            --text-secondary: #a0a0a0;
            --border-color: #3a3a5a;
        }}

        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: 'Segoe UI', Tahoma, sans-serif;
            background-color: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
            padding: 20px;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}

        header {{
            text-align: center;
            margin-bottom: 30px;
            padding: 20px;
            background: linear-gradient(135deg, var(--bg-secondary), var(--bg-primary));
            border-radius: 12px;
            border: 1px solid var(--border-color);
        }}

        h1 {{
            font-size: 2em;
            margin-bottom: 10px;
        }}

        .meta-info {{
            color: var(--text-secondary);
            font-size: 0.9em;
        }}

        .section {{
            background-color: var(--bg-secondary);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            border: 1px solid var(--border-color);
        }}

        .section-title {{
            font-size: 1.3em;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 1px solid var(--border-color);
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
        }}

        .stat-card {{
            background-color: var(--bg-primary);
            padding: 15px;
            border-radius: 8px;
            text-align: center;
        }}

        .stat-value {{
            font-size: 2em;
            font-weight: bold;
        }}

        .stat-label {{
            color: var(--text-secondary);
            font-size: 0.9em;
        }}

        .stat-added {{ color: var(--color-added); }}
        .stat-deleted {{ color: var(--color-deleted); }}
        .stat-modified {{ color: var(--color-modified); }}

        .image-container {{
            text-align: center;
            overflow: auto;
            max-height: 600px;
            background: #000;
            border-radius: 8px;
            padding: 10px;
        }}

        .image-container img {{
            max-width: 100%;
            height: auto;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }}

        th, td {{
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
        }}

        th {{
            background-color: var(--bg-primary);
            font-weight: 600;
        }}

        tr:hover {{
            background-color: rgba(255, 255, 255, 0.05);
        }}

        .badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 0.85em;
            font-weight: 500;
        }}

        .badge-added {{
            background-color: rgba(0, 255, 0, 0.2);
            color: var(--color-added);
        }}

        .badge-deleted {{
            background-color: rgba(255, 0, 0, 0.2);
            color: var(--color-deleted);
        }}

        .badge-modified {{
            background-color: rgba(255, 165, 0, 0.2);
            color: var(--color-modified);
        }}

        .legend {{
            display: flex;
            gap: 20px;
            justify-content: center;
            margin-top: 10px;
        }}

        .legend-item {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .legend-color {{
            width: 16px;
            height: 16px;
            border-radius: 4px;
        }}

        .footer {{
            text-align: center;
            margin-top: 30px;
            padding: 20px;
            color: var(--text-secondary);
            font-size: 0.85em;
        }}

        @media (max-width: 768px) {{
            .stats-grid {{
                grid-template-columns: repeat(2, 1fr);
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>{title}</h1>
            <div class="meta-info">
                <p>이전 파일: {Path(old_path).name}</p>
                <p>새 파일: {Path(new_path).name}</p>
                <p>생성 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            </div>
        </header>

        <!-- 요약 통계 -->
        {stats_html}

        <!-- 오버레이 이미지 -->
        {f'''<section class="section">
            <h2 class="section-title">📷 변경점 시각화</h2>
            <div class="image-container">
                <img src="data:image/png;base64,{image_base64}" alt="Overlay Image">
            </div>
            <div class="legend">
                <div class="legend-item">
                    <div class="legend-color" style="background-color: var(--color-added);"></div>
                    <span>추가 (Added)</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background-color: var(--color-deleted);"></div>
                    <span>삭제 (Deleted)</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background-color: var(--color-modified);"></div>
                    <span>수정 (Modified)</span>
                </div>
            </div>
        </section>''' if image_base64 else ''}

        <!-- 레이어 통계 -->
        {layer_stats_html}

        <!-- 변경 목록 -->
        <section class="section">
            <h2 class="section-title">📋 변경 목록</h2>
            {changes_html}
        </section>

        <footer>
            <p>Generated by TEKLA_MCP Visualization Service</p>
            <p>Phase 3 P3-6: DXF 시각화 UI 연결</p>
        </footer>
    </div>
</body>
</html>"""

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        logger.info(f"HTML 리포트 저장: {output_path}")
        return output_path

    def _generate_stats_html(self, result: DxfComparisonResult) -> str:
        """통계 섹션 HTML 생성"""
        return f"""<section class="section">
            <h2 class="section-title">📊 요약 통계</h2>
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-value">{result.total_changes}</div>
                    <div class="stat-label">전체 변경</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value stat-added">{result.added_count}</div>
                    <div class="stat-label">추가됨</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value stat-deleted">{result.deleted_count}</div>
                    <div class="stat-label">삭제됨</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value stat-modified">{result.modified_count}</div>
                    <div class="stat-label">수정됨</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">{len(result.get_layers())}</div>
                    <div class="stat-label">영향받은 레이어</div>
                </div>
            </div>
        </section>"""

    def _generate_layer_stats_html(self, result: DxfComparisonResult) -> str:
        """레이어 통계 HTML 생성"""
        if not result.layer_statistics:
            return ""

        rows = ""
        for layer, stat in sorted(result.layer_statistics.items()):
            priority_class = {
                "critical": "stat-deleted",
                "high": "stat-modified",
                "medium": "",
                "low": "stat-added",
            }.get(stat.priority, "")

            rows += f"""<tr>
                <td>{layer}</td>
                <td class="{priority_class}">{stat.priority}</td>
                <td class="stat-added">{stat.added_count}</td>
                <td class="stat-deleted">{stat.deleted_count}</td>
                <td class="stat-modified">{stat.modified_count}</td>
                <td>{stat.layer_move_count}</td>
                <td><strong>{stat.total_changes}</strong></td>
            </tr>"""

        return f"""<section class="section">
            <h2 class="section-title">🗂️ 레이어별 통계</h2>
            <table>
                <thead>
                    <tr>
                        <th>레이어</th>
                        <th>우선순위</th>
                        <th>추가</th>
                        <th>삭제</th>
                        <th>수정</th>
                        <th>레이어 이동</th>
                        <th>합계</th>
                    </tr>
                </thead>
                <tbody>
                    {rows}
                </tbody>
            </table>
        </section>"""

    def _generate_changes_table_html(self, changes: List[DxfChange]) -> str:
        """변경 목록 테이블 HTML 생성"""
        if not changes:
            return "<p>변경 사항이 없습니다.</p>"

        rows = ""
        for i, change in enumerate(changes):
            badge_class = {
                DxfChangeType.ADDED: "badge-added",
                DxfChangeType.DELETED: "badge-deleted",
                DxfChangeType.MODIFIED: "badge-modified",
            }.get(change.change_type, "")

            location_str = ""
            if change.location:
                location_str = f"({change.location[0]:.1f}, {change.location[1]:.1f})"

            # 변경 상세
            detail_str = change.change_detail or "-"

            rows += f"""<tr>
                <td>{i + 1}</td>
                <td><span class="badge {badge_class}">{change.change_type.value}</span></td>
                <td>{change.entity_type}</td>
                <td>{change.layer}</td>
                <td>{location_str}</td>
                <td>{detail_str}</td>
            </tr>"""

        return f"""<table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>변경 타입</th>
                    <th>엔티티</th>
                    <th>레이어</th>
                    <th>위치</th>
                    <th>상세</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>"""


# 편의 함수
def create_visualization_from_comparison(
    old_dxf: Path,
    new_dxf: Path,
    comparison_result: DxfComparisonResult,
    output_image: Optional[Path] = None,
    output_html: Optional[Path] = None,
    dpi: int = 150,
) -> VisualizationResult:
    """비교 결과로부터 시각화 생성 (편의 함수)

    Args:
        old_dxf: 이전 DXF 경로
        new_dxf: 새 DXF 경로
        comparison_result: 비교 결과
        output_image: 이미지 출력 경로 (optional)
        output_html: HTML 출력 경로 (optional)
        dpi: 렌더링 해상도

    Returns:
        VisualizationResult
    """
    service = VisualizationService(dpi=dpi)

    # 오버레이 생성
    result = service.create_overlay(
        dxf_path=new_dxf,
        changes=comparison_result.changes,
    )

    # 이미지 저장
    if output_image and result.overlay_image is not None:
        service.save_overlay_image(result, output_image)

    # HTML 리포트 저장
    if output_html:
        service.export_html_report(
            old_path=old_dxf,
            new_path=new_dxf,
            comparison_result=comparison_result,
            visualization_result=result,
            output_path=output_html,
        )

    return result
