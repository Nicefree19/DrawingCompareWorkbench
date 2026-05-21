# -*- coding: utf-8 -*-
"""Dataset composition stratification (recommendation #4).

외부 감사 리뷰 권고 ④ 대응 모듈. 기존 inventory/manifest 가
20-50쌍 카운트만 강제하면 PDF/CAD/blocked/no-expand/large-DWG
편식이 가능하다는 약점을 해소.

manifest 의 ``dataset_composition`` block 을 자체 schema 로 검증해
audit gate 가 missing/incomplete/non-compliant 를 명시적으로 보고.

Stratification minimum (권장값, customer-grade)
==============================================
- ``cad_pairs``: ≥ 8
- ``pdf_pairs``: ≥ 8
- ``blocked_pairs``: ≥ 1   (CAD-PDF mismatch evidence)
- ``no_expand_pairs``: ≥ 2 (block-text-without-expansion evidence)
- ``large_drawing_pairs``: ≥ 2 (S15+ class)
- coverage_buckets:
  - ``member``: ≥ 4
  - ``section_dimension``: ≥ 3
  - ``d13_shd13``: ≥ 3
  - ``grid``: ≥ 3
  - ``structural_text``: ≥ 2

audit script 는 ``DEFAULT_STRATIFICATION_REQUIREMENTS`` 를 기본값으로
사용하되 ``--composition-mode advisory`` 인자로 모니터링-only 모드도 허용.

Author: TEKLA_MCP Team
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional


SCHEMA_VERSION = 1


DEFAULT_STRATIFICATION_REQUIREMENTS: dict[str, int] = {
    "cad_pairs": 8,
    "pdf_pairs": 8,
    "blocked_pairs": 1,
    "no_expand_pairs": 2,
    "large_drawing_pairs": 2,
}


DEFAULT_COVERAGE_REQUIREMENTS: dict[str, int] = {
    "member": 4,
    "section_dimension": 3,
    "d13_shd13": 3,
    "grid": 3,
    "structural_text": 2,
}


COVERAGE_KEY = "coverage_buckets"


@dataclass(frozen=True)
class CompositionShortfall:
    """단일 stratification 위반."""

    bucket: str
    actual: int
    required: int
    category: str  # "stratification" | "coverage"

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "actual": self.actual,
            "required": self.required,
            "category": self.category,
            "shortfall": max(0, self.required - self.actual),
        }


@dataclass(frozen=True)
class DatasetCompositionReport:
    """audit/inventory 가 manifest 에서 조립한 stratification 평가 결과.

    Attributes:
        total_pairs: 전체 쌍 수
        stratification_compliant: 모든 stratification 임계값 통과 여부
        coverage_compliant: 모든 coverage_buckets 임계값 통과 여부
        compliant: 위 둘이 모두 True 일 때만 True
        shortfalls: 미달 항목 리스트
        applied_requirements: 사용된 stratification 임계값 (override 추적)
        applied_coverage: 사용된 coverage 임계값
    """

    schema_version: int = SCHEMA_VERSION
    total_pairs: int = 0
    stratification_compliant: bool = False
    coverage_compliant: bool = False
    compliant: bool = False
    shortfalls: tuple[CompositionShortfall, ...] = field(default_factory=tuple)
    applied_requirements: dict[str, int] = field(default_factory=dict)
    applied_coverage: dict[str, int] = field(default_factory=dict)
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "total_pairs": self.total_pairs,
            "stratification_compliant": self.stratification_compliant,
            "coverage_compliant": self.coverage_compliant,
            "compliant": self.compliant,
            "shortfalls": [s.to_dict() for s in self.shortfalls],
            "applied_requirements": dict(self.applied_requirements),
            "applied_coverage": dict(self.applied_coverage),
            "notes": list(self.notes),
        }


def evaluate_dataset_composition(
    composition: Optional[Mapping[str, Any]],
    *,
    requirements: Optional[Mapping[str, int]] = None,
    coverage_requirements: Optional[Mapping[str, int]] = None,
) -> DatasetCompositionReport:
    """``customer_evidence_manifest.dataset_composition`` 을 평가.

    Args:
        composition: manifest 의 ``dataset_composition`` 딕셔너리 또는 None
        requirements: stratification 임계값 override (dict). None 이면
            ``DEFAULT_STRATIFICATION_REQUIREMENTS`` 사용
        coverage_requirements: coverage 임계값 override

    Returns:
        DatasetCompositionReport
    """
    applied_strat = dict(requirements or DEFAULT_STRATIFICATION_REQUIREMENTS)
    applied_cover = dict(coverage_requirements or DEFAULT_COVERAGE_REQUIREMENTS)
    notes: list[str] = []

    if composition is None or not isinstance(composition, Mapping):
        return DatasetCompositionReport(
            total_pairs=0,
            stratification_compliant=False,
            coverage_compliant=False,
            compliant=False,
            shortfalls=tuple(
                CompositionShortfall(bucket=name, actual=0, required=req, category="stratification")
                for name, req in applied_strat.items()
            )
            + tuple(
                CompositionShortfall(bucket=name, actual=0, required=req, category="coverage")
                for name, req in applied_cover.items()
            ),
            applied_requirements=applied_strat,
            applied_coverage=applied_cover,
            notes=("composition_block_missing",),
        )

    total_pairs = _safe_int(composition.get("total_pairs")) or 0
    stratification_block = composition.get("stratification") or {}
    if not isinstance(stratification_block, Mapping):
        notes.append("stratification_block_invalid_type")
        stratification_block = {}

    shortfalls: list[CompositionShortfall] = []

    for bucket, required in applied_strat.items():
        actual = _safe_int(stratification_block.get(bucket)) or 0
        if actual < required:
            shortfalls.append(
                CompositionShortfall(
                    bucket=bucket,
                    actual=actual,
                    required=required,
                    category="stratification",
                )
            )
    coverage_block = stratification_block.get(COVERAGE_KEY) or {}
    if not isinstance(coverage_block, Mapping):
        notes.append("coverage_buckets_invalid_type")
        coverage_block = {}
    for bucket, required in applied_cover.items():
        actual = _safe_int(coverage_block.get(bucket)) or 0
        if actual < required:
            shortfalls.append(
                CompositionShortfall(
                    bucket=bucket,
                    actual=actual,
                    required=required,
                    category="coverage",
                )
            )

    stratification_compliant = not any(
        s.category == "stratification" for s in shortfalls
    )
    coverage_compliant = not any(s.category == "coverage" for s in shortfalls)
    compliant = stratification_compliant and coverage_compliant

    return DatasetCompositionReport(
        total_pairs=total_pairs,
        stratification_compliant=stratification_compliant,
        coverage_compliant=coverage_compliant,
        compliant=compliant,
        shortfalls=tuple(shortfalls),
        applied_requirements=applied_strat,
        applied_coverage=applied_cover,
        notes=tuple(notes),
    )


def render_composition_summary(
    composition: Mapping[str, Any],
    pairs: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """inventory script helper: 도면쌍 분류 결과 → composition dict.

    각 pair 는 다음 키를 가진다고 가정:
    - ``source_format``: ``"cad"`` | ``"pdf"`` | ``"blocked"``
    - ``no_expand``: bool
    - ``is_large_drawing``: bool
    - ``coverage_buckets``: list[str]
    """
    cad = pdf = blocked = no_expand = large = 0
    coverage_counts: dict[str, int] = {key: 0 for key in DEFAULT_COVERAGE_REQUIREMENTS}
    seen_total = 0
    for pair in pairs:
        if not isinstance(pair, Mapping):
            continue
        seen_total += 1
        fmt = str(pair.get("source_format") or "").strip().lower()
        if fmt == "cad":
            cad += 1
        elif fmt == "pdf":
            pdf += 1
        elif fmt == "blocked":
            blocked += 1
        if pair.get("no_expand"):
            no_expand += 1
        if pair.get("is_large_drawing"):
            large += 1
        for bucket in pair.get("coverage_buckets") or []:
            key = str(bucket).strip().lower()
            if key in coverage_counts:
                coverage_counts[key] += 1
    composition_out = dict(composition or {})
    composition_out.update(
        {
            "total_pairs": seen_total,
            "stratification": {
                "cad_pairs": cad,
                "pdf_pairs": pdf,
                "blocked_pairs": blocked,
                "no_expand_pairs": no_expand,
                "large_drawing_pairs": large,
                COVERAGE_KEY: coverage_counts,
            },
            "stratification_compliant": evaluate_dataset_composition(
                {
                    "total_pairs": seen_total,
                    "stratification": {
                        "cad_pairs": cad,
                        "pdf_pairs": pdf,
                        "blocked_pairs": blocked,
                        "no_expand_pairs": no_expand,
                        "large_drawing_pairs": large,
                        COVERAGE_KEY: coverage_counts,
                    },
                }
            ).compliant,
        }
    )
    return composition_out


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "COVERAGE_KEY",
    "CompositionShortfall",
    "DEFAULT_COVERAGE_REQUIREMENTS",
    "DEFAULT_STRATIFICATION_REQUIREMENTS",
    "DatasetCompositionReport",
    "SCHEMA_VERSION",
    "evaluate_dataset_composition",
    "render_composition_summary",
]
