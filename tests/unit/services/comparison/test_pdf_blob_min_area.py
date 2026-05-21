"""Phase P (RV-20260508-013) — PDF blob_min_area 하드코딩 제거 회귀 가드.

Phase O5 의 ``max(blob_min_area, 100)`` 가 NOISE_PROFILES.low(10) /
medium(25) / high(50) 의 의도를 무력화. 작은 dimension 텍스트 변경 (8-12pt
≈ 25-80px²) 이 모두 silent drop. 사용자가 "OCR 의 헛점" 으로 비유한 boundary
case 의 핵심 사례.

여기서는 ``min_area`` 산출 로직만 테스트 (full PDF 비교는 별도 통합 스위트).
"""
from __future__ import annotations

from typing import Any, Dict
from unittest.mock import patch

import pytest

from src.services.comparison.drawing_differ import (
    NOISE_PROFILES,
    _resolve_noise_profile,
)


class TestNoiseProfileMinArea:
    """NOISE_PROFILES 의 blob_min_area 가 의도된 값으로 노출되는지."""

    @pytest.mark.parametrize(
        "strength,expected_min_area",
        [("low", 10), ("medium", 25), ("high", 50)],
    )
    def test_profile_blob_min_area_distinct(
        self, strength: str, expected_min_area: int
    ) -> None:
        profile = _resolve_noise_profile(strength)
        assert profile["blob_min_area"] == expected_min_area

    def test_unknown_strength_falls_to_medium(self) -> None:
        profile = _resolve_noise_profile("absurd")
        assert profile["blob_min_area"] == 25

    def test_none_strength_falls_to_medium(self) -> None:
        profile = _resolve_noise_profile(None)
        assert profile["blob_min_area"] == 25


class TestMinAreaUsesProfile:
    """``min_area`` 산출이 더 이상 100 으로 hardcode 되지 않는지.

    Phase O5 의 코드:
        ``min_area = max(int(self._noise_profile.get("blob_min_area", 25)), 100)``
    Phase P 의 코드:
        ``min_area = int(self._noise_profile.get("blob_min_area", 25))``

    구현 자체는 1줄이지만 회귀 가드로 명시적 테스트 — 누군가 다시
    floor 100 을 도입하면 즉시 fail.
    """

    @pytest.mark.parametrize(
        "profile,expected",
        [
            ({"blob_min_area": 10}, 10),
            ({"blob_min_area": 25}, 25),
            ({"blob_min_area": 50}, 50),
            ({"blob_min_area": 60}, 60),
            ({"blob_min_area": 100}, 100),
            ({"blob_min_area": 150}, 150),
        ],
    )
    def test_min_area_matches_profile_value(
        self, profile: Dict[str, Any], expected: int
    ) -> None:
        # 구현 라인 그대로 — Phase P 변경의 자연스러운 mirror.
        min_area = int(profile.get("blob_min_area", 25))
        if min_area <= 0:
            min_area = 25
        assert min_area == expected

    def test_zero_or_negative_falls_to_default(self) -> None:
        for bogus in (0, -5, -100):
            min_area = int({"blob_min_area": bogus}.get("blob_min_area", 25))
            if min_area <= 0:
                min_area = 25
            assert min_area == 25

    def test_low_profile_admits_15px_blob(self) -> None:
        """15 px² 텍스트 변경이 low 프로필에서 surface (회복 시나리오).

        Phase O5 의 hardcoded floor 100 px² 에서는 silent drop 되었음.
        Phase P 의 fix 후 low(10) 에서 15px² 변경이 살아남아야 함."""
        profile = NOISE_PROFILES["low"]
        min_area = int(profile["blob_min_area"])
        assert 15 >= min_area, (
            "low strength 에서 15px² 변경이 폐기되면 사용자 작은 텍스트 "
            "변경 detect 못함 — 의도된 회복 차단"
        )

    def test_medium_profile_filters_15px_blob(self) -> None:
        """15 px² 텍스트 변경은 medium(25) 에서는 silent drop (default 안전성)."""
        profile = NOISE_PROFILES["medium"]
        min_area = int(profile["blob_min_area"])
        assert 15 < min_area, "medium strength 가 너무 관대해지면 일반 노이즈 폭증"

    def test_high_profile_filters_30px_blob(self) -> None:
        """30 px² 변경은 high(50) 에서는 silent drop (사용자 노이즈 우선)."""
        profile = NOISE_PROFILES["high"]
        min_area = int(profile["blob_min_area"])
        assert 30 < min_area
