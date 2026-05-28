from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from src.services.comparison.import_pipeline import ImportPipeline
from src.services.comparison.structural_evidence_analyzer import (
    SCHEMA_VERSION,
    analyze_structural_evidence,
)


ROOT = Path(__file__).resolve().parents[4]
SCHEMA_PATH = ROOT / "docs" / "schemas" / "structural-drawing-evidence-v0.1.schema.json"
DXF_DIR = ROOT / "tests" / "data" / "comparison" / "cad_samples" / "dxf"


def _dxf(*lines: object) -> str:
    return "\n".join(str(line) for line in lines) + "\n"


def _section(name: str, *body: object) -> list[object]:
    return ["0", "SECTION", "2", name, *body, "0", "ENDSEC"]


def _header() -> list[object]:
    return _section("HEADER", "9", "$ACADVER", "1", "AC1032", "9", "$INSUNITS", "70", "4")


def _tables(*layer_names: str) -> list[object]:
    body: list[object] = ["0", "TABLE", "2", "LAYER"]
    for idx, name in enumerate(layer_names or ("0",)):
        body.extend(["0", "LAYER", "2", name, "70", "0", "62", str(7 + idx), "6", "Continuous"])
    body.extend(["0", "ENDTAB"])
    return _section("TABLES", *body)


def _wrap(*sections: object) -> str:
    return _dxf(*sections, "0", "EOF")


def _validate_schema(packet: dict) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(packet)


def test_builds_schema_valid_packet_with_keyword_evidence() -> None:
    imported = ImportPipeline().import_file(DXF_DIR / "text_centered.dxf")

    packet = analyze_structural_evidence(
        imported,
        question="Find GRID-A1 and C1",
        max_evidence=2,
    )

    _validate_schema(packet)
    assert packet["schema_version"] == SCHEMA_VERSION
    assert packet["status"] == "ok"
    assert packet["source"]["source_health"] == "parsed"
    assert packet["summary"]["judgment_level"] == "issue_suggestion_only"
    assert packet["summary"]["requires_human_review"] is True
    assert len(packet["evidence"]) <= 2
    assert packet["artifact_paths"] == {}
    assert "canonical_drawing" not in packet
    assert any("GRID-A1" in item["anchor_text"] for item in packet["evidence"])
    assert all(len(item["nearby_entities"]) <= 8 for item in packet["evidence"])
    assert all(item["source_kind"] == "drawing_anchor" for item in packet["evidence"])


def test_questionless_review_returns_bounded_anchor_overview() -> None:
    imported = ImportPipeline().import_file(DXF_DIR / "text_centered.dxf")

    packet = analyze_structural_evidence(imported, max_evidence=30)

    _validate_schema(packet)
    assert packet["intent"]["name"] == "general_review"
    assert packet["question"]["keywords"] == []
    assert packet["summary"]["evidence_count"] == len(packet["evidence"])
    assert 1 <= len(packet["evidence"]) <= 30


def test_partial_import_exposes_unsupported_counts_without_hiding_failure_mode() -> None:
    imported = ImportPipeline().import_file(DXF_DIR / "unsupported_objects.dxf")

    packet = analyze_structural_evidence(
        imported,
        question="Review unsupported objects",
        max_evidence=30,
    )

    _validate_schema(packet)
    assert packet["status"] == "partial"
    assert packet["source"]["source_health"] == "partial"
    assert packet["unsupported_counts"] == {
        "3DSOLID": 1,
        "ACAD_PROXY_ENTITY": 1,
        "IMAGE": 1,
    }
    assert any(
        item["kind"] == "unsupported_content_review"
        for item in packet["issue_suggestions"]
    )
    assert packet["summary"]["requires_human_review"] is True


def test_domain_patterns_tag_grid_member_section_and_dimension(tmp_path) -> None:
    drawing = _wrap(
        *_header(),
        *_tables("S-GRID", "S-COL", "S-DIM", "S-NOTE"),
        *_section("BLOCKS"),
        *_section(
            "ENTITIES",
            "0", "TEXT", "5", "TGRID", "8", "S-GRID",
            "10", "0", "20", "0", "40", "2.5", "1", "GRID-A1",
            "0", "TEXT", "5", "TCOL", "8", "S-COL",
            "10", "5", "20", "5", "40", "2.5", "1", "COLUMN C1",
            "0", "TEXT", "5", "TREF", "8", "S-NOTE",
            "10", "10", "20", "10", "40", "2.5", "1", "SEE S-301/A",
            "0", "DIMENSION", "5", "D1", "8", "S-DIM",
            "10", "0", "20", "0", "13", "0", "23", "0", "14", "4", "24", "0",
            "11", "2", "21", "1", "42", "4000", "1", "4000",
        ),
    )
    path = tmp_path / "domain_patterns.dxf"
    path.write_text(drawing, encoding="utf-8")
    imported = ImportPipeline().import_file(path)

    packet = analyze_structural_evidence(
        imported,
        question="Check missing section reference for grid A1 column C1 dimension 4000",
        checklist=["section reference check", "member tag check"],
        max_evidence=10,
    )

    _validate_schema(packet)
    by_text = {item["anchor_text"]: item for item in packet["evidence"]}
    assert "grid" in by_text["GRID-A1"]["domain_tags"]
    assert "member_tag" in by_text["COLUMN C1"]["domain_tags"]
    assert "section_reference" in by_text["SEE S-301/A"]["domain_tags"]
    assert "dimension" in by_text["4000"]["domain_tags"]
    assert by_text["GRID-A1"]["pattern_matches"][0]["kind"] == "grid"
    assert packet["intent"]["name"] == "check_missing"
    suggestion = packet["issue_suggestions"][0]
    evidence_ids = {item["evidence_id"] for item in packet["evidence"]}
    assert suggestion["kind"] == "missing_reference_review"
    assert suggestion["judgment_level"] == "issue_suggestion_only"
    assert suggestion["human_review_required"] is True
    assert set(suggestion["evidence_ids"]).issubset(evidence_ids)


def test_note_line_number_is_not_tagged_as_dimension(tmp_path) -> None:
    drawing = _wrap(
        *_header(),
        *_tables("S-NOTE"),
        *_section("BLOCKS"),
        *_section(
            "ENTITIES",
            "0", "TEXT", "5", "TNOTE", "8", "S-NOTE",
            "10", "0", "20", "0", "40", "2.5", "1", "NOTE Line 2",
        ),
    )
    path = tmp_path / "note_line_number.dxf"
    path.write_text(drawing, encoding="utf-8")
    imported = ImportPipeline().import_file(path)

    packet = analyze_structural_evidence(imported, question="Review note", max_evidence=5)

    _validate_schema(packet)
    note = packet["evidence"][0]
    assert note["anchor_text"] == "NOTE Line 2"
    assert "note" in note["domain_tags"]
    assert "dimension" not in note["domain_tags"]


def test_korean_structural_terms_and_layer_context_are_tagged(tmp_path) -> None:
    drawing = _wrap(
        *_header(),
        *_tables("S-GRID", "구조-기둥", "S-SEC"),
        *_section("BLOCKS"),
        *_section(
            "ENTITIES",
            "0", "TEXT", "5", "TGRID", "8", "S-GRID",
            "10", "0", "20", "0", "40", "2.5", "1", "A1",
            "0", "TEXT", "5", "TKCOL", "8", "구조-기둥",
            "10", "5", "20", "5", "40", "2.5", "1", "기둥 C1",
            "0", "TEXT", "5", "TSEC", "8", "S-SEC",
            "10", "10", "20", "10", "40", "2.5", "1", "상세 S-501/2",
        ),
    )
    path = tmp_path / "korean_structural_terms.dxf"
    path.write_text(drawing, encoding="utf-8")
    imported = ImportPipeline().import_file(path)

    packet = analyze_structural_evidence(
        imported,
        question="A1 그리드 기둥 C1 상세 참조 검토",
        max_evidence=10,
    )

    _validate_schema(packet)
    by_text = {item["anchor_text"]: item for item in packet["evidence"]}
    assert "grid" in by_text["A1"]["domain_tags"]
    assert "structural_layer" in by_text["A1"]["domain_tags"]
    assert by_text["A1"]["pattern_matches"][0]["kind"] == "grid"
    assert "member_tag" in by_text["기둥 C1"]["domain_tags"]
    assert any(match["value"] == "기둥 C1" for match in by_text["기둥 C1"]["pattern_matches"])
    assert "section_reference" in by_text["상세 S-501/2"]["domain_tags"]
