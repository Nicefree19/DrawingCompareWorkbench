# -*- coding: utf-8 -*-
"""Adaptive viewer quality selection.

Audit-gates §10 follow-up — the workbench previously defaulted to "구조도면
정밀 (DPI 400 / 10,000 px)", a setting that requires the user to consciously
downgrade for any drawing larger than ~50 MB. The S20 hang incident proved
this is the wrong default: a non-expert reviewer cannot translate
"DPI 400" into "1.5 GB of PIL allocation per pair × N pairs".

This module replaces user choice with **automatic** selection driven by
measurable input characteristics:

- Total input size (sum of A + B file sizes)
- Per-pair maximum size (worst-case driver)
- Page / pair count
- Available memory budget (RuntimeBudget cap)

The function returns a fully-populated ``QualityDecision`` with both the
selected DPI/edge values and a human-readable rationale string the GUI can
display so users understand why a particular tier was chosen.

Design contract
===============
- **Pure function**: no I/O beyond ``Path.stat()`` for size lookups.
- **Deterministic**: identical inputs always produce identical decisions
  (no time, randomness, or external state).
- **Safe defaults**: missing files / unreadable inputs collapse to the
  conservative tier. Never raise on the happy path — log via the returned
  ``notes`` list instead.
- **Backward compatible**: callers that omit the new arguments still get the
  legacy "구조도면 정밀" preset by setting ``mode="explicit"``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Quality tier table
# ---------------------------------------------------------------------------
#
# Each tuple = (label, dpi, max_edge_px, memory_estimate_mb_per_pair).
# Memory estimate is empirical: based on PIL Image footprint + matplotlib
# figure + tile cache for one pair on the S15 reference DXF set.
# Reviewers can trust the estimate as an order-of-magnitude guide, not a
# strict guarantee — actual usage scales with entity count and overlay density.

@dataclass(frozen=True)
class QualityTier:
    label: str
    dpi: int
    max_edge_px: int
    estimated_mb_per_pair: float


QUALITY_TIERS: tuple[QualityTier, ...] = (
    QualityTier(label="안전 모드 (DPI 80)", dpi=80, max_edge_px=2400, estimated_mb_per_pair=120.0),
    QualityTier(label="기본 (DPI 120)", dpi=120, max_edge_px=3600, estimated_mb_per_pair=250.0),
    QualityTier(label="고화질 (DPI 200)", dpi=200, max_edge_px=6000, estimated_mb_per_pair=600.0),
    QualityTier(label="초고화질 (DPI 300)", dpi=300, max_edge_px=8000, estimated_mb_per_pair=1100.0),
)
# DPI 400 is intentionally excluded from auto-selection — it is the
# documented hang risk on S20-class drawings and exists only for explicit
# manual override.


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InputCharacteristics:
    """Aggregated metrics about the comparison inputs."""

    file_count_a: int
    file_count_b: int
    total_bytes: int
    max_pair_bytes: int
    average_pair_bytes: int
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def file_count(self) -> int:
        return self.file_count_a + self.file_count_b

    @property
    def total_mb(self) -> float:
        return self.total_bytes / (1024 * 1024)

    @property
    def max_pair_mb(self) -> float:
        return self.max_pair_bytes / (1024 * 1024)

    def to_dict(self) -> dict:
        return {
            "file_count_a": self.file_count_a,
            "file_count_b": self.file_count_b,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "total_mb": round(self.total_mb, 2),
            "max_pair_bytes": self.max_pair_bytes,
            "max_pair_mb": round(self.max_pair_mb, 2),
            "average_pair_bytes": self.average_pair_bytes,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class QualityDecision:
    """Quality tier selected for a comparison run plus rationale."""

    schema_version: int
    tier: QualityTier
    rationale: str  # Korean, one-sentence
    auto_selected: bool
    inputs: InputCharacteristics
    memory_cap_mb: float
    safety_margin_ratio: float
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def dpi(self) -> int:
        return self.tier.dpi

    @property
    def max_edge_px(self) -> int:
        return self.tier.max_edge_px

    @property
    def estimated_mb_per_pair(self) -> float:
        return self.tier.estimated_mb_per_pair

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "auto_selected": self.auto_selected,
            "tier_label": self.tier.label,
            "dpi": self.dpi,
            "max_edge_px": self.max_edge_px,
            "estimated_mb_per_pair": self.estimated_mb_per_pair,
            "rationale": self.rationale,
            "inputs": self.inputs.to_dict(),
            "memory_cap_mb": self.memory_cap_mb,
            "safety_margin_ratio": self.safety_margin_ratio,
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


def measure_inputs(
    paths_a: Sequence[Path], paths_b: Sequence[Path]
) -> InputCharacteristics:
    """Aggregate file size statistics for the two input collections.

    Missing or unreadable files contribute zero bytes and a note. This is
    intentional — auto-quality should err on the side of completing the run
    rather than refusing it because one file metadata read failed.
    """
    notes: list[str] = []
    total_bytes = 0
    pair_sizes: list[int] = []

    sizes_a = _file_sizes(paths_a, side="a", notes=notes)
    sizes_b = _file_sizes(paths_b, side="b", notes=notes)

    paired = list(zip(sizes_a, sizes_b))
    for size_a, size_b in paired:
        pair_total = size_a + size_b
        pair_sizes.append(pair_total)
        total_bytes += pair_total
    # Tail (unmatched on one side) — bill to total but ignore for max-pair.
    if len(sizes_a) > len(paired):
        for size_a in sizes_a[len(paired) :]:
            total_bytes += size_a
    if len(sizes_b) > len(paired):
        for size_b in sizes_b[len(paired) :]:
            total_bytes += size_b

    max_pair_bytes = max(pair_sizes) if pair_sizes else 0
    average_pair_bytes = (
        int(round(sum(pair_sizes) / len(pair_sizes))) if pair_sizes else 0
    )
    return InputCharacteristics(
        file_count_a=len(sizes_a),
        file_count_b=len(sizes_b),
        total_bytes=total_bytes,
        max_pair_bytes=max_pair_bytes,
        average_pair_bytes=average_pair_bytes,
        notes=tuple(notes),
    )


def _file_sizes(
    paths: Iterable[Path], *, side: str, notes: list[str]
) -> list[int]:
    sizes: list[int] = []
    for path in paths:
        try:
            sizes.append(int(Path(path).stat().st_size))
        except OSError as exc:
            notes.append(f"size_unavailable_{side}:{path}:{exc.errno}")
            sizes.append(0)
    return sizes


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


def select_quality(
    inputs: InputCharacteristics,
    *,
    memory_cap_mb: float = 4096.0,
    safety_margin_ratio: float = 0.6,
    explicit_dpi: Optional[int] = None,
) -> QualityDecision:
    """Pick the best ``QualityTier`` for the given inputs and memory cap.

    Algorithm
    =========
    1. If ``explicit_dpi`` is supplied (legacy override), find the tier whose
       DPI matches; if none, build an ad-hoc tier so callers can still bypass
       the safety net.
    2. Compute an *effective* memory budget = ``memory_cap_mb * safety_margin_ratio``
       (default 60%) — this leaves headroom for the OS, viewer overlays, and
       AI classifier load.
    3. Walk QUALITY_TIERS from highest DPI down. The first tier whose
       ``estimated_mb_per_pair * load_factor <= effective_budget`` wins.
    4. If even the lowest tier exceeds the budget (rare), still return the
       lowest tier and add a warning note so the caller can advise the user.

    ``load_factor`` is derived from ``max_pair_mb``:
      - <= 25 MB: 1.0x (small drawings render close to estimate)
      - 25-100 MB: 1.5x
      - 100-300 MB: 2.5x (S15-class CAD)
      - > 300 MB: 4.0x (S20-class with dense MTEXT/dimensions)

    Page count contributes a smaller multiplier (1 + file_count/100) so a
    thick set of small drawings drifts toward a more conservative tier.
    """
    notes: list[str] = []

    if explicit_dpi is not None:
        return _explicit_decision(
            explicit_dpi,
            inputs=inputs,
            memory_cap_mb=memory_cap_mb,
            safety_margin_ratio=safety_margin_ratio,
        )

    effective_budget = memory_cap_mb * max(0.1, min(1.0, safety_margin_ratio))
    load_factor = _load_factor(inputs.max_pair_mb)
    page_multiplier = 1.0 + (inputs.file_count / 100.0)

    selected: Optional[QualityTier] = None
    # Walk from highest DPI downward so we pick the most accurate tier the
    # budget can sustain.
    for tier in reversed(QUALITY_TIERS):
        projected_mb = tier.estimated_mb_per_pair * load_factor * page_multiplier
        if projected_mb <= effective_budget:
            selected = tier
            notes.append(
                f"selected_tier:{tier.label};projected_mb={projected_mb:.0f}<=budget={effective_budget:.0f}"
            )
            break

    if selected is None:
        selected = QUALITY_TIERS[0]
        notes.append(
            f"fallback_to_safe_mode:projected_exceeds_budget;load_factor={load_factor};budget={effective_budget:.0f}"
        )

    rationale = _build_rationale(selected, inputs, load_factor, effective_budget)
    return QualityDecision(
        schema_version=SCHEMA_VERSION,
        tier=selected,
        rationale=rationale,
        auto_selected=True,
        inputs=inputs,
        memory_cap_mb=memory_cap_mb,
        safety_margin_ratio=safety_margin_ratio,
        notes=tuple(notes),
    )


def _load_factor(max_pair_mb: float) -> float:
    if max_pair_mb <= 25.0:
        return 1.0
    if max_pair_mb <= 100.0:
        return 1.5
    if max_pair_mb <= 300.0:
        return 2.5
    return 4.0


def _build_rationale(
    tier: QualityTier,
    inputs: InputCharacteristics,
    load_factor: float,
    effective_budget: float,
) -> str:
    """Korean one-sentence explanation suitable for a GUI tooltip."""
    if inputs.file_count == 0:
        return f"입력 없음 — {tier.label} 적용"
    return (
        f"{inputs.file_count}개 파일 / 합계 {inputs.total_mb:.0f}MB "
        f"(최대 쌍 {inputs.max_pair_mb:.0f}MB, 부담 계수 {load_factor:.1f}x) "
        f"→ {tier.label} 자동 선택 (예상 {tier.estimated_mb_per_pair * load_factor:.0f}MB / pair, "
        f"안전 한계 {effective_budget:.0f}MB)"
    )


def _explicit_decision(
    dpi: int,
    *,
    inputs: InputCharacteristics,
    memory_cap_mb: float,
    safety_margin_ratio: float,
) -> QualityDecision:
    """Legacy override path: caller demanded a specific DPI."""
    matching = next((t for t in QUALITY_TIERS if t.dpi == dpi), None)
    if matching is None:
        # Build an ad-hoc tier so callers can still pass DPI 400 manually.
        matching = QualityTier(
            label=f"수동 (DPI {dpi})",
            dpi=int(dpi),
            max_edge_px=max(2400, int(dpi) * 25),
            estimated_mb_per_pair=max(100.0, float(dpi) * 4.0),
        )
    notes = (
        f"explicit_override:dpi={dpi};budget_check_skipped",
    )
    return QualityDecision(
        schema_version=SCHEMA_VERSION,
        tier=matching,
        rationale=f"수동 선택 — DPI {dpi}",
        auto_selected=False,
        inputs=inputs,
        memory_cap_mb=memory_cap_mb,
        safety_margin_ratio=safety_margin_ratio,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Adaptive degradation
# ---------------------------------------------------------------------------
#
# History note: ``should_downgrade()`` previously lived here too but was
# removed on 2026-05-15 because no production code path called it. The
# remaining ``downgrade_one_step()`` IS now wired into production — the
# folder_compare_pipeline catch block for ``MemoryBudgetExceeded`` (Plan C-1
# of the §1.3 finding #2 follow-up) calls it once to obtain a lower-DPI
# decision, then re-invokes ``export_viewer_package_isolated`` so the user
# does not have to manually drop the quality combo and re-run.
#
# If ``should_downgrade`` is needed in the future (e.g. for *proactive*
# downgrades before MemoryBudgetExceeded is actually raised), restore it
# from git commit ``094b1fe9`` and wire it into the same place — do not
# let dead code accumulate again.


def downgrade_one_step(decision: QualityDecision, *, reason: str) -> QualityDecision:
    """Return the next-lower tier given a current decision (or stay if at floor).

    Used by a runtime sampler that detects the working set approaching the
    cap. The new decision keeps the same inputs/budget context but advertises
    the downgrade in its notes for audit purposes.
    """
    current_index = next(
        (i for i, tier in enumerate(QUALITY_TIERS) if tier.dpi == decision.tier.dpi),
        None,
    )
    if current_index is None or current_index == 0:
        # Already at the lowest tier — no further downgrade available.
        notes = decision.notes + (f"downgrade_blocked:reason={reason}",)
        return QualityDecision(
            schema_version=decision.schema_version,
            tier=decision.tier,
            rationale=decision.rationale + " (이미 최저 품질)",
            auto_selected=decision.auto_selected,
            inputs=decision.inputs,
            memory_cap_mb=decision.memory_cap_mb,
            safety_margin_ratio=decision.safety_margin_ratio,
            notes=notes,
        )
    new_tier = QUALITY_TIERS[current_index - 1]
    notes = decision.notes + (
        f"downgraded:from={decision.tier.label};to={new_tier.label};reason={reason}",
    )
    new_rationale = (
        f"{decision.rationale} → 진행 중 메모리 부담 감지로 {new_tier.label} 로 자동 하향"
    )
    return QualityDecision(
        schema_version=decision.schema_version,
        tier=new_tier,
        rationale=new_rationale,
        auto_selected=True,  # forced by adaptive logic
        inputs=decision.inputs,
        memory_cap_mb=decision.memory_cap_mb,
        safety_margin_ratio=decision.safety_margin_ratio,
        notes=notes,
    )


__all__ = [
    "InputCharacteristics",
    "QUALITY_TIERS",
    "QualityDecision",
    "QualityTier",
    "SCHEMA_VERSION",
    "downgrade_one_step",
    "measure_inputs",
    "select_quality",
]
