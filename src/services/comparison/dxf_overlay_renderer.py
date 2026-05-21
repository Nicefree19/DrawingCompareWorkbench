"""DXF 변경점 오버레이 렌더러

Sprint 9 Phase 3.2: DxfOverlayRenderer
DXF 이미지에 변경점 마커를 오버레이합니다.

기능:
    - 변경점 위치에 색상 마커 표시
    - CAD 좌표 → 픽셀 좌표 변환
    - 범례 추가
"""

import logging
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# OpenCV 임포트
try:
    import cv2

    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    logger.warning("OpenCV가 설치되지 않았습니다")

from .dxf_comparator import DxfChange, DxfChangeType

# ColorConfig 순환 임포트 방지 (TYPE_CHECKING)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .visualization_service import ColorConfig


class DxfOverlayRenderer:
    """DXF 변경점 오버레이 렌더러

    DXF 이미지에 변경점 마커를 오버레이합니다.

    사용 예시:
        overlay = DxfOverlayRenderer()
        result = overlay.render(base_img, changes, extents)
    """

    # 변경 타입별 색상 (BGR)
    COLORS = {
        DxfChangeType.ADDED: (0, 255, 0),  # 녹색
        DxfChangeType.DELETED: (0, 0, 255),  # 빨강
        DxfChangeType.MODIFIED: (0, 165, 255),  # 주황
    }

    # 마커 설정
    MARKER_RADIUS = 12
    MARKER_THICKNESS = 2

    def __init__(self):
        if not CV2_AVAILABLE:
            raise ImportError("OpenCV가 필요합니다: pip install opencv-python")

    def render(
        self,
        base_image: np.ndarray,
        changes: List[DxfChange],
        extents: Tuple[Tuple[float, float], Tuple[float, float]],
        color_config: Optional["ColorConfig"] = None,
    ) -> np.ndarray:
        """변경점 오버레이 렌더링

        Args:
            base_image: 기본 이미지 (RGB numpy 배열)
            changes: 변경점 목록
            extents: CAD 좌표 범위 ((min_x, min_y), (max_x, max_y))
            color_config: 색상 토글 설정 (QW-2, None이면 기본 색상 사용)

        Returns:
            오버레이된 이미지 (RGB numpy 배열)
        """
        if len(changes) == 0:
            logger.info("변경점 없음 - 원본 이미지 반환")
            return base_image

        # 이미지 복사
        result = base_image.copy()

        # 이미지 크기
        img_height, img_width = result.shape[:2]

        # CAD 좌표 → 픽셀 좌표 변환 함수
        (min_x, min_y), (max_x, max_y) = extents
        cad_width = max_x - min_x
        cad_height = max_y - min_y

        def cad_to_pixel(x: float, y: float) -> Tuple[int, int]:
            """CAD 좌표 → 픽셀 좌표"""
            if cad_width == 0 or cad_height == 0:
                return (img_width // 2, img_height // 2)

            px = int((x - min_x) / cad_width * img_width)
            # Y축 반전 (CAD는 Y가 위로, 이미지는 아래로)
            py = int((1 - (y - min_y) / cad_height) * img_height)

            return (max(0, min(px, img_width - 1)), max(0, min(py, img_height - 1)))

        def rgb_to_bgr(rgb: Tuple[int, int, int]) -> Tuple[int, int, int]:
            """RGB → BGR 변환 (OpenCV 형식)"""
            return (rgb[2], rgb[1], rgb[0])

        # RGB → BGR (OpenCV 형식)
        result_bgr = cv2.cvtColor(result, cv2.COLOR_RGB2BGR)

        # 변경점 마커 그리기
        added_count = 0
        deleted_count = 0

        for change in changes:
            if change.location is None:
                continue

            x, y = change.location
            px, py = cad_to_pixel(x, y)

            # ColorConfig가 있으면 사용, 없으면 기본 색상
            if color_config is not None:
                rgb_color = color_config.get_color_for_type(change.change_type)
                color = rgb_to_bgr(rgb_color)
            else:
                color = self.COLORS.get(change.change_type, (128, 128, 128))

            # 원형 마커
            cv2.circle(result_bgr, (px, py), self.MARKER_RADIUS, color, self.MARKER_THICKNESS)

            # 내부 점
            cv2.circle(result_bgr, (px, py), 3, color, -1)

            if change.change_type == DxfChangeType.ADDED:
                added_count += 1
            elif change.change_type == DxfChangeType.DELETED:
                deleted_count += 1

        # 범례 추가
        self._draw_legend(result_bgr, added_count, deleted_count)

        # BGR → RGB
        result = cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB)

        logger.info(f"오버레이 완료: 추가 {added_count}, 삭제 {deleted_count}")

        return result

    def _draw_legend(self, img: np.ndarray, added_count: int, deleted_count: int):
        """범례 그리기"""
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.6
        thickness = 2

        # 배경 박스
        x, y = 10, 10
        box_width = 180
        box_height = 70

        # 반투명 배경
        overlay = img.copy()
        cv2.rectangle(overlay, (x, y), (x + box_width, y + box_height), (255, 255, 255), -1)
        cv2.addWeighted(overlay, 0.7, img, 0.3, 0, img)

        # 테두리
        cv2.rectangle(img, (x, y), (x + box_width, y + box_height), (100, 100, 100), 1)

        # 추가 항목
        cv2.circle(img, (x + 15, y + 25), 8, self.COLORS[DxfChangeType.ADDED], -1)
        cv2.putText(
            img, f"Added: {added_count}", (x + 30, y + 30), font, font_scale, (0, 0, 0), thickness
        )

        # 삭제 항목
        cv2.circle(img, (x + 15, y + 50), 8, self.COLORS[DxfChangeType.DELETED], -1)
        cv2.putText(
            img,
            f"Deleted: {deleted_count}",
            (x + 30, y + 55),
            font,
            font_scale,
            (0, 0, 0),
            thickness,
        )

    def render_side_by_side(
        self,
        img_a: np.ndarray,
        img_b: np.ndarray,
        changes: List[DxfChange],
        extents: Tuple[Tuple[float, float], Tuple[float, float]],
    ) -> np.ndarray:
        """좌우 비교 이미지 생성

        Args:
            img_a: 기준(Old) 이미지
            img_b: 대상(New) 이미지
            changes: 변경점 목록
            extents: CAD 좌표 범위

        Returns:
            좌우 병합된 이미지
        """
        # 크기 맞추기
        h = max(img_a.shape[0], img_b.shape[0])
        w = max(img_a.shape[1], img_b.shape[1])

        img_a_resized = cv2.resize(img_a, (w, h))
        img_b_resized = cv2.resize(img_b, (w, h))

        # 변경점 오버레이
        img_a_overlay = self.render(
            img_a_resized, [c for c in changes if c.change_type == DxfChangeType.DELETED], extents
        )
        img_b_overlay = self.render(
            img_b_resized, [c for c in changes if c.change_type == DxfChangeType.ADDED], extents
        )

        # 좌우 병합
        result = np.hstack([img_a_overlay, img_b_overlay])

        return result
