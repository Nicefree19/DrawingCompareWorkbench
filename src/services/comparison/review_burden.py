# -*- coding: utf-8 -*-
"""Review-queue precision and reviewer burden estimation.

외부 감사 리뷰 권고 ③ 대응 모듈. 기존 ``accuracy_metrics.py`` 가
잘 만든 precision/recall/F1 인프라 위에 다음을 추가:

- ``top_queue_precision``: top-N 큐 한정 precision (reviewer 가 실제로
  처리하는 큐 윈도우의 신호 대 노이즈 비율)
- ``false_positive_burden_per_sheet``: 도면(sheet) 당 reviewer 가
  처리한 false_positive 평균 개수
- ``review_burden_minutes_per_sheet``: 분당 처리 추정 시간 환산

설계 원칙
=========
1. **기존 schema 변경 없음**: ``MatchReport`` / ``AccuracyMetrics`` 미변경
2. **외부 입력 의존 명시**: review_ground_truth.csv + operator_decisions
   둘 다 필요. ground truth 부재 시 ``None`` 반환 (audit gate가 missing
   처리).
3. **operator decision 정의**:
   - confirmed: reviewer 가 "실제 변경" 으로 확정
   - false_positive: reviewer 가 "오탐" 으로 분류 (예측이 틀림)
   - hold: 재검토 보류 (precision 계산에서 제외)

Author: TEKLA_MCP Team
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence


SCHEMA_VERSION = 1
DEFAULT_MINUTES_PER_DECISION = 0.5  # 30 sec per decision (operator survey 기반 추정)


@dataclass(frozen=True)
class OperatorDecision:
    """단일 review queue 결정.

    Attributes:
        zone_id: 비교 영역 ID (review queue item key)
        sheet_id: 도면 sheet ID (per-sheet 집계용)
        decision: ``confirmed`` / ``false_positive`` / ``hold``
        rank: top-N 순위 (1-based, 큐에 노출된 순서)
    """

    zone_id: str
    sheet_id: str
    decision: str
    rank: Optional[int] = None


@dataclass(frozen=True)
class ReviewBurdenStats:
    """precision + burden 메트릭 집계 결과.

    분모 0 시 None — 기존 AccuracyMetrics 와 동일한 규약.
    """

    schema_version: int = SCHEMA_VERSION
    total_decisions: int = 0
    confirmed_count: int = 0
    false_positive_count: int = 0
    hold_count: int = 0
    sheet_count: int = 0
    top_queue_size: Optional[int] = None
    top_queue_confirmed: int = 0
    top_queue_false_positive: int = 0
    top_queue_precision: Optional[float] = None
    overall_precision: Optional[float] = None
    false_positive_burden_per_sheet: Optional[float] = None
    review_burden_minutes_per_sheet: Optional[float] = None
    minutes_per_decision: float = DEFAULT_MINUTES_PER_DECISION
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "total_decisions": self.total_decisions,
            "confirmed_count": self.confirmed_count,
            "false_positive_count": self.false_positive_count,
            "hold_count": self.hold_count,
            "sheet_count": self.sheet_count,
            "top_queue_size": self.top_queue_size,
            "top_queue_confirmed": self.top_queue_confirmed,
            "top_queue_false_positive": self.top_queue_false_positive,
            "top_queue_precision": (
                round(self.top_queue_precision, 4)
                if self.top_queue_precision is not None
                else None
            ),
            "overall_precision": (
                round(self.overall_precision, 4)
                if self.overall_precision is not None
                else None
            ),
            "false_positive_burden_per_sheet": (
                round(self.false_positive_burden_per_sheet, 4)
                if self.false_positive_burden_per_sheet is not None
                else None
            ),
            "review_burden_minutes_per_sheet": (
                round(self.review_burden_minutes_per_sheet, 4)
                if self.review_burden_minutes_per_sheet is not None
                else None
            ),
            "minutes_per_decision": self.minutes_per_decision,
            "notes": list(self.notes),
        }


def normalize_decision(value: Any) -> str:
    """operator 결정 문자열을 표준 형식으로 정규화."""
    if value is None:
        return "hold"
    text = str(value).strip().lower()
    if text in {"confirmed", "confirm", "true_positive", "tp"}:
        return "confirmed"
    if text in {"false_positive", "fp", "rejected", "reject"}:
        return "false_positive"
    if text in {"hold", "pending", "skip"}:
        return "hold"
    # 알 수 없는 결정은 hold 로 간주 (precision 계산 제외)
    return "hold"


def operator_decision_from_dict(data: dict[str, Any]) -> OperatorDecision:
    """JSON 라인 / CSV 행 → OperatorDecision."""
    return OperatorDecision(
        zone_id=str(data.get("zone_id") or ""),
        sheet_id=str(data.get("sheet_id") or data.get("drawing_label") or ""),
        decision=normalize_decision(data.get("decision") or data.get("status")),
        rank=_safe_int(data.get("rank")),
    )


def compute_review_burden(
    decisions: Iterable[OperatorDecision | dict[str, Any]],
    *,
    top_n: Optional[int] = None,
    minutes_per_decision: float = DEFAULT_MINUTES_PER_DECISION,
) -> ReviewBurdenStats:
    """OperatorDecision 컬렉션 → precision + burden 집계.

    Args:
        decisions: OperatorDecision 또는 dict 형태의 결정 리스트
        top_n: top_queue_precision 산출에 사용할 상한 (1-based rank)
            ``None`` 이면 top_queue 메트릭은 None
        minutes_per_decision: 결정 1건 당 reviewer 시간 (기본 0.5분)
    """
    normalised: list[OperatorDecision] = []
    notes: list[str] = []
    for entry in decisions:
        if isinstance(entry, OperatorDecision):
            normalised.append(entry)
        elif isinstance(entry, dict):
            normalised.append(operator_decision_from_dict(entry))
        else:
            notes.append(f"non_decision_skipped:{type(entry).__name__}")

    total = len(normalised)
    confirmed = sum(1 for d in normalised if d.decision == "confirmed")
    false_pos = sum(1 for d in normalised if d.decision == "false_positive")
    hold = sum(1 for d in normalised if d.decision == "hold")
    sheet_ids = {d.sheet_id for d in normalised if d.sheet_id}
    sheet_count = len(sheet_ids)

    overall_precision: Optional[float] = None
    if confirmed + false_pos > 0:
        overall_precision = confirmed / (confirmed + false_pos)

    top_queue_size: Optional[int] = None
    top_confirmed = 0
    top_false_pos = 0
    top_precision: Optional[float] = None
    if top_n is not None and top_n > 0:
        top_queue_size = top_n
        top_window = [
            d for d in normalised if d.rank is not None and d.rank <= top_n
        ]
        if not top_window:
            notes.append("top_queue_window_empty")
        top_confirmed = sum(1 for d in top_window if d.decision == "confirmed")
        top_false_pos = sum(1 for d in top_window if d.decision == "false_positive")
        if top_confirmed + top_false_pos > 0:
            top_precision = top_confirmed / (top_confirmed + top_false_pos)

    fp_burden_per_sheet: Optional[float] = None
    burden_minutes_per_sheet: Optional[float] = None
    if sheet_count > 0:
        fp_burden_per_sheet = false_pos / sheet_count
        burden_minutes_per_sheet = (
            (confirmed + false_pos) * minutes_per_decision / sheet_count
        )

    return ReviewBurdenStats(
        total_decisions=total,
        confirmed_count=confirmed,
        false_positive_count=false_pos,
        hold_count=hold,
        sheet_count=sheet_count,
        top_queue_size=top_queue_size,
        top_queue_confirmed=top_confirmed,
        top_queue_false_positive=top_false_pos,
        top_queue_precision=top_precision,
        overall_precision=overall_precision,
        false_positive_burden_per_sheet=fp_burden_per_sheet,
        review_burden_minutes_per_sheet=burden_minutes_per_sheet,
        minutes_per_decision=minutes_per_decision,
        notes=tuple(notes),
    )


def review_burden_from_dict(payload: Optional[dict[str, Any]]) -> ReviewBurdenStats:
    """ReviewBurdenStats round-trip — manifest 호환."""
    if not isinstance(payload, dict):
        return ReviewBurdenStats()
    return ReviewBurdenStats(
        schema_version=int(payload.get("schema_version") or SCHEMA_VERSION),
        total_decisions=int(payload.get("total_decisions") or 0),
        confirmed_count=int(payload.get("confirmed_count") or 0),
        false_positive_count=int(payload.get("false_positive_count") or 0),
        hold_count=int(payload.get("hold_count") or 0),
        sheet_count=int(payload.get("sheet_count") or 0),
        top_queue_size=_safe_int(payload.get("top_queue_size")),
        top_queue_confirmed=int(payload.get("top_queue_confirmed") or 0),
        top_queue_false_positive=int(payload.get("top_queue_false_positive") or 0),
        top_queue_precision=_safe_float(payload.get("top_queue_precision")),
        overall_precision=_safe_float(payload.get("overall_precision")),
        false_positive_burden_per_sheet=_safe_float(
            payload.get("false_positive_burden_per_sheet")
        ),
        review_burden_minutes_per_sheet=_safe_float(
            payload.get("review_burden_minutes_per_sheet")
        ),
        minutes_per_decision=float(
            payload.get("minutes_per_decision") or DEFAULT_MINUTES_PER_DECISION
        ),
        notes=tuple(str(n) for n in (payload.get("notes") or [])),
    )


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "DEFAULT_MINUTES_PER_DECISION",
    "OperatorDecision",
    "ReviewBurdenStats",
    "compute_review_burden",
    "normalize_decision",
    "operator_decision_from_dict",
    "review_burden_from_dict",
    "SCHEMA_VERSION",
]
