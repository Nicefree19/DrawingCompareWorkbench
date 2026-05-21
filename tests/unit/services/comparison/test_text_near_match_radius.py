"""Phase P (RV-20260508-013) — TEXT/DIMENSION near-match radius 확장 회귀 가드.

Phase O 까지는 ``find_near_matches`` 의 tolerance (default 1.0mm 또는
alignment 확장값) 가 모든 entity 에 동일 적용. TEXT 의 좌표 5-30mm 시프트
+ 내용 변경 시 radius 안 잡혀 added+deleted 분리. 사용자 보고: "치수가
위치 + 값 같이 바뀌었는데 두 개로 표시됨".

Phase P fix: ``_distance_threshold_for(entity_type)`` 가 TEXT/MTEXT/
DIMENSION/MULTILEADER 한정으로 ``text_near_match_radius`` (default 50mm)
까지 매칭 허용.
"""
from __future__ import annotations

from typing import Any, Optional, Tuple

import pytest

from src.services.comparison.dxf_comparator import DxfComparator


class TestDistanceThresholdHelper:
    """``_distance_threshold_for`` 의 entity_type 별 분기 검증."""

    @pytest.mark.parametrize(
        "entity_type", ["TEXT", "MTEXT", "DIMENSION", "MULTILEADER"],
    )
    def test_text_like_returns_extended_radius(self, entity_type: str) -> None:
        cmp = DxfComparator()
        cmp.tolerance = 1.0
        cmp.sensitivity["text_near_match_radius"] = 50.0
        assert cmp._distance_threshold_for(entity_type) == 50.0

    @pytest.mark.parametrize(
        "entity_type", ["LINE", "CIRCLE", "ARC", "POLYLINE", "INSERT", "ATTRIB"],
    )
    def test_non_text_returns_default_tolerance(self, entity_type: str) -> None:
        cmp = DxfComparator()
        cmp.tolerance = 1.0
        cmp.sensitivity["text_near_match_radius"] = 50.0
        assert cmp._distance_threshold_for(entity_type) == 1.0

    def test_text_radius_at_least_default_tolerance(self) -> None:
        """text_near_match_radius < tolerance 인 경우 tolerance 가 우선 (max)."""
        cmp = DxfComparator()
        cmp.tolerance = 100.0  # alignment 가 크게 확장한 시나리오
        cmp.sensitivity["text_near_match_radius"] = 50.0
        assert cmp._distance_threshold_for("TEXT") == 100.0

    def test_zero_radius_falls_back_to_tolerance(self) -> None:
        cmp = DxfComparator()
        cmp.tolerance = 1.0
        cmp.sensitivity["text_near_match_radius"] = 0.0
        # 0 vs 1.0 → 1.0 (max). max(1.0, 0.0) = 1.0
        assert cmp._distance_threshold_for("TEXT") == 1.0


class TestNearMatchExtendedRadiusLinear:
    """``_find_near_matches_linear`` 가 TEXT 30mm shift 를 매칭하는지."""

    @staticmethod
    def _make_change(
        entity_type: str,
        location: Tuple[float, float],
        change_type: Any,
        layer: str = "DIM",
    ):
        from src.services.comparison.dxf_comparator import (
            DxfChange,
            DxfChangeType,
        )
        return DxfChange(
            change_type=change_type,
            entity_type=entity_type,
            location=location,
            layer=layer,
        )

    def test_text_30mm_shift_matched_with_linear_backend(self) -> None:
        from src.services.comparison.dxf_comparator import DxfChangeType

        cmp = DxfComparator()
        cmp.tolerance = 1.0
        cmp.sensitivity["text_near_match_radius"] = 50.0
        cmp._use_near_match_index = False  # linear 강제
        cmp.near_match_index = "linear"

        deleted = [
            self._make_change("TEXT", (100.0, 100.0), DxfChangeType.DELETED)
        ]
        added = [
            self._make_change("TEXT", (130.0, 100.0), DxfChangeType.ADDED)
        ]
        # Phase O: 30mm > 1mm tolerance → no match → added+deleted 잔존.
        # Phase P: TEXT radius 50mm > 30 → 1 match.
        matches = cmp._find_near_matches_linear(deleted, added)
        assert len(matches) == 1, "TEXT 30mm shift should match within text radius"

    def test_line_30mm_shift_not_matched_default_tolerance(self) -> None:
        """LINE entity 는 30mm 시프트 시 매칭 안 됨 (회귀 가드 — TEXT 만 확장)."""
        from src.services.comparison.dxf_comparator import DxfChangeType

        cmp = DxfComparator()
        cmp.tolerance = 1.0
        cmp.sensitivity["text_near_match_radius"] = 50.0
        cmp._use_near_match_index = False
        cmp.near_match_index = "linear"

        deleted = [
            self._make_change("LINE", (100.0, 100.0), DxfChangeType.DELETED, "BEAM")
        ]
        added = [
            self._make_change("LINE", (130.0, 100.0), DxfChangeType.ADDED, "BEAM")
        ]
        matches = cmp._find_near_matches_linear(deleted, added)
        assert len(matches) == 0, "LINE 30mm shift > 1mm tolerance → no near match"

    def test_text_60mm_shift_not_matched_outside_radius(self) -> None:
        """TEXT 도 50mm 초과는 매칭 안 됨 (의도 — 너무 멀면 다른 텍스트)."""
        from src.services.comparison.dxf_comparator import DxfChangeType

        cmp = DxfComparator()
        cmp.tolerance = 1.0
        cmp.sensitivity["text_near_match_radius"] = 50.0
        cmp._use_near_match_index = False
        cmp.near_match_index = "linear"

        deleted = [
            self._make_change("TEXT", (100.0, 100.0), DxfChangeType.DELETED)
        ]
        added = [
            self._make_change("TEXT", (200.0, 100.0), DxfChangeType.ADDED)
        ]
        matches = cmp._find_near_matches_linear(deleted, added)
        assert len(matches) == 0


class TestSensitivityConfigHasField:
    """SensitivityConfig 가 ``text_near_match_radius`` 노출하는지 (회귀 가드)."""

    def test_default_config_has_field(self) -> None:
        from src.services.comparison.comparison_config import SensitivityConfig

        cfg = SensitivityConfig()
        assert hasattr(cfg, "text_near_match_radius")
        assert cfg.text_near_match_radius == 50.0

    def test_to_dict_round_trip(self) -> None:
        from src.services.comparison.comparison_config import SensitivityConfig

        cfg = SensitivityConfig(text_near_match_radius=25.0)
        d = cfg.to_dict()
        assert d["text_near_match_radius"] == 25.0
        cfg2 = SensitivityConfig.from_dict(d)
        assert cfg2.text_near_match_radius == 25.0
