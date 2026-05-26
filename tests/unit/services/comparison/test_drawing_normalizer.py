from __future__ import annotations

import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator

from src.services.comparison.drawing_normalizer import (
    DrawingNormalizer,
    NormalizationOptions,
)
from src.services.comparison.dxf_importer import DxfImporter


def _point(x: float, y: float, z: float = 0.0) -> dict:
    return {"x": float(x), "y": float(y), "z": float(z)}


def _vertex(x: float, y: float, z: float = 0.0, *, bulge: float = 0.0) -> dict:
    return {
        "point": _point(x, y, z),
        "bulge": bulge,
        "start_width": None,
        "end_width": None,
    }


def _line(
    entity_id: str,
    start: dict,
    end: dict,
    *,
    layer_id: str = "layer:0",
    style: dict | None = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "id": entity_id,
        "type": "line",
        "source": {"format": "test", "raw_type": "LINE"},
        "layer_id": layer_id,
        "block_id": None,
        "space": "model",
        "layout_name": "Model",
        "geometry": {"type": "line", "start": start, "end": end},
        "bbox": {"min_x": start["x"], "min_y": start["y"], "min_z": 0.0, "max_x": end["x"], "max_y": end["y"], "max_z": 0.0, "quality": "exact"},
        "style": style or {"color": None, "lineweight": None, "linetype": None},
        "visible": True,
        "metadata": metadata or {},
        "hashes": {},
    }


def _drawing(entities: list[dict], *, layers: list[dict] | None = None, blocks: list[dict] | None = None) -> dict:
    return {
        "schema_version": "canonical-drawing/v1",
        "drawing": {"id": "drawing:test", "title": "test", "source": {"format": "test"}},
        "units": {"canonical_unit": "mm", "source_unit": "mm", "scale_to_canonical": 1.0},
        "coordinate_system": {"space": "WCS"},
        "tolerances": {},
        "extents": {},
        "layers": layers
        or [
            {
                "id": "layer:0",
                "name": "0",
                "normalized_name": "0",
                "color": 7,
                "linetype": "Continuous",
                "lineweight": 0,
                "visible": True,
                "locked": False,
                "frozen": False,
                "plot": True,
                "metadata": {},
            }
        ],
        "blocks": blocks or [],
        "entities": entities,
        "metadata": {},
    }


def _validate_schema(doc: dict) -> None:
    schema = json.loads(Path("docs/canonical-drawing.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(doc), key=str)
    assert not errors, "\n".join(f"{list(error.path)}: {error.message}" for error in errors[:10])


def test_rounding_and_near_zero_geometry_removal_are_reported() -> None:
    doc = _drawing(
        [
            _line("line:tiny", _point(0.003, 0.003), _point(0.004, 0.004)),
            _line("line:keep", _point(1.004, 2.006), _point(10.004, 2.006)),
        ]
    )

    normalized, report = DrawingNormalizer(
        NormalizationOptions(
            coordinate_quantum_mm=0.01,
            bbox_quantum_mm=0.01,
            near_zero_length_mm=0.01,
            resolve_bylayer_byblock=False,
        )
    ).normalize(doc)

    assert [entity["id"] for entity in normalized["entities"]] == ["line:keep"]
    geometry = normalized["entities"][0]["geometry"]
    assert geometry["start"] == {"x": 1.0, "y": 2.01, "z": 0.0}
    assert geometry["end"] == {"x": 10.0, "y": 2.01, "z": 0.0}
    assert report.output_entity_count == 1
    assert report.removed_near_zero_count == 1
    assert report.rounded_coordinate_count >= 4
    assert report.to_dict()["counts_by_code"]["NEAR_ZERO_GEOMETRY_REMOVED"] == 1


def test_closed_polyline_start_and_direction_are_canonicalized() -> None:
    polyline_a = {
        "id": "polyline:a",
        "type": "polyline",
        "source": {"format": "test", "raw_type": "LWPOLYLINE"},
        "layer_id": "layer:0",
        "block_id": None,
        "space": "model",
        "layout_name": "Model",
        "geometry": {
            "type": "polyline",
            "vertices": [_vertex(10, 0), _vertex(10, 10), _vertex(0, 10), _vertex(0, 0)],
            "closed": True,
            "polyline_kind": "lwpolyline",
        },
        "bbox": {},
        "style": {},
        "visible": True,
        "metadata": {},
        "hashes": {},
    }
    polyline_b = {
        **polyline_a,
        "id": "polyline:b",
        "geometry": {
            "type": "polyline",
            "vertices": [_vertex(0, 10), _vertex(10, 10), _vertex(10, 0), _vertex(0, 0)],
            "closed": True,
            "polyline_kind": "lwpolyline",
        },
    }

    options = NormalizationOptions(resolve_bylayer_byblock=False)
    normalized_a, report_a = DrawingNormalizer(options).normalize(_drawing([polyline_a]))
    normalized_b, report_b = DrawingNormalizer(options).normalize(_drawing([polyline_b]))

    expected_points = [_point(0, 0), _point(0, 10), _point(10, 10), _point(10, 0)]
    assert [v["point"] for v in normalized_a["entities"][0]["geometry"]["vertices"]] == expected_points
    assert [v["point"] for v in normalized_b["entities"][0]["geometry"]["vertices"]] == expected_points
    assert normalized_a["entities"][0]["hashes"]["geometry_hash"] == normalized_b["entities"][0]["hashes"]["geometry_hash"]
    assert report_a.normalized_polyline_count == 1
    assert report_b.normalized_polyline_count == 1


def test_bylayer_and_byblock_styles_are_resolved_to_effective_values() -> None:
    layers = [
        {
            "id": "layer:beam",
            "name": "BEAM",
            "normalized_name": "BEAM",
            "color": 3,
            "linetype": "Continuous",
            "lineweight": 25,
            "visible": True,
            "locked": False,
            "frozen": False,
            "plot": True,
            "metadata": {},
        }
    ]
    insert = {
        "id": "block_reference:1",
        "type": "block_reference",
        "source": {"format": "test", "raw_type": "INSERT"},
        "layer_id": "layer:beam",
        "block_id": None,
        "space": "model",
        "layout_name": "Model",
        "geometry": {
            "type": "block_reference",
            "block_id": "block:b1",
            "block_name": "B1",
            "insert": _point(0, 0),
            "scale": _point(1, 1, 1),
            "rotation_deg": 0.0,
            "matrix": [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0],
            "attributes": [],
            "expanded_entity_ids": ["line:child"],
        },
        "bbox": {},
        "style": {"color": 2, "linetype": "DASHED", "lineweight": 50},
        "visible": True,
        "metadata": {},
        "hashes": {},
    }
    bylayer_line = _line(
        "line:bylayer",
        _point(0, 0),
        _point(10, 0),
        layer_id="layer:beam",
        style={"color": 256, "linetype": "BYLAYER", "lineweight": -1},
    )
    byblock_line = _line(
        "line:child",
        _point(0, 1),
        _point(10, 1),
        layer_id="layer:beam",
        style={"color": 0, "linetype": "BYBLOCK", "lineweight": -2},
        metadata={"expanded_from_insert_id": "block_reference:1"},
    )
    explicit_line = _line(
        "line:explicit",
        _point(0, 2),
        _point(10, 2),
        layer_id="layer:beam",
        style={"color": 3, "linetype": "Continuous", "lineweight": 25},
    )

    normalized, report = DrawingNormalizer().normalize(
        _drawing([insert, bylayer_line, byblock_line, explicit_line], layers=layers)
    )

    bylayer_entity = next(e for e in normalized["entities"] if e["id"] == "line:bylayer")
    explicit_entity = next(e for e in normalized["entities"] if e["id"] == "line:explicit")
    bylayer_style = bylayer_entity["style"]
    byblock_style = next(e for e in normalized["entities"] if e["id"] == "line:child")["style"]
    assert bylayer_style["effective_color"] == 3
    assert bylayer_style["effective_linetype"] == "Continuous"
    assert bylayer_style["effective_lineweight"] == 25
    assert bylayer_style["resolution"] == {
        "color": "bylayer",
        "linetype": "bylayer",
        "lineweight": "bylayer",
    }
    assert byblock_style["effective_color"] == 2
    assert byblock_style["effective_linetype"] == "DASHED"
    assert byblock_style["effective_lineweight"] == 50
    assert byblock_style["resolution"] == {
        "color": "byblock",
        "linetype": "byblock",
        "lineweight": "byblock",
    }
    assert bylayer_entity["hashes"]["style_hash"] == explicit_entity["hashes"]["style_hash"]
    assert report.resolved_style_count == 4


def test_mtext_formatting_and_text_whitespace_are_normalized() -> None:
    mtext = {
        "id": "mtext:1",
        "type": "mtext",
        "source": {"format": "test", "raw_type": "MTEXT"},
        "layer_id": "layer:0",
        "block_id": None,
        "space": "model",
        "layout_name": "Model",
        "geometry": {
            "type": "mtext",
            "insert": _point(0, 0),
            "raw_content": "{\\fArial|b0;A\\P  B   \\C1; C}",
            "plain_text": "{\\fArial|b0;A\\P  B   \\C1; C}",
            "canonical_text": "{\\fArial|b0;A\\P  B   \\C1; C}",
            "height": 2.5,
            "box_width": 100,
            "rotation_deg": 0.0,
        },
        "bbox": {},
        "style": {},
        "visible": True,
        "metadata": {},
        "hashes": {},
    }

    normalized, report = DrawingNormalizer(
        NormalizationOptions(resolve_bylayer_byblock=False)
    ).normalize(_drawing([mtext]))

    geometry = normalized["entities"][0]["geometry"]
    assert geometry["plain_text"] == "A B C"
    assert geometry["canonical_text"] == "A B C"
    assert report.normalized_text_count == 1
    assert normalized["entities"][0]["hashes"]["semantic_hash"].startswith("sem:v1:sha256:")


def test_flatten_curve_option_converts_ellipse_to_polyline_snapshot() -> None:
    ellipse = {
        "id": "ellipse:1",
        "type": "ellipse",
        "source": {"format": "test", "raw_type": "ELLIPSE"},
        "layer_id": "layer:0",
        "block_id": None,
        "space": "model",
        "layout_name": "Model",
        "geometry": {
            "type": "ellipse",
            "center": _point(0, 0),
            "major_axis": _point(10, 0),
            "minor_to_major_ratio": 0.5,
            "start_param": 0.0,
            "end_param": math.tau,
        },
        "bbox": {},
        "style": {},
        "visible": True,
        "metadata": {},
        "hashes": {},
    }

    normalized, report = DrawingNormalizer(
        NormalizationOptions(
            flatten_curves=True,
            flatten_tolerance_mm=10.0,
            resolve_bylayer_byblock=False,
        )
    ).normalize(_drawing([ellipse]))

    entity = normalized["entities"][0]
    assert entity["type"] == "polyline"
    assert entity["geometry"]["polyline_kind"] == "flattened_ellipse"
    assert entity["geometry"]["closed"] is True
    assert len(entity["geometry"]["vertices"]) == 8
    assert report.flattened_curve_count == 1


def test_normalization_reduces_false_diff_between_dxf_save_variants() -> None:
    dxf_a = "\n".join(
        [
            "0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1027", "0", "ENDSEC",
            "0", "SECTION", "2", "TABLES", "0", "TABLE", "2", "LAYER", "0", "LAYER", "2", "BEAM", "62", "3", "6", "Continuous", "0", "ENDTAB", "0", "ENDSEC",
            "0", "SECTION", "2", "ENTITIES",
            "0", "LINE", "5", "A1", "8", "BEAM", "10", "0.0004", "20", "-0.0003", "11", "10.0004", "21", "0.0003",
            "0", "TEXT", "5", "T1", "8", "BEAM", "10", "0", "20", "5", "40", "2.5", "1", "  H-400   A  ",
            "0", "ENDSEC", "0", "EOF", "",
        ]
    )
    dxf_b = "\n".join(
        [
            "0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1032", "0", "ENDSEC",
            "0", "SECTION", "2", "TABLES", "0", "TABLE", "2", "LAYER", "0", "LAYER", "2", "BEAM", "62", "3", "6", "Continuous", "0", "ENDTAB", "0", "ENDSEC",
            "0", "SECTION", "2", "ENTITIES",
            "0", "LINE", "5", "B1", "8", "BEAM", "10", "0", "20", "0", "11", "10", "21", "0",
            "0", "TEXT", "5", "T2", "8", "BEAM", "10", "0", "20", "5", "40", "2.5", "1", "H-400 A",
            "0", "ENDSEC", "0", "EOF", "",
        ]
    )

    options = NormalizationOptions(coordinate_quantum_mm=0.01, resolve_bylayer_byblock=True)
    normalized_a, _report_a = DrawingNormalizer(options).normalize(
        DxfImporter().import_text(dxf_a, file_name="a.dxf")
    )
    normalized_b, _report_b = DrawingNormalizer(options).normalize(
        DxfImporter().import_text(dxf_b, file_name="b.dxf")
    )

    _validate_schema(normalized_a)
    _validate_schema(normalized_b)
    line_a = next(e for e in normalized_a["entities"] if e["type"] == "line")
    line_b = next(e for e in normalized_b["entities"] if e["type"] == "line")
    text_a = next(e for e in normalized_a["entities"] if e["type"] == "text")
    text_b = next(e for e in normalized_b["entities"] if e["type"] == "text")
    assert line_a["hashes"]["geometry_hash"] == line_b["hashes"]["geometry_hash"]
    assert text_a["hashes"]["semantic_hash"] == text_b["hashes"]["semantic_hash"]
