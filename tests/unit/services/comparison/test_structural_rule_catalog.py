from __future__ import annotations

from src.services.comparison.structural_rule_catalog import (
    classify_domain_patterns,
    is_grid_layer,
    is_structural_layer,
    looks_like_dimension_text,
)


def test_layer_context_tags_plain_grid_label() -> None:
    tags, matches = classify_domain_patterns(
        entity_type="text",
        text="A1",
        layer_name="S-GRID",
    )

    assert "grid" in tags
    assert "structural_layer" in tags
    assert matches[0] == {"kind": "grid", "value": "A1", "confidence": "medium"}


def test_korean_member_and_section_terms_are_explicit_matches() -> None:
    member_tags, member_matches = classify_domain_patterns(
        entity_type="text",
        text="기둥 C1",
        layer_name="구조-기둥",
    )
    section_tags, section_matches = classify_domain_patterns(
        entity_type="text",
        text="상세 S-501/2",
        layer_name="S-SEC",
    )

    assert "member_tag" in member_tags
    assert any(match["value"] == "기둥 C1" for match in member_matches)
    assert "section_reference" in section_tags
    assert any(match["kind"] == "section_reference" for match in section_matches)


def test_dimension_detection_requires_dimension_context() -> None:
    note_tags, _ = classify_domain_patterns(
        entity_type="text",
        text="NOTE Line 2",
        layer_name="S-NOTE",
    )
    dim_tags, _ = classify_domain_patterns(
        entity_type="text",
        text="4000",
        layer_name="S-DIM",
    )

    assert "dimension" not in note_tags
    assert "dimension" in dim_tags
    assert looks_like_dimension_text("4000", "S-DIM") is True


def test_layer_hint_helpers_are_conservative() -> None:
    assert is_grid_layer("S-GRID") is True
    assert is_structural_layer("구조-기둥") is True
    assert is_grid_layer("S-NOTE") is False
