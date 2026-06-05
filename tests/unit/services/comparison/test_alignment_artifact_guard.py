"""Phase P (RV-20260508-013) — alignment artifact guard 회귀 가드.

Phase O2 의 ``_is_pure_alignment_artifact`` 가 RANSAC alignment 와
일치하는 모든 변경을 흡수하던 동작을 두 단계 guard 로 보호:

1. ``inlier_ratio < strict_threshold`` → artifact 흡수 비활성
2. ``alignment_protect_structural_layers=True`` + structural layer →
   alignment 흡수 거부 (사용자 의도 zone-level shift 보존)

테스트 시나리오 (사용자 보고와 직결):
- 한 zone 의 보 50mm 이동: 영향 받은 entity 들의 변경이 alignment artifact
  로 분류되어 silent drop 되던 회귀 차단
- 한국어 layer ("기둥-1F") 도 동일 보호 (Phase P SSoT 효과)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import pytest

from src.services.comparison.dxf_comparator import DxfComparator
from src.services.comparison.global_alignment import RigidTransform


@dataclass
class _ToyChange:
    """Minimal DxfChange surface used by ``_is_pure_alignment_artifact``.

    WI-20260509-006: Phase Q-FU2 work on ``dxf_comparator.py`` started
    accessing ``change.layer`` directly (production code now reads it for
    layer bucketing in ``_bucket_for(by_layer, change.layer)`` and elsewhere).
    Existing tests pass layer through ``metadata={"layer": ...}``; mirror
    that onto a top-level ``.layer`` field via ``__post_init__`` so both
    access patterns work without touching every existing instantiation.
    """

    location: Optional[Tuple[float, float]]
    metadata: Dict[str, Any] = field(default_factory=dict)
    layer: str = ""

    def __post_init__(self) -> None:
        if not self.layer and isinstance(self.metadata, dict):
            self.layer = str(self.metadata.get("layer", "") or "")


def _make_comparator(
    strict_inlier: float = 0.85,
    protect_structural: bool = True,
    position: float = 1.0,
) -> DxfComparator:
    cmp = DxfComparator()
    cmp.sensitivity["alignment_strict_inlier_ratio"] = strict_inlier
    cmp.sensitivity["alignment_protect_structural_layers"] = protect_structural
    cmp.sensitivity["position"] = position
    return cmp


def _alignment(
    dx: float = 50.0,
    dy: float = 0.0,
    inlier_ratio: float = 1.0,
) -> RigidTransform:
    return RigidTransform(
        dx=dx,
        dy=dy,
        theta_rad=0.0,
        inlier_ratio=inlier_ratio,
        candidate_count=20,
    )


class TestInlierRatioGuard:
    """Guard 1 — inlier_ratio 미달 시 alignment 흡수 거부."""

    def test_high_inlier_alignment_absorbs_matched_displacement(self) -> None:
        """inlier 95% — 도면 전체 시프트 신뢰. artifact 로 분류 (회귀 가드)."""
        cmp = _make_comparator(strict_inlier=0.85)
        # B 가 A 대비 +50mm shift. dx_displacement = +50, alignment.dx = -50 (B→A)
        d = _ToyChange(location=(100.0, 100.0), metadata={"layer": "MISC"})
        a = _ToyChange(location=(150.0, 100.0), metadata={"layer": "MISC"})
        align = _alignment(dx=-50.0, inlier_ratio=0.95)
        assert cmp._is_pure_alignment_artifact(d, a, align) is True

    def test_low_inlier_alignment_preserves_change(self) -> None:
        """inlier 60% — 부분 시프트 의심. 변경 보존 (Phase P 회복)."""
        cmp = _make_comparator(strict_inlier=0.85)
        d = _ToyChange(location=(100.0, 100.0), metadata={"layer": "MISC"})
        a = _ToyChange(location=(150.0, 100.0), metadata={"layer": "MISC"})
        align = _alignment(dx=-50.0, inlier_ratio=0.60)
        assert cmp._is_pure_alignment_artifact(d, a, align) is False

    def test_inlier_at_threshold_absorbs(self) -> None:
        """inlier 정확히 threshold (0.85) — absorb (>= 임계)."""
        cmp = _make_comparator(strict_inlier=0.85)
        d = _ToyChange(location=(100.0, 100.0), metadata={"layer": "MISC"})
        a = _ToyChange(location=(150.0, 100.0), metadata={"layer": "MISC"})
        align = _alignment(dx=-50.0, inlier_ratio=0.85)
        assert cmp._is_pure_alignment_artifact(d, a, align) is True


class TestStructuralLayerGuard:
    """High-inlier global shift 케이스 — Codex P1 회귀 검증.

    초기 P1 패치는 structural layer (BEAM/COL/기둥) 의 모든 alignment-
    matched 변경을 unconditional 보존하여 fixture 03 (전체 도면 0.5mm
    시프트, 변경 0건) 에서 5건의 false-positive 를 만들었음. 수정:
    structural-layer guard 를 제거 (inlier_ratio guard 단독). 사용자의
    "한 zone 만 시프트" 케이스는 inlier_ratio < 0.85 로 잡힘.
    """

    @pytest.mark.parametrize(
        "layer",
        ["기둥-1F", "BEAM_MAIN", "S-COL-EXIST", "보", "BRACE-3F", "GIRDER",
         "MISC", "TEXT_GENERAL", "DEFPOINTS", "DIM-LAYER"],
    )
    def test_high_inlier_global_shift_absorbed_regardless_of_layer(
        self, layer: str
    ) -> None:
        """High inlier (=0.95) → 진짜 글로벌 시프트. 모든 layer 에 대해
        suppress (Codex P1 회귀 가드: structural layer 도 흡수)."""
        cmp = _make_comparator(protect_structural=True, strict_inlier=0.85)
        d = _ToyChange(location=(100.0, 100.0), metadata={"layer": layer})
        a = _ToyChange(location=(150.0, 100.0), metadata={"layer": layer})
        align = _alignment(dx=-50.0, inlier_ratio=0.95)
        assert cmp._is_pure_alignment_artifact(d, a, align) is True, (
            f"layer={layer} 에서도 진짜 글로벌 시프트는 alignment artifact 로 흡수"
        )

    @pytest.mark.parametrize(
        "layer", ["기둥-1F", "BEAM", "MISC", "TEXT_GENERAL"],
    )
    def test_low_inlier_partial_shift_preserved_regardless_of_layer(
        self, layer: str
    ) -> None:
        """Low inlier (0.6) → 부분 시프트 의심. 모든 layer 에 대해 보존
        (사용자 의도된 zone-level shift 회복)."""
        cmp = _make_comparator(strict_inlier=0.85)
        d = _ToyChange(location=(100.0, 100.0), metadata={"layer": layer})
        a = _ToyChange(location=(150.0, 100.0), metadata={"layer": layer})
        align = _alignment(dx=-50.0, inlier_ratio=0.60)
        assert cmp._is_pure_alignment_artifact(d, a, align) is False


class TestGuardInteractions:
    """inlier_ratio guard 가 다른 path 와 상호작용."""

    def test_low_inlier_with_legacy_strict_zero_legacy_behavior(self) -> None:
        """``alignment_strict_inlier_ratio=0.0`` 이면 모든 inlier 흡수."""
        cmp = _make_comparator(strict_inlier=0.0, protect_structural=False)
        d = _ToyChange(location=(100.0, 100.0), metadata={"layer": "BEAM"})
        a = _ToyChange(location=(150.0, 100.0), metadata={"layer": "BEAM"})
        align = _alignment(dx=-50.0, inlier_ratio=0.10)
        assert cmp._is_pure_alignment_artifact(d, a, align) is True

    def test_displacement_outside_position_threshold_not_artifact(self) -> None:
        """변위가 alignment 와 일치하지 않으면 artifact 아님 (기존 path)."""
        cmp = _make_comparator(strict_inlier=0.85)
        d = _ToyChange(location=(100.0, 100.0), metadata={"layer": "MISC"})
        a = _ToyChange(location=(200.0, 100.0), metadata={"layer": "MISC"})
        align = _alignment(dx=-50.0, inlier_ratio=0.95)
        assert cmp._is_pure_alignment_artifact(d, a, align) is False

    def test_no_location_returns_false(self) -> None:
        """location=None 이면 artifact 아님."""
        cmp = _make_comparator()
        d = _ToyChange(location=None, metadata={"layer": "MISC"})
        a = _ToyChange(location=(150.0, 100.0), metadata={"layer": "MISC"})
        align = _alignment(dx=-50.0, inlier_ratio=0.95)
        assert cmp._is_pure_alignment_artifact(d, a, align) is False


@dataclass
class _Anchor:
    """Minimal NormalizedEntity surface for alignment-input filtering."""

    layer: str = "BEAM"
    location: Tuple[float, float] = (0.0, 0.0)


def _entities(count: int) -> Dict[str, Any]:
    return {"LINE": [_Anchor(location=(float(i), 0.0)) for i in range(count)]}


class TestLowConfidenceFlag:
    """P0-1 — silently-rejected low-inlier alignment becomes a user signal."""

    def test_flag_set_when_rejected_with_ample_geometry(self, monkeypatch) -> None:
        """estimator returns None despite >=8 anchors → low-confidence flag."""
        import src.services.comparison.global_alignment as ga

        monkeypatch.setattr(ga, "estimate_rigid_transform", lambda a, b: None)
        cmp = _make_comparator()
        out = cmp._estimate_global_alignment(_entities(5), _entities(5))  # 10 total
        assert out is None
        assert cmp._alignment_low_confidence is True

    def test_no_flag_when_too_few_anchors(self, monkeypatch) -> None:
        """Below the anchor floor an empty result is 'too little data',
        not low confidence — don't cry wolf."""
        import src.services.comparison.global_alignment as ga

        monkeypatch.setattr(ga, "estimate_rigid_transform", lambda a, b: None)
        cmp = _make_comparator()
        out = cmp._estimate_global_alignment(_entities(2), _entities(2))  # 4 total
        assert out is None
        assert cmp._alignment_low_confidence is False

    def test_no_flag_when_alignment_succeeds(self, monkeypatch) -> None:
        import src.services.comparison.global_alignment as ga

        align = _alignment(dx=-50.0, inlier_ratio=0.95)
        monkeypatch.setattr(ga, "estimate_rigid_transform", lambda a, b: align)
        cmp = _make_comparator()
        out = cmp._estimate_global_alignment(_entities(5), _entities(5))
        assert out is align
        assert cmp._alignment_low_confidence is False

    def test_flag_written_to_result_metadata(self, monkeypatch) -> None:
        """The call site copies the transient flag into result.metadata."""

        def _fake(self_, a, b):
            self_._alignment_low_confidence = True
            return None

        monkeypatch.setattr(DxfComparator, "_estimate_global_alignment", _fake)
        cmp = _make_comparator()
        result = cmp.compare_with_modified_detection({}, {})
        assert result.metadata.get("alignment_low_confidence") is True

    def test_clean_run_has_no_low_confidence_key(self, monkeypatch) -> None:
        """No alignment + no rejection → key absent (honest clean run)."""

        def _fake(self_, a, b):
            self_._alignment_low_confidence = False
            return None

        monkeypatch.setattr(DxfComparator, "_estimate_global_alignment", _fake)
        cmp = _make_comparator()
        result = cmp.compare_with_modified_detection({}, {})
        assert "alignment_low_confidence" not in result.metadata
