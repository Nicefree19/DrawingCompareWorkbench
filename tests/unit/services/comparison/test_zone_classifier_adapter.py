# -*- coding: utf-8 -*-
"""Phase N integration tests for the workbench ↔ AI cascade adapter.

These tests guard against the regression that motivated Phase N:
the 3-tier AI classifier cascade was wired into the public_api but
the workbench's actual zone classification flow bypassed it entirely
(it called zone_classifier.classify_zone directly). Unit tests of
the cascade in isolation passed for 12 commits while production users
saw zero benefit from Quality / LLM / RAG modes.

The new tests assert the integration contract:

  1. Schema bridge — ChangeClassification → ZoneCategoryResult
     produces a valid result for every ChangeCategory enum value
     and tier marker shows up in rationale_ko.

  2. Routing — classify_zone_with_cascade(zone, cfg) routes to:
     * heuristic when use_embedding=False AND use_llm=False
     * cascade when either flag is True
     * heuristic when cfg=None
     * heuristic when cascade raises (defensive)

  3. End-to-end — feeding a real overlay-shaped zone through the
     adapter with hybrid_mode() produces a ZoneCategoryResult with
     a Korean category label + AI tier marker in the rationale.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Schema bridge
# ---------------------------------------------------------------------------


def test_adapter_handles_every_change_category() -> None:
    """Every value of ChangeCategory enum must map to a non-None
    workbench label (no orphan enum values)."""
    from src.services.comparison.ai_classifier import (
        ChangeCategory, ChangeClassification, Severity,
    )
    from src.services.comparison.zone_classifier_adapter import (
        adapt_change_classification,
    )

    for cat in ChangeCategory:
        cls = ChangeClassification(
            zone_id="z1", category=cat, severity=Severity.NORMAL,
            confidence=0.5, suggested_action="review",
            summary_ko=f"테스트 {cat.value}",
            classifier_used="heuristic",
        )
        result = adapt_change_classification(cls)
        assert result.category, f"empty label for {cat}"
        assert isinstance(result.confidence, float)
        assert isinstance(result.severity_boost, int)
        # Rationale carries the tier marker
        assert "[Stage-1" in result.rationale_ko or "휴리스틱" in result.rationale_ko


def test_adapter_severity_boost_critical_floats_to_top() -> None:
    """CRITICAL severity → highest boost so structural-member zones
    sort to the top of the workbench's zone list."""
    from src.services.comparison.ai_classifier import (
        ChangeCategory, ChangeClassification, Severity,
    )
    from src.services.comparison.zone_classifier_adapter import (
        adapt_change_classification,
    )

    cls_crit = ChangeClassification(
        zone_id="z1", category=ChangeCategory.STRUCTURAL_MEMBER,
        severity=Severity.CRITICAL, confidence=0.9,
        suggested_action="review", summary_ko="...",
        classifier_used="embedding",
    )
    cls_minor = ChangeClassification(
        zone_id="z2", category=ChangeCategory.NOTE,
        severity=Severity.MINOR, confidence=0.4,
        suggested_action="confirm", summary_ko="...",
        classifier_used="heuristic",
    )
    r_crit = adapt_change_classification(cls_crit)
    r_minor = adapt_change_classification(cls_minor)
    assert r_crit.severity_boost > r_minor.severity_boost


def test_adapter_tier_marker_in_rationale() -> None:
    """Rationale prefix tells the user which classifier tier produced
    the result — important for debugging + transparency."""
    from src.services.comparison.ai_classifier import (
        ChangeCategory, ChangeClassification, Severity,
    )
    from src.services.comparison.zone_classifier_adapter import (
        adapt_change_classification,
    )

    cases = [
        ("heuristic", "Stage-1 휴리스틱"),
        ("embedding", "Stage-2 임베딩"),
        ("hybrid", "Stage-3 LLM"),
        ("disabled", "AI 비활성"),
        ("error", "분류 오류"),
    ]
    for tier, expected_marker in cases:
        cls = ChangeClassification(
            zone_id="z1", category=ChangeCategory.STRUCTURAL_MEMBER,
            severity=Severity.NORMAL, confidence=0.5,
            suggested_action="review", summary_ko="테스트",
            classifier_used=tier,
        )
        result = adapt_change_classification(cls)
        assert expected_marker in result.rationale_ko, (
            f"tier {tier!r} → expected marker {expected_marker!r} "
            f"in rationale, got: {result.rationale_ko!r}"
        )


def test_adapter_handles_none_input() -> None:
    """Defensive — None input should not crash."""
    from src.services.comparison.zone_classifier_adapter import (
        adapt_change_classification,
    )

    result = adapt_change_classification(None)
    assert result.category  # non-empty fallback
    assert result.confidence == 0.0
    assert "결과 없음" in result.rationale_ko


def test_adapter_handles_dict_input() -> None:
    """Defensive — dict input (instead of dataclass) should adapt
    correctly. This is paranoid: the cascade returns dataclasses,
    but if a future change passes dicts (e.g. from JSON deserialise),
    the adapter should still produce something sensible."""
    from src.services.comparison.zone_classifier_adapter import (
        adapt_change_classification,
    )

    result = adapt_change_classification({
        "category": "structural_member",
        "severity": "critical",
        "confidence": 0.8,
        "summary_ko": "보 단면 변경",
        "classifier_used": "embedding",
    })
    assert "구조" in result.category  # mapped to CATEGORY_STRUCTURAL_MEMBER
    assert result.confidence == 0.8
    assert "Stage-2 임베딩" in result.rationale_ko


# ---------------------------------------------------------------------------
# Routing — classify_zone_with_cascade
# ---------------------------------------------------------------------------


def test_routing_heuristic_only_when_config_none() -> None:
    """No config supplied → fast path (heuristic only).
    The result MUST NOT have any AI tier marker — pure heuristic
    rationale only."""
    from src.services.comparison.zone_classifier_adapter import (
        classify_zone_with_cascade,
    )

    zone = {
        "zone_id": "z1", "layer": "BEAM",
        "change_type": "modified", "raw_change_count": 3,
    }
    result = classify_zone_with_cascade(zone, config=None)
    # The heuristic path doesn't add the tier marker prefix
    assert "Stage-1" not in result.rationale_ko
    assert "Stage-2" not in result.rationale_ko
    assert "Stage-3" not in result.rationale_ko


def test_routing_heuristic_when_use_embedding_false_and_use_llm_false() -> None:
    """heuristic_only() config → fast path."""
    from src.services.comparison.ai_classifier import AiClassifierConfig
    from src.services.comparison.zone_classifier_adapter import (
        classify_zone_with_cascade,
    )

    cfg = AiClassifierConfig.heuristic_only()
    result = classify_zone_with_cascade(
        {"zone_id": "z1", "layer": "BEAM", "change_type": "modified"},
        config=cfg,
    )
    # No tier marker → confirmed heuristic path
    assert "Stage-" not in result.rationale_ko


def test_routing_cascade_when_use_embedding_true() -> None:
    """Setting use_embedding=True must route through the cascade.
    The result MUST carry an AI tier marker (proving the cascade ran)."""
    from src.services.comparison.ai_classifier import AiClassifierConfig
    from src.services.comparison.zone_classifier_adapter import (
        classify_zone_with_cascade,
    )

    # Use auto_mode — falls back to Stage-1 heuristic when no
    # embedding model is on disk (which is the case in CI). The
    # adapter still routes through the cascade; cascade produces a
    # heuristic result with classifier_used="heuristic" because no
    # backend is available. The adapter then adds [Stage-1 휴리스틱]
    # marker, proving the cascade DID run (not the bypass path).
    cfg = AiClassifierConfig(
        enabled=True, use_embedding=True, use_llm=False,
        embedding_backend_id="auto",
    )
    result = classify_zone_with_cascade(
        {"zone_id": "z1", "layer": "BEAM", "change_type": "modified"},
        config=cfg,
    )
    # The cascade ran → tier marker present → integration is wired
    assert "[Stage-" in result.rationale_ko, (
        f"cascade was NOT invoked — result.rationale_ko = "
        f"{result.rationale_ko!r}. This is the regression Phase N "
        f"existed to fix."
    )


def test_routing_cascade_when_use_llm_true() -> None:
    """use_llm=True → cascade. With stub_llm backend the LLM fires
    and the result classifier_used should reflect that."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, clear_llm_dispatcher_cache,
    )
    from src.services.comparison.zone_classifier_adapter import (
        classify_zone_with_cascade,
    )

    clear_llm_dispatcher_cache()
    cfg = AiClassifierConfig(
        enabled=True, use_embedding=False, use_llm=True,
        llm_backend_id="stub_llm",
    )
    result = classify_zone_with_cascade(
        {"zone_id": "z1", "layer": "BEAM", "change_type": "modified",
         "text_snippet": "보 단면 변경"},
        config=cfg,
    )
    # Stage-3 stub LLM should have fired → "Stage-3 LLM" marker
    assert "[Stage-3 LLM]" in result.rationale_ko, (
        f"LLM cascade did NOT fire — result.rationale_ko = "
        f"{result.rationale_ko!r}"
    )


def test_routing_falls_back_to_heuristic_when_cascade_raises(monkeypatch) -> None:
    """If cascade raises (which it shouldn't per its contract), the
    adapter falls back to bare heuristic instead of crashing. This
    is the safety net for future changes that might break the
    cascade's never-raise contract."""
    from src.services.comparison.ai_classifier import AiClassifierConfig
    from src.services.comparison.zone_classifier_adapter import (
        classify_zone_with_cascade,
    )

    def raising_classify(zones, *, config=None):
        raise RuntimeError("forced for test")

    monkeypatch.setattr(
        "src.services.comparison.ai_classifier.classify_zones",
        raising_classify,
    )

    cfg = AiClassifierConfig(use_embedding=True, embedding_backend_id="auto")
    # Should NOT raise, should fall back to heuristic
    result = classify_zone_with_cascade(
        {"zone_id": "z1", "layer": "BEAM", "change_type": "modified"},
        config=cfg,
    )
    assert result.category  # got SOMETHING back, didn't crash
    # Heuristic fallback → no tier marker
    assert "[Stage-" not in result.rationale_ko


def test_routing_disabled_config_uses_heuristic() -> None:
    """enabled=False → still heuristic (preserves last-known
    classification, doesn't drop to no-op)."""
    from src.services.comparison.ai_classifier import AiClassifierConfig
    from src.services.comparison.zone_classifier_adapter import (
        classify_zone_with_cascade,
    )

    cfg = AiClassifierConfig(enabled=False, use_embedding=True, use_llm=True)
    result = classify_zone_with_cascade(
        {"zone_id": "z1", "layer": "BEAM", "change_type": "modified"},
        config=cfg,
    )
    # disabled → heuristic fast path (no tier marker)
    assert "[Stage-" not in result.rationale_ko


# ---------------------------------------------------------------------------
# Workbench integration — end-to-end via the adapter
# ---------------------------------------------------------------------------


def test_workbench_overlay_shape_classified_via_cascade() -> None:
    """Real workbench-shape overlay (layer + change_type +
    raw_change_count, NO text_snippet — pipeline doesn't populate
    that field) routes through the cascade without crashing.

    This is the test that would have caught the Phase N regression
    from day 1: the cascade actually accepts what the workbench
    gives it, and the adapter produces something the UI can display.
    """
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, clear_llm_dispatcher_cache,
    )
    from src.services.comparison.zone_classifier_adapter import (
        classify_zone_with_cascade,
    )

    clear_llm_dispatcher_cache()
    # Full hybrid mode (uses stub LLM by default)
    cfg = AiClassifierConfig.hybrid_mode()

    # Realistic workbench overlay shape — no text_snippet (the pipeline
    # doesn't carry OCR'd text), all the fields are heuristic-friendly
    overlay = {
        "zone_id": "real_zone_123",
        "pair_id": "p1",
        "drawing_number": "S20-0002",
        "layer": "BEAM",
        "entity_types": ["LWPOLYLINE", "MTEXT"],
        "change_type": "modified",
        "raw_change_count": 7,
        "severity": "high",
        "bbox": [100.0, 200.0, 300.0, 400.0],
    }
    result = classify_zone_with_cascade(overlay, config=cfg)
    # Cascade ran (tier marker present)
    assert "[Stage-" in result.rationale_ko
    # Result has the workbench-expected fields
    assert isinstance(result.category, str) and result.category
    assert 0.0 <= result.confidence <= 1.0
    assert isinstance(result.severity_boost, int)
    assert result.rationale_ko


def test_workbench_overlay_shape_classified_via_heuristic_only() -> None:
    """Mirror of the previous test for the heuristic-only path —
    verifies the fast path still produces a valid result."""
    from src.services.comparison.ai_classifier import AiClassifierConfig
    from src.services.comparison.zone_classifier_adapter import (
        classify_zone_with_cascade,
    )

    cfg = AiClassifierConfig.heuristic_only()
    overlay = {
        "zone_id": "real_zone_456",
        "layer": "BEAM",
        "entity_types": ["LWPOLYLINE"],
        "change_type": "modified",
        "raw_change_count": 7,
    }
    result = classify_zone_with_cascade(overlay, config=cfg)
    assert result.category
    # Heuristic result — no tier marker
    assert "[Stage-" not in result.rationale_ko
