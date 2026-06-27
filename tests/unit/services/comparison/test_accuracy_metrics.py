# -*- coding: utf-8 -*-
"""accuracy_metrics 단위 테스트 (Phase O1).

precision/recall/F1 계산, DxfChange/ChangeRecord 어댑터, manifest
JSON round-trip 검증.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple

import pytest

from src.services.comparison.accuracy_metrics import (
    AccuracyMetrics,
    ExpectedChange,
    MatchReport,
    _entity_types_compatible,
    compute_metrics,
    expected_change_from_dict,
    expected_change_to_dict,
    match_changes_to_truth,
)
from src.services.comparison.base import ChangeRecord, ChangeType
from src.services.comparison.dxf_comparator import DxfChange, DxfChangeType

# ---------------------------------------------------------------------------
# 테스트용 헬퍼 — 실제 DxfChange / ChangeRecord 인스턴스 빌더
# ---------------------------------------------------------------------------


def _dxf(
    *,
    location: Tuple[float, float],
    change_type: DxfChangeType = DxfChangeType.MODIFIED,
    layer: str = "BEAM",
    entity_type: str = "LINE",
    change_category: Optional[str] = None,
) -> DxfChange:
    return DxfChange(
        entity_type=entity_type,
        layer=layer,
        change_type=change_type,
        location=location,
        change_category=change_category,
    )


def _pdf_region(
    *,
    x: float,
    y: float,
    w: float = 10.0,
    h: float = 10.0,
    region_id: int = 0,
) -> ChangeRecord:
    """PDF visual diff 결과를 모방."""
    return ChangeRecord(
        key=f"Region_{region_id}",
        change_type=ChangeType.MODIFIED,
        location=f"({x}, {y}) - ({x + w}, {y + h})",
        metadata={"x": x, "y": y, "w": w, "h": h, "id": region_id},
    )


# ---------------------------------------------------------------------------
# 시나리오 1 — 1:1 정확 매칭 (precision=recall=1.0)
# ---------------------------------------------------------------------------


def test_perfect_match_dxf():
    predicted = [
        _dxf(location=(100.0, 200.0)),
        _dxf(location=(300.0, 400.0)),
    ]
    truth = [
        ExpectedChange(location=(100.0, 200.0), change_type="modified"),
        ExpectedChange(location=(300.0, 400.0), change_type="modified"),
    ]

    report = match_changes_to_truth(predicted, truth, location_tol=0.5)
    metrics = compute_metrics(report)

    assert report.tp_count == 2
    assert report.fp_count == 0
    assert report.fn_count == 0
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.f1 == 1.0
    assert metrics.noise_ratio == 0.0


def test_perfect_match_pdf():
    predicted = [
        _pdf_region(x=100, y=200, w=20, h=20, region_id=0),  # centroid (110, 210)
        _pdf_region(x=300, y=400, w=10, h=10, region_id=1),  # centroid (305, 405)
    ]
    truth = [
        ExpectedChange(location=(110.0, 210.0), change_type="modified"),
        ExpectedChange(location=(305.0, 405.0), change_type="modified"),
    ]

    report = match_changes_to_truth(predicted, truth, location_tol=1.0)
    metrics = compute_metrics(report)

    assert metrics.precision == 1.0
    assert metrics.recall == 1.0


# ---------------------------------------------------------------------------
# 시나리오 2 — 예측이 truth 모두 포함 + 노이즈 5건 추가
# ---------------------------------------------------------------------------


def test_noise_inflates_false_positives():
    truth = [
        ExpectedChange(location=(100.0, 100.0), change_type="modified"),
        ExpectedChange(location=(200.0, 200.0), change_type="modified"),
    ]
    # 진짜 변경 2건 + 노이즈 5건 (멀리 떨어진 위치)
    predicted = [
        _dxf(location=(100.0, 100.0)),
        _dxf(location=(200.0, 200.0)),
        _dxf(location=(900.0, 100.0)),
        _dxf(location=(900.0, 200.0)),
        _dxf(location=(900.0, 300.0)),
        _dxf(location=(900.0, 400.0)),
        _dxf(location=(900.0, 500.0)),
    ]

    report = match_changes_to_truth(predicted, truth, location_tol=1.0)
    metrics = compute_metrics(report)

    assert report.tp_count == 2
    assert report.fp_count == 5
    assert report.fn_count == 0
    assert metrics.recall == 1.0
    assert metrics.precision == pytest.approx(2.0 / 7.0)
    assert metrics.noise_ratio == pytest.approx(5.0 / 7.0)


# ---------------------------------------------------------------------------
# 시나리오 3 — type 불일치 strict 모드
# ---------------------------------------------------------------------------


def test_strict_type_rejects_mismatched():
    truth = [ExpectedChange(location=(100.0, 100.0), change_type="added")]
    predicted = [_dxf(location=(100.0, 100.0), change_type=DxfChangeType.MODIFIED)]

    # strict=False → 위치만 맞으면 매칭
    loose = match_changes_to_truth(predicted, truth, location_tol=0.5, strict_type=False)
    assert loose.tp_count == 1

    # strict=True → type 불일치 → FN+FP
    strict = match_changes_to_truth(predicted, truth, location_tol=0.5, strict_type=True)
    assert strict.tp_count == 0
    assert strict.fn_count == 1
    assert strict.fp_count == 1


def test_strict_type_modified_cosmetic_alias():
    """modified ↔ cosmetic은 strict 모드에서도 매칭 (Phase O3 cosmetic)."""
    truth = [ExpectedChange(location=(100.0, 100.0), change_type="cosmetic")]
    predicted = [
        _dxf(
            location=(100.0, 100.0),
            change_type=DxfChangeType.MODIFIED,
            change_category="cosmetic",
        )
    ]

    report = match_changes_to_truth(predicted, truth, location_tol=0.5, strict_type=True)
    assert report.tp_count == 1


# ---------------------------------------------------------------------------
# 시나리오 4 — location_tol 변동
# ---------------------------------------------------------------------------


def test_location_tolerance_zero_strict():
    truth = [ExpectedChange(location=(100.0, 100.0), change_type="modified")]
    # 0.5mm 시프트
    predicted = [_dxf(location=(100.5, 100.0))]

    report_strict = match_changes_to_truth(predicted, truth, location_tol=0.0)
    assert report_strict.tp_count == 0  # 0.5mm > 0 → 매칭 실패

    report_loose = match_changes_to_truth(predicted, truth, location_tol=1.0)
    assert report_loose.tp_count == 1


def test_per_truth_tolerance_overrides_default():
    """ExpectedChange.tolerance_mm 가 호출자 location_tol 보다 우선."""
    truth = [
        ExpectedChange(location=(100.0, 100.0), change_type="modified", tolerance_mm=10.0),
        ExpectedChange(location=(200.0, 200.0), change_type="modified", tolerance_mm=0.1),
    ]
    predicted = [
        _dxf(location=(105.0, 100.0)),  # 5mm shift — truth[0] 허용
        _dxf(location=(205.0, 200.0)),  # 5mm shift — truth[1] 거부
    ]

    report = match_changes_to_truth(predicted, truth, location_tol=1.0)
    assert report.tp_count == 1
    assert report.fn_count == 1
    assert report.fp_count == 1


# ---------------------------------------------------------------------------
# 시나리오 5 — 빈 입력 None 반환 (분모 0 방어)
# ---------------------------------------------------------------------------


def test_empty_predicted_and_truth():
    report = match_changes_to_truth([], [])
    metrics = compute_metrics(report)

    assert report.tp_count == 0
    assert report.fp_count == 0
    assert report.fn_count == 0
    assert metrics.precision is None
    assert metrics.recall is None
    assert metrics.f1 is None
    assert metrics.noise_ratio is None


def test_only_predicted_no_truth():
    """예측 5건, truth 0건 → precision=0, recall은 None."""
    predicted = [_dxf(location=(float(i), 0.0)) for i in range(5)]
    report = match_changes_to_truth(predicted, [])
    metrics = compute_metrics(report)

    assert metrics.precision == 0.0
    assert metrics.recall is None
    assert metrics.f1 is None
    assert metrics.noise_ratio == 1.0


def test_only_truth_no_predicted():
    """예측 0건, truth 3건 → recall=0, precision은 None."""
    truth = [ExpectedChange(location=(float(i), 0.0), change_type="modified") for i in range(3)]
    report = match_changes_to_truth([], truth)
    metrics = compute_metrics(report)

    assert metrics.recall == 0.0
    assert metrics.precision is None
    assert metrics.f1 is None
    assert metrics.noise_ratio is None


# ---------------------------------------------------------------------------
# 시나리오 6 — manifest YAML round-trip
# ---------------------------------------------------------------------------


def test_expected_change_dict_roundtrip():
    original = ExpectedChange(
        location=(123.4, 567.8),
        change_type="modified",
        layer="BEAM",
        entity_type="LINE",
        tolerance_mm=2.5,
        notes="단일 LINE 5mm 시프트",
    )
    restored = expected_change_from_dict(expected_change_to_dict(original))
    assert restored == original


def test_expected_change_from_dict_minimal():
    """필수 필드만 있어도 파싱 OK."""
    data = {"change_type": "added"}
    restored = expected_change_from_dict(data)
    assert restored.location is None
    assert restored.change_type == "added"
    assert restored.layer is None
    assert restored.tolerance_mm is None


def test_expected_change_from_dict_invalid_location():
    with pytest.raises(ValueError, match="location"):
        expected_change_from_dict({"location": [1.0], "change_type": "modified"})


# ---------------------------------------------------------------------------
# Adapter 검증 — PDF visual region location 추출
# ---------------------------------------------------------------------------


def test_pdf_change_record_centroid_extraction():
    """ChangeRecord.metadata 의 {x,y,w,h} 에서 centroid 추출 검증."""
    pdf_change = _pdf_region(x=100.0, y=200.0, w=40.0, h=20.0, region_id=0)
    truth = [ExpectedChange(location=(120.0, 210.0), change_type="modified")]

    report = match_changes_to_truth([pdf_change], truth, location_tol=0.5)
    assert report.tp_count == 1


def test_change_record_string_location_fallback():
    """ChangeRecord.location 이 ``"(123.4, 567.8) - ..."`` 문자열일 때."""
    cr = ChangeRecord(
        key="LegacyRegion",
        change_type=ChangeType.MODIFIED,
        location="(50.0, 75.0) - (60.0, 85.0)",
        metadata={},
    )
    truth = [ExpectedChange(location=(50.0, 75.0), change_type="modified")]
    report = match_changes_to_truth([cr], truth, location_tol=0.1)
    assert report.tp_count == 1


# ---------------------------------------------------------------------------
# Greedy 매칭 — 두 truth가 같은 예측 후보를 공유할 때 더 가까운 쪽이 우선
# ---------------------------------------------------------------------------


def test_greedy_matching_prefers_closer():
    """예측 1건이 truth 2개 후보 → 가까운 쪽 매칭 (greedy 순서 영향 검증)."""
    predicted = [_dxf(location=(100.0, 100.0))]
    truth = [
        ExpectedChange(location=(100.0, 100.0), change_type="modified"),  # 거리 0
        ExpectedChange(location=(100.5, 100.0), change_type="modified"),  # 거리 0.5
    ]

    report = match_changes_to_truth(predicted, truth, location_tol=1.0)
    # 첫 truth가 0거리로 우선 매칭 (greedy iteration order)
    assert report.tp_count == 1
    assert report.fn_count == 1
    # 매칭된 truth는 거리 0짜리
    matched_expected = report.true_positives[0][1]
    assert matched_expected.location == (100.0, 100.0)


# ---------------------------------------------------------------------------
# 시나리오 — 블록-속성 entity_type synonym (golden 07: block_reference == attrib)
# ---------------------------------------------------------------------------


def test_entity_types_compatible_block_family():
    # 같은 블록-속성 family는 호환 (엔진 block_reference == truth attrib)
    assert _entity_types_compatible("block_reference", "attrib")
    assert _entity_types_compatible("attrib", "insert")
    assert _entity_types_compatible("block_reference", "block_reference")  # exact
    # 비-family는 정확 일치만 (무차별 완화 금지)
    assert not _entity_types_compatible("line", "attrib")
    assert not _entity_types_compatible("text", "attrib")  # text는 family 밖
    assert _entity_types_compatible("line", "line")  # 비-family도 exact는 OK


def test_block_reference_prediction_matches_attrib_truth():
    """golden 07: 엔진이 block_reference로 검출한 블록 속성 변경이 attrib truth와 매칭."""
    predicted = [_dxf(location=(500.0, 400.0), entity_type="block_reference", layer="0")]
    truth = [
        ExpectedChange(
            location=(500.0, 400.0),
            change_type="modified",
            entity_type="attrib",
            layer="TEXT_LAYER",
            tolerance_mm=1.0,
        )
    ]
    report = match_changes_to_truth(predicted, truth, location_tol=1.0, strict_type=False)
    assert report.tp_count == 1
    assert report.fn_count == 0


def test_unrelated_entity_type_still_rejected():
    """비-family 타입(line)은 같은 위치라도 attrib truth와 매칭 안 됨 (과매칭 방지)."""
    predicted = [_dxf(location=(500.0, 400.0), entity_type="line")]
    truth = [
        ExpectedChange(
            location=(500.0, 400.0),
            change_type="modified",
            entity_type="attrib",
            tolerance_mm=1.0,
        )
    ]
    report = match_changes_to_truth(predicted, truth, location_tol=1.0, strict_type=False)
    assert report.tp_count == 0
    assert report.fn_count == 1


def test_block_family_still_respects_distance():
    """family 호환이어도 위치 게이트는 그대로 — 먼 block_reference는 비매칭."""
    predicted = [_dxf(location=(900.0, 900.0), entity_type="block_reference")]
    truth = [
        ExpectedChange(
            location=(500.0, 400.0),
            change_type="modified",
            entity_type="attrib",
            tolerance_mm=1.0,
        )
    ]
    report = match_changes_to_truth(predicted, truth, location_tol=1.0, strict_type=False)
    assert report.tp_count == 0
    assert report.fn_count == 1
