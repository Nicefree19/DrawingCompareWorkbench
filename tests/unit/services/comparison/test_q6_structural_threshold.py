"""Phase Q6 (RV-20260509-002) — structural layer-aware position threshold.

사용자 보고: "변경사항 미탐지가 많다." Phase Q1-Q5 완료 후에도 구조
도면에서 sub-mm 위치 변경 (예: rebar 위치 0.5mm shift, anchor bolt
재배치) 이 default ``position_threshold=1.0mm`` 에 의해 silent drop.
구조 부재 (기둥/보/가새/벽 등) 의 위치 변경은 sub-mm 차이도 critical
한 의미가 있을 수 있어 **layer-aware 더 엄격한 threshold** 필요.

Q6:

1. ``SensitivityConfig.structural_position_threshold: float = 0.1``
   (default 0.1 mm — 100 um. structural layer 일 때만 적용).
2. ``DxfComparator._is_significant_change`` 가 ``layer`` 인자 추가
   받음. 구조 layer 면 더 엄격 threshold 적용.
3. ``_position_threshold_for_layer(layer)`` helper:
   - structural_position_threshold <= 0 또는 layer 비어있으면
     default position_threshold 사용 (legacy 동작).
   - ``is_structural_layer(layer)`` True 면 structural_position_threshold.
   - 아니면 default position_threshold.
4. 두 호출 site (``_compare_one_to_one_only`` line 1152,
   ``_create_modified_change`` line 1816) 가 ``layer=`` 전달.
"""
from __future__ import annotations

import pytest

from src.services.comparison.comparison_config import (
    ComparisonConfig,
    SensitivityConfig,
)
from src.services.comparison.dxf_comparator import DxfComparator


@pytest.fixture
def comparator_with_q6_default():
    """default Q6 settings (structural_position=0.1, position=1.0)."""
    sens = SensitivityConfig(
        position_threshold=1.0,
        structural_position_threshold=0.1,
    )
    config = ComparisonConfig(sensitivity=sens)
    return DxfComparator(config=config)


@pytest.fixture
def comparator_legacy_disabled():
    """structural_position=0.0 → legacy 동작 (구조 layer 도 default 사용)."""
    sens = SensitivityConfig(
        position_threshold=1.0,
        structural_position_threshold=0.0,
    )
    config = ComparisonConfig(sensitivity=sens)
    return DxfComparator(config=config)


class TestStructuralThresholdActivation:
    """Q6 — structural layer 일 때 더 엄격한 threshold 적용."""

    def test_structural_beam_05mm_shift_detected(self, comparator_with_q6_default):
        """BEAM layer 의 0.5mm shift 는 structural threshold 0.1mm 를
        초과하므로 significant 로 판정 (default 1.0mm 였으면 silent)."""
        result = comparator_with_q6_default._is_significant_change(
            categories=["position"],
            old_data={"location": (0, 0, 0)},
            new_data={"location": (0.5, 0, 0)},
            pos_diff=0.5,
            layer="BEAM",
        )
        assert result is True, (
            "BEAM layer 의 0.5mm shift 는 구조적으로 의미 있는 변경 "
            "— Q6 structural_position_threshold=0.1mm 가 적용되어야 함"
        )

    def test_structural_korean_layer_detected(self, comparator_with_q6_default):
        """한국어 구조 layer (기둥-1F) 도 동일하게 적용."""
        result = comparator_with_q6_default._is_significant_change(
            categories=["position"],
            old_data={"location": (0, 0, 0)},
            new_data={"location": (0.3, 0, 0)},
            pos_diff=0.3,
            layer="기둥-1F",
        )
        assert result is True, (
            "한국어 구조 layer (기둥) 도 structural threshold 적용 — "
            "structural_layer_patterns SSoT 가 한국어 매칭"
        )

    def test_structural_threshold_not_triggered_below(
        self, comparator_with_q6_default
    ):
        """0.05mm shift 는 structural threshold 0.1mm 미만이라
        여전히 noise 로 판정 (= False)."""
        result = comparator_with_q6_default._is_significant_change(
            categories=["position"],
            old_data={"location": (0, 0, 0)},
            new_data={"location": (0.05, 0, 0)},
            pos_diff=0.05,
            layer="COLUMN",
        )
        assert result is False, (
            "0.05mm shift 는 structural_position_threshold 0.1mm 미만 "
            "→ noise"
        )


class TestNonStructuralLayerUsesDefault:
    """Q6 — 비-구조 layer 는 default position_threshold 사용."""

    def test_dimension_layer_05mm_ignored(self, comparator_with_q6_default):
        """DIMENSION layer 의 0.5mm shift 는 default 1.0mm 미만 → ignore.
        DIMENSION 은 structural_layer_patterns 에 매칭 안 됨."""
        result = comparator_with_q6_default._is_significant_change(
            categories=["position"],
            old_data={"location": (0, 0, 0)},
            new_data={"location": (0.5, 0, 0)},
            pos_diff=0.5,
            layer="DIMENSION",
        )
        assert result is False, (
            "DIMENSION 은 비-구조 → default 1.0mm 적용 → 0.5mm 는 noise"
        )

    def test_text_layer_default_threshold(self, comparator_with_q6_default):
        """TEXT layer 도 default 사용 (구조 layer 아님)."""
        result = comparator_with_q6_default._is_significant_change(
            categories=["position"],
            old_data={"location": (0, 0, 0)},
            new_data={"location": (0.7, 0, 0)},
            pos_diff=0.7,
            layer="TEXT",
        )
        assert result is False
        # 1.5mm 는 default 1.0mm 초과
        result2 = comparator_with_q6_default._is_significant_change(
            categories=["position"],
            old_data={"location": (0, 0, 0)},
            new_data={"location": (1.5, 0, 0)},
            pos_diff=1.5,
            layer="TEXT",
        )
        assert result2 is True

    def test_empty_layer_falls_back_to_default(
        self, comparator_with_q6_default
    ):
        """layer="" → structural 판정 못함 → default 사용."""
        result = comparator_with_q6_default._is_significant_change(
            categories=["position"],
            old_data={"location": (0, 0, 0)},
            new_data={"location": (0.5, 0, 0)},
            pos_diff=0.5,
            layer="",
        )
        assert result is False, "layer 비어있으면 default → 0.5 < 1.0 → noise"


class TestLegacyBackwardCompat:
    """Q6 — structural_position_threshold=0.0 → 완전 legacy 동작."""

    def test_structural_layer_with_zero_threshold_uses_default(
        self, comparator_legacy_disabled
    ):
        """structural_position_threshold=0.0 면 구조 layer 도 default 사용.
        0.5mm shift 는 default 1.0mm 미만 → noise."""
        result = comparator_legacy_disabled._is_significant_change(
            categories=["position"],
            old_data={"location": (0, 0, 0)},
            new_data={"location": (0.5, 0, 0)},
            pos_diff=0.5,
            layer="BEAM",
        )
        assert result is False, (
            "structural_position_threshold=0.0 → layer-aware 비활성 → "
            "BEAM 도 default 1.0mm 사용 → 0.5mm 는 noise (legacy 동작)"
        )

    def test_legacy_caller_without_layer_param(
        self, comparator_with_q6_default
    ):
        """layer 인자 안 넘기면 (기존 caller backward-compat) default 사용."""
        result = comparator_with_q6_default._is_significant_change(
            categories=["position"],
            old_data={"location": (0, 0, 0)},
            new_data={"location": (0.5, 0, 0)},
            pos_diff=0.5,
            # layer 인자 생략
        )
        assert result is False, (
            "layer 인자 미전달 (legacy caller) → default threshold 사용"
        )


class TestPositionThresholdHelper:
    """Q6 — _position_threshold_for_layer helper 단위 테스트."""

    def test_helper_returns_structural_for_beam(
        self, comparator_with_q6_default
    ):
        threshold = comparator_with_q6_default._position_threshold_for_layer(
            "BEAM-2F"
        )
        assert threshold == pytest.approx(0.1)

    def test_helper_returns_default_for_non_structural(
        self, comparator_with_q6_default
    ):
        threshold = comparator_with_q6_default._position_threshold_for_layer(
            "DIMENSION"
        )
        assert threshold == pytest.approx(1.0)

    def test_helper_returns_default_when_threshold_zero(
        self, comparator_legacy_disabled
    ):
        # structural layer 라도 structural threshold = 0 이면 default 반환
        threshold = comparator_legacy_disabled._position_threshold_for_layer(
            "COLUMN"
        )
        assert threshold == pytest.approx(1.0)

    def test_helper_handles_korean_layer(self, comparator_with_q6_default):
        threshold = comparator_with_q6_default._position_threshold_for_layer(
            "기둥-1F"
        )
        assert threshold == pytest.approx(0.1)


class TestConfigSerialisation:
    """Q6 — SensitivityConfig 직렬화 round-trip."""

    def test_to_dict_includes_structural_position_threshold(self):
        cfg = SensitivityConfig(structural_position_threshold=0.05)
        data = cfg.to_dict()
        assert "structural_position_threshold" in data
        assert data["structural_position_threshold"] == pytest.approx(0.05)

    def test_from_dict_default_is_01mm(self):
        # 빈 dict 에서 from_dict → default 0.1mm
        cfg = SensitivityConfig.from_dict({})
        assert cfg.structural_position_threshold == pytest.approx(0.1)

    def test_from_dict_legacy_zero_preserved(self):
        cfg = SensitivityConfig.from_dict({"structural_position_threshold": 0.0})
        assert cfg.structural_position_threshold == pytest.approx(0.0)

    def test_from_dict_custom_value(self):
        cfg = SensitivityConfig.from_dict({"structural_position_threshold": 0.5})
        assert cfg.structural_position_threshold == pytest.approx(0.5)

    def test_round_trip(self):
        cfg = SensitivityConfig(
            position_threshold=2.0, structural_position_threshold=0.2
        )
        data = cfg.to_dict()
        rebuilt = SensitivityConfig.from_dict(data)
        assert rebuilt.position_threshold == pytest.approx(2.0)
        assert rebuilt.structural_position_threshold == pytest.approx(0.2)


class TestSensitivityDictMapping:
    """Q6 — _config_to_sensitivity_dict 매핑."""

    def test_sensitivity_dict_includes_structural_position(
        self, comparator_with_q6_default
    ):
        # comparator 의 internal sensitivity dict 에 'structural_position' key 있어야 함
        assert "structural_position" in comparator_with_q6_default.sensitivity
        assert comparator_with_q6_default.sensitivity[
            "structural_position"
        ] == pytest.approx(0.1)


class TestCodexRound1Fixes:
    """Phase Q6 Codex round-1 follow-up — 2 P2 finding regression guards."""

    def test_p2_default_constructor_includes_structural_threshold(self):
        """[P2-2] ``DxfComparator()`` no-config 생성자도 structural_position
        default 0.1mm 가 적용되어야 함. 기존엔 DEFAULT_SENSITIVITY 가
        ``structural_position`` 누락 → 0.0 fallback → Q6 비활성."""
        comparator = DxfComparator()
        assert "structural_position" in comparator.sensitivity, (
            "DEFAULT_SENSITIVITY 에 structural_position 포함되어야 함 — "
            "Codex Q6 round-1 P2-2 fix"
        )
        assert comparator.sensitivity["structural_position"] == pytest.approx(
            0.1
        )
        assert comparator._position_threshold_for_layer("BEAM") == pytest.approx(
            0.1
        ), "default 생성자에서도 BEAM layer 가 0.1mm threshold 적용되어야 함"

    def test_p2_analyze_change_details_uses_layer_threshold(
        self, comparator_with_q6_default
    ):
        """[P2-1] ``_analyze_change_details`` 가 layer-aware threshold 를
        사용해 구조 layer 의 sub-mm shift 가 'position' 카테고리에 분류
        되어야 함. 기존엔 self.sensitivity['position'] (1.0mm) 만 사용 →
        BEAM 0.5mm shift 가 'position' 안 들어가서 _is_significant_change
        호출 시 categories=['content'] 가 되어 Q6 효과 무력화."""
        # BEAM layer + 0.5mm shift
        categories, details = comparator_with_q6_default._analyze_change_details(
            old_data={},
            new_data={},
            entity_type="LINE",
            old_loc=(0.0, 0.0),
            new_loc=(0.5, 0.0),
            layer="BEAM",
        )
        assert "position" in categories, (
            "BEAM (구조 layer) 의 0.5mm shift 가 categories 에 'position' "
            "으로 분류되어야 함 — Codex Q6 round-1 P2-1 fix"
        )

    def test_p2_analyze_change_details_default_threshold_for_non_structural(
        self, comparator_with_q6_default
    ):
        """[P2-1 회귀 가드] 비-구조 layer (DIMENSION) 는 여전히 default
        1.0mm 적용. 0.5mm 는 categories 에서 제외."""
        categories, details = comparator_with_q6_default._analyze_change_details(
            old_data={},
            new_data={},
            entity_type="LINE",
            old_loc=(0.0, 0.0),
            new_loc=(0.5, 0.0),
            layer="DIMENSION",
        )
        # 0.5mm < 1.0mm default → position 제외
        assert "position" not in categories, (
            "DIMENSION layer 의 0.5mm shift 는 default 1.0mm 미만 → "
            "position 제외 (regression guard)"
        )

    def test_p2_analyze_change_details_legacy_no_layer(
        self, comparator_with_q6_default
    ):
        """[P2-1 회귀 가드] layer 인자 미전달 (legacy caller) 은 default
        threshold 사용 — backward-compat."""
        categories, details = comparator_with_q6_default._analyze_change_details(
            old_data={},
            new_data={},
            entity_type="LINE",
            old_loc=(0.0, 0.0),
            new_loc=(0.5, 0.0),
            # layer 인자 생략
        )
        assert "position" not in categories, (
            "layer 미전달 (legacy) → default 1.0mm → 0.5mm 는 position 제외"
        )
