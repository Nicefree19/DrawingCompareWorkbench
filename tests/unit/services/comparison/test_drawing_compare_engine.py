from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

import src.services.comparison.drawing_compare_engine as compare_engine
from src.services.comparison.drawing_compare_engine import (
    CompareTolerance,
    DrawingCompareEngine,
    DrawingCompareOptions,
    EntityMatcher,
    GeometryDiff,
    _CanonicalSpatialIndex,
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


def test_engine_suppresses_style_only_diffs_from_product_changes() -> None:
    before_line = _line("line:before", 0, 0, 10, 0)
    after_line = _line("line:after", 0, 0, 10, 0)
    after_line["style"] = {**after_line["style"], "color": 8}
    before = _drawing([before_line])
    after = _drawing([after_line])

    result = DrawingCompareEngine(DrawingCompareOptions(include_unchanged=False)).compare(before, after)

    assert result.summary == {
        "added": 0,
        "removed": 0,
        "modified": 0,
        "unchanged": 1,
        "total_changes": 0,
        "total_records": 1,
    }
    assert result.to_dict()["changes"] == []


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


def test_entity_matcher_filters_spatial_index_by_type_before_scoring() -> None:
    class CountingMatcher(EntityMatcher):
        def __init__(self, options: DrawingCompareOptions) -> None:
            super().__init__(options)
            self.scored_pairs: list[tuple[str, str]] = []

        def score(self, old_entity: dict, new_entity: dict):
            self.scored_pairs.append((str(old_entity["id"]), str(new_entity["id"])))
            return super().score(old_entity, new_entity)

    before = _drawing([_line("old:line", 0, 0, 10, 0)])
    after = _drawing(
        [
            *[_circle(f"new:circle:{index}", 5, 0, 5) for index in range(20)],
            _line("new:line", 0.2, 0, 10.2, 0),
        ]
    )
    matcher = CountingMatcher(DrawingCompareOptions(search_radius_mm=20.0, match_threshold=0.5))

    result = matcher.match(before["entities"], after["entities"])

    assert [(match.candidate.old_entity_id, match.candidate.new_entity_id) for match in result.matches] == [
        ("old:line", "new:line")
    ]
    assert matcher.scored_pairs == [("old:line", "new:line")]


def test_spatial_index_checks_multicell_candidate_once(monkeypatch) -> None:
    index = _CanonicalSpatialIndex(cell_size=1.0, max_cells_per_entity=10)
    index.insert(_line("new:wide", 0, 0, 2, 0))
    calls = 0
    original = compare_engine._bbox_distance

    def counting_bbox_distance(a, b):
        nonlocal calls
        calls += 1
        return original(a, b)

    monkeypatch.setattr(compare_engine, "_bbox_distance", counting_bbox_distance)

    result = index.query(_line("old:far", 100, 0, 120, 0), radius=1.0)

    assert result == []
    assert calls == 1


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


def test_circle_normal_flip_is_geometry_change() -> None:
    old_circle = _circle("circle:old", 100, 200, 10)
    new_circle = _circle("circle:new", 100, 200, 10)
    old_circle["geometry"]["normal"] = _point(0, 0, 1)
    new_circle["geometry"]["normal"] = _point(0, 0, -1)

    result = DrawingCompareEngine(DrawingCompareOptions(include_unchanged=False)).compare(
        _drawing([old_circle]),
        _drawing([new_circle]),
    )

    assert result.summary["modified"] == 1
    fields = result.to_dict()["changes"][0]["geometry_diff"]["fields"]
    assert fields[0]["path"] == "geometry.normal"


def test_layout_name_move_is_attribute_change() -> None:
    old_circle = _circle("circle:old", 50, 50, 10)
    new_circle = _circle("circle:new", 50, 50, 10)
    old_circle["space"] = "paper"
    old_circle["layout_name"] = "Layout1"
    new_circle["space"] = "paper"
    new_circle["layout_name"] = "DETAIL_VIEW"

    result = DrawingCompareEngine(DrawingCompareOptions(include_unchanged=False)).compare(
        _drawing([old_circle]),
        _drawing([new_circle]),
    )

    assert result.summary["modified"] == 1
    fields = result.to_dict()["changes"][0]["attribute_diffs"]
    assert fields == [{"path": "layout_name", "old": "Layout1", "new": "DETAIL_VIEW"}]


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


def test_structural_position_tolerance_detects_submillimeter_beam_shift() -> None:
    before = _drawing([_line("line:a", 0, 0, 10, 0)])
    after = _drawing([_line("line:b", 0, 0.5, 10, 0.5)])

    default_tolerance = CompareTolerance(position_tolerance_mm=1.0, bbox_tolerance_mm=1.0)
    options = DrawingCompareOptions(
        tolerance=default_tolerance,
        structural_position_tolerance_mm=0.1,
        search_radius_mm=2.0,
        include_unchanged=False,
    )

    result = DrawingCompareEngine(options).compare(before, after)

    assert result.summary["modified"] == 1
    change = result.to_dict()["changes"][0]
    assert change["change_type"] == "modified"
    assert change["geometry_diff"]["fields"][0]["tolerance"] == 0.1


def test_structural_position_tolerance_keeps_global_micro_shift_suppressed() -> None:
    before = _drawing(
        [
            _line("line:a", 0, 0, 10, 0),
            _line("line:b", 0, 10, 10, 10),
            _line("line:c", 0, 20, 10, 20),
            _line("line:d", 0, 30, 10, 30),
        ]
    )
    after = _drawing(
        [
            _line("line:a2", 0.5, 0.5, 10.5, 0.5),
            _line("line:b2", 0.5, 10.5, 10.5, 10.5),
            _line("line:c2", 0.5, 20.5, 10.5, 20.5),
            _line("line:d2", 0.5, 30.5, 10.5, 30.5),
        ]
    )
    options = DrawingCompareOptions(
        tolerance=CompareTolerance(position_tolerance_mm=1.0, bbox_tolerance_mm=1.0),
        structural_position_tolerance_mm=0.1,
        search_radius_mm=2.0,
        include_unchanged=False,
    )

    result = DrawingCompareEngine(options).compare(before, after)

    assert result.summary["modified"] == 0
    assert result.summary["unchanged"] == 4
    assert result.to_dict()["changes"] == []


def test_canonical_reorigin_registered_matching_surfaces_real_changes() -> None:
    dx, dy = 150000.0, -90000.0
    grid = [
        _line(f"grid:a:{index}", 1000 + index * 137, 2000 + (index % 7) * 211, 1100 + index * 137, 2000 + (index % 7) * 211, layer_id="layer:grid")
        for index in range(30)
    ]
    before = _drawing(
        [
            *grid,
            _text("text:a", 1500, 2500, "OLD"),
            _line("delete:a", 3000, 2500, 3050, 2500, layer_id="layer:grid"),
            _line("beam:a", 7000, 7000, 7200, 7000, layer_id="layer:beam"),
        ],
        title="before",
    )
    after = _drawing(
        [
            *[
                _line(
                    f"grid:b:{index}",
                    1000 + index * 137 + dx,
                    2000 + (index % 7) * 211 + dy,
                    1100 + index * 137 + dx,
                    2000 + (index % 7) * 211 + dy,
                    layer_id="layer:grid",
                )
                for index in range(30)
            ],
            _text("text:b", 1500 + dx, 2500 + dy, "NEW"),
            _line("add:b", 3500 + dx, 2600 + dy, 3550 + dx, 2600 + dy, layer_id="layer:grid"),
            _line("beam:b", 7000 + dx + 0.5, 7000 + dy, 7200 + dx + 0.5, 7000 + dy, layer_id="layer:beam"),
        ],
        title="after",
    )
    options = DrawingCompareOptions(
        tolerance=CompareTolerance(position_tolerance_mm=1.0, bbox_tolerance_mm=1.0),
        structural_position_tolerance_mm=0.1,
        include_unchanged=False,
    )

    result = DrawingCompareEngine(options).compare(before, after)
    payload = result.to_dict()

    assert result.summary == {
        "added": 1,
        "removed": 1,
        "modified": 2,
        "unchanged": 30,
        "total_changes": 4,
        "total_records": 34,
    }
    assert [change["change_type"] for change in payload["changes"]] == [
        "removed",
        "added",
        "modified",
        "modified",
    ]
    assert all(
        "registered_reorigin" in (change.get("match") or {}).get("components", {})
        for change in payload["changes"]
        if change["change_type"] == "modified"
    )
    text_change = next(change for change in payload["changes"] if change["entity_type"] == "text")
    assert text_change["geometry_diff"]["categories"] == ["text"]
    beam_change = next(change for change in payload["changes"] if change["entity_type"] == "line" and change["layer_name"] == "BEAM")
    assert beam_change["geometry_diff"]["fields"][0]["delta"] == 0.5


def test_compact_mode_preserves_unchanged_summary_without_records() -> None:
    before = _drawing([
        _line("line:same:a", 0, 0, 10, 0),
        _circle("circle:old", 100, 100, 10),
    ])
    after = _drawing([
        _line("line:same:b", 0, 0, 10, 0),
        _circle("circle:new", 100, 100, 12),
    ])

    result = DrawingCompareEngine(
        DrawingCompareOptions(include_unchanged=False, include_entity_snapshots=False)
    ).compare(before, after)

    assert result.summary == {
        "added": 0,
        "removed": 0,
        "modified": 1,
        "unchanged": 1,
        "total_changes": 1,
        "total_records": 2,
    }
    payload = result.to_dict()
    assert [change["change_type"] for change in payload["changes"]] == ["modified"]
    assert payload["changes"][0]["old_entity"] is None
    assert payload["changes"][0]["new_entity"] is None


def test_result_converts_to_change_records_for_zone_pipeline() -> None:
    before = _drawing([_text("text:a", 0, 0, "A")])
    after = _drawing([_text("text:b", 0, 0, "B")])

    result = DrawingCompareEngine().compare(before, after)
    records = result.to_change_records()

    assert len(records) == 1
    assert records[0].change_type.value == "modified"
    assert records[0].metadata["entity_type"] == "text"
    assert records[0].metadata["bbox"]["min_x"] == 0.0
