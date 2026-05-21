"""비교 설정 모듈 테스트

Phase 3 P3-2: 민감도 설정 분리 테스트
"""

import tempfile
from pathlib import Path

import pytest
import yaml

from src.services.comparison.comparison_config import (
    ComparisonConfig,
    LayerPriorityConfig,
    SensitivityConfig,
    SensitivityPreset,
    clear_config_cache,
    get_default_config,
    load_config,
)


class TestSensitivityConfig:
    """SensitivityConfig 테스트"""

    def test_default_values(self):
        """기본값 테스트"""
        config = SensitivityConfig()
        assert config.coordinate_precision == 1
        assert config.dimension_abs_threshold == 1.0
        assert config.dimension_rel_threshold == 0.001
        assert config.position_threshold == 1.0
        assert config.rotation_threshold == 0.1
        assert config.scale_threshold == 0.01
        assert config.near_match_radius == 10.0

    def test_custom_values(self):
        """사용자 정의 값 테스트"""
        config = SensitivityConfig(
            coordinate_precision=2,
            dimension_abs_threshold=0.5,
            position_threshold=0.5,
        )
        assert config.coordinate_precision == 2
        assert config.dimension_abs_threshold == 0.5
        assert config.position_threshold == 0.5
        # 지정하지 않은 값은 기본값
        assert config.rotation_threshold == 0.1

    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        config = SensitivityConfig()
        data = config.to_dict()
        assert "coordinate_precision" in data
        assert "dimension_abs_threshold" in data
        assert data["position_threshold"] == 1.0

    def test_from_dict(self):
        """딕셔너리에서 생성 테스트"""
        data = {
            "coordinate_precision": 3,
            "dimension_abs_threshold": 2.0,
        }
        config = SensitivityConfig.from_dict(data)
        assert config.coordinate_precision == 3
        assert config.dimension_abs_threshold == 2.0
        # 누락된 값은 기본값
        assert config.position_threshold == 1.0

    def test_is_position_significant(self):
        """위치 변경 유의미성 테스트"""
        config = SensitivityConfig(position_threshold=1.0)
        assert config.is_position_significant(1.0) is True
        assert config.is_position_significant(0.5) is False
        assert config.is_position_significant(2.0) is True

    def test_is_dimension_significant_absolute(self):
        """치수 변경 유의미성 (절대값) 테스트"""
        config = SensitivityConfig(
            dimension_abs_threshold=1.0,
            dimension_rel_threshold=0.1,  # 10% - 높은 상대 임계값
        )
        # 절대값 1.0mm 이상
        assert config.is_dimension_significant(100.0, 101.5) is True
        # 절대값 0.5mm, 상대값 0.5% - 둘 다 임계값 미만
        assert config.is_dimension_significant(100.0, 100.5) is False

    def test_is_dimension_significant_relative(self):
        """치수 변경 유의미성 (상대값) 테스트"""
        config = SensitivityConfig(
            dimension_abs_threshold=10.0,  # 높은 절대 임계값
            dimension_rel_threshold=0.001,  # 0.1%
        )
        # 절대값은 작지만 상대값 0.1% 이상
        assert config.is_dimension_significant(1000.0, 1002.0) is True
        assert config.is_dimension_significant(1000.0, 1000.5) is False

    def test_is_rotation_significant(self):
        """회전 변경 유의미성 테스트"""
        config = SensitivityConfig(rotation_threshold=0.1)
        assert config.is_rotation_significant(0.15) is True
        assert config.is_rotation_significant(0.05) is False

    def test_is_scale_significant(self):
        """스케일 변경 유의미성 테스트"""
        config = SensitivityConfig(scale_threshold=0.01)  # 1%
        assert config.is_scale_significant(1.0, 1.02) is True
        assert config.is_scale_significant(1.0, 1.005) is False

    def test_from_preset_strict(self):
        """STRICT 프리셋에서 생성 테스트"""
        config = SensitivityConfig.from_preset(SensitivityPreset.STRICT)
        assert config.coordinate_precision == 2
        assert config.dimension_abs_threshold == 0.1
        assert config.position_threshold == 0.1
        assert config.near_match_radius == 5.0

    def test_from_preset_normal(self):
        """NORMAL 프리셋에서 생성 테스트"""
        config = SensitivityConfig.from_preset(SensitivityPreset.NORMAL)
        # 기본값과 동일
        assert config.coordinate_precision == 1
        assert config.position_threshold == 1.0
        assert config.near_match_radius == 10.0

    def test_from_preset_relaxed(self):
        """RELAXED 프리셋에서 생성 테스트"""
        config = SensitivityConfig.from_preset(SensitivityPreset.RELAXED)
        assert config.coordinate_precision == 0
        assert config.dimension_abs_threshold == 5.0
        assert config.position_threshold == 5.0
        assert config.near_match_radius == 20.0


class TestSensitivityPreset:
    """SensitivityPreset 열거형 테스트 (Phase 3+ QW-1)"""

    def test_preset_values(self):
        """프리셋 값 테스트"""
        assert SensitivityPreset.STRICT.value == "strict"
        assert SensitivityPreset.NORMAL.value == "normal"
        assert SensitivityPreset.RELAXED.value == "relaxed"

    def test_from_string_valid(self):
        """유효한 문자열에서 프리셋 생성"""
        assert SensitivityPreset.from_string("strict") == SensitivityPreset.STRICT
        assert SensitivityPreset.from_string("normal") == SensitivityPreset.NORMAL
        assert SensitivityPreset.from_string("relaxed") == SensitivityPreset.RELAXED

    def test_from_string_case_insensitive(self):
        """대소문자 무관 문자열 변환"""
        assert SensitivityPreset.from_string("STRICT") == SensitivityPreset.STRICT
        assert SensitivityPreset.from_string("Relaxed") == SensitivityPreset.RELAXED
        assert SensitivityPreset.from_string("NoRmAl") == SensitivityPreset.NORMAL

    def test_from_string_invalid_returns_normal(self):
        """잘못된 문자열은 NORMAL 반환"""
        assert SensitivityPreset.from_string("invalid") == SensitivityPreset.NORMAL
        assert SensitivityPreset.from_string("") == SensitivityPreset.NORMAL
        assert SensitivityPreset.from_string("ultra") == SensitivityPreset.NORMAL

    def test_to_korean(self):
        """한국어 레이블 반환"""
        assert SensitivityPreset.STRICT.to_korean() == "엄격"
        assert SensitivityPreset.NORMAL.to_korean() == "일반"
        assert SensitivityPreset.RELAXED.to_korean() == "완화"

    def test_get_description(self):
        """프리셋 설명 반환"""
        assert "작은 변경" in SensitivityPreset.STRICT.get_description()
        assert "일반적인" in SensitivityPreset.NORMAL.get_description()
        assert "큰 변경" in SensitivityPreset.RELAXED.get_description()

    def test_all_presets_have_korean(self):
        """모든 프리셋에 한국어 레이블 있음"""
        for preset in SensitivityPreset:
            assert len(preset.to_korean()) > 0

    def test_all_presets_have_description(self):
        """모든 프리셋에 설명 있음"""
        for preset in SensitivityPreset:
            assert len(preset.get_description()) > 0


class TestLayerPriorityConfig:
    """LayerPriorityConfig 테스트"""

    def test_default_patterns(self):
        """기본 패턴 테스트"""
        config = LayerPriorityConfig()
        assert "DIM*" in config.high_priority_patterns
        assert "DEFPOINTS" in config.low_priority_patterns
        assert "HIDDEN*" in config.ignore_patterns

    def test_get_priority_high(self):
        """높은 우선순위 테스트"""
        config = LayerPriorityConfig()
        assert config.get_priority("DIM_LAYER") == 2
        assert config.get_priority("DIMENSION_1") == 2
        assert config.get_priority("TEXT_LABELS") == 2

    def test_get_priority_low(self):
        """낮은 우선순위 테스트"""
        config = LayerPriorityConfig()
        assert config.get_priority("DEFPOINTS") == 0
        assert config.get_priority("TEMP_LINES") == 0

    def test_get_priority_ignore(self):
        """무시 레이어 테스트"""
        config = LayerPriorityConfig()
        assert config.get_priority("HIDDEN_LAYER") == -1
        assert config.get_priority("OLD_LAYER_OLD") == -1

    def test_get_priority_normal(self):
        """일반 우선순위 테스트"""
        config = LayerPriorityConfig()
        assert config.get_priority("STRUCTURE") == 1
        assert config.get_priority("WALLS") == 1

    def test_should_ignore(self):
        """무시 여부 테스트"""
        config = LayerPriorityConfig()
        assert config.should_ignore("HIDDEN_LAYER") is True
        assert config.should_ignore("STRUCTURE") is False

    def test_case_insensitive(self):
        """대소문자 무관 테스트"""
        config = LayerPriorityConfig()
        assert config.get_priority("dim_layer") == 2
        assert config.get_priority("DIM_LAYER") == 2

    def test_custom_patterns(self):
        """사용자 정의 패턴 테스트"""
        config = LayerPriorityConfig(
            high_priority_patterns=["BEAM*"],
            low_priority_patterns=["GRID*"],
            ignore_patterns=["TEMP*"],
        )
        assert config.get_priority("BEAM_1") == 2
        assert config.get_priority("GRID_A") == 0
        assert config.get_priority("TEMP_WORK") == -1

    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        config = LayerPriorityConfig()
        data = config.to_dict()
        assert "high_priority_patterns" in data
        assert "low_priority_patterns" in data
        assert "ignore_patterns" in data

    def test_from_dict(self):
        """딕셔너리에서 생성 테스트"""
        data = {
            "high_priority_patterns": ["CUSTOM*"],
            "low_priority_patterns": [],
            "ignore_patterns": [],
        }
        config = LayerPriorityConfig.from_dict(data)
        assert config.get_priority("CUSTOM_LAYER") == 2


class TestComparisonConfig:
    """ComparisonConfig 테스트"""

    def test_default_values(self):
        """기본값 테스트.

        Phase Q3 (RV-20260509-002): expand_blocks default flipped
        False → True so block geometry changes are detected by default.
        """
        config = ComparisonConfig()
        assert isinstance(config.sensitivity, SensitivityConfig)
        assert isinstance(config.layer_priority, LayerPriorityConfig)
        assert config.expand_blocks is True  # Phase Q3 — default flipped
        assert config.use_spatial_index is True
        assert config.use_ocr is False
        assert config.max_entities == 0
        assert config.report_format == "json"

    def test_nested_config(self):
        """중첩 설정 테스트"""
        config = ComparisonConfig(
            sensitivity=SensitivityConfig(position_threshold=2.0),
            layer_priority=LayerPriorityConfig(ignore_patterns=["TEST*"]),
        )
        assert config.sensitivity.position_threshold == 2.0
        assert "TEST*" in config.layer_priority.ignore_patterns

    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        config = ComparisonConfig()
        data = config.to_dict()
        assert "sensitivity" in data
        assert "layer_priority" in data
        assert "expand_blocks" in data
        assert isinstance(data["sensitivity"], dict)

    def test_from_dict(self):
        """딕셔너리에서 생성 테스트"""
        data = {
            "sensitivity": {"position_threshold": 3.0},
            "expand_blocks": False,
            "report_format": "html",
        }
        config = ComparisonConfig.from_dict(data)
        assert config.sensitivity.position_threshold == 3.0
        assert config.expand_blocks is False
        assert config.report_format == "html"

    def test_to_yaml_and_from_yaml(self):
        """YAML 저장/로드 테스트"""
        config = ComparisonConfig(
            sensitivity=SensitivityConfig(position_threshold=5.0),
            expand_blocks=False,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "test_config.yaml"
            config.to_yaml(yaml_path)

            loaded = ComparisonConfig.from_yaml(yaml_path)
            assert loaded.sensitivity.position_threshold == 5.0
            assert loaded.expand_blocks is False

    def test_from_yaml_file_not_found(self):
        """YAML 파일 없음 오류 테스트"""
        with pytest.raises(FileNotFoundError):
            ComparisonConfig.from_yaml("/nonexistent/path.yaml")

    def test_from_yaml_empty_file(self):
        """빈 YAML 파일 테스트"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "empty.yaml"
            yaml_path.write_text("")

            config = ComparisonConfig.from_yaml(yaml_path)
            # 빈 파일이면 기본 설정
            assert config.sensitivity.position_threshold == 1.0

    def test_get_default(self):
        """기본 설정 팩토리 테스트"""
        config = ComparisonConfig.get_default()
        assert config.sensitivity.position_threshold == 1.0

    def test_get_strict(self):
        """엄격한 설정 팩토리 테스트"""
        config = ComparisonConfig.get_strict()
        assert config.sensitivity.coordinate_precision == 2
        assert config.sensitivity.position_threshold == 0.1
        assert config.sensitivity.near_match_radius == 5.0

    def test_get_relaxed(self):
        """완화된 설정 팩토리 테스트"""
        config = ComparisonConfig.get_relaxed()
        assert config.sensitivity.coordinate_precision == 0
        assert config.sensitivity.position_threshold == 5.0
        assert config.sensitivity.near_match_radius == 20.0

    def test_from_preset_strict(self):
        """STRICT 프리셋에서 ComparisonConfig 생성"""
        config = ComparisonConfig.from_preset(SensitivityPreset.STRICT)
        assert config.sensitivity.position_threshold == 0.1
        assert config.sensitivity.coordinate_precision == 2

    def test_from_preset_normal(self):
        """NORMAL 프리셋에서 ComparisonConfig 생성"""
        config = ComparisonConfig.from_preset(SensitivityPreset.NORMAL)
        assert config.sensitivity.position_threshold == 1.0
        assert config.sensitivity.coordinate_precision == 1

    def test_from_preset_relaxed(self):
        """RELAXED 프리셋에서 ComparisonConfig 생성"""
        config = ComparisonConfig.from_preset(SensitivityPreset.RELAXED)
        assert config.sensitivity.position_threshold == 5.0
        assert config.sensitivity.coordinate_precision == 0

    def test_from_preset_string_strict(self):
        """문자열에서 STRICT 프리셋 생성"""
        config = ComparisonConfig.from_preset_string("strict")
        assert config.sensitivity.position_threshold == 0.1

    def test_from_preset_string_normal(self):
        """문자열에서 NORMAL 프리셋 생성"""
        config = ComparisonConfig.from_preset_string("normal")
        assert config.sensitivity.position_threshold == 1.0

    def test_from_preset_string_relaxed(self):
        """문자열에서 RELAXED 프리셋 생성"""
        config = ComparisonConfig.from_preset_string("relaxed")
        assert config.sensitivity.position_threshold == 5.0

    def test_from_preset_string_case_insensitive(self):
        """문자열 대소문자 무관 테스트"""
        config = ComparisonConfig.from_preset_string("STRICT")
        assert config.sensitivity.position_threshold == 0.1

    def test_from_preset_string_invalid_defaults_to_normal(self):
        """잘못된 문자열은 NORMAL로 기본 처리"""
        config = ComparisonConfig.from_preset_string("invalid")
        assert config.sensitivity.position_threshold == 1.0


class TestConfigFunctions:
    """설정 함수 테스트"""

    def setup_method(self):
        """각 테스트 전 캐시 초기화"""
        clear_config_cache()

    def test_get_default_config(self):
        """기본 설정 반환 테스트"""
        config = get_default_config()
        assert isinstance(config, ComparisonConfig)

    def test_get_default_config_caching(self):
        """기본 설정 캐싱 테스트"""
        config1 = get_default_config()
        config2 = get_default_config()
        assert config1 is config2

    def test_clear_config_cache(self):
        """캐시 초기화 테스트"""
        config1 = get_default_config()
        clear_config_cache()
        config2 = get_default_config()
        # 캐시 초기화 후 새 인스턴스
        assert config1 is not config2

    def test_load_config_none(self):
        """load_config None 파라미터 테스트"""
        config = load_config(None)
        assert isinstance(config, ComparisonConfig)

    def test_load_config_from_yaml(self):
        """load_config YAML 로드 테스트"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "config.yaml"
            yaml_content = {
                "sensitivity": {"position_threshold": 7.0},
                "report_format": "excel",
            }
            with open(yaml_path, "w", encoding="utf-8") as f:
                yaml.dump(yaml_content, f)

            config = load_config(yaml_path)
            assert config.sensitivity.position_threshold == 7.0
            assert config.report_format == "excel"


class TestDxfComparatorConfigIntegration:
    """DxfComparator와 ComparisonConfig 통합 테스트"""

    def test_comparator_with_config(self):
        """Comparator에 Config 적용 테스트"""
        from src.services.comparison.dxf_comparator import DxfComparator

        config = ComparisonConfig(
            sensitivity=SensitivityConfig(position_threshold=2.5),
        )
        comparator = DxfComparator(config=config)

        assert comparator.sensitivity["position"] == 2.5

    def test_comparator_from_config_factory(self):
        """from_config 팩토리 메서드 테스트"""
        from src.services.comparison.dxf_comparator import DxfComparator

        config = ComparisonConfig.get_strict()
        comparator = DxfComparator.from_config(config)

        assert comparator.sensitivity["position"] == 0.1

    def test_comparator_config_property(self):
        """config 프로퍼티 테스트"""
        from src.services.comparison.dxf_comparator import DxfComparator

        config = ComparisonConfig()
        comparator = DxfComparator(config=config)

        assert comparator.config is config

    def test_comparator_backward_compatibility(self):
        """하위 호환성 테스트 - sensitivity 딕셔너리"""
        from src.services.comparison.dxf_comparator import DxfComparator

        # 기존 방식 (sensitivity 딕셔너리)
        comparator = DxfComparator(
            sensitivity={"position": 3.0},
        )
        assert comparator.sensitivity["position"] == 3.0

    def test_comparator_config_overrides_sensitivity(self):
        """config가 sensitivity 딕셔너리보다 우선"""
        from src.services.comparison.dxf_comparator import DxfComparator

        config = ComparisonConfig(
            sensitivity=SensitivityConfig(position_threshold=5.0),
        )
        # sensitivity와 config 둘 다 제공 시 config 우선
        comparator = DxfComparator(
            sensitivity={"position": 1.0},
            config=config,
        )
        assert comparator.sensitivity["position"] == 5.0

    def test_comparator_layer_priority_integration(self):
        """레이어 우선순위 통합 테스트"""
        from src.services.comparison.dxf_comparator import DxfComparator

        config = ComparisonConfig(
            layer_priority=LayerPriorityConfig(
                ignore_patterns=["TEST_IGNORE*"],
            ),
        )
        comparator = DxfComparator(config=config)

        # 레이어 필터링 확인
        assert comparator._layer_priority.should_ignore("TEST_IGNORE_LAYER") is True
        assert comparator._layer_priority.should_ignore("NORMAL_LAYER") is False

    def test_comparator_from_preset(self):
        """프리셋에서 DxfComparator 생성 테스트 (Phase 3+ QW-1)"""
        from src.services.comparison.dxf_comparator import DxfComparator

        # STRICT 프리셋으로 생성
        config = ComparisonConfig.from_preset(SensitivityPreset.STRICT)
        comparator = DxfComparator(config=config)

        assert comparator.sensitivity["position"] == 0.1
        assert comparator.sensitivity["dimension"] == 0.1  # dimension_abs_threshold

    def test_comparator_from_preset_string(self):
        """문자열 프리셋에서 DxfComparator 생성 테스트"""
        from src.services.comparison.dxf_comparator import DxfComparator

        # 문자열로 프리셋 지정
        config = ComparisonConfig.from_preset_string("relaxed")
        comparator = DxfComparator(config=config)

        assert comparator.sensitivity["position"] == 5.0
        assert comparator.sensitivity["dimension"] == 5.0  # dimension_abs_threshold

    def test_comparator_preset_affects_comparison(self):
        """프리셋이 실제 비교에 영향을 미치는지 테스트"""
        from src.services.comparison.dxf_comparator import DxfComparator

        # RELAXED: 5mm 임계값 (작은 변경 무시)
        relaxed_config = ComparisonConfig.from_preset(SensitivityPreset.RELAXED)
        relaxed_comparator = DxfComparator(config=relaxed_config)

        # STRICT: 0.1mm 임계값 (작은 변경도 감지)
        strict_config = ComparisonConfig.from_preset(SensitivityPreset.STRICT)
        strict_comparator = DxfComparator(config=strict_config)

        # 3mm 위치 차이: RELAXED는 무시, STRICT는 감지
        assert relaxed_config.sensitivity.is_position_significant(3.0) is False
        assert strict_config.sensitivity.is_position_significant(3.0) is True
