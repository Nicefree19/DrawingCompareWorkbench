# -*- coding: utf-8 -*-
"""Phase N — Workbench ↔ AI classifier integration adapter.

CRITICAL CONTEXT — read this before touching:

The Drawing Compare Workbench has historically used
``zone_classifier.classify_zone`` (pure heuristic, fast, always-on)
to populate the zone-list category column + detail panel. Phase H/I/J/K/L
shipped a separate ``ai_classifier.classify_zones`` 3-tier cascade
(heuristic → embedding → LLM with KDS RAG). The two evolved in
parallel — the cascade was wired into the AI settings dialog's
"테스트 인코드" button but NOT into the workbench's per-pair zone
classification flow. The 12-commit rollout passed all unit tests
because the unit tests covered the cascade in isolation, but the
real workbench user never saw any benefit.

This adapter closes that gap by:
  1. Bridging the schema mismatch — converting
     ``ai_classifier.ChangeClassification`` (rich, enum-based) to
     ``zone_classifier.ZoneCategoryResult`` (Korean string label,
     simpler) so the workbench's existing display code keeps working.
  2. Providing ``classify_zone_with_cascade`` — a drop-in replacement
     for ``zone_classifier.classify_zone`` that routes through the
     full cascade when the user has it enabled, otherwise falls back
     to the heuristic.

The workbench then calls ``classify_zone_with_cascade(zone, config)``
in ``_compute_zone_categories_for_pair_v2`` instead of bare
``classify_zone(zone)``. Heuristic-only users see no change; users
with `use_embedding=True` or `use_llm=True` get cascade results
displayed in the existing UI panels.

Why an adapter instead of refactoring the whole UI to consume
``ChangeClassification`` directly:

  * Lower risk — workbench panel code (8 call sites) keeps the same
    schema. Single-file change instead of cross-cutting refactor.
  * The cascade's enum categories don't 1:1 match the workbench's
    Korean-label categories. The mapping is best done in one place.
  * The workbench's `severity_boost` is a downstream sort key; the
    cascade's `Severity` enum + `confidence` produce a comparable
    integer.
  * Test coverage stays focused — adapter has its own unit tests
    instead of having to retrofit every UI panel test.

A future Phase N+ may push ``ChangeClassification`` deeper into the
UI (so callers can see `kds_references`, `summary_ko`, `classifier_used`
without going through the adapter). This adapter is the migration
seam.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .zone_classifier import (
    CATEGORY_DETAIL,
    CATEGORY_DIMENSION,
    CATEGORY_GRID,
    CATEGORY_LAYER,
    CATEGORY_OTHER,
    CATEGORY_STRUCTURAL_MEMBER,
    ZoneCategoryResult,
    classify_zone as _heuristic_classify_zone,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema bridge — ai_classifier.ChangeCategory → workbench Korean label
# ---------------------------------------------------------------------------

# Map ai_classifier's ChangeCategory enum values to the workbench's
# Korean category strings (the ones zone_classifier produces). This is
# a many-to-many mapping in places (the AI cascade has finer-grained
# categories than the workbench surface — e.g. NOTE vs DIMENSION) so
# the chosen target is the closest semantic match the existing UI
# already understands.
_AI_CATEGORY_TO_LABEL: dict[str, str] = {
    "structural_member": CATEGORY_STRUCTURAL_MEMBER,
    "dimension": CATEGORY_DIMENSION,
    "text_label": CATEGORY_DIMENSION,   # workbench has no separate TEXT bucket
    "grid": CATEGORY_GRID,
    "layout": CATEGORY_DETAIL,           # closest match — layout = detail-ish
    "detail_drawing": CATEGORY_DETAIL,
    "note": CATEGORY_DIMENSION,          # notes are typically text-on-drawing
    "unknown": CATEGORY_OTHER,
}


# Severity enum → boost magnitude for the workbench's sort axis
_SEVERITY_TO_BOOST: dict[str, int] = {
    "critical": 30,    # highest priority — float to top of zone list
    "normal": 10,
    "minor": 0,
}


def _ai_category_to_label(ai_category_value: str) -> str:
    """Map ChangeCategory enum value → Korean workbench label.

    Unknown values fall through to CATEGORY_OTHER so a future enum
    extension doesn't crash the workbench's display code.
    """
    return _AI_CATEGORY_TO_LABEL.get(ai_category_value, CATEGORY_OTHER)


def _severity_to_boost(severity_value: str) -> int:
    return _SEVERITY_TO_BOOST.get(severity_value, 0)


def adapt_change_classification(result: Any) -> ZoneCategoryResult:
    """Convert one ``ai_classifier.ChangeClassification`` →
    ``zone_classifier.ZoneCategoryResult``.

    Preserves the AI-generated rationale (``summary_ko``) for display
    and adds a small marker indicating which classifier tier produced
    the result so reviewers can see "this was the LLM, not the
    heuristic" in the rationale text.

    Defensive: tolerates None, dict, or any object with the expected
    attributes — since the workbench's call site catches exceptions
    and the cascade itself can return None on abstain.
    """

    if result is None:
        return ZoneCategoryResult(
            category=CATEGORY_OTHER, confidence=0.0,
            severity_boost=0,
            rationale_ko="AI 분류 결과 없음",
        )

    # Tolerate both dataclass and dict (defensive — should always be
    # the dataclass from public_api but adapter shouldn't crash if
    # somebody passes a dict by accident)
    if isinstance(result, dict):
        category_raw = str(result.get("category", "unknown"))
        severity_raw = str(result.get("severity", "normal"))
        confidence = float(result.get("confidence", 0.0))
        summary_ko = str(result.get("summary_ko", "") or "")
        classifier_used = str(result.get("classifier_used", "?"))
    else:
        # Dataclass with .category as enum (or string) — handle both
        cat = getattr(result, "category", None)
        category_raw = str(getattr(cat, "value", None) or cat or "unknown")
        sev = getattr(result, "severity", None)
        severity_raw = str(getattr(sev, "value", None) or sev or "normal")
        confidence = float(getattr(result, "confidence", 0.0) or 0.0)
        summary_ko = str(getattr(result, "summary_ko", "") or "")
        classifier_used = str(getattr(result, "classifier_used", "?") or "?")

    label = _ai_category_to_label(category_raw)
    boost = _severity_to_boost(severity_raw)

    # Tag the rationale with which tier produced it so the user can
    # tell heuristic from embedding from LLM in the detail panel.
    tier_marker = {
        "heuristic": "[Stage-1 휴리스틱]",
        "embedding": "[Stage-2 임베딩]",
        "hybrid": "[Stage-3 LLM]",
        "disabled": "[AI 비활성]",
        "error": "[분류 오류]",
    }.get(classifier_used, f"[{classifier_used}]")

    rationale = (
        f"{tier_marker} {summary_ko}".strip()
        if summary_ko
        else f"{tier_marker} 신뢰도 {confidence:.2f}"
    )

    return ZoneCategoryResult(
        category=label,
        confidence=confidence,
        severity_boost=boost,
        rationale_ko=rationale,
    )


# ---------------------------------------------------------------------------
# Drop-in replacement for zone_classifier.classify_zone
# ---------------------------------------------------------------------------


def classify_zone_with_cascade(
    zone: dict[str, Any],
    config: Optional[Any] = None,
) -> ZoneCategoryResult:
    """Drop-in replacement for ``zone_classifier.classify_zone`` that
    routes through the full AI cascade when ``config.use_embedding``
    or ``config.use_llm`` is True.

    Heuristic-only fast path:
      * config is None → use_embedding=False AND use_llm=False
      * No bridge cost — calls bare ``zone_classifier.classify_zone``

    Cascade path (use_embedding OR use_llm enabled):
      * Build single-zone batch + call ``ai_classifier.classify_zones``
      * Adapt the single result back to ZoneCategoryResult
      * Any exception from the cascade → fall back to heuristic
        (defensive — cascade contract says it never raises, but the
        workbench shouldn't crash if a future change breaks that)

    The single-zone batch invocation is intentional: the workbench's
    ``_compute_zone_categories_for_pair_v2`` already iterates per
    overlay, so batching at this layer would change call-site shape.
    The cascade's batching wins are mostly for embedding (one
    backend call per N zones); for the workbench's use case (one
    pair = ~5-50 zones, classified once on selection) the latency
    win is small enough not to justify the call-site refactor here.
    """

    use_embedding = bool(getattr(config, "use_embedding", False))
    use_llm = bool(getattr(config, "use_llm", False))
    enabled = bool(getattr(config, "enabled", True))

    # Fast path — heuristic only
    if not enabled or (not use_embedding and not use_llm):
        return _heuristic_classify_zone(zone)

    # Cascade path — route through public_api
    try:
        from .ai_classifier import classify_zones
        results = classify_zones([zone], config=config)
    except Exception:  # noqa: BLE001
        # Cascade should never raise per its public contract, but if
        # it does, do not crash the workbench's zone list — fall back
        # to heuristic so the user still sees a category.
        logger.exception(
            "ai_classifier.classify_zones raised — falling back to heuristic"
        )
        return _heuristic_classify_zone(zone)

    if not results:
        # Empty result — also fall back. This shouldn't happen with
        # a non-empty input but be defensive.
        return _heuristic_classify_zone(zone)

    return adapt_change_classification(results[0])


__all__ = [
    "adapt_change_classification",
    "classify_zone_with_cascade",
]
