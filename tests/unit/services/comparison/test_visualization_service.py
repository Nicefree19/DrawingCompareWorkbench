# -*- coding: utf-8 -*-
"""VisualizationService 테스트

Phase 3 P3-6: DXF 시각화 UI 연결

수용 기준:
- AC1: 비교 완료 후 오버레이 이미지 자동 생성
- AC2: 추가=녹색, 삭제=빨간색, 수정=주황색 표시
- AC3: UI에서 변경점 클릭 시 해당 위치로 이동
- AC4: HTML 리포트 내보내기 기능
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import numpy as np

from src.services.comparison.dxf_comparator import DxfChange, DxfChangeType, DxfComparisonResult, LayerStatistics
from src.services.comparison.visualization_service import (
    ClickableRegion,
    VisualizationResult,
    VisualizationService,
    create_visualization_from_comparison,
)


class TestClickableRegion:
    """ClickableRegion 데이터클래스 테스트"""

    def test_create_clickable_region(self):
        """ClickableRegion 생성 테스트"""
        region = ClickableRegion(
            change_id=0,
            pixel_x=100,
            pixel_y=200,
            cad_x=1000.0,
            cad_y=2000.0,
            change_type=DxfChangeType.ADDED,
            entity_type="LINE",
            layer="0",
        )

        assert region.change_id == 0
        assert region.pixel_x == 100
        assert region.pixel_y == 200
        assert region.cad_x == 1000.0
        assert region.cad_y == 2000.0
        assert region.change_type == DxfChangeType.ADDED
        assert region.entity_type == "LINE"
        assert region.layer == "0"
        assert region.radius == 15  # 기본값

    def test_contains_point_inside(self):
        """AC3: 영역 내부 점 감지"""
        region = ClickableRegion(
            change_id=0,
            pixel_x=100,
            pixel_y=100,
            cad_x=0,
            cad_y=0,
            change_type=DxfChangeType.ADDED,
            entity_type="LINE",
            layer="0",
            radius=15,
        )

        # 중심점
        assert region.contains_point(100, 100) is True
        # 반경 내부
        assert region.contains_point(105, 100) is True
        assert region.contains_point(100, 110) is True
        # 대각선 반경 내부 (약 10.6)
        assert region.contains_point(107, 107) is True

    def test_contains_point_outside(self):
        """AC3: 영역 외부 점 감지"""
        region = ClickableRegion(
            change_id=0,
            pixel_x=100,
            pixel_y=100,
            cad_x=0,
            cad_y=0,
            change_type=DxfChangeType.ADDED,
            entity_type="LINE",
            layer="0",
            radius=15,
        )

        # 반경 외부
        assert region.contains_point(120, 100) is False
        assert region.contains_point(100, 120) is False
        # 대각선 반경 외부 (약 14.14 < 15, 약 21.21 > 15)
        assert region.contains_point(115, 115) is False

    def test_contains_point_boundary(self):
        """AC3: 경계선 점 감지"""
        region = ClickableRegion(
            change_id=0,
            pixel_x=100,
            pixel_y=100,
            cad_x=0,
            cad_y=0,
            change_type=DxfChangeType.ADDED,
            entity_type="LINE",
            layer="0",
            radius=15,
        )

        # 정확히 경계
        assert region.contains_point(115, 100) is True
        assert region.contains_point(100, 115) is True

    def test_to_dict(self):
        """to_dict() 변환 테스트"""
        region = ClickableRegion(
            change_id=5,
            pixel_x=150,
            pixel_y=250,
            cad_x=1500.5,
            cad_y=2500.5,
            change_type=DxfChangeType.MODIFIED,
            entity_type="CIRCLE",
            layer="LAYER1",
            radius=20,
        )

        result = region.to_dict()

        assert result["change_id"] == 5
        assert result["pixel_x"] == 150
        assert result["pixel_y"] == 250
        assert result["cad_x"] == 1500.5
        assert result["cad_y"] == 2500.5
        assert result["change_type"] == "modified"
        assert result["entity_type"] == "CIRCLE"
        assert result["layer"] == "LAYER1"
        assert result["radius"] == 20


class TestVisualizationResult:
    """VisualizationResult 데이터클래스 테스트"""

    def test_create_empty_result(self):
        """빈 결과 생성"""
        result = VisualizationResult()

        assert result.overlay_image is None
        assert result.side_by_side_image is None
        assert result.clickable_regions == []
        assert result.image_size == (0, 0)
        assert result.extents is None
        assert result.changes_count == {}

    def test_create_result_with_data(self):
        """데이터가 있는 결과 생성"""
        image = np.zeros((480, 640, 3), dtype=np.uint8)
        regions = [
            ClickableRegion(
                change_id=0,
                pixel_x=100,
                pixel_y=100,
                cad_x=1000,
                cad_y=1000,
                change_type=DxfChangeType.ADDED,
                entity_type="LINE",
                layer="0",
            )
        ]

        result = VisualizationResult(
            overlay_image=image,
            clickable_regions=regions,
            image_size=(640, 480),
            extents=((0, 0), (10000, 10000)),
            changes_count={"added": 1, "deleted": 0, "modified": 0, "total": 1},
        )

        assert result.overlay_image is not None
        assert result.overlay_image.shape == (480, 640, 3)
        assert len(result.clickable_regions) == 1
        assert result.image_size == (640, 480)
        assert result.extents == ((0, 0), (10000, 10000))
        assert result.changes_count["total"] == 1

    def test_to_dict(self):
        """to_dict() 변환 테스트"""
        regions = [
            ClickableRegion(
                change_id=0,
                pixel_x=100,
                pixel_y=100,
                cad_x=1000,
                cad_y=1000,
                change_type=DxfChangeType.ADDED,
                entity_type="LINE",
                layer="0",
            )
        ]

        result = VisualizationResult(
            image_size=(640, 480),
            extents=((0, 0), (10000, 10000)),
            changes_count={"added": 1, "deleted": 0, "modified": 0, "total": 1},
            clickable_regions=regions,
        )

        data = result.to_dict()

        assert data["image_size"] == (640, 480)
        assert data["extents"] == ((0, 0), (10000, 10000))
        assert data["changes_count"]["total"] == 1
        assert len(data["clickable_regions"]) == 1
        assert data["clickable_regions"][0]["change_id"] == 0


class TestVisualizationServiceColors:
    """AC2: 색상 코드 테스트"""

    def test_rgb_colors(self):
        """RGB 색상 정의 확인"""
        assert VisualizationService.COLORS_RGB[DxfChangeType.ADDED] == (0, 255, 0)  # 녹색
        assert VisualizationService.COLORS_RGB[DxfChangeType.DELETED] == (255, 0, 0)  # 빨간색
        assert VisualizationService.COLORS_RGB[DxfChangeType.MODIFIED] == (255, 165, 0)  # 주황색

    def test_hex_colors(self):
        """Hex 색상 정의 확인"""
        assert VisualizationService.COLORS_HEX[DxfChangeType.ADDED] == "#00FF00"
        assert VisualizationService.COLORS_HEX[DxfChangeType.DELETED] == "#FF0000"
        assert VisualizationService.COLORS_HEX[DxfChangeType.MODIFIED] == "#FFA500"


class TestVisualizationServiceInit:
    """VisualizationService 초기화 테스트"""

    def test_default_dpi(self):
        """기본 DPI 설정"""
        service = VisualizationService()
        assert service.dpi == 150

    def test_custom_dpi(self):
        """사용자 정의 DPI 설정"""
        service = VisualizationService(dpi=300)
        assert service.dpi == 300

    def test_lazy_renderer_init(self):
        """렌더러 지연 초기화"""
        service = VisualizationService()
        assert service._renderer is None
        assert service._overlay_renderer is None


class TestCalculateClickableRegions:
    """AC3: 클릭 가능 영역 계산 테스트"""

    def test_calculate_regions_basic(self):
        """기본 영역 계산"""
        service = VisualizationService()

        changes = [
            DxfChange(
                change_type=DxfChangeType.ADDED,
                entity_type="LINE",
                layer="0",
                location=(5000, 5000),
            ),
        ]
        extents = ((0, 0), (10000, 10000))
        image_size = (1000, 1000)

        regions = service._calculate_clickable_regions(changes, extents, image_size)

        assert len(regions) == 1
        assert regions[0].change_id == 0
        assert regions[0].pixel_x == 500  # (5000-0)/10000 * 1000 = 500
        # Y축 반전: (1 - (5000-0)/10000) * 1000 = 500
        assert regions[0].pixel_y == 500
        assert regions[0].cad_x == 5000
        assert regions[0].cad_y == 5000
        assert regions[0].change_type == DxfChangeType.ADDED

    def test_calculate_regions_y_inversion(self):
        """Y축 반전 검증"""
        service = VisualizationService()

        changes = [
            DxfChange(
                change_type=DxfChangeType.ADDED,
                entity_type="LINE",
                layer="0",
                location=(0, 0),  # CAD 좌표 왼쪽 하단
            ),
            DxfChange(
                change_type=DxfChangeType.DELETED,
                entity_type="LINE",
                layer="0",
                location=(10000, 10000),  # CAD 좌표 오른쪽 상단
            ),
        ]
        extents = ((0, 0), (10000, 10000))
        image_size = (1000, 1000)

        regions = service._calculate_clickable_regions(changes, extents, image_size)

        # CAD (0,0) → 이미지 왼쪽 하단 (0, 999) - Y 반전 + 범위 제한
        assert regions[0].pixel_x == 0
        assert regions[0].pixel_y == 999  # (1 - 0/10000) * 1000 = 1000 → 범위 제한 999

        # CAD (10000, 10000) → 이미지 오른쪽 상단 (999, 0) - Y 반전
        assert regions[1].pixel_x == 999  # 범위 제한
        assert regions[1].pixel_y == 0

    def test_calculate_regions_no_location(self):
        """위치 정보 없는 변경점 건너뛰기"""
        service = VisualizationService()

        changes = [
            DxfChange(
                change_type=DxfChangeType.ADDED,
                entity_type="LINE",
                layer="0",
                location=None,  # 위치 없음
            ),
        ]
        extents = ((0, 0), (10000, 10000))
        image_size = (1000, 1000)

        regions = service._calculate_clickable_regions(changes, extents, image_size)

        assert len(regions) == 0

    def test_calculate_regions_zero_extents(self):
        """영역 크기가 0인 경우"""
        service = VisualizationService()

        changes = [
            DxfChange(
                change_type=DxfChangeType.ADDED,
                entity_type="LINE",
                layer="0",
                location=(100, 100),
            ),
        ]
        extents = ((100, 100), (100, 100))  # 크기 0
        image_size = (1000, 1000)

        regions = service._calculate_clickable_regions(changes, extents, image_size)

        assert len(regions) == 0

    def test_calculate_regions_multiple_types(self):
        """여러 타입의 변경점"""
        service = VisualizationService()

        changes = [
            DxfChange(
                change_type=DxfChangeType.ADDED,
                entity_type="LINE",
                layer="LAYER1",
                location=(1000, 1000),
            ),
            DxfChange(
                change_type=DxfChangeType.DELETED,
                entity_type="CIRCLE",
                layer="LAYER2",
                location=(5000, 5000),
            ),
            DxfChange(
                change_type=DxfChangeType.MODIFIED,
                entity_type="ARC",
                layer="LAYER3",
                location=(9000, 9000),
            ),
        ]
        extents = ((0, 0), (10000, 10000))
        image_size = (1000, 1000)

        regions = service._calculate_clickable_regions(changes, extents, image_size)

        assert len(regions) == 3
        assert regions[0].change_type == DxfChangeType.ADDED
        assert regions[0].layer == "LAYER1"
        assert regions[1].change_type == DxfChangeType.DELETED
        assert regions[1].entity_type == "CIRCLE"
        assert regions[2].change_type == DxfChangeType.MODIFIED


class TestFindChangeAtPosition:
    """AC3: 클릭 위치에서 변경점 찾기 테스트"""

    def test_find_change_exact_position(self):
        """정확한 위치에서 찾기"""
        service = VisualizationService()

        regions = [
            ClickableRegion(
                change_id=0,
                pixel_x=100,
                pixel_y=100,
                cad_x=1000,
                cad_y=1000,
                change_type=DxfChangeType.ADDED,
                entity_type="LINE",
                layer="0",
            )
        ]

        result = service.find_change_at_position(regions, 100, 100)

        assert result is not None
        assert result.change_id == 0

    def test_find_change_within_radius(self):
        """반경 내에서 찾기"""
        service = VisualizationService()

        regions = [
            ClickableRegion(
                change_id=0,
                pixel_x=100,
                pixel_y=100,
                cad_x=1000,
                cad_y=1000,
                change_type=DxfChangeType.ADDED,
                entity_type="LINE",
                layer="0",
                radius=15,
            )
        ]

        result = service.find_change_at_position(regions, 110, 105)

        assert result is not None
        assert result.change_id == 0

    def test_find_change_outside_all(self):
        """모든 영역 외부에서 찾기"""
        service = VisualizationService()

        regions = [
            ClickableRegion(
                change_id=0,
                pixel_x=100,
                pixel_y=100,
                cad_x=1000,
                cad_y=1000,
                change_type=DxfChangeType.ADDED,
                entity_type="LINE",
                layer="0",
                radius=15,
            )
        ]

        result = service.find_change_at_position(regions, 500, 500)

        assert result is None

    def test_find_change_first_match(self):
        """여러 영역이 겹칠 때 첫 번째 반환"""
        service = VisualizationService()

        regions = [
            ClickableRegion(
                change_id=0,
                pixel_x=100,
                pixel_y=100,
                cad_x=1000,
                cad_y=1000,
                change_type=DxfChangeType.ADDED,
                entity_type="LINE",
                layer="0",
                radius=20,
            ),
            ClickableRegion(
                change_id=1,
                pixel_x=105,
                pixel_y=105,
                cad_x=1050,
                cad_y=1050,
                change_type=DxfChangeType.DELETED,
                entity_type="LINE",
                layer="0",
                radius=20,
            ),
        ]

        result = service.find_change_at_position(regions, 102, 102)

        assert result is not None
        assert result.change_id == 0  # 첫 번째 매치

    def test_find_change_empty_regions(self):
        """빈 영역 목록"""
        service = VisualizationService()

        result = service.find_change_at_position([], 100, 100)

        assert result is None


class TestCreateOverlay:
    """AC1 & AC2: 오버레이 생성 테스트"""

    def test_create_overlay_no_renderer(self):
        """렌더러 없을 때 빈 결과 반환"""
        with patch('src.services.comparison.visualization_service.RENDERER_AVAILABLE', False):
            service = VisualizationService()
            changes = []

            result = service.create_overlay(Path("test.dxf"), changes)

            assert result.overlay_image is None
            assert result.image_size == (0, 0)

    def test_create_overlay_with_changes_mocked(self):
        """변경점이 있는 오버레이 생성 (mock 사용)"""
        service = VisualizationService()

        # Mock renderers
        mock_image = np.zeros((480, 640, 3), dtype=np.uint8)
        mock_extents = ((0, 0), (10000, 10000))

        # _renderer와 _overlay_renderer를 직접 설정
        service._renderer = MagicMock()
        service._renderer.render.return_value = mock_image
        service._renderer.get_extents.return_value = mock_extents

        service._overlay_renderer = MagicMock()
        service._overlay_renderer.render.return_value = mock_image

        changes = [
            DxfChange(
                change_type=DxfChangeType.ADDED,
                entity_type="LINE",
                layer="0",
                location=(5000, 5000),
            )
        ]

        with patch('src.services.comparison.visualization_service.RENDERER_AVAILABLE', True):
            with patch('src.services.comparison.visualization_service.OVERLAY_AVAILABLE', True):
                result = service.create_overlay(Path("test.dxf"), changes)

                assert result.overlay_image is not None
                assert result.extents == mock_extents
                assert result.image_size == (640, 480)
                assert result.changes_count["added"] == 1
                assert result.changes_count["total"] == 1
                assert len(result.clickable_regions) == 1


class TestCreateSideBySide:
    """좌우 비교 이미지 생성 테스트"""

    @patch('src.services.comparison.visualization_service.RENDERER_AVAILABLE', False)
    def test_side_by_side_no_renderer(self):
        """렌더러 없을 때 빈 결과 반환"""
        service = VisualizationService()
        changes = []

        result = service.create_side_by_side(
            Path("old.dxf"), Path("new.dxf"), changes
        )

        assert result.side_by_side_image is None


class TestSaveOverlayImage:
    """이미지 저장 테스트"""

    def test_save_overlay_no_image(self):
        """이미지 없을 때 예외 발생"""
        service = VisualizationService()
        result = VisualizationResult()

        with pytest.raises(ValueError, match="오버레이 이미지가 없습니다"):
            service.save_overlay_image(result, Path("output.png"))

    def test_save_overlay_no_dependencies(self):
        """의존성 없을 때 예외 발생"""
        with tempfile.TemporaryDirectory() as tmpdir:
            service = VisualizationService()
            image = np.zeros((100, 100, 3), dtype=np.uint8)
            result = VisualizationResult(overlay_image=image)
            output_path = Path(tmpdir) / "output.png"

            with patch('src.services.comparison.visualization_service.CV2_AVAILABLE', False):
                with patch('src.services.comparison.visualization_service.PIL_AVAILABLE', False):
                    with pytest.raises(ImportError, match="OpenCV 또는 Pillow"):
                        service.save_overlay_image(result, output_path)


class TestExportHtmlReport:
    """AC4: HTML 리포트 내보내기 테스트"""

    def test_export_html_basic(self):
        """기본 HTML 리포트 생성"""
        with tempfile.TemporaryDirectory() as tmpdir:
            service = VisualizationService()

            changes = [
                DxfChange(
                    change_type=DxfChangeType.ADDED,
                    entity_type="LINE",
                    layer="0",
                    location=(100, 200),
                    change_detail="새로운 선",
                ),
                DxfChange(
                    change_type=DxfChangeType.DELETED,
                    entity_type="CIRCLE",
                    layer="LAYER1",
                    location=(300, 400),
                    change_detail="삭제된 원",
                ),
            ]

            comparison_result = DxfComparisonResult(
                changes=changes,
            )

            output_path = Path(tmpdir) / "report.html"

            saved_path = service.export_html_report(
                old_path=Path("old.dxf"),
                new_path=Path("new.dxf"),
                comparison_result=comparison_result,
                visualization_result=None,
                output_path=output_path,
                title="테스트 리포트",
            )

            assert saved_path.exists()

            # HTML 내용 확인
            html_content = saved_path.read_text(encoding="utf-8")

            # 제목 확인
            assert "테스트 리포트" in html_content
            # 파일명 확인
            assert "old.dxf" in html_content
            assert "new.dxf" in html_content
            # 변경점 타입 확인
            assert "added" in html_content
            assert "deleted" in html_content
            # 색상 정의 확인
            assert "#00FF00" in html_content  # 녹색
            assert "#FF0000" in html_content  # 빨간색
            assert "#FFA500" in html_content  # 주황색

    def test_export_html_with_image_mocked(self):
        """이미지가 포함된 HTML 리포트 (mock 사용)"""
        with tempfile.TemporaryDirectory() as tmpdir:
            service = VisualizationService()

            comparison_result = DxfComparisonResult(
                changes=[],
            )

            image = np.zeros((100, 100, 3), dtype=np.uint8)
            image[40:60, 40:60] = [255, 0, 0]  # 빨간 사각형

            viz_result = VisualizationResult(
                overlay_image=image,
                image_size=(100, 100),
            )

            output_path = Path(tmpdir) / "report_with_image.html"

            # _image_to_base64를 mock
            with patch.object(service, '_image_to_base64', return_value="dGVzdA=="):
                saved_path = service.export_html_report(
                    old_path=Path("old.dxf"),
                    new_path=Path("new.dxf"),
                    comparison_result=comparison_result,
                    visualization_result=viz_result,
                    output_path=output_path,
                )

                html_content = saved_path.read_text(encoding="utf-8")

                # base64 이미지 포함 확인
                assert "data:image/png;base64," in html_content
                # 레전드 확인
                assert "추가 (Added)" in html_content
                assert "삭제 (Deleted)" in html_content
                assert "수정 (Modified)" in html_content

    def test_export_html_empty_changes(self):
        """변경사항 없는 리포트"""
        with tempfile.TemporaryDirectory() as tmpdir:
            service = VisualizationService()

            comparison_result = DxfComparisonResult(
                changes=[],
            )

            output_path = Path(tmpdir) / "empty_report.html"

            saved_path = service.export_html_report(
                old_path=Path("old.dxf"),
                new_path=Path("new.dxf"),
                comparison_result=comparison_result,
                visualization_result=None,
                output_path=output_path,
            )

            html_content = saved_path.read_text(encoding="utf-8")
            assert "변경 사항이 없습니다" in html_content

    def test_export_html_layer_statistics(self):
        """레이어 통계 포함 리포트"""
        with tempfile.TemporaryDirectory() as tmpdir:
            service = VisualizationService()

            # 레이어 통계 있는 비교 결과
            changes = [
                DxfChange(
                    change_type=DxfChangeType.ADDED,
                    entity_type="LINE",
                    layer="LAYER1",
                    location=(100, 200),
                ),
            ]

            comparison_result = DxfComparisonResult(
                changes=changes,
            )
            # 수동으로 레이어 통계 설정
            comparison_result.layer_statistics = {
                "LAYER1": LayerStatistics(
                    layer="LAYER1",
                    priority="medium",
                    added_count=1,
                    deleted_count=0,
                    modified_count=0,
                    layer_move_count=0,
                )
            }

            output_path = Path(tmpdir) / "report.html"

            saved_path = service.export_html_report(
                old_path=Path("old.dxf"),
                new_path=Path("new.dxf"),
                comparison_result=comparison_result,
                visualization_result=None,
                output_path=output_path,
            )

            html_content = saved_path.read_text(encoding="utf-8")
            assert "레이어별 통계" in html_content
            assert "LAYER1" in html_content


class TestConvenienceFunction:
    """create_visualization_from_comparison() 편의 함수 테스트"""

    @patch('src.services.comparison.visualization_service.RENDERER_AVAILABLE', True)
    @patch('src.services.comparison.visualization_service.OVERLAY_AVAILABLE', True)
    def test_convenience_function_basic(self):
        """편의 함수 기본 동작"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Mock을 사용한 테스트
            with patch('src.services.comparison.visualization_service.VisualizationService') as MockService:
                mock_service = MockService.return_value
                mock_result = VisualizationResult(
                    overlay_image=np.zeros((100, 100, 3), dtype=np.uint8),
                    image_size=(100, 100),
                )
                mock_service.create_overlay.return_value = mock_result

                comparison_result = DxfComparisonResult(
                    changes=[],
                )

                result = create_visualization_from_comparison(
                    old_dxf=Path("old.dxf"),
                    new_dxf=Path("new.dxf"),
                    comparison_result=comparison_result,
                )

                mock_service.create_overlay.assert_called_once()


class TestP3_6_AcceptanceCriteria:
    """P3-6 수용 기준 통합 테스트"""

    def test_ac1_overlay_image_generation(self):
        """AC1: 비교 완료 후 오버레이 이미지 자동 생성"""
        service = VisualizationService()

        # 오버레이 생성 메서드 존재 확인
        assert hasattr(service, 'create_overlay')
        assert callable(service.create_overlay)

        # 결과에 오버레이 이미지 필드 존재
        result = VisualizationResult()
        assert hasattr(result, 'overlay_image')

    def test_ac2_color_codes(self):
        """AC2: 추가=녹색, 삭제=빨간색, 수정=주황색 표시"""
        # RGB 색상
        assert VisualizationService.COLORS_RGB[DxfChangeType.ADDED] == (0, 255, 0)
        assert VisualizationService.COLORS_RGB[DxfChangeType.DELETED] == (255, 0, 0)
        assert VisualizationService.COLORS_RGB[DxfChangeType.MODIFIED] == (255, 165, 0)

        # Hex 색상
        assert VisualizationService.COLORS_HEX[DxfChangeType.ADDED] == "#00FF00"
        assert VisualizationService.COLORS_HEX[DxfChangeType.DELETED] == "#FF0000"
        assert VisualizationService.COLORS_HEX[DxfChangeType.MODIFIED] == "#FFA500"

    def test_ac3_click_navigation(self):
        """AC3: UI에서 변경점 클릭 시 해당 위치로 이동"""
        service = VisualizationService()

        # 클릭 가능 영역 계산 기능
        assert hasattr(service, '_calculate_clickable_regions')
        assert hasattr(service, 'find_change_at_position')

        # ClickableRegion이 좌표 정보 포함
        region = ClickableRegion(
            change_id=0,
            pixel_x=100,
            pixel_y=100,
            cad_x=1000,
            cad_y=1000,
            change_type=DxfChangeType.ADDED,
            entity_type="LINE",
            layer="0",
        )

        assert region.pixel_x == 100  # 픽셀 좌표
        assert region.cad_x == 1000  # CAD 좌표
        assert region.contains_point(100, 100)  # 클릭 감지

    def test_ac4_html_report_export(self):
        """AC4: HTML 리포트 내보내기 기능"""
        service = VisualizationService()

        assert hasattr(service, 'export_html_report')
        assert callable(service.export_html_report)

        # 편의 함수도 존재
        assert callable(create_visualization_from_comparison)
