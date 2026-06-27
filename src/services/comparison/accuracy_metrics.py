# -*- coding: utf-8 -*-
"""정확도 측정 유틸 (Phase O — 노이즈 필터 강화).

도면 비교 결과(`DxfChange` 또는 `ChangeRecord`)를 사람이 라벨링한
ground-truth(`ExpectedChange`)와 매칭해 precision / recall / F1을
산출한다. O1 commit의 핵심 — 이 metric이 있어야 O2-O5 개선 효과를
객관적으로 측정·회귀 감시 가능.

매칭 알고리즘은 단순 greedy nearest-neighbor (location 기반). O2의
hybrid Hungarian과 동일 인터페이스를 재사용할 수 있도록 candidate
filter → 1:1 assignment 구조로 분리.

PDF/DXF 양쪽 입력 형식을 모두 지원:
- `DxfChange.location` → tuple[float, float]
- `ChangeRecord.metadata` → {"x", "y", "w", "h"} bbox centroid 또는
  `ChangeRecord.location` 의 "(x, y) - ..." 문자열 파싱

Author: TEKLA_MCP Team
Phase: O1 (정확도 측정 인프라)
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExpectedChange:
    """ground-truth 변경 한 건.

    Attributes:
        location: (x, y) world / image-pixel 좌표. ``None`` 이면 위치-
            free 매칭 (entity_type + layer 만으로 1:1).
        change_type: "added" | "deleted" | "modified" | "cosmetic".
            대소문자 구분 없음, 비교 시 lower-case.
        layer: 레이어 이름. ``None`` 이면 layer 무시.
        entity_type: "LINE", "CIRCLE", ... DXF 전용. PDF visual diff
            는 "REGION" 또는 ``None``.
        tolerance_mm: 위치 매칭 허용오차. ``None`` 이면 호출자
            기본값 사용.
        notes: 사람이 읽는 코멘트 (matching에 영향 없음).
    """

    location: Optional[Tuple[float, float]]
    change_type: str
    layer: Optional[str] = None
    entity_type: Optional[str] = None
    tolerance_mm: Optional[float] = None
    notes: str = ""


@dataclass(frozen=True)
class _Predicted:
    """내부용 정규화된 예측 어댑터.

    DxfChange / ChangeRecord 어느 쪽이 들어와도 같은 shape으로 비교.
    """

    raw: Any  # 원본 객체 (보고서에 다시 노출용)
    location: Optional[Tuple[float, float]]
    change_type: str  # lower-case
    layer: Optional[str]
    entity_type: Optional[str]
    change_category: Optional[str]


@dataclass
class MatchReport:
    """매칭 결과 보고서.

    각 리스트는 사람이 디버깅하기 위한 원본 보존:
    - true_positives: (predicted, expected) 페어. 매칭된 변경.
    - false_positives: 예측에만 있는 변경 (오탐).
    - false_negatives: ground-truth 에만 있는 변경 (누락).
    """

    true_positives: List[Tuple[Any, ExpectedChange]] = field(default_factory=list)
    false_positives: List[Any] = field(default_factory=list)
    false_negatives: List[ExpectedChange] = field(default_factory=list)
    location_tol_used: float = 1.0
    strict_type: bool = False

    @property
    def tp_count(self) -> int:
        return len(self.true_positives)

    @property
    def fp_count(self) -> int:
        return len(self.false_positives)

    @property
    def fn_count(self) -> int:
        return len(self.false_negatives)


@dataclass(frozen=True)
class AccuracyMetrics:
    """매칭 보고서로부터 산출한 정확도 metric.

    분모가 0인 경우 (예: 예측도 truth도 모두 비어있음) 해당 metric은
    ``None`` — 0.0 과 명확히 구분 (분모 0 / 모두 0 일치는 다른 의미).
    """

    precision: Optional[float]
    recall: Optional[float]
    f1: Optional[float]
    tp_count: int
    fp_count: int
    fn_count: int
    noise_ratio: Optional[float]  # FP / (TP + FP) — "예측 중 노이즈 비율"

    def to_dict(self) -> dict:
        return {
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "tp_count": self.tp_count,
            "fp_count": self.fp_count,
            "fn_count": self.fn_count,
            "noise_ratio": self.noise_ratio,
        }


# ---------------------------------------------------------------------------
# Adapters — DxfChange / ChangeRecord → _Predicted
# ---------------------------------------------------------------------------


_LOCATION_STR_RE = re.compile(r"\(\s*([-+]?\d+(?:\.\d+)?)\s*,\s*([-+]?\d+(?:\.\d+)?)")


def _coerce_change_type(value: Any) -> str:
    """Enum / str 양쪽을 lower-case 문자열로 정규화."""
    if value is None:
        return ""
    if isinstance(value, Enum):
        return str(value.value).lower()
    return str(value).lower()


def _extract_xy_from_metadata(meta: Any) -> Optional[Tuple[float, float]]:
    """PDF visual region metadata({x,y,w,h}) → centroid."""
    if not isinstance(meta, dict):
        return None
    x = meta.get("x")
    y = meta.get("y")
    if x is None or y is None:
        return None
    try:
        cx = float(x) + float(meta.get("w", 0)) / 2.0
        cy = float(y) + float(meta.get("h", 0)) / 2.0
    except (TypeError, ValueError):
        return None
    return (cx, cy)


def _extract_xy_from_str(text: Any) -> Optional[Tuple[float, float]]:
    """``"(123.4, 567.8) - (200.0, 600.0)"`` → (123.4, 567.8)."""
    if not isinstance(text, str):
        return None
    m = _LOCATION_STR_RE.search(text)
    if not m:
        return None
    try:
        return (float(m.group(1)), float(m.group(2)))
    except ValueError:
        return None


def _normalise_predicted(obj: Any) -> _Predicted:
    """DxfChange / ChangeRecord / dict 어느 쪽이든 _Predicted로 변환.

    location 추출 우선순위:
    1. ``obj.location`` 이 tuple → 그대로 (DxfChange)
    2. ``obj.metadata`` 의 {x,y,w,h} → centroid (PDF visual region —
       string location 보다 우선. string 은 corner 좌표만 담고 있어
       centroid 와 차이가 크기 때문)
    3. ``obj.location`` 이 str → 정규식 파싱 (legacy / 메타 없는 경우)
    4. None (text-only change)
    """
    raw_loc = getattr(obj, "location", None)
    location: Optional[Tuple[float, float]] = None

    if isinstance(raw_loc, tuple) and len(raw_loc) >= 2:
        try:
            location = (float(raw_loc[0]), float(raw_loc[1]))
        except (TypeError, ValueError):
            location = None

    if location is None:
        location = _extract_xy_from_metadata(getattr(obj, "metadata", None))

    if location is None and isinstance(raw_loc, str):
        location = _extract_xy_from_str(raw_loc)

    return _Predicted(
        raw=obj,
        location=location,
        change_type=_coerce_change_type(getattr(obj, "change_type", None)),
        layer=getattr(obj, "layer", None),
        entity_type=getattr(obj, "entity_type", None),
        change_category=getattr(obj, "change_category", None),
    )


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _euclid(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _types_compatible(predicted_type: str, expected_type: str) -> bool:
    """DXF MODIFIED == cosmetic (같은 좌표의 cosmetic 변경은 MODIFIED).

    ground-truth 가 "cosmetic" 이면 예측도 MODIFIED + change_category=
    cosmetic 이어야 한다 (Phase O3에서 도입). O1 단계에서는 일단
    type 문자열 정확 일치 또는 modified ↔ cosmetic alias 만 허용.
    """
    if predicted_type == expected_type:
        return True
    if {predicted_type, expected_type} <= {"modified", "cosmetic"}:
        return True
    return False


# Block-attribute family: the canonical engine surfaces a block-internal ATTRIB
# value change as a `block_reference` change at the attribute's location, while
# golden truth (authored from the user's view) names it `attrib`. These describe
# the same detected change, so treat the block/insert/attrib family as one
# entity-type bucket. Verified on golden 07: the engine detects the change at
# distance 0.000 and the only disqualifier was this name gap (adapt_prediction's
# own comment flagged "ATTRIB vs block_reference stays visible"). Scoring-only —
# the detection engine is unchanged; this corrects a false-negative mis-score.
_ENTITY_TYPE_SYNONYMS = frozenset({"attrib", "attdef", "insert", "block_reference", "block"})


def _entity_types_compatible(predicted_type: str, expected_type: str) -> bool:
    """Exact match, or both within the block-attribute family (see set above)."""
    if predicted_type == expected_type:
        return True
    return predicted_type in _ENTITY_TYPE_SYNONYMS and expected_type in _ENTITY_TYPE_SYNONYMS


def match_changes_to_truth(
    predicted: Iterable[Any],
    truth: Sequence[ExpectedChange],
    *,
    location_tol: float = 1.0,
    strict_type: bool = False,
    require_layer_match: bool = False,
) -> MatchReport:
    """예측 변경과 ground-truth 를 1:1 매칭 → MatchReport.

    매칭 알고리즘 (greedy):
    1. 모든 예측을 _Predicted로 정규화
    2. 각 truth마다 후보 예측 수집:
       - location 거리 ≤ tolerance (truth.tolerance_mm 우선, 없으면
         호출자 location_tol)
       - strict_type=True 이면 change_type 일치 필수, 아니면 후보에는
         넣되 비용에 페널티 (현재 O1에서는 strict_type만 분기)
       - require_layer_match=True 이고 truth.layer 명시 → 일치 필수
    3. 후보들 중 거리 최소를 선택, 매칭 후 양쪽 제거 (greedy)
    4. 매칭되지 않은 truth → FN, 매칭되지 않은 예측 → FP

    Returns:
        MatchReport with TP/FP/FN 분리.
    """
    predictions = [_normalise_predicted(p) for p in predicted]
    available = list(range(len(predictions)))
    tp: List[Tuple[Any, ExpectedChange]] = []
    fn: List[ExpectedChange] = []

    for expected in truth:
        tol = expected.tolerance_mm if expected.tolerance_mm is not None else location_tol
        best_idx: Optional[int] = None
        best_dist = float("inf")

        for idx in available:
            p = predictions[idx]

            # Type filter (strict 모드에서만 hard reject)
            if strict_type and not _types_compatible(p.change_type, expected.change_type.lower()):
                continue

            # Layer filter
            if require_layer_match and expected.layer is not None:
                if (p.layer or "") != expected.layer:
                    continue

            # Entity type filter (truth 명시 시) — block-attribute family compatible
            if expected.entity_type is not None and p.entity_type is not None:
                if not _entity_types_compatible(p.entity_type, expected.entity_type):
                    continue

            # Location distance
            if expected.location is None:
                # 위치-free 매칭 — type/layer/entity_type 만으로 OK
                dist = 0.0
            elif p.location is None:
                # 예측에 위치 정보 없음 — 위치-free 후보로만 인정
                continue
            else:
                dist = _euclid(p.location, expected.location)
                if dist > tol:
                    continue

            if dist < best_dist:
                best_dist = dist
                best_idx = idx

        if best_idx is not None:
            tp.append((predictions[best_idx].raw, expected))
            available.remove(best_idx)
        else:
            fn.append(expected)

    fp = [predictions[i].raw for i in available]

    return MatchReport(
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        location_tol_used=location_tol,
        strict_type=strict_type,
    )


def compute_metrics(report: MatchReport) -> AccuracyMetrics:
    """MatchReport → AccuracyMetrics.

    분모 0 케이스 처리:
    - precision: tp + fp == 0 → None (예측이 0건 — 의미 없음)
    - recall: tp + fn == 0 → None (truth가 0건 — 의미 없음)
    - f1: precision 또는 recall 이 None 또는 0 → None
    - noise_ratio: tp + fp == 0 → None
    """
    tp = report.tp_count
    fp = report.fp_count
    fn = report.fn_count

    precision: Optional[float] = None
    recall: Optional[float] = None
    f1: Optional[float] = None
    noise_ratio: Optional[float] = None

    if tp + fp > 0:
        precision = tp / (tp + fp)
        noise_ratio = fp / (tp + fp)
    if tp + fn > 0:
        recall = tp / (tp + fn)
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = 2.0 * precision * recall / (precision + recall)

    return AccuracyMetrics(
        precision=precision,
        recall=recall,
        f1=f1,
        tp_count=tp,
        fp_count=fp,
        fn_count=fn,
        noise_ratio=noise_ratio,
    )


# ---------------------------------------------------------------------------
# YAML / JSON helpers (manifest round-trip)
# ---------------------------------------------------------------------------


def expected_change_from_dict(data: dict) -> ExpectedChange:
    """``expected_changes.json`` 의 한 항목 → ExpectedChange."""
    loc = data.get("location")
    location: Optional[Tuple[float, float]]
    if loc is None:
        location = None
    else:
        if not (isinstance(loc, (list, tuple)) and len(loc) == 2):
            raise ValueError(f"location must be [x, y] pair, got {loc!r}")
        location = (float(loc[0]), float(loc[1]))

    return ExpectedChange(
        location=location,
        change_type=str(data["change_type"]).lower(),
        layer=data.get("layer"),
        entity_type=data.get("entity_type"),
        tolerance_mm=(
            float(data["tolerance_mm"]) if data.get("tolerance_mm") is not None else None
        ),
        notes=str(data.get("notes", "")),
    )


def expected_change_to_dict(change: ExpectedChange) -> dict:
    """ExpectedChange → manifest JSON 항목 (round-trip 보장)."""
    out: dict = {"change_type": change.change_type}
    if change.location is not None:
        out["location"] = [change.location[0], change.location[1]]
    if change.layer is not None:
        out["layer"] = change.layer
    if change.entity_type is not None:
        out["entity_type"] = change.entity_type
    if change.tolerance_mm is not None:
        out["tolerance_mm"] = change.tolerance_mm
    if change.notes:
        out["notes"] = change.notes
    return out


__all__ = [
    "AccuracyMetrics",
    "ExpectedChange",
    "MatchReport",
    "compute_metrics",
    "expected_change_from_dict",
    "expected_change_to_dict",
    "match_changes_to_truth",
]
