"""Phase Q-FU-2 (RV-20260510-001) — alignment artifact guard 의 layer-aware threshold.

Phase Q6 가 ``_is_significant_change`` 와 ``_analyze_change_details``
에 layer-aware threshold (structural layer 0.1mm vs default 1.0mm)
를 적용했지만 ``_is_pure_alignment_artifact`` 의 displacement vs
alignment 비교 threshold 는 여전히 ``position`` (1.0mm) 만 사용 →
구조 layer 의 sub-mm shift 가 alignment 흡수에 가려져 silent drop.

Q8 fixture 14 (structural_submm_shift) 가 expected_to_fail=True 로
임시 baseline 제외 → Codex Q8 round-1 의 "regression guard 가 가드
역할 못함" 지적의 두 번째 원인.

Q-FU-2: ``_is_pure_alignment_artifact`` 가 entity layer 기반으로
``_position_threshold_for_layer`` 호출 → 구조 layer 면 0.1mm 임계값
사용 → BEAM 0.5mm sub-mm shift 가 alignment 흡수되지 않고 보존됨.
"""
from __future__ import annotations

import pytest

from src.services.comparison.dxf_comparator import (
    DxfChange,
    DxfChangeType,
    DxfComparator,
)


class _StubAlignment:
    """RigidTransform-like minimal stub."""

    def __init__(self, dx, dy, inlier_ratio=1.0):
        self.dx = dx
        self.dy = dy
        self.inlier_ratio = inlier_ratio


def _make_change(layer, location, change_type=DxfChangeType.DELETED):
    return DxfChange(
        entity_type="LINE",
        layer=layer,
        change_type=change_type,
        location=location,
    )


@pytest.fixture
def comparator():
    """Default DxfComparator — Q6 default 가 활성된 상태 (structural=0.1)."""
    return DxfComparator()


class TestStructuralLayerNotSuppressed:
    """Q-FU-2 — 구조 layer 의 sub-mm shift 가 alignment 흡수 안 됨."""

    def test_beam_05mm_shift_preserved(self, comparator):
        """[Q-FU-2 핵심] BEAM 의 0.5mm shift + alignment dy=-0.083mm.
        |0.5 + (-0.083)| = 0.417mm > 0.1mm structural threshold →
        alignment artifact 아님 (False). Q-FU-1 까지는 default 1.0mm
        만 비교해 0.417 < 1.0 → True (artifact) → silent drop 했음."""
        d = _make_change("BEAM", (500.0, 400.0), DxfChangeType.DELETED)
        a = _make_change("BEAM", (500.0, 400.5), DxfChangeType.ADDED)
        alignment = _StubAlignment(dx=0.0, dy=-0.083, inlier_ratio=1.0)
        result = comparator._is_pure_alignment_artifact(d, a, alignment)
        assert result is False, (
            "BEAM (구조 layer) 의 0.5mm shift 는 0.1mm structural threshold "
            "초과 → alignment artifact 아님 → 보존되어야 함 (Q-FU-2)"
        )

    def test_korean_structural_layer_preserved(self, comparator):
        """한국어 구조 layer (기둥) 도 동일하게 적용."""
        d = _make_change("기둥-1F", (300.0, 200.0), DxfChangeType.DELETED)
        a = _make_change("기둥-1F", (300.3, 200.0), DxfChangeType.ADDED)
        alignment = _StubAlignment(dx=0.0, dy=0.0, inlier_ratio=1.0)
        # |0.3 + 0| = 0.3 > 0.1 → not artifact
        result = comparator._is_pure_alignment_artifact(d, a, alignment)
        assert result is False

    def test_structural_below_01mm_still_artifact(self, comparator):
        """구조 layer 라도 0.1mm 미만 displacement 는 alignment artifact 로 흡수.
        이게 정상 동작 — 진짜 registration noise 만 흡수."""
        d = _make_change("BEAM", (500.0, 400.0), DxfChangeType.DELETED)
        a = _make_change("BEAM", (500.0, 400.05), DxfChangeType.ADDED)
        alignment = _StubAlignment(dx=0.0, dy=-0.05, inlier_ratio=1.0)
        # |0.05 + (-0.05)| = 0 < 0.1mm → artifact
        result = comparator._is_pure_alignment_artifact(d, a, alignment)
        assert result is True, (
            "0.05mm shift + alignment 0.05mm 보정 → 잔차 0 → "
            "structural threshold 0.1mm 미만 → 정상 alignment artifact"
        )


class TestNonStructuralLayerUsesDefault:
    """Q-FU-2 — 비-구조 layer 는 default 1.0mm 적용."""

    def test_dimension_05mm_shift_suppressed(self, comparator):
        """DIMENSION 의 0.5mm shift + alignment 0 dy = 0.5mm 잔차.
        default 1.0mm 미만 → alignment artifact 로 흡수 (정상)."""
        d = _make_change("DIMENSION", (500.0, 400.0), DxfChangeType.DELETED)
        a = _make_change("DIMENSION", (500.0, 400.5), DxfChangeType.ADDED)
        alignment = _StubAlignment(dx=0.0, dy=0.0, inlier_ratio=1.0)
        result = comparator._is_pure_alignment_artifact(d, a, alignment)
        assert result is True, (
            "DIMENSION 의 0.5mm shift 는 default 1.0mm 미만 → alignment "
            "artifact 흡수 (회귀 가드)"
        )

    def test_dimension_15mm_shift_preserved(self, comparator):
        """DIMENSION 의 1.5mm shift 는 default 1.0mm 초과 → 보존."""
        d = _make_change("DIMENSION", (500.0, 400.0), DxfChangeType.DELETED)
        a = _make_change("DIMENSION", (500.0, 401.5), DxfChangeType.ADDED)
        alignment = _StubAlignment(dx=0.0, dy=0.0, inlier_ratio=1.0)
        result = comparator._is_pure_alignment_artifact(d, a, alignment)
        assert result is False


class TestInlierRatioGuardStillActive:
    """Q-FU-2 — Phase P 의 inlier_ratio guard 는 그대로 (Q-FU-2 가 회귀
    안 만드는지 확인)."""

    def test_low_inlier_ratio_skips_artifact_check(self, comparator):
        """inlier_ratio < 0.85 면 layer 와 무관하게 artifact 아님."""
        d = _make_change("DIMENSION", (500.0, 400.0), DxfChangeType.DELETED)
        a = _make_change("DIMENSION", (500.0, 400.05), DxfChangeType.ADDED)
        alignment = _StubAlignment(dx=0.0, dy=-0.05, inlier_ratio=0.5)
        # 0.05mm shift + alignment 보정 → 정상이면 artifact (true)
        # 그러나 inlier_ratio=0.5 < 0.85 → 부분 시프트 의심 → False
        result = comparator._is_pure_alignment_artifact(d, a, alignment)
        assert result is False


class TestFixture14Activation:
    """Q-FU-2 — fixture 14 (structural_submm_shift) 가 active 전환."""

    def test_fixture_14_no_longer_expected_to_fail(self):
        """fixture 14 의 truth.json 에서 expected_to_fail 제거됨."""
        import json
        from pathlib import Path

        truth_path = Path(
            "tests/data/comparison/golden/dxf/14_structural_submm_shift/truth.json"
        )
        data = json.loads(truth_path.read_text(encoding="utf-8"))
        assert data.get("expected_to_fail", False) is False, (
            "Q-FU-2: fixture 14 가 active baseline 으로 동작 — "
            "expected_to_fail=False"
        )


class TestEmptyOrMissingLayer:
    """Q-FU-2 — layer 비어있거나 None 인 edge case."""

    def test_empty_layer_uses_default(self, comparator):
        """layer="" → _position_threshold_for_layer fallback to default."""
        d = _make_change("", (500.0, 400.0), DxfChangeType.DELETED)
        a = _make_change("", (500.0, 400.5), DxfChangeType.ADDED)
        alignment = _StubAlignment(dx=0.0, dy=0.0, inlier_ratio=1.0)
        result = comparator._is_pure_alignment_artifact(d, a, alignment)
        # layer 없음 → default 1.0mm → 0.5 < 1.0 → artifact (suppressed)
        assert result is True
