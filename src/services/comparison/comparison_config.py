"""비교 설정 모듈

Phase 3 P3-2: 민감도 설정 분리
- SensitivityConfig: 민감도 임계값 설정
- LayerPriorityConfig: 레이어 우선순위 설정
- ComparisonConfig: 통합 비교 설정

Phase 3+ QW-1: Sensitivity Preset
- SensitivityPreset: 민감도 프리셋 열거형
- 프리셋 팩토리 메서드 (strict, normal, relaxed)
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any
import yaml


class SensitivityPreset(Enum):
    """민감도 프리셋 (Phase 3+ QW-1)

    사전 정의된 민감도 설정을 제공합니다.

    Attributes:
        STRICT: 엄격 모드 - 작은 변경도 감지 (검토 작업용)
        NORMAL: 일반 모드 - 기본 설정 (일반 비교용)
        RELAXED: 완화 모드 - 큰 변경만 감지 (개요 파악용)
    """

    STRICT = "strict"
    NORMAL = "normal"
    RELAXED = "relaxed"

    @classmethod
    def from_string(cls, value: str) -> "SensitivityPreset":
        """문자열에서 프리셋 생성"""
        value_lower = value.lower()
        for preset in cls:
            if preset.value == value_lower:
                return preset
        return cls.NORMAL  # 기본값

    def to_korean(self) -> str:
        """한국어 레이블 반환"""
        labels = {
            self.STRICT: "엄격",
            self.NORMAL: "일반",
            self.RELAXED: "완화",
        }
        return labels.get(self, "일반")

    def get_description(self) -> str:
        """프리셋 설명 반환"""
        descriptions = {
            self.STRICT: "작은 변경도 감지합니다. 상세 검토 작업에 적합합니다.",
            self.NORMAL: "일반적인 변경을 감지합니다. 대부분의 비교 작업에 적합합니다.",
            self.RELAXED: "큰 변경만 감지합니다. 빠른 개요 파악에 적합합니다.",
        }
        return descriptions.get(self, "")


@dataclass
class SensitivityConfig:
    """민감도 임계값 설정

    도면 비교 시 변경 감지의 민감도를 제어하는 설정입니다.
    값이 작을수록 더 민감하게 변경을 감지합니다.

    Attributes:
        coordinate_precision: 좌표 정밀도 (소수점 자릿수). 기본 1 = 0.1mm 정밀도
        dimension_abs_threshold: 치수 절대 변경 임계값 (mm). 기본 1.0mm
        dimension_rel_threshold: 치수 상대 변경 임계값 (비율). 기본 0.001 = 0.1%
        position_threshold: 위치 변경 임계값 (mm). 기본 1.0mm
        rotation_threshold: 회전 변경 임계값 (degrees). 기본 0.1°
        scale_threshold: 스케일 변경 임계값 (비율). 기본 0.01 = 1%
        near_match_radius: 근접 매칭 검색 반경 (mm). 기본 10.0mm
        global_alignment_enabled: Phase O2 — 도면 전체 rigid 시프트/회전을
            추정 후 B 좌표를 A 좌표계로 백투영. 미세 시프트로 인한
            false positive 폭증 방지. 기본 True (backward-compat OK
            — 시프트가 임계 미만이면 적용 안 함).
        hungarian_max_subset: Phase O2 — entity matching 시 cluster 당
            scipy linear_sum_assignment 적용 최대 크기. 초과 시 greedy
            fallback. 너무 크면 OOM 위험, 너무 작으면 정확도 손실.
            기본 200 (실측 5만 entity 도면에서도 안전).
    """

    coordinate_precision: int = 1
    dimension_abs_threshold: float = 1.0
    dimension_rel_threshold: float = 0.001
    position_threshold: float = 1.0
    rotation_threshold: float = 0.1
    scale_threshold: float = 0.01
    near_match_radius: float = 10.0
    # Phase O2 — 좌표 노이즈 흡수
    global_alignment_enabled: bool = True
    hungarian_max_subset: int = 200
    # Phase P (RV-20260508-013) — alignment artifact 흡수 가드.
    # ``_is_pure_alignment_artifact`` 가 RANSAC 추정 alignment 와 일치하는
    # 모든 변경을 무조건 흡수하던 동작을 두 단계로 보호.
    #   1. ``alignment_strict_inlier_ratio``: RANSAC inlier 비율이 이 임계
    #      미만이면 alignment 신뢰도가 낮다고 보고 artifact 흡수 비활성.
    #      0.85 이상이면 도면 전체가 같은 방향으로 시프트한 것으로 간주
    #      가능. 50-85% 영역은 부분 시프트 의심 → 보존.
    #   2. ``alignment_protect_structural_layers``: ``True`` 면 structural
    #      layer 의 변경은 alignment 와 일치해도 흡수하지 않음. 사용자가
    #      의도적으로 한 zone 만 시프트하는 시나리오 (예: 보 전체를 50mm
    #      이동) 에서 alignment 가 그 시프트를 글로벌로 추정해 모든 변경을
    #      삼키는 회귀 차단.
    alignment_strict_inlier_ratio: float = 0.85
    # Codex RV-20260509 P1 — default False 로 변경. 초기 Phase P 구현은
    # True 였으나 inlier_ratio=1.0 인 진짜 글로벌 시프트 (예: fixture
    # 03_micro_shift_global) 에서 BEAM layer 의 모든 변경을 false-positive
    # 로 surface 시켜 5건의 회귀 발생. inlier_ratio guard 단독으로 partial
    # shift 의심 케이스 충분히 커버. 이 flag 는 backward-compat 위해
    # 유지하지만 dxf_comparator 가 더 이상 참조하지 않음.
    alignment_protect_structural_layers: bool = False
    # Phase P (RV-20260508-013) — TEXT/DIMENSION near-match 확장 radius.
    # ``find_near_matches`` 의 일반 tolerance (default 1.0mm 또는 alignment
    # 확장값) 가 텍스트 entity 에는 너무 좁아, 좌표 5-30mm 시프트 + 내용
    # 변경 시 added+deleted 로 분리. 사용자 보고: "치수가 위치 + 값 같이
    # 바뀌었는데 두 개로 표시됨". TEXT/MTEXT/DIMENSION/MULTILEADER entity
    # 한정으로 이 radius 까지 후보 매칭 허용. content 매칭 신뢰도가 부족
    # 하면 후속 검증으로 false-pair 차단.
    text_near_match_radius: float = 50.0
    # Phase O3 — cosmetic 변경 분리
    # 좌표가 동일한 entity 페어에서 color/lineweight/linetype 차이를
    # 별도 ``change_category="cosmetic"`` 으로 기록할지 여부.
    # 기본 True (탐지). 노이즈로 분류해 숨기려면 ``suppress_cosmetic_only``.
    cosmetic_detection_enabled: bool = True
    # cosmetic-only 변경을 결과에서 제외할지. 기본 False (보존 — 사용자가
    # 결과 패널/dialog 에서 토글). True 면 result.changes 에서 제외하고
    # ``stats["cosmetic_suppressed"]`` 에 카운터만 남김.
    suppress_cosmetic_only: bool = False
    # 탐지/비교할 cosmetic 속성 화이트리스트.
    cosmetic_attributes: tuple = ("color", "lineweight", "linetype")
    # Phase Q6 (RV-20260509-002) — structural-layer 별 더 엄격한 위치 임계값.
    # 기둥/보/가새/벽 같은 구조 layer 의 변경은 sub-mm 차이도 의미가 클
    # 수 있음 (slot, rebar, anchor 위치). default position_threshold 1.0mm
    # 는 구조 도면에서 critical 변경 누락 위험. structural_position_threshold
    # 가 비-zero 이고 entity 의 layer 가 ``structural_layer_patterns.is_structural_layer``
    # 에 해당하면 _is_significant_change 가 이 값을 사용. 0.0 이면 비활성
    # (legacy 동작 = position_threshold 만 사용).
    structural_position_threshold: float = 0.1

    def to_dict(self) -> Dict[str, Any]:
        """설정을 딕셔너리로 변환"""
        return {
            "coordinate_precision": self.coordinate_precision,
            "dimension_abs_threshold": self.dimension_abs_threshold,
            "dimension_rel_threshold": self.dimension_rel_threshold,
            "position_threshold": self.position_threshold,
            "rotation_threshold": self.rotation_threshold,
            "scale_threshold": self.scale_threshold,
            "near_match_radius": self.near_match_radius,
            "global_alignment_enabled": self.global_alignment_enabled,
            "hungarian_max_subset": self.hungarian_max_subset,
            "alignment_strict_inlier_ratio": self.alignment_strict_inlier_ratio,
            "alignment_protect_structural_layers": self.alignment_protect_structural_layers,
            "text_near_match_radius": self.text_near_match_radius,
            "cosmetic_detection_enabled": self.cosmetic_detection_enabled,
            "suppress_cosmetic_only": self.suppress_cosmetic_only,
            "structural_position_threshold": self.structural_position_threshold,
            "cosmetic_attributes": list(self.cosmetic_attributes),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SensitivityConfig":
        """딕셔너리에서 설정 생성"""
        return cls(
            coordinate_precision=data.get("coordinate_precision", 1),
            dimension_abs_threshold=data.get("dimension_abs_threshold", 1.0),
            dimension_rel_threshold=data.get("dimension_rel_threshold", 0.001),
            position_threshold=data.get("position_threshold", 1.0),
            rotation_threshold=data.get("rotation_threshold", 0.1),
            scale_threshold=data.get("scale_threshold", 0.01),
            near_match_radius=data.get("near_match_radius", 10.0),
            global_alignment_enabled=data.get("global_alignment_enabled", True),
            hungarian_max_subset=data.get("hungarian_max_subset", 200),
            alignment_strict_inlier_ratio=float(data.get("alignment_strict_inlier_ratio", 0.85)),
            alignment_protect_structural_layers=bool(
                data.get("alignment_protect_structural_layers", False)
            ),
            text_near_match_radius=float(data.get("text_near_match_radius", 50.0)),
            cosmetic_detection_enabled=data.get("cosmetic_detection_enabled", True),
            suppress_cosmetic_only=data.get("suppress_cosmetic_only", False),
            cosmetic_attributes=tuple(
                data.get("cosmetic_attributes", ("color", "lineweight", "linetype"))
            ),
            structural_position_threshold=float(
                data.get("structural_position_threshold", 0.1)
            ),
        )

    def is_position_significant(self, distance: float) -> bool:
        """위치 변경이 유의미한지 확인"""
        return distance >= self.position_threshold

    def is_dimension_significant(self, old_value: float, new_value: float) -> bool:
        """치수 변경이 유의미한지 확인

        절대 임계값과 상대 임계값 중 하나라도 초과하면 유의미한 변경으로 판단
        """
        abs_diff = abs(new_value - old_value)
        if abs_diff >= self.dimension_abs_threshold:
            return True

        if old_value != 0:
            rel_diff = abs_diff / abs(old_value)
            if rel_diff >= self.dimension_rel_threshold:
                return True

        return False

    def is_rotation_significant(self, angle_diff: float) -> bool:
        """회전 변경이 유의미한지 확인"""
        return abs(angle_diff) >= self.rotation_threshold

    def is_scale_significant(self, old_scale: float, new_scale: float) -> bool:
        """스케일 변경이 유의미한지 확인"""
        if old_scale == 0:
            return new_scale != 0
        rel_diff = abs(new_scale - old_scale) / abs(old_scale)
        return rel_diff >= self.scale_threshold

    @classmethod
    def from_preset(cls, preset: SensitivityPreset) -> "SensitivityConfig":
        """프리셋에서 민감도 설정 생성 (Phase 3+ QW-1)

        Args:
            preset: 민감도 프리셋 (STRICT, NORMAL, RELAXED)

        Returns:
            해당 프리셋에 맞는 SensitivityConfig 인스턴스
        """
        if preset == SensitivityPreset.STRICT:
            return cls(
                coordinate_precision=2,       # 0.01mm 정밀도
                dimension_abs_threshold=0.1,  # 0.1mm
                dimension_rel_threshold=0.0001,  # 0.01%
                position_threshold=0.1,       # 0.1mm
                rotation_threshold=0.01,      # 0.01°
                scale_threshold=0.001,        # 0.1%
                near_match_radius=5.0,        # 5mm
            )
        elif preset == SensitivityPreset.RELAXED:
            return cls(
                coordinate_precision=0,       # 1mm 정밀도
                dimension_abs_threshold=5.0,  # 5mm
                dimension_rel_threshold=0.01, # 1%
                position_threshold=5.0,       # 5mm
                rotation_threshold=1.0,       # 1°
                scale_threshold=0.05,         # 5%
                near_match_radius=20.0,       # 20mm
            )
        else:  # NORMAL (기본값)
            return cls()


@dataclass
class LayerPriorityConfig:
    """레이어 우선순위 설정

    특정 레이어에 대해 높은 우선순위 또는 낮은 우선순위를 부여합니다.
    패턴 매칭을 지원합니다 (fnmatch 스타일).

    Attributes:
        high_priority_patterns: 높은 우선순위 레이어 패턴 목록
        low_priority_patterns: 낮은 우선순위 레이어 패턴 목록
        ignore_patterns: 무시할 레이어 패턴 목록
    """

    high_priority_patterns: List[str] = field(default_factory=lambda: [
        "DIM*",      # 치수 레이어
        "TEXT*",     # 텍스트 레이어
        "ANNO*",     # 주석 레이어
        "*DIMENSION*",
        "*ANNOTATION*",
    ])
    low_priority_patterns: List[str] = field(default_factory=lambda: [
        "DEFPOINTS",
        "0",         # 기본 레이어
        "TEMP*",     # 임시 레이어
        "*_BACKUP",
    ])
    ignore_patterns: List[str] = field(default_factory=lambda: [
        "HIDDEN*",
        "*_OLD",
        "*_DELETED",
    ])

    def to_dict(self) -> Dict[str, Any]:
        """설정을 딕셔너리로 변환"""
        return {
            "high_priority_patterns": self.high_priority_patterns,
            "low_priority_patterns": self.low_priority_patterns,
            "ignore_patterns": self.ignore_patterns,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LayerPriorityConfig":
        """딕셔너리에서 설정 생성"""
        return cls(
            high_priority_patterns=data.get("high_priority_patterns", []),
            low_priority_patterns=data.get("low_priority_patterns", []),
            ignore_patterns=data.get("ignore_patterns", []),
        )

    def get_priority(self, layer_name: str) -> int:
        """레이어 우선순위 반환

        Returns:
            2: 높은 우선순위
            1: 일반 우선순위
            0: 낮은 우선순위
            -1: 무시 (비교에서 제외)
        """
        import fnmatch

        # 무시 패턴 먼저 확인
        for pattern in self.ignore_patterns:
            if fnmatch.fnmatch(layer_name.upper(), pattern.upper()):
                return -1

        # 높은 우선순위 확인
        for pattern in self.high_priority_patterns:
            if fnmatch.fnmatch(layer_name.upper(), pattern.upper()):
                return 2

        # 낮은 우선순위 확인
        for pattern in self.low_priority_patterns:
            if fnmatch.fnmatch(layer_name.upper(), pattern.upper()):
                return 0

        # 기본 우선순위
        return 1

    def should_ignore(self, layer_name: str) -> bool:
        """레이어를 무시해야 하는지 확인"""
        return self.get_priority(layer_name) == -1


@dataclass
class ComparisonConfig:
    """통합 비교 설정

    도면 비교에 필요한 모든 설정을 통합 관리합니다.

    Attributes:
        sensitivity: 민감도 설정
        layer_priority: 레이어 우선순위 설정
        expand_blocks: 블록 확장 여부
        use_spatial_index: 공간 인덱싱 사용 여부
        use_ocr: OCR 텍스트 인식 사용 여부
        ocr_language: OCR 언어 설정
        ocr_confidence_threshold: OCR 신뢰도 임계값 (P3-5). 기본 0.7 (70%)
        max_entities: 최대 처리 엔티티 수 (0=무제한)
        report_format: 보고서 형식 (json, html, excel)
    """

    sensitivity: SensitivityConfig = field(default_factory=SensitivityConfig)
    layer_priority: LayerPriorityConfig = field(default_factory=LayerPriorityConfig)
    # Phase Q3 (RV-20260509-002) — default flipped False → True so block
    # geometry changes (LINE/CIRCLE/POLYLINE inside block definitions) are
    # detected. Phase O Commit 2 only fingerprinted block-internal TEXT;
    # geometry changes were silent dropped under the False default. User
    # report: "변경사항 미탐지가 많다." Set False explicitly to revert to
    # the legacy faster path (block child entities skipped).
    expand_blocks: bool = True
    use_spatial_index: bool = True
    use_ocr: bool = False
    ocr_language: str = "kor+eng"
    ocr_confidence_threshold: float = 0.7  # Phase 3 P3-5: 70% 기본 임계값
    max_entities: int = 0
    report_format: str = "json"
    large_drawing_mode: str = "auto"
    large_entity_threshold: int = 100000
    max_change_records_in_memory: int = 50000
    near_match_index: str = "auto"

    def to_dict(self) -> Dict[str, Any]:
        """설정을 딕셔너리로 변환"""
        return {
            "sensitivity": self.sensitivity.to_dict(),
            "layer_priority": self.layer_priority.to_dict(),
            "expand_blocks": self.expand_blocks,
            "use_spatial_index": self.use_spatial_index,
            "use_ocr": self.use_ocr,
            "ocr_language": self.ocr_language,
            "ocr_confidence_threshold": self.ocr_confidence_threshold,
            "max_entities": self.max_entities,
            "report_format": self.report_format,
            "large_drawing_mode": self.large_drawing_mode,
            "large_entity_threshold": self.large_entity_threshold,
            "max_change_records_in_memory": self.max_change_records_in_memory,
            "near_match_index": self.near_match_index,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ComparisonConfig":
        """딕셔너리에서 설정 생성"""
        sensitivity_data = data.get("sensitivity", {})
        layer_priority_data = data.get("layer_priority", {})

        return cls(
            sensitivity=SensitivityConfig.from_dict(sensitivity_data),
            layer_priority=LayerPriorityConfig.from_dict(layer_priority_data),
            expand_blocks=data.get("expand_blocks", True),  # Phase Q3 default flipped
            use_spatial_index=data.get("use_spatial_index", True),
            use_ocr=data.get("use_ocr", False),
            ocr_language=data.get("ocr_language", "kor+eng"),
            ocr_confidence_threshold=data.get("ocr_confidence_threshold", 0.7),
            max_entities=data.get("max_entities", 0),
            report_format=data.get("report_format", "json"),
            large_drawing_mode=data.get("large_drawing_mode", "auto"),
            large_entity_threshold=data.get("large_entity_threshold", 100000),
            max_change_records_in_memory=data.get("max_change_records_in_memory", 50000),
            near_match_index=data.get("near_match_index", "auto"),
        )

    @classmethod
    def from_yaml(cls, yaml_path: str | Path) -> "ComparisonConfig":
        """YAML 파일에서 설정 로드

        Args:
            yaml_path: YAML 설정 파일 경로

        Returns:
            ComparisonConfig 인스턴스

        Raises:
            FileNotFoundError: 파일이 존재하지 않는 경우
            yaml.YAMLError: YAML 파싱 오류
        """
        path = Path(yaml_path)
        if not path.exists():
            raise FileNotFoundError(f"설정 파일을 찾을 수 없습니다: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if data is None:
            return cls()

        return cls.from_dict(data)

    def to_yaml(self, yaml_path: str | Path) -> None:
        """설정을 YAML 파일로 저장

        Args:
            yaml_path: 저장할 YAML 파일 경로
        """
        path = Path(yaml_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(
                self.to_dict(),
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

    @classmethod
    def get_default(cls) -> "ComparisonConfig":
        """기본 설정 반환"""
        return cls()

    @classmethod
    def get_strict(cls) -> "ComparisonConfig":
        """엄격한 설정 (높은 민감도) 반환"""
        return cls(
            sensitivity=SensitivityConfig(
                coordinate_precision=2,       # 0.01mm 정밀도
                dimension_abs_threshold=0.1,  # 0.1mm
                dimension_rel_threshold=0.0001,  # 0.01%
                position_threshold=0.1,       # 0.1mm
                rotation_threshold=0.01,      # 0.01°
                scale_threshold=0.001,        # 0.1%
                near_match_radius=5.0,        # 5mm
            )
        )

    @classmethod
    def get_relaxed(cls) -> "ComparisonConfig":
        """완화된 설정 (낮은 민감도) 반환"""
        return cls(
            sensitivity=SensitivityConfig(
                coordinate_precision=0,       # 1mm 정밀도
                dimension_abs_threshold=5.0,  # 5mm
                dimension_rel_threshold=0.01, # 1%
                position_threshold=5.0,       # 5mm
                rotation_threshold=1.0,       # 1°
                scale_threshold=0.05,         # 5%
                near_match_radius=20.0,       # 20mm
            )
        )

    @classmethod
    def from_preset(cls, preset: SensitivityPreset) -> "ComparisonConfig":
        """프리셋에서 비교 설정 생성 (Phase 3+ QW-1)

        Args:
            preset: 민감도 프리셋 (STRICT, NORMAL, RELAXED)

        Returns:
            해당 프리셋에 맞는 ComparisonConfig 인스턴스

        Example:
            >>> config = ComparisonConfig.from_preset(SensitivityPreset.STRICT)
            >>> config.sensitivity.position_threshold
            0.1
        """
        if preset == SensitivityPreset.STRICT:
            return cls.get_strict()
        elif preset == SensitivityPreset.RELAXED:
            return cls.get_relaxed()
        else:  # NORMAL
            return cls.get_default()

    @classmethod
    def from_preset_string(cls, preset_name: str) -> "ComparisonConfig":
        """문자열에서 프리셋 기반 비교 설정 생성 (Phase 3+ QW-1)

        UI에서 문자열로 프리셋을 선택할 때 유용합니다.

        Args:
            preset_name: 프리셋 이름 ("strict", "normal", "relaxed")

        Returns:
            해당 프리셋에 맞는 ComparisonConfig 인스턴스

        Example:
            >>> config = ComparisonConfig.from_preset_string("strict")
            >>> config.sensitivity.position_threshold
            0.1
        """
        preset = SensitivityPreset.from_string(preset_name)
        return cls.from_preset(preset)


# 전역 기본 설정 캐시
_DEFAULT_CONFIG: Optional[ComparisonConfig] = None


def get_default_config() -> ComparisonConfig:
    """전역 기본 설정 반환 (캐싱)"""
    global _DEFAULT_CONFIG
    if _DEFAULT_CONFIG is None:
        _DEFAULT_CONFIG = ComparisonConfig.get_default()
    return _DEFAULT_CONFIG


def load_config(yaml_path: Optional[str | Path] = None) -> ComparisonConfig:
    """설정 로드

    Args:
        yaml_path: YAML 설정 파일 경로. None이면 기본 설정 반환

    Returns:
        ComparisonConfig 인스턴스
    """
    if yaml_path is None:
        return get_default_config()
    return ComparisonConfig.from_yaml(yaml_path)


def clear_config_cache() -> None:
    """설정 캐시 초기화"""
    global _DEFAULT_CONFIG
    _DEFAULT_CONFIG = None
