"""Phase P (RV-20260508-013) — structural_layer_patterns SSoT 회귀 가드.

한국어 layer ("기둥-1F") 가 ``is_structural_layer`` 에서 매칭되어야 하고,
영문 layer ("A-COL-EXIST"), 일반 약어 ("BM_2F") 도 매칭. 비-구조 layer
("MISC", "TEXT", "DEFPOINTS") 는 미매칭.
"""
from __future__ import annotations

import pytest

from src.services.comparison.structural_layer_patterns import (
    STRUCTURAL_LAYER_PATTERNS_FNMATCH,
    STRUCTURAL_LAYER_PATTERNS_KOREAN,
    any_structural_layer,
    is_structural_layer,
)


class TestKoreanLayerRecognition:
    """Phase O 의 영문 fnmatch 만으로는 매칭 안 되던 한국어 layer 회복 검증."""

    @pytest.mark.parametrize(
        "layer",
        ["기둥", "기둥-1F", "기둥_지하1층", "보", "보-2F", "가새", "벽-외측", "슬래브", "기초"],
    )
    def test_korean_structural_names_match(self, layer: str) -> None:
        assert is_structural_layer(layer), f"{layer} should be classified structural"

    @pytest.mark.parametrize(
        "layer",
        ["A-COL-EXIST", "S-BEAM-NEW", "BRACE_FRAME_3F", "GIRDER", "TRUSS-MAIN"],
    )
    def test_english_structural_names_match(self, layer: str) -> None:
        assert is_structural_layer(layer)

    @pytest.mark.parametrize(
        "layer",
        ["BM_2F", "CL_5F", "GR_1F", "WL_EXTERIOR", "FT_BASE"],
    )
    def test_short_abbreviations_match(self, layer: str) -> None:
        assert is_structural_layer(layer)

    @pytest.mark.parametrize(
        "layer",
        ["MISC", "TEXT", "DEFPOINTS", "0", "DIM_GENERAL", "TITLE_BLOCK", "VIEWPORT"],
    )
    def test_non_structural_layers_unmatched(self, layer: str) -> None:
        assert not is_structural_layer(layer), f"{layer} should NOT be structural"

    def test_empty_or_none_layer_returns_false(self) -> None:
        assert is_structural_layer("") is False
        assert is_structural_layer(None) is False  # type: ignore[arg-type]
        assert is_structural_layer("   ") is False

    def test_any_structural_in_iterable(self) -> None:
        assert any_structural_layer(["TEXT", "기둥-1F", "DIM"]) is True
        assert any_structural_layer(["TEXT", "DIM", "MISC"]) is False
        assert any_structural_layer([]) is False


class TestPatternRegistries:
    """SSoT 모듈이 fnmatch + 한국어 substring 패턴을 모두 노출하는지."""

    def test_fnmatch_patterns_are_uppercase_wildcards(self) -> None:
        for pattern in STRUCTURAL_LAYER_PATTERNS_FNMATCH:
            assert "*" in pattern
            assert pattern == pattern.upper()

    def test_korean_patterns_are_substrings(self) -> None:
        for kr in STRUCTURAL_LAYER_PATTERNS_KOREAN:
            assert "*" not in kr
            assert kr == kr.strip()
            # 한국어 (CJK) 만으로 구성되어야 함 (영문 substring 은 fnmatch 으로)
            assert any(0xAC00 <= ord(c) <= 0xD7A3 for c in kr)
