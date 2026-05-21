# -*- coding: utf-8 -*-
"""Public ``classify_zones`` entry point.

Dispatches to the heuristic / embedding / LLM tier based on
``AiClassifierConfig``. The heuristic tier is always available and
runs as a fallback for any zone the higher tiers can't classify.

Cascade order (Phase J Step 5 (J2) — full 3-tier cascade live):

    Stage 1 — keyword heuristic (ALWAYS runs, < 1ms)
        │
        ▼  ChangeClassification (baseline)
        │
    Stage 2 — embedding cosine (when cfg.use_embedding=True AND
              backend successfully prepared)
        │
        ▼  Optional[ChangeClassification] — None = abstain
        │  (top-K candidates stashed in raw_evidence["top_categories"])
        │
    Stage 3 — LLM (when cfg.use_llm=True AND
              dispatcher.should_invoke(stage2_result) is True)
        │
        ▼  ChangeClassification(classifier_used="hybrid") with
           rationale_ko + kds_references[] — OR None (LLM abstain)

Each stage's result REPLACES the previous one when present; abstain
keeps the lower-tier answer. The cascade NEVER returns fewer items
than were given — Stage-1 is always present as the final safety net.
LLM is gated on ``should_invoke`` so confident Stage-2 zones skip
the 1-3 s LLM round-trip entirely.
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

from .schema import (
    AiClassifierConfig,
    ChangeCategory,
    ChangeClassification,
    Severity,
)
from .heuristic_classifier import classify_zone_heuristic

logger = logging.getLogger(__name__)


def classify_zones(
    zones: Sequence[dict],
    *,
    config: Optional[AiClassifierConfig] = None,
) -> list[ChangeClassification]:
    """Classify each zone using the configured pipeline.

    Args:
        zones: Overlay-shaped dicts (workbench's
            ``_active_overlays_by_zone.values()``).
        config: Optional runtime config; defaults to heuristic-only.

    Returns:
        One ``ChangeClassification`` per input zone, in input order.
        Even disabled / empty input returns ``[]`` cleanly.
    """

    if not zones:
        return []
    cfg = config or AiClassifierConfig.heuristic_only()
    if not cfg.enabled:
        # Caller wants AI off entirely — return UNKNOWN classifications
        # so the schema contract still holds but no work happens.
        return [
            ChangeClassification(
                zone_id=str((z or {}).get("zone_id") or ""),
                category=ChangeCategory.UNKNOWN,
                severity=Severity.NORMAL,
                confidence=0.0,
                suggested_action="review",
                summary_ko="AI 분류 비활성화",
                classifier_used="disabled",
            )
            for z in zones
        ]

    # ----- Stage 1 — keyword heuristic (always) -----
    stage1_results: list[ChangeClassification] = []
    for zone in zones:
        if not isinstance(zone, dict):
            continue
        try:
            result = classify_zone_heuristic(zone)
        except Exception as exc:  # noqa: BLE001
            logger.exception("heuristic classifier crashed for zone %s",
                             (zone or {}).get("zone_id"))
            result = ChangeClassification(
                zone_id=str((zone or {}).get("zone_id") or ""),
                category=ChangeCategory.UNKNOWN,
                severity=Severity.NORMAL,
                confidence=0.0,
                suggested_action="review",
                summary_ko=f"분류 오류: {exc}",
                classifier_used="error",
            )
        stage1_results.append(result)

    # ----- Stage 2 — embedding cosine (opt-in) -----
    # Only invoked when the user explicitly opts in. The dispatcher's
    # first call blocks for ~3-5 s on cold start (loads the GGUF) — we
    # protect against that latency hitting the user via prepare_async()
    # in the workbench bootstrap, but even if it wasn't pre-warmed,
    # only the first batch pays the cost.
    pre_stage2_results = list(stage1_results)  # snapshot for Stage-3
    if cfg.use_embedding:
        try:
            stage1_results = _apply_stage2(stage1_results, zones, cfg)
        except Exception:  # noqa: BLE001
            # Defensive — Stage-2 failures NEVER affect Stage-1 output.
            logger.exception("Stage-2 embedding cascade crashed; "
                             "keeping Stage-1 results")

    # ----- Stage 3 — LLM (opt-in) -----
    # Only invoked for zones the dispatcher's should_invoke() flags as
    # ambiguous (Stage-2 abstained or top-1 confidence below threshold).
    # Confident Stage-2 zones skip the LLM round-trip entirely.
    if cfg.use_llm:
        try:
            stage1_results = _apply_stage3(
                stage1_results, pre_stage2_results, zones, cfg,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Stage-3 LLM cascade crashed; "
                             "keeping Stage-2 results")

    return stage1_results


# ---------------------------------------------------------------------------
# Stage-2 cascade
# ---------------------------------------------------------------------------


def _apply_stage2(
    stage1_results: list[ChangeClassification],
    zones: Sequence[dict],
    cfg: AiClassifierConfig,
) -> list[ChangeClassification]:
    """Apply embedding tier on top of Stage-1 results.

    Replacement policy: a Stage-2 result REPLACES the matching
    Stage-1 result when present (None = abstain → keep Stage-1). The
    classifier_used field becomes "embedding" so downstream telemetry
    can attribute the call. Stage-1 confidence/category are preserved
    in raw_evidence for diagnostics.

    Returns the same list (possibly with replacements). Order matches
    ``stage1_results`` exactly.
    """

    # Lazy import — keeps the heuristic-only fast path free of numpy /
    # backend imports until the user actually opts into embeddings.
    try:
        from .embedding_classifier import get_embedding_dispatcher
    except ImportError:
        logger.warning(
            "embedding_classifier import failed — Stage-2 disabled "
            "(numpy / backend module missing?)",
            exc_info=True,
        )
        return stage1_results

    dispatcher = get_embedding_dispatcher(cfg)

    # Build a zone_id → zone dict map so we can re-pair Stage-1 result
    # with the original zone (the input list may contain non-dicts that
    # were silently skipped during Stage-1).
    zone_by_id: dict[str, dict] = {}
    for z in zones:
        if isinstance(z, dict):
            zid = str(z.get("zone_id") or "")
            if zid:
                zone_by_id[zid] = z

    out: list[ChangeClassification] = []
    for s1 in stage1_results:
        zone = zone_by_id.get(s1.zone_id)
        if zone is None:
            out.append(s1)
            continue
        try:
            s2 = dispatcher.classify_zone(zone)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Stage-2 classify_zone crashed for %s; keeping Stage-1",
                s1.zone_id,
            )
            out.append(s1)
            continue
        if s2 is None:
            out.append(s1)
            continue
        # Stage-2 wins — but stash Stage-1 metadata for diagnostics so
        # the workbench panel can still surface "heuristic said X,
        # embedding said Y" when both disagree.
        merged_evidence = dict(s2.raw_evidence or {})
        merged_evidence["stage1_category"] = s1.category.value
        merged_evidence["stage1_confidence"] = float(s1.confidence)
        merged_evidence["stage1_summary"] = s1.summary_ko
        out.append(ChangeClassification(
            zone_id=s2.zone_id,
            category=s2.category,
            severity=s2.severity,
            confidence=s2.confidence,
            suggested_action=s2.suggested_action,
            summary_ko=s2.summary_ko,
            kds_references=list(s2.kds_references),
            classifier_used=s2.classifier_used,
            elapsed_ms=s2.elapsed_ms,
            raw_evidence=merged_evidence,
        ))
    return out


# ---------------------------------------------------------------------------
# Stage-3 cascade
# ---------------------------------------------------------------------------


def _apply_stage3(
    current_results: list[ChangeClassification],
    stage1_snapshot: list[ChangeClassification],
    zones: Sequence[dict],
    cfg: AiClassifierConfig,
) -> list[ChangeClassification]:
    """Apply Stage-3 LLM tier on top of current (Stage-2 or Stage-1)
    results.

    Replacement policy:
      * dispatcher.should_invoke(s2) decides which zones get LLM.
        Confident Stage-2 zones (above llm_invoke_below_confidence)
        skip — no LLM round-trip.
      * LLM result REPLACES the input result with classifier_used=
        "hybrid" + raw_evidence carrying lower-tier metadata + the
        LLM rationale.
      * LLM abstain → keep the input result as-is.

    The dispatcher's classify_zone NEVER raises (it abstains via
    None on any error), so this method's try/except is just defensive
    against unexpected import / config issues.
    """

    try:
        from .llm_classifier import get_llm_dispatcher
    except ImportError:
        logger.warning(
            "llm_classifier import failed — Stage-3 disabled "
            "(llm_backends module missing?)",
            exc_info=True,
        )
        return current_results

    dispatcher = get_llm_dispatcher(cfg)

    # Build zone_id → zone dict map (same pattern as _apply_stage2)
    zone_by_id: dict[str, dict] = {}
    for z in zones:
        if isinstance(z, dict):
            zid = str(z.get("zone_id") or "")
            if zid:
                zone_by_id[zid] = z

    # Index Stage-1 snapshot by zone_id so we can pass through to
    # the LLM result for diagnostics.
    stage1_by_id: dict[str, ChangeClassification] = {
        s1.zone_id: s1 for s1 in stage1_snapshot
    }

    out: list[ChangeClassification] = []
    for s2 in current_results:
        zone = zone_by_id.get(s2.zone_id)
        s1 = stage1_by_id.get(s2.zone_id, s2)
        if zone is None:
            out.append(s2)
            continue
        if not dispatcher.should_invoke(s2):
            # Stage-2 was confident enough — skip LLM
            out.append(s2)
            continue
        # Extract candidate categories from Stage-2 raw_evidence.
        # Falls back to (s2.category,) if Stage-2 didn't surface
        # top_categories (e.g. when s2 is actually a Stage-1
        # heuristic result that passed through unchanged).
        candidates = _extract_candidates(s2)
        try:
            s3 = dispatcher.classify_zone(
                zone, s1, s2,
                candidate_categories=candidates,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Stage-3 classify_zone crashed for %s; keeping input",
                s2.zone_id,
            )
            out.append(s2)
            continue
        if s3 is None:
            out.append(s2)
            continue
        out.append(s3)
    return out


def _extract_candidates(
    result: ChangeClassification,
) -> list[ChangeCategory]:
    """Pull top-K candidate categories from a Stage-2 result's
    raw_evidence. Falls back to ``[result.category]`` when the
    raw_evidence doesn't carry a ranked candidate list (e.g.
    Stage-1-only results passing through)."""

    raw = result.raw_evidence or {}
    raw_top = raw.get("top_categories") or []
    candidates: list[ChangeCategory] = []
    for entry in raw_top:
        # entry shape: ("structural_member", 0.92) — defensive parse
        try:
            value = entry[0] if isinstance(entry, (list, tuple)) else entry
            cat = ChangeCategory(str(value))
        except (KeyError, ValueError, TypeError, IndexError):
            continue
        if cat not in candidates:
            candidates.append(cat)
    if not candidates:
        candidates = [result.category]
    return candidates


__all__ = ["classify_zones"]
