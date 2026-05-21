# -*- coding: utf-8 -*-
"""Tests for the Phase H Stage-1 heuristic classifier.

Pure-Python — no model download, no LLM call. Pins the keyword
matching contract so future "improvements" can't silently regress
the customer-facing labels.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Layer-name → category dispatch
# ---------------------------------------------------------------------------


def test_beam_layer_classifies_as_structural_member() -> None:
    from src.services.comparison.ai_classifier import classify_zones, ChangeCategory
    out = classify_zones([{"zone_id": "z1", "layer": "BEAM", "change_type": "added"}])
    assert len(out) == 1
    assert out[0].category == ChangeCategory.STRUCTURAL_MEMBER
    assert out[0].confidence >= 0.85


def test_column_layer_classifies_as_structural_member() -> None:
    from src.services.comparison.ai_classifier import classify_zones, ChangeCategory
    out = classify_zones([{"zone_id": "z1", "layer": "STR_COLUMN", "change_type": "modified"}])
    assert out[0].category == ChangeCategory.STRUCTURAL_MEMBER


def test_dim_layer_classifies_as_dimension() -> None:
    from src.services.comparison.ai_classifier import classify_zones, ChangeCategory
    out = classify_zones([{"zone_id": "z1", "layer": "DIM_2", "change_type": "modified"}])
    assert out[0].category == ChangeCategory.DIMENSION


def test_grid_layer_classifies_as_grid() -> None:
    from src.services.comparison.ai_classifier import classify_zones, ChangeCategory
    out = classify_zones([{"zone_id": "z1", "layer": "GRID_X", "change_type": "moved"}])
    assert out[0].category == ChangeCategory.GRID


def test_text_layer_classifies_as_text_label() -> None:
    from src.services.comparison.ai_classifier import classify_zones, ChangeCategory
    out = classify_zones([{"zone_id": "z1", "layer": "BEAM_TEXT", "change_type": "added"}])
    # Text layer wins over BEAM
    assert out[0].category == ChangeCategory.TEXT_LABEL


def test_korean_layer_names_match() -> None:
    from src.services.comparison.ai_classifier import classify_zones, ChangeCategory
    cases = [
        ("보_레이어", ChangeCategory.STRUCTURAL_MEMBER),
        ("기둥_S1", ChangeCategory.STRUCTURAL_MEMBER),
        ("그리드", ChangeCategory.GRID),
        ("치수_층", ChangeCategory.DIMENSION),
    ]
    for layer, expected_cat in cases:
        out = classify_zones([{"zone_id": "z1", "layer": layer, "change_type": "added"}])
        assert out[0].category == expected_cat, f"layer {layer!r} → {out[0].category} (expected {expected_cat})"


# ---------------------------------------------------------------------------
# Entity-type fallback
# ---------------------------------------------------------------------------


def test_entity_type_used_when_layer_unhelpful() -> None:
    from src.services.comparison.ai_classifier import classify_zones, ChangeCategory
    out = classify_zones([{
        "zone_id": "z1", "layer": "UNKNOWN_LAYER",
        "entity_type": "DIMENSION", "change_type": "modified",
    }])
    assert out[0].category == ChangeCategory.DIMENSION


def test_pdf_text_entity_classifies_as_text_label() -> None:
    from src.services.comparison.ai_classifier import classify_zones, ChangeCategory
    out = classify_zones([{
        "zone_id": "z1", "layer": "PDF_PAGE_1",
        "entity_type": "PDF_TEXT", "change_type": "modified",
    }])
    assert out[0].category == ChangeCategory.TEXT_LABEL


# ---------------------------------------------------------------------------
# Severity mapping
# ---------------------------------------------------------------------------


def test_structural_member_is_critical_severity() -> None:
    from src.services.comparison.ai_classifier import classify_zones, Severity
    out = classify_zones([{"zone_id": "z1", "layer": "BEAM", "change_type": "added"}])
    assert out[0].severity == Severity.CRITICAL


def test_text_label_is_minor_severity() -> None:
    from src.services.comparison.ai_classifier import classify_zones, Severity
    out = classify_zones([{"zone_id": "z1", "layer": "TEXT", "change_type": "modified"}])
    assert out[0].severity == Severity.MINOR


def test_pdf_page_layer_forces_minor_severity() -> None:
    """PDF_PAGE_x layer (visual diff) shouldn't escalate to critical
    just because the entity_type matches a structural shape."""

    from src.services.comparison.ai_classifier import classify_zones, Severity
    out = classify_zones([{
        "zone_id": "z1", "layer": "PDF_PAGE_1",
        "entity_type": "LWPOLYLINE", "change_type": "modified",
    }])
    assert out[0].severity == Severity.MINOR


# ---------------------------------------------------------------------------
# Korean summary
# ---------------------------------------------------------------------------


def test_summary_contains_korean_change_type() -> None:
    from src.services.comparison.ai_classifier import classify_zones
    cases = [("added", "추가"), ("deleted", "삭제"),
             ("modified", "수정"), ("moved", "이동")]
    for ct, expected in cases:
        out = classify_zones([{"zone_id": "z1", "layer": "BEAM",
                               "change_type": ct, "raw_change_count": 3}])
        assert expected in out[0].summary_ko, f"{ct} → {out[0].summary_ko}"


def test_summary_includes_count_when_present() -> None:
    from src.services.comparison.ai_classifier import classify_zones
    out = classify_zones([{"zone_id": "z1", "layer": "BEAM",
                           "change_type": "added", "raw_change_count": 5}])
    assert "5건" in out[0].summary_ko


def test_summary_includes_text_snippet_when_present() -> None:
    from src.services.comparison.ai_classifier import classify_zones
    out = classify_zones([{
        "zone_id": "z1", "layer": "DIM",
        "change_type": "modified",
        "text_snippet": "5000mm → 5500mm",
    }])
    assert "5000mm" in out[0].summary_ko


# ---------------------------------------------------------------------------
# Fallbacks + edge cases
# ---------------------------------------------------------------------------


def test_empty_zone_returns_unknown_classification() -> None:
    from src.services.comparison.ai_classifier import classify_zones, ChangeCategory
    out = classify_zones([{"zone_id": "z1"}])
    assert out[0].category == ChangeCategory.UNKNOWN
    assert out[0].confidence == 0.3
    assert out[0].classifier_used == "heuristic"


def test_disabled_config_returns_disabled_label() -> None:
    from src.services.comparison.ai_classifier import classify_zones, AiClassifierConfig
    cfg = AiClassifierConfig(enabled=False)
    out = classify_zones([{"zone_id": "z1", "layer": "BEAM"}], config=cfg)
    assert out[0].classifier_used == "disabled"
    assert "비활성화" in out[0].summary_ko


def test_empty_input_returns_empty_list() -> None:
    from src.services.comparison.ai_classifier import classify_zones
    assert classify_zones([]) == []


def test_non_dict_zones_skipped_safely() -> None:
    from src.services.comparison.ai_classifier import classify_zones
    out = classify_zones([
        {"zone_id": "z1", "layer": "BEAM"},
        "garbage",        # type: ignore[list-item]
        None,             # type: ignore[list-item]
        42,               # type: ignore[list-item]
        {"zone_id": "z2", "layer": "DIM"},
    ])
    # Only the 2 valid dicts make it through
    assert len(out) == 2
    assert out[0].zone_id == "z1"
    assert out[1].zone_id == "z2"


def test_all_classifications_have_required_fields() -> None:
    """Schema contract — every result has every field populated."""

    from src.services.comparison.ai_classifier import classify_zones
    out = classify_zones([{"zone_id": "z1", "layer": "BEAM",
                           "change_type": "added", "raw_change_count": 1}])
    c = out[0]
    assert isinstance(c.zone_id, str)
    assert c.category is not None
    assert c.severity is not None
    assert 0.0 <= c.confidence <= 1.0
    assert c.suggested_action in {"confirm", "review", "ignore"}
    assert c.summary_ko
    assert isinstance(c.kds_references, list)
    assert c.classifier_used
    assert c.elapsed_ms >= 0.0


# ---------------------------------------------------------------------------
# Realistic zones from the user's S20 dataset + 17-change PDF revision
# ---------------------------------------------------------------------------


def test_user_pdf_revision_zones_classify_sensibly() -> None:
    """The 17 zones from 01.3PG1 vs 02.3PG1_R1 are PDF text/dimension
    changes. None should land as STRUCTURAL_MEMBER (would falsely
    elevate severity to CRITICAL)."""

    from src.services.comparison.ai_classifier import classify_zones, Severity
    sample_pdf_zones = [
        {"zone_id": "C-001", "layer": "PDF_PAGE_1", "entity_type": "PDF_TEXT",
         "change_type": "modified", "raw_change_count": 1},
        {"zone_id": "C-002", "layer": "PDF_PAGE_1", "entity_type": "PDF_TEXT",
         "change_type": "modified", "raw_change_count": 2},
    ]
    out = classify_zones(sample_pdf_zones)
    # All PDF_PAGE_x layer zones get minor severity (visual diff only)
    assert all(c.severity == Severity.MINOR for c in out)


def test_dwg_beam_change_classifies_as_critical() -> None:
    """A real S20 DWG zone on BEAM layer with 5 modifications must
    light up as critical."""

    from src.services.comparison.ai_classifier import classify_zones, Severity, ChangeCategory
    out = classify_zones([{
        "zone_id": "B12",
        "layer": "STR_BEAM",
        "entity_type": "LINE",
        "change_type": "modified",
        "raw_change_count": 5,
        "text_snippet": "H400×200×8×13",
    }])
    assert out[0].category == ChangeCategory.STRUCTURAL_MEMBER
    assert out[0].severity == Severity.CRITICAL
    # Confidence should be high — both layer + text strongly match
    assert out[0].confidence >= 0.85


# ---------------------------------------------------------------------------
# Phase I review fix #4 — Unicode / NFKC normalisation regression guards
# ---------------------------------------------------------------------------


def test_fullwidth_beam_layer_classifies_correctly() -> None:
    """CAD exports sometimes emit fullwidth ASCII (ＢＥＡＭ instead of
    BEAM). NFKC normalisation should collapse to STRUCTURAL_MEMBER."""

    from src.services.comparison.ai_classifier import classify_zones, ChangeCategory
    out = classify_zones([{"zone_id": "z1", "layer": "ＢＥＡＭ", "change_type": "added"}])
    assert out[0].category == ChangeCategory.STRUCTURAL_MEMBER


def test_decomposed_korean_layer_classifies_correctly() -> None:
    """NFD-decomposed '보' (jamo split: ㅂ + ㅗ) must still match
    the composed '보' regex."""

    import unicodedata
    from src.services.comparison.ai_classifier import classify_zones, ChangeCategory
    decomposed = unicodedata.normalize("NFD", "보")
    out = classify_zones([{"zone_id": "z1", "layer": decomposed, "change_type": "added"}])
    assert out[0].category == ChangeCategory.STRUCTURAL_MEMBER


def test_fullwidth_digits_in_text_snippet() -> None:
    """Fullwidth digits (Ｂ-１) get NFKC'd to halfwidth so the text
    keyword regex matches."""

    from src.services.comparison.ai_classifier import classify_zones, ChangeCategory
    out = classify_zones([{
        "zone_id": "z1", "layer": "UNKNOWN_LAYER",
        "entity_type": "TEXT",
        "change_type": "modified",
        "text_snippet": "Ｈ400×200×8×13",  # fullwidth H
    }])
    # Fullwidth H normalised to halfwidth → STRUCTURAL_MEMBER text match
    assert out[0].category == ChangeCategory.STRUCTURAL_MEMBER


def test_zero_width_chars_stripped() -> None:
    """BOM / zero-width joiner inserted by some exports doesn't break
    layer matching."""

    from src.services.comparison.ai_classifier import classify_zones, ChangeCategory
    layer_with_zwj = "BE‍AM"  # zero-width joiner inside BEAM
    out = classify_zones([{"zone_id": "z1", "layer": layer_with_zwj, "change_type": "added"}])
    assert out[0].category == ChangeCategory.STRUCTURAL_MEMBER
