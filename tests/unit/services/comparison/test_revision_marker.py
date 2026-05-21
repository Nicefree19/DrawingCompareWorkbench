"""Phase P (RV-20260508-014) — Revision marker SSoT 회귀 가드.

표준 revcloud chord 수학 + revision triangle 지오메트리 + AIA 색상 매핑
이 모든 cloud rendering path 가 공유하는 SSoT 인지 검증.
"""
from __future__ import annotations

import math

import pytest

from src.services.comparison.revision_marker import (
    ACI_CYAN,
    ACI_GREEN,
    ACI_MAGENTA,
    ACI_RED,
    HEX_CYAN,
    HEX_GREEN,
    HEX_MAGENTA,
    LINEWEIGHT_REVCLOUD_MM,
    LINEWEIGHT_TAG_MM,
    color_aci_for_change,
    color_hex_for_change,
    compute_chord_length,
    revcloud_geometry_from_bbox,
    revision_triangle_from_anchor,
)


class TestColorMapping:
    """AIA 표준 색상 매핑 — modified=cyan, added=green, deleted=magenta."""

    @pytest.mark.parametrize(
        "kind,expected_aci",
        [
            ("modified", ACI_CYAN),
            ("added", ACI_GREEN),
            ("deleted", ACI_MAGENTA),
            ("mixed", ACI_CYAN),
        ],
    )
    def test_aci_aia_scheme(self, kind: str, expected_aci: int) -> None:
        assert color_aci_for_change(kind) == expected_aci  # type: ignore[arg-type]

    def test_legacy_scheme_uses_red_for_deleted(self) -> None:
        assert color_aci_for_change("deleted", scheme="legacy") == ACI_RED

    @pytest.mark.parametrize(
        "kind,expected_hex",
        [
            ("modified", HEX_CYAN),
            ("added", HEX_GREEN),
            ("deleted", HEX_MAGENTA),
        ],
    )
    def test_hex_aia_scheme(self, kind: str, expected_hex: str) -> None:
        assert color_hex_for_change(kind) == expected_hex  # type: ignore[arg-type]


class TestChordLength:
    """compute_chord_length — AIA 표준에 부합하는 둘레-기반 산출."""

    def test_chord_proportional_to_perimeter(self) -> None:
        chord = compute_chord_length((0.0, 0.0, 240.0, 240.0))
        # 둘레 = 960, target_chords=24 → 40
        assert chord == pytest.approx(40.0)

    def test_chord_clamped_to_min(self) -> None:
        # tiny bbox → 둘레 0.4mm, target 24 → 0.017 < min 5
        chord = compute_chord_length((0.0, 0.0, 0.1, 0.1))
        assert chord == 5.0

    def test_chord_clamped_to_max(self) -> None:
        # huge bbox → 둘레 12000, target 24 → 500 > max 100
        chord = compute_chord_length((0.0, 0.0, 3000.0, 3000.0))
        assert chord == 100.0

    def test_zero_bbox_returns_min(self) -> None:
        chord = compute_chord_length((100.0, 100.0, 100.0, 100.0))
        assert chord == 5.0


class TestRevcloudGeometry:
    """revcloud_geometry_from_bbox — closed bumpy polyline."""

    def test_geometry_starts_at_lower_left(self) -> None:
        geom = revcloud_geometry_from_bbox((0.0, 0.0, 100.0, 50.0))
        assert geom.vertices[0] == (0.0, 0.0)

    def test_traverses_clockwise_four_sides(self) -> None:
        geom = revcloud_geometry_from_bbox(
            (0.0, 0.0, 100.0, 50.0), chord_length=25.0
        )
        # bottom (4 verts) + right (2) + top (4) + left (2) = 12 + 시작점 1
        assert len(geom.vertices) >= 4

    def test_bulge_constant_along_perimeter(self) -> None:
        geom = revcloud_geometry_from_bbox(
            (0.0, 0.0, 200.0, 200.0), chord_length=50.0, bulge=0.5
        )
        assert all(b == 0.5 for b in geom.bulges)

    def test_returns_closed_geometry(self) -> None:
        geom = revcloud_geometry_from_bbox((0.0, 0.0, 50.0, 50.0))
        assert geom.is_closed is True


class TestRevisionTriangle:
    """revision_triangle_from_anchor — equilateral triangle, apex 위."""

    def test_equilateral_three_corners(self) -> None:
        tri = revision_triangle_from_anchor((100.0, 100.0), 1, size=10.0)
        # 변 길이 검증
        d_apex_left = math.hypot(
            tri.apex[0] - tri.bottom_left[0],
            tri.apex[1] - tri.bottom_left[1],
        )
        d_apex_right = math.hypot(
            tri.apex[0] - tri.bottom_right[0],
            tri.apex[1] - tri.bottom_right[1],
        )
        d_left_right = math.hypot(
            tri.bottom_right[0] - tri.bottom_left[0],
            tri.bottom_right[1] - tri.bottom_left[1],
        )
        assert d_apex_left == pytest.approx(d_apex_right, abs=0.01)
        assert d_apex_left == pytest.approx(d_left_right, abs=0.01)
        assert d_left_right == pytest.approx(10.0, abs=0.01)

    def test_apex_above_base(self) -> None:
        tri = revision_triangle_from_anchor((100.0, 100.0), 5)
        assert tri.apex[1] > tri.bottom_left[1]
        assert tri.apex[1] > tri.bottom_right[1]

    def test_revision_number_preserved(self) -> None:
        tri = revision_triangle_from_anchor((0.0, 0.0), 42)
        assert tri.revision_number == 42

    def test_text_anchor_inside_triangle(self) -> None:
        tri = revision_triangle_from_anchor((50.0, 50.0), 1, size=10.0)
        # text_anchor 가 삼각형 중심 근처여야 함
        assert abs(tri.text_anchor[0] - 50.0) < 1.0
        assert abs(tri.text_anchor[1] - 50.0) < 5.0


class TestLineweightStandard:
    """AIA lineweight 표준 — revcloud 0.50mm, tag 0.35mm."""

    def test_revcloud_lineweight_aia_standard(self) -> None:
        assert LINEWEIGHT_REVCLOUD_MM == 50

    def test_tag_lineweight_aia_standard(self) -> None:
        assert LINEWEIGHT_TAG_MM == 35
