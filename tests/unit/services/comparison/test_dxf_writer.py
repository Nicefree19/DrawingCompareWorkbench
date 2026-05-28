from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from src.services.comparison.dxf_importer import DxfImporter
from src.services.comparison.dxf_writer import DxfExportOptions, DxfWriter


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
        body.extend(["0", "LAYER", "2", name, "70", "0", "62", str(7 + idx), "6", "Continuous"])
    body.extend(["0", "ENDTAB"])
    return _section("TABLES", *body)


def _wrap(*sections: object) -> str:
    return _dxf(*sections, "0", "EOF")


def _validate_schema(doc: dict) -> None:
    schema = json.loads(Path("docs/canonical-drawing.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(doc), key=str)
    assert not errors, "\n".join(f"{list(error.path)}: {error.message}" for error in errors[:10])


def _source_dxf() -> str:
    return _wrap(
        *_header(),
        *_tables("0", "BEAM", "ANNO"),
        *_section(
            "BLOCKS",
            "0", "BLOCK",
            "2", "B1",
            "10", "0", "20", "0", "30", "0",
            "0", "LINE",
            "5", "B10",
            "8", "BEAM",
            "10", "0", "20", "0",
            "11", "10", "21", "0",
            "0", "ENDBLK",
        ),
        *_section(
            "ENTITIES",
            "0", "LINE",
            "5", "10",
            "8", "BEAM",
            "10", "0", "20", "0",
            "11", "100", "21", "0",
            "0", "LWPOLYLINE",
            "5", "20",
            "8", "BEAM",
            "70", "1",
            "10", "0", "20", "10", "42", "0",
            "10", "20", "20", "10", "42", "0",
            "10", "20", "20", "30", "42", "0",
            "0", "CIRCLE",
            "5", "30",
            "8", "BEAM",
            "10", "50", "20", "50",
            "40", "10",
            "0", "ARC",
            "5", "40",
            "8", "BEAM",
            "10", "80", "20", "50",
            "40", "12",
            "50", "0",
            "51", "90",
            "0", "TEXT",
            "5", "50",
            "8", "ANNO",
            "10", "0", "20", "80",
            "40", "2.5",
            "1", " H-400 ",
            "0", "MTEXT",
            "5", "60",
            "8", "ANNO",
            "10", "0", "20", "90",
            "40", "2.5",
            "41", "100",
            "1", "A\\PB",
            "0", "INSERT",
            "5", "70",
            "8", "BEAM",
            "2", "B1",
            "10", "100", "20", "100",
            "41", "2", "42", "2", "43", "1",
            "50", "0",
            "66", "1",
            "0", "ATTRIB",
            "2", "MARK",
            "1", "B1",
            "10", "100", "20", "100",
            "0", "SEQEND",
        ),
    )


def test_writer_emits_r2000_required_sections_and_tables() -> None:
    doc = DxfImporter(expand_blocks=True).import_text(_source_dxf(), file_name="source.dxf")

    text = DxfWriter(DxfExportOptions(acad_version="AC1015")).write_string(doc)

    assert "0\nSECTION\n2\nHEADER\n" in text
    assert "9\n$ACADVER\n1\nAC1015\n" in text
    assert "0\nTABLE\n2\nLAYER\n" in text
    assert "0\nSECTION\n2\nBLOCKS\n" in text
    assert "0\nSECTION\n2\nENTITIES\n" in text
    assert "0\nLWPOLYLINE\n" in text
    assert "0\nINSERT\n" in text
    assert text.endswith("0\nEOF\n")


def test_import_export_import_roundtrip_preserves_primary_geometry() -> None:
    importer = DxfImporter(expand_blocks=True)
    source = importer.import_text(_source_dxf(), file_name="roundtrip-source.dxf")
    exported = DxfWriter().write_string(source)
    roundtrip = DxfImporter(expand_blocks=True).import_text(exported, file_name="roundtrip-export.dxf")

    _validate_schema(roundtrip)
    assert roundtrip["drawing"]["source"]["acad_version"] == "AC1015"
    assert {layer["name"] for layer in roundtrip["layers"]} >= {"0", "BEAM", "ANNO"}
    assert [block["name"] for block in roundtrip["blocks"]] == ["B1"]

    line = next(
        entity
        for entity in roundtrip["entities"]
        if entity["type"] == "line"
        and entity["space"] == "model"
        and not entity["metadata"].get("expanded_from_insert_id")
        and entity["geometry"]["end"]["x"] == 100.0
    )
    assert line["geometry"]["start"] == {"x": 0.0, "y": 0.0, "z": 0.0}
    assert line["geometry"]["end"] == {"x": 100.0, "y": 0.0, "z": 0.0}

    polyline = next(entity for entity in roundtrip["entities"] if entity["type"] == "polyline" and entity["space"] == "model")
    assert polyline["geometry"]["closed"] is True
    assert [vertex["point"] for vertex in polyline["geometry"]["vertices"]] == [
        {"x": 0.0, "y": 10.0, "z": 0.0},
        {"x": 20.0, "y": 10.0, "z": 0.0},
        {"x": 20.0, "y": 30.0, "z": 0.0},
    ]

    circle = next(entity for entity in roundtrip["entities"] if entity["type"] == "circle")
    assert circle["geometry"]["center"] == {"x": 50.0, "y": 50.0, "z": 0.0}
    assert circle["geometry"]["radius"] == 10.0

    arc = next(entity for entity in roundtrip["entities"] if entity["type"] == "arc")
    assert arc["geometry"]["start_angle_deg"] == 0.0
    assert arc["geometry"]["end_angle_deg"] == 90.0

    text = next(entity for entity in roundtrip["entities"] if entity["type"] == "text")
    assert text["geometry"]["canonical_text"] == "H-400"
    mtext = next(entity for entity in roundtrip["entities"] if entity["type"] == "mtext")
    assert mtext["geometry"]["canonical_text"] == "A\nB"

    insert = next(entity for entity in roundtrip["entities"] if entity["type"] == "block_reference")
    assert insert["geometry"]["block_name"] == "B1"
    assert insert["geometry"]["attributes"][0]["tag"] == "MARK"
    assert insert["geometry"]["attributes"][0]["canonical_text"] == "B1"
    expanded = [
        entity for entity in roundtrip["entities"]
        if entity["metadata"].get("expanded_from_insert_id") == insert["id"]
    ]
    assert len(expanded) == 1


def test_writer_writes_file_that_importer_can_read(tmp_path: Path) -> None:
    doc = DxfImporter(expand_blocks=True).import_text(_source_dxf(), file_name="source.dxf")
    path = tmp_path / "debug-export.dxf"

    written = DxfWriter().write_file(doc, path)
    roundtrip = DxfImporter(expand_blocks=False).import_file(written)

    assert written == path
    assert path.exists()
    _validate_schema(roundtrip)
    assert roundtrip["import_report"]["status"] == "ok"
    assert any(entity["type"] == "block_reference" for entity in roundtrip["entities"])


def test_debug_export_sample_fixture_is_readable() -> None:
    sample = Path("tests/data/comparison/dxf_writer/debug_export_r2000.dxf")

    doc = DxfImporter(expand_blocks=True).import_file(sample)

    _validate_schema(doc)
    assert doc["drawing"]["source"]["acad_version"] == "AC1015"
    assert {entity["type"] for entity in doc["entities"]} >= {"line", "circle", "text"}
