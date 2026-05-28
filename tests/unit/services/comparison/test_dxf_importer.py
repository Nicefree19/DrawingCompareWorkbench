from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from src.services.comparison.dxf_importer import DxfImporter, DxfTokenizer


def _dxf(*lines: object) -> str:
    return "\n".join(str(line) for line in lines) + "\n"


def _section(name: str, *body: object) -> list[object]:
    return ["0", "SECTION", "2", name, *body, "0", "ENDSEC"]


def _header() -> list[object]:
    return _section(
        "HEADER",
        "9", "$ACADVER", "1", "AC1032",
        "9", "$INSUNITS", "70", "4",
    )


def _tables(*layer_names: str) -> list[object]:
    body: list[object] = ["0", "TABLE", "2", "LAYER"]
    for idx, name in enumerate(layer_names or ("0",)):
        body.extend(
            [
                "0", "LAYER",
                "2", name,
                "70", "0",
                "62", str(7 + idx),
                "6", "Continuous",
            ]
        )
    body.extend(["0", "ENDTAB"])
    return _section("TABLES", *body)


def _wrap(*sections: object) -> str:
    return _dxf(*sections, "0", "EOF")


def _load_schema() -> dict:
    schema_path = Path("docs/canonical-drawing.schema.json")
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _validate_schema(doc: dict) -> None:
    schema = _load_schema()
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(doc), key=str)
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors[:10])


def test_tokenizer_reads_group_code_value_pairs() -> None:
    tokens = DxfTokenizer(_dxf("0", "SECTION", "2", "HEADER")).tokenize()

    assert [(t.code, t.value.strip()) for t in tokens] == [
        (0, "SECTION"),
        (2, "HEADER"),
    ]
    assert tokens[0].line_no == 1


def test_importer_maps_basic_sections_entities_layers_and_bbox() -> None:
    text = _wrap(
        *_header(),
        *_tables("BEAM", "GRID"),
        *_section("BLOCKS"),
        *_section(
            "ENTITIES",
            "0", "LINE",
            "5", "10",
            "8", "BEAM",
            "10", "0", "20", "0",
            "11", "100", "21", "0",
            "0", "CIRCLE",
            "5", "11",
            "8", "GRID",
            "10", "50", "20", "50",
            "40", "10",
            "0", "TEXT",
            "5", "12",
            "8", "BEAM",
            "10", "0", "20", "20",
            "40", "2.5",
            "1", " H-400 ",
        ),
        *_section("OBJECTS", "0", "DICTIONARY", "5", "AB"),
    )

    doc = DxfImporter().import_text(text, file_name="basic.dxf")

    _validate_schema(doc)
    assert doc["schema_version"] == "canonical-drawing/v1"
    assert doc["units"]["canonical_unit"] == "mm"
    assert doc["units"]["source_unit"] == "mm"
    assert {e["type"] for e in doc["entities"]} >= {"line", "circle", "text"}
    assert len(doc["layers"]) == 3  # table layers + default 0
    assert doc["extents"]["min_x"] == 0.0
    assert doc["extents"]["max_x"] >= 100.0
    assert doc["metadata"]["object_counts"]["DICTIONARY"] == 1
    text_entity = next(e for e in doc["entities"] if e["type"] == "text")
    assert text_entity["geometry"]["canonical_text"] == "H-400"
    assert text_entity["hashes"]["semantic_hash"].startswith("sem:v1:sha256:")


def test_block_definition_and_insert_transform_are_imported() -> None:
    text = _wrap(
        *_header(),
        *_tables("0", "BEAM"),
        *_section(
            "BLOCKS",
            "0", "BLOCK",
            "2", "B1",
            "10", "0", "20", "0", "30", "0",
            "0", "LINE",
            "5", "B10",
            "8", "0",
            "10", "0", "20", "0",
            "11", "10", "21", "0",
            "0", "ENDBLK",
        ),
        *_section(
            "ENTITIES",
            "0", "INSERT",
            "5", "I1",
            "8", "BEAM",
            "2", "B1",
            "10", "100",
            "20", "200",
            "41", "2",
            "42", "2",
            "50", "90",
        ),
    )

    doc = DxfImporter(expand_blocks=True).import_text(text, file_name="block.dxf")

    _validate_schema(doc)
    assert len(doc["blocks"]) == 1
    insert = next(e for e in doc["entities"] if e["type"] == "block_reference")
    assert insert["geometry"]["block_name"] == "B1"
    assert insert["geometry"]["expanded_entity_ids"]

    expanded = [
        e for e in doc["entities"]
        if e["metadata"].get("expanded_from_insert_id") == insert["id"]
    ]
    assert len(expanded) == 1
    line = expanded[0]
    assert line["geometry"]["start"] == {"x": 100.0, "y": 200.0, "z": 0.0}
    assert line["geometry"]["end"]["x"] == 100.0
    assert line["geometry"]["end"]["y"] == 220.0
    assert insert["bbox"]["min_x"] == 100.0
    assert insert["bbox"]["max_y"] == 220.0


def test_maps_polyline_text_mtext_ellipse_spline_and_insert_attributes() -> None:
    text = _wrap(
        *_header(),
        *_tables("ANNO", "CURVE"),
        *_section("BLOCKS"),
        *_section(
            "ENTITIES",
            "0", "LWPOLYLINE",
            "5", "P1",
            "8", "CURVE",
            "70", "1",
            "10", "0", "20", "0",
            "10", "10", "20", "0", "42", "0.5",
            "10", "10", "20", "10",
            "0", "POLYLINE",
            "5", "P2",
            "8", "CURVE",
            "70", "1",
            "0", "VERTEX", "10", "20", "20", "0",
            "0", "VERTEX", "10", "25", "20", "5",
            "0", "SEQEND",
            "0", "ELLIPSE",
            "5", "E1",
            "8", "CURVE",
            "10", "50", "20", "50",
            "11", "20", "21", "0",
            "40", "0.5",
            "41", "0",
            "42", "6.283185307179586",
            "0", "SPLINE",
            "5", "S1",
            "8", "CURVE",
            "10", "0", "20", "40",
            "10", "10", "20", "45",
            "10", "20", "20", "40",
            "0", "MTEXT",
            "5", "M1",
            "8", "ANNO",
            "10", "0", "20", "80",
            "40", "2.5",
            "1", "A\\PB",
            "0", "INSERT",
            "5", "I2",
            "8", "ANNO",
            "2", "MISSING_BLOCK",
            "10", "1", "20", "2",
            "66", "1",
            "0", "ATTRIB",
            "2", "TAG1",
            "1", "VALUE1",
            "10", "1", "20", "2",
            "0", "SEQEND",
        ),
    )

    doc = DxfImporter(expand_blocks=True).import_text(text, file_name="rich.dxf")

    _validate_schema(doc)
    types = [e["type"] for e in doc["entities"]]
    assert types.count("polyline") >= 4  # LWPOLYLINE, POLYLINE, ELLIPSE, SPLINE
    assert any(w["code"] == "ENTITY_APPROXIMATED" and w["raw_type"] == "ELLIPSE" for w in doc["import_report"]["warnings"])
    assert any(w["code"] == "ENTITY_APPROXIMATED" and w["raw_type"] == "SPLINE" for w in doc["import_report"]["warnings"])
    mtext = next(e for e in doc["entities"] if e["type"] == "mtext")
    assert mtext["geometry"]["canonical_text"] == "A\nB"
    insert = next(e for e in doc["entities"] if e["type"] == "block_reference")
    assert insert["geometry"]["attributes"][0]["tag"] == "TAG1"
    assert any(w["code"] == "XREF_NOT_RESOLVED" for w in doc["import_report"]["warnings"])


def test_unsupported_entity_is_collected_as_warning_and_report() -> None:
    text = _wrap(
        *_header(),
        *_tables("MODEL"),
        *_section(
            "ENTITIES",
            "0", "3DSOLID",
            "5", "3D1",
            "8", "MODEL",
        ),
    )

    doc = DxfImporter().import_text(text, file_name="unsupported.dxf")

    _validate_schema(doc)
    assert doc["import_report"]["status"] == "partial"
    assert doc["import_report"]["warnings"][0]["code"] == "UNSUPPORTED_ENTITY"
    assert doc["import_report"]["unsupported_entities"][0]["raw_type"] == "3DSOLID"
    assert doc["import_report"]["unsupported_entities"][0]["count"] == 1


def _sample_entities(index: int) -> list[object]:
    x = index * 10
    return [
        "0", "LINE", "5", f"L{index}", "8", "BEAM",
        "10", x, "20", "0", "11", x + 5, "21", "5",
        "0", "CIRCLE", "5", f"C{index}", "8", "BEAM",
        "10", x + 2, "20", "10", "40", "2",
        "0", "ARC", "5", f"A{index}", "8", "BEAM",
        "10", x + 4, "20", "20", "40", "3", "50", "0", "51", "180",
        "0", "LWPOLYLINE", "5", f"P{index}", "8", "GRID",
        "70", str(index % 2),
        "10", x, "20", "30",
        "10", x + 5, "20", "30",
        "10", x + 5, "20", "35",
        "0", "TEXT", "5", f"T{index}", "8", "ANNO",
        "10", x, "20", "40", "40", "2.5", "1", f"MARK-{index}",
    ]


def test_imports_100_ascii_dxf_samples_without_crashing() -> None:
    importer = DxfImporter()
    for index in range(100):
        text = _wrap(
            *_header(),
            *_tables("BEAM", "GRID", "ANNO"),
            *_section("BLOCKS"),
            *_section("ENTITIES", *_sample_entities(index)),
        )

        doc = importer.import_text(text, file_name=f"sample-{index:03d}.dxf")

        assert doc["import_report"]["status"] == "ok"
        assert doc["import_report"]["stats"]["canonical_entity_count"] >= 5
        assert len(doc["layers"]) >= 4
        assert doc["extents"]["quality"] in {"exact", "estimated"}
        assert doc["extents"]["max_x"] >= doc["extents"]["min_x"]
        assert doc["extents"]["max_y"] >= doc["extents"]["min_y"]
