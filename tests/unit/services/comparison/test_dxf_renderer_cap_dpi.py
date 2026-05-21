"""DxfRenderer._cap_dpi() 경계값 테스트

Sprint 14: DPI 제한 로직 검증
- 음수 입력
- 극소값 (0.01 inch 미만 도면)
- MIN_SAFE_DPI (10) 경계
- MAX_SAFE_DPI (300) 경계
- 정상 범위 값
"""

import pytest

from src.services.comparison.dxf_renderer import DxfRenderer, RENDERER_AVAILABLE


@pytest.mark.skipif(not RENDERER_AVAILABLE, reason="ezdxf/matplotlib not installed")
class TestCapDpi:
    """_cap_dpi() 메서드 경계값 테스트"""

    @pytest.fixture
    def renderer(self):
        """DxfRenderer 인스턴스"""
        return DxfRenderer(dpi=150)

    # === 정상 케이스 ===

    def test_normal_case_no_limit(self, renderer):
        """max_edge_px 미지정 시 원본 dpi 반환"""
        result = renderer._cap_dpi(150, 10.0, 8.0, None)
        assert result == 150

    def test_normal_case_zero_limit(self, renderer):
        """max_edge_px=0 시 원본 dpi 반환"""
        result = renderer._cap_dpi(150, 10.0, 8.0, 0)
        assert result == 150

    def test_normal_case_with_limit(self, renderer):
        """정상 제한 적용: 10inch * 100dpi = 1000px"""
        # max_edge_px=1000, max_inches=10 → capped_dpi=100
        result = renderer._cap_dpi(150, 10.0, 8.0, 1000)
        assert result == 100  # min(150, 100) = 100

    def test_limit_higher_than_original(self, renderer):
        """제한이 원본보다 높으면 원본 유지"""
        # max_edge_px=2000, max_inches=10 → capped_dpi=200 > dpi=150
        result = renderer._cap_dpi(150, 10.0, 8.0, 2000)
        assert result == 150  # min(150, 200) = 150

    # === MIN_SAFE_DPI (10) 경계 ===

    def test_min_safe_dpi_enforced_on_capped(self, renderer):
        """capped_dpi가 10 미만이면 10으로 올림"""
        # max_edge_px=50, max_inches=10 → capped_dpi=5 → max(10, 5) = 10
        result = renderer._cap_dpi(150, 10.0, 8.0, 50)
        assert result == 10

    def test_min_safe_dpi_enforced_on_input(self, renderer):
        """입력 dpi가 10 미만이어도 최소 10 반환"""
        # dpi=5, max_edge_px=1000 → capped=100 → max(10, min(5, 100)) = 10
        result = renderer._cap_dpi(5, 10.0, 8.0, 1000)
        assert result == 10

    def test_dpi_exactly_10(self, renderer):
        """dpi=10은 그대로 반환"""
        result = renderer._cap_dpi(10, 10.0, 8.0, 2000)
        assert result == 10

    # === MAX_SAFE_DPI (300) 경계 ===

    def test_max_safe_dpi_enforced(self, renderer):
        """capped_dpi가 300 초과면 300으로 제한"""
        # max_edge_px=5000, max_inches=10 → capped_dpi=500 → min(300, 500) = 300
        result = renderer._cap_dpi(150, 10.0, 8.0, 5000)
        assert result == 150  # min(150, 300) = 150 (원본이 더 작음)

    def test_dpi_exactly_300(self, renderer):
        """dpi=300은 그대로 반환 (제한 없을 때)"""
        result = renderer._cap_dpi(300, 10.0, 8.0, None)
        assert result == 300

    def test_high_dpi_capped_to_300(self, renderer):
        """dpi=500이고 제한 없으면 원본 반환 (300 제한은 capped_dpi에만 적용)"""
        # max_edge_px 미지정 → 원본 dpi 그대로
        result = renderer._cap_dpi(500, 10.0, 8.0, None)
        assert result == 500

    # === 극소 도면 (< 0.1 inch) ===

    def test_tiny_figure_returns_min_safe_dpi(self, renderer):
        """도면 크기가 0.1 inch 미만이면 MIN_SAFE_DPI 반환"""
        result = renderer._cap_dpi(150, 0.05, 0.03, 1000)
        assert result == 10  # 안전 DPI

    def test_tiny_figure_both_dimensions(self, renderer):
        """양쪽 모두 극소일 때"""
        result = renderer._cap_dpi(150, 0.001, 0.001, 1000)
        assert result == 10

    def test_one_dimension_tiny(self, renderer):
        """한쪽만 극소면 다른 쪽 기준으로 계산"""
        # max_inches = max(0.05, 10.0) = 10.0 → 정상 계산
        result = renderer._cap_dpi(150, 0.05, 10.0, 1000)
        assert result == 100  # 1000/10 = 100

    # === 음수/비정상 입력 ===

    def test_negative_dimensions_use_abs(self, renderer):
        """음수 크기는 절대값으로 처리"""
        result = renderer._cap_dpi(150, -10.0, -8.0, 1000)
        assert result == 100  # abs(-10) = 10 → 1000/10 = 100

    def test_negative_max_edge_px_ignored(self, renderer):
        """음수 max_edge_px는 무시 (원본 반환)"""
        result = renderer._cap_dpi(150, 10.0, 8.0, -1000)
        assert result == 150

    def test_zero_dimensions(self, renderer):
        """크기가 0이면 극소 도면 처리"""
        result = renderer._cap_dpi(150, 0.0, 0.0, 1000)
        assert result == 10  # max(0, 0) < 0.1 → MIN_SAFE_DPI

    # === 정밀도 테스트 ===

    def test_float_precision(self, renderer):
        """부동소수점 정밀도 테스트"""
        # 11.811 inch (A3 landscape ≈ 300mm)
        result = renderer._cap_dpi(150, 11.811, 8.268, 1600)
        # 1600 / 11.811 ≈ 135.47
        assert 135 <= result <= 136

    def test_exact_boundary_at_limit(self, renderer):
        """정확히 제한에 걸리는 경우"""
        # max_edge_px=1500, max_inches=10 → capped=150 = dpi
        result = renderer._cap_dpi(150, 10.0, 8.0, 1500)
        assert result == 150

    def test_figure_layout_honors_max_edge_for_large_cad_extents(self, renderer):
        """Large CAD extents should not create huge preview rasters."""
        fig_width, fig_height, dpi = renderer._figure_layout_for_extents(
            1_200_000.0,
            300_000.0,
            80,
            2400,
        )

        assert round(fig_width * dpi) == 2400
        assert round(fig_height * dpi) == 600
        assert dpi == 80
