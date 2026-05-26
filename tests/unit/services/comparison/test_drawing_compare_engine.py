from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from src.services.comparison.drawing_compare_engine import (
    CompareTolerance,
    DrawingCompareEngine,
    DrawingCompareOptions,
    EntityMatcher,
    GeometryDiff,
    result_fingerprint,
)
from src.services.comparison.drawing_normalizer import DrawingNormalizer, NormalizationOptions


def _point(x: float, y: float, z: float = 0.0) -> dict:
    return {"x": float(x), "y": float(y), "z": float(z)}


def _bbox(min_x: float, min_y: float, max_x: float, max_y: float) -> dict:
    return {
        "min_x": float(min_x),
        "min_y": float(min_y),
        "min_z": 0.0,
        "max_x": float(max_x),
        "max_y": float(max_y),
        "max_z": 0.0,
        "quality": "exact",
    }


def _base_entity(entity_id: str, entity_type: str, geometry: dict, bbox: dict, *, layer_id: str = "layer:beam") -> dict:
    return {
        "id": entity_id,
        "type": entity_type,
        "source": {"format": "test", "raw_type": entity_type.upper()},
        "layer_id": layer_id,
        "block_id": None,
        "space": "model",
        "layout_name": "Model",
        "geometry": geometry,
        "bbox": bbox,
        "style": {"color": 7, "linetype": "Continuous", "lineweight": 0},
        "visible": True,
        "metadata": {},
        "hashes": {},
    }


def _line(entity_id: str, x1: float, y1: float, x2: float, y2: float, *, layer_id: str = "layer:beam") -> dict:
    return _base_entity(
        entity_id,
        "line",
        {"type": "line", "start": _point(x1, y1), "end": _point(x2, y2)},
        _bbox(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)),
        layer_id=layer_id,
    )


def _circle(entity_id: str, x: float, y: float, radius: float) -> dict:
    return _base_entity(
        entity_id,
        "circle",
        {"type": "circle", "center": _point(x, y), "radius": radius},
        _bbox(x - radius, y - radius, x + radius, y + radius),
    )


def _arc(entity_id: str, x: float, y: float, radius: float, start: float, end: float) -> dict:
    return _base_entity(
        entity_id,
        "arc",
        {
            "type": "arc",
            "center": _point(x, y),
            "radius": radius,
            "start_angle_deg": start,
            "end_angle_deg": end,
            "sweep_direction": "ccw",
        },
        _bbox(x - radius, y - radius, x + radius, y + radius),
    )


def _polyline(entity_id: str, points: list[tuple[float, float]], *, closed: bool = False) -> dict:
    vertices = [
        {"point": _point(x, y), "bulge": 0.0, "start_width": None, "end_width": None}
        for x, y in points
    ]
    return _base_entity(
        entity_id,
        "polyline",
        {"type": "polyline", "vertices": vertices, "closed": closed, "polyline_kind": "lwpolyline"},
        _bbox(min(x for x, _y in points), min(y for _x, y in points), max(x for x, _y in points), max(y for _x, y in points)),
    )


def _text(entity_id: str, x: float, y: float, text: str) -> dict:
    return _base_entity(
        entity_id,
        "text",
        {
            "type": "text",
            "insert": _point(x, y),
            "text": text,
            "canonical_text": text,
            "height": 2.5,
            "rotation_deg": 0.0,
            "alignment": "0:0",
        },
        _bbox(x, y, x + max(2.5, len(text) * 1.5), y + 2.5),
    )


def _block(entity_id: str, x: float, y: float, *, attr: str = "A") -> dict:
    return _base_entity(
        entity_id,
        "block_reference",
        {
            "type": "block_reference",
            "block_id": "block:b1",
            "block_name": "B1",
            "insert": _point(x, y),
            "scale": _point(1.0, 1.0, 1.0),
            "rotation_deg": 0.0,
            "matrix": [1.0, 0.0, 0.0, x, 0.0, 1.0, 0.0, y, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0],
            "attributes": [{"tag": "MARK", "text": attr, "canonical_text": attr, "insert": _point(x, y)}],
            "expanded_entity_ids": [],
        },
        _bbox(x, y, x, y),
    )


def _drawing(entities: list[dict], *, title: str = "drawing") -> dict:
    doc = {
        "schema_version": "canonical-drawing/v1",
        "drawing": {
            "id": f"drawing:{title}",
            "title": title,
            "source": {"format": "dxf", "file_name": f"{title}.dxf"},
        },
        "units": {"canonical_unit": "mm", "source_unit": "mm", "scale_to_mm": 1.0},
        "coordinate_system": {"space": "WCS"},
        "tolerances": {},
        "extents": {},
        "layers": [
            {
                "id": "layer:beam",
                "name": "BEAM",
                "normalized_name": "BEAM",
                "color": 7,
                "linetype": "Continuous",
                "lineweight": 0,
                "visible": True,
                "locked": False,
                "frozen": False,
                "plot": True,
                "metadata": {},
            },
            {
                "id": "layer:grid",
                "name": "GRID",
                "normalized_name": "GRID",
                "color": 3,
                "linetype": "Continuous",
                "lineweight": 0,
                "visible": True,
                "locked": False,
                "frozen": False,
                "plot": True,
                "metadata": {},
            },
        ],
        "blocks": [],
        "entities": entities,
        "import_report": {"status": "ok", "warnings": [], "unsupported_entities": [], "stats": {}},
        "metadata": {},
    }
    normalized, _report = DrawingNormalizer(
        NormalizationOptions(resolve_bylayer_byblock=True, remove_near_zero_geometry=False)
    ).normalize(doc)
    return normalized


def _validate_diff_schema(payload: dict) -> None:
    schema = json.loads(Path("docs/drawing-diff.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=str)
    assert not errors, "\n".join(f"{list(error.path)}: {error.message}" for error in errors[:10])


def test_engine_outputs_added_removed_modified_unchanged_for_ui_snapshot() -> None:
    before = _drawing(
        [
            _line("line:same:a", 0, 0, 10, 0),
            _circle("circle:old", 100, 100, 10),
            _text("text:removed", 200, 0, "OLD"),
        ],
        title="before",
    )
    after = _drawing(
        [
            _line("line:same:b", 0, 0, 10, 0),
            _circle("circle:new", 100, 100, 12),
            _arc("arc:added", 300, 0, 5, 0, 90),
        ],
        title="after",
    )

    result = DrawingCompareEngine().compare(before, after)
    payload = result.to_dict()

    _validate_diff_schema(payload)
    assert payload["summary"] == {
        "added": 1,
        "removed": 1,
        "modified": 1,
        "unchanged": 1,
        "total_changes": 3,
        "total_records": 4,
    }
    assert [change["change_type"] for change in payload["changes"]] == [
        "removed",
        "added",
        "modified",
        "unchanged",
    ]
    modified = next(change for change in payload["changes"] if change["change_type"] == "modified")
    assert modified["old_entity_id"] == "circle:old"
    assert modified["new_entity_id"] == "circle:new"
    assert modified["geometry_diff"]["fields"][0]["path"] == "geometry.radius"
    assert modified["visualization"]["side"] == "matched"
    added = next(change for change in payload["changes"] if change["change_type"] == "added")
    assert added["bbox"] == added["new_bbox"]
    removed = next(change for change in payload["changes"] if change["change_type"] == "removed")
    assert removed["bbox"] == removed["old_bbox"]


def test_entity_matcher_uses_spatial_candidates_and_stable_score() -> None:
    old = [_line("old:near", 0, 0, 10, 0)]
    new = [
        _line("new:far", 100, 100, 110, 100),
        _line("new:near", 0.2, 0.0, 10.2, 0.0),
    ]
    before = _drawing(old)
    after = _drawing(new)
    match_result = EntityMatcher(
        DrawingCompareOptions(search_radius_mm=2.0, match_threshold=0.5)
    ).match(before["entities"], after["entities"])

    assert len(match_result.matches) == 1
    match = match_result.matches[0].candidate
    assert match.old_entity_id == "old:near"
    assert match.new_entity_id == "new:near"
    assert set(match.components) == {"type", "layer", "bbox", "centroid", "geometry_hash"}
    assert match.score > 0.7


def test_geometry_diff_algorithms_cover_supported_entity_types() -> None:
    tolerance = CompareTolerance(position_tolerance_mm=0.01, numeric_tolerance=0.001, angle_tolerance_deg=0.01)
    cases = [
        (_line("l1", 0, 0, 10, 0), _line("l2", 0, 0, 11, 0), "geometry.end"),
        (_circle("c1", 0, 0, 10), _circle("c2", 0, 0, 11), "geometry.radius"),
        (_arc("a1", 0, 0, 10, 0, 90), _arc("a2", 0, 0, 10, 0, 91), "geometry.end_angle_deg"),
        (_polyline("p1", [(0, 0), (10, 0)]), _polyline("p2", [(0, 0), (10, 1)]), "geometry.vertices[1].point"),
        (_text("t1", 0, 0, "A"), _text("t2", 0, 0, "B"), "geometry.canonical_text"),
        (_block("b1", 0, 0, attr="A"), _block("b2", 0, 0, attr="B"), "geometry.attributes"),
    ]

    for old_entity, new_entity, expected_path in cases:
        diff = GeometryDiff.compare(old_entity, new_entity, tolerance)
        assert diff.changed, expected_path
        assert expected_path in [field.path for field in diff.fields]


def test_tolerance_changes_output_and_fingerprint_is_reproducible() -> None:
    before = _drawing([_line("line:a", 0, 0, 10, 0)])
    after = _drawing([_line("line:b", 0, 0, 10.05, 0)])

    loose_options = DrawingCompareOptions(
        tolerance=CompareTolerance(position_tolerance_mm=0.1),
        search_radius_mm=2.0,
    )
    strict_options = DrawingCompareOptions(
        tolerance=CompareTolerance(position_tolerance_mm=0.01),
        search_radius_mm=2.0,
    )
    loose_1 = DrawingCompareEngine(loose_options).compare(before, after)
    loose_2 = DrawingCompareEngine(loose_options).compare(before, after)
    strict = DrawingCompareEngine(strict_options).compare(before, after)

    assert loose_1.summary["unchanged"] == 1
    assert loose_1.summary["modified"] == 0
    assert strict.summary["modified"] == 1
    assert result_fingerprint(loose_1) == result_fingerprint(loose_2)
    assert result_fingerprint(loose_1) != result_fingerprint(strict)


def test_result_converts_to_change_records_for_zone_pipeline() -> None:
    before = _drawing([_text("text:a", 0, 0, "A")])
    after = _drawing([_text("text:b", 0, 0, "B")])

    result = DrawingCompareEngine().compare(before, after)
    records = result.to_change_records()

    assert len(records) == 1
    assert records[0].change_type.value == "modified"
    assert records[0].metadata["entity_type"] == "text"
    assert records[0].metadata["bbox"]["min_x"] == 0.0
