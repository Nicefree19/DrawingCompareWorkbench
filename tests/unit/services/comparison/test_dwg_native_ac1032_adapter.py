"""Experimental opt-in AC1032 native adapter — wiring + default-unchanged tests.

The real-file integration test needs the local git-ignored AC1032 corpus and
SKIPs in CI (real-file verification is local-only), matching
``test_dwg_r2018_reader``.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from src.services.comparison.base import ComparisonResult
from src.services.comparison.change_zones import ChangeZoneOptions, build_change_zones
from src.services.comparison.drawing_compare_engine import (
    DrawingCompareEngine,
    DrawingCompareOptions,
)
from src.services.comparison.dwg_backend import create_dwg_backend_selection
from src.services.comparison.dwg_importer import (
    DwgAdapterDrawing,
    DwgImporter,
    DwgImporterAdapter,
    DwgVersionDetector,
    DwgVersionInfo,
)
from src.services.comparison.dwg_native_ac1032_adapter import (
    AC1032_NATIVE_OPT_IN_ENV,
    DwgNativeAc1032Adapter,
    ac1032_native_opt_in,
)
from src.services.comparison.dwg_native_reader import DwgNativeAc1015Adapter
from src.services.comparison.revision_marker import revcloud_geometry_from_bbox

_AC1032 = DwgVersionInfo("AC1032", "AutoCAD 2018+", "R2018", False)
_AC1015 = DwgVersionInfo("AC1015", "AutoCAD 2000", "R15", True)
_SAMPLE = Path(".local/native_cad_real_samples/acadsharp/sample_AC1032.dwg")


class _RecordingFallback(DwgImporterAdapter):
    """Fallback that records the versions it is asked about / read with."""

    name = "recording-fallback"
    version = "0"
    license_id = "INTERNAL"

    def __init__(self) -> None:
        self.supported_calls: list[str] = []
        self.read_calls: list[str] = []

    def is_available(self) -> bool:
        return True

    def supports_version(self, version: DwgVersionInfo) -> bool:
        self.supported_calls.append(version.code)
        return version.code == "AC1015"

    def read_file(self, path, version: DwgVersionInfo) -> DwgAdapterDrawing:
        self.read_calls.append(version.code)
        return DwgAdapterDrawing(header={"$ACADVER": version.code})


def test_opt_in_defaults_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"  # absent -> no saved decision
    monkeypatch.delenv(AC1032_NATIVE_OPT_IN_ENV, raising=False)
    assert ac1032_native_opt_in(settings_path=settings) is False
    for truthy in ("1", "true", "YES", "on"):
        monkeypatch.setenv(AC1032_NATIVE_OPT_IN_ENV, truthy)
        assert ac1032_native_opt_in(settings_path=settings) is True
    monkeypatch.setenv(AC1032_NATIVE_OPT_IN_ENV, "0")
    assert ac1032_native_opt_in(settings_path=settings) is False


def test_opt_in_resolves_env_over_settings_over_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from src.services.comparison.dwg_native_ac1032_adapter import set_ac1032_native_opt_in

    settings = tmp_path / "settings.json"
    monkeypatch.delenv(AC1032_NATIVE_OPT_IN_ENV, raising=False)
    # 3. default: no env, no settings -> off
    assert ac1032_native_opt_in(settings_path=settings) is False
    # 2. persisted setting (no env) -> the GUI/user can enable it without an env var
    set_ac1032_native_opt_in(True, settings_path=settings)
    assert ac1032_native_opt_in(settings_path=settings) is True
    # 1. env is an explicit override BOTH ways, beating the saved setting
    monkeypatch.setenv(AC1032_NATIVE_OPT_IN_ENV, "0")
    assert ac1032_native_opt_in(settings_path=settings) is False  # env off > settings on
    monkeypatch.setenv(AC1032_NATIVE_OPT_IN_ENV, "1")
    set_ac1032_native_opt_in(False, settings_path=settings)
    assert ac1032_native_opt_in(settings_path=settings) is True  # env on > settings off


def test_backend_selection_unchanged_when_opt_in_off(monkeypatch: pytest.MonkeyPatch) -> None:
    # With the opt-in off (default), the clean-room native selection is exactly
    # the AC1015 adapter — no AC1032 wrapper, so the default path is unchanged.
    monkeypatch.delenv(AC1032_NATIVE_OPT_IN_ENV, raising=False)
    selection = create_dwg_backend_selection("native")
    assert selection.adapter.name == "native-ac1015"
    assert selection.adapter.supports_version(_AC1032) is False  # -> existing default path


def test_backend_selection_wraps_when_opt_in_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AC1032_NATIVE_OPT_IN_ENV, "1")
    selection = create_dwg_backend_selection("native")
    assert selection.adapter.name == "native-ac1032"
    assert selection.adapter.supports_version(_AC1032) is True
    # AC1015 is still handled (delegated to the wrapped fallback).
    assert selection.adapter.supports_version(_AC1015) is True


def test_adapter_inert_for_ac1032_when_opt_in_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(AC1032_NATIVE_OPT_IN_ENV, raising=False)
    adapter = DwgNativeAc1032Adapter(fallback_adapter=_RecordingFallback())
    assert adapter.supports_version(_AC1032) is False  # falls through to fallback (False)


def test_adapter_delegates_non_ac1032_to_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AC1032_NATIVE_OPT_IN_ENV, "1")
    fallback = _RecordingFallback()
    adapter = DwgNativeAc1032Adapter(fallback_adapter=fallback)
    # AC1015 is not handled by the AC1032 reader -> delegated.
    assert adapter.supports_version(_AC1015) is True
    drawing = adapter.read_file("ignored.dwg", _AC1015)
    assert isinstance(drawing, DwgAdapterDrawing)
    assert fallback.read_calls == ["AC1015"]
    # AC1032 IS handled here (opt-in on) -> not delegated.
    assert adapter.supports_version(_AC1032) is True


def test_adapter_license_is_internal() -> None:
    adapter = DwgNativeAc1032Adapter()
    assert adapter.license_id == "INTERNAL"  # in DwgImporter.DEFAULT_ALLOWED_LICENSES


def test_real_ac1032_imports_through_pipeline_zero_oda(monkeypatch: pytest.MonkeyPatch) -> None:
    # Opt-in on: a real AC1032 DWG flows native reader -> DwgAdapterDrawing ->
    # DwgImporter canonical, with ZERO commercial-converter / ezdxf calls.
    if not _SAMPLE.exists():
        pytest.skip(f"local AC1032 sample not present: {_SAMPLE}")
    monkeypatch.setenv(AC1032_NATIVE_OPT_IN_ENV, "1")

    version = DwgVersionDetector.detect_file(_SAMPLE)
    assert version.code == "AC1032"

    adapter = DwgNativeAc1032Adapter(fallback_adapter=DwgNativeAc1015Adapter())
    drawing = adapter.read_file(_SAMPLE, version)
    assert isinstance(drawing, DwgAdapterDrawing)
    assert len(drawing.model_space) > 100  # decoded entities passed to the importer

    canonical = DwgImporter(adapter=adapter).import_file(_SAMPLE)
    report = canonical["import_report"]
    assert report["adapter"]["name"] == "native-ac1032"
    types = {entity["type"] for entity in canonical["entities"]}
    # Every decoded type — including the DIMENSION/HATCH/POINT payloads — reaches
    # canonical in the DXF importer's shape, so the structural diff reads them.
    assert {"line", "text", "mtext", "circle", "dimension", "hatch", "point"}.issubset(types)
    assert len(canonical["entities"]) > 200

    dimension = next(e for e in canonical["entities"] if e["type"] == "dimension")
    assert dimension["geometry"]["measurement"] is not None
    assert dimension["geometry"]["dimension_type"] in {
        "linear",
        "aligned",
        "angular",
        "diameter",
        "radius",
        "ordinate",
    }
    hatch = next(e for e in canonical["entities"] if e["type"] == "hatch")
    assert hatch["geometry"]["pattern_name"]  # non-empty, upper-cased

    # Linetype (handle-stream resolved) reaches the canonical style; named
    # linetypes from the LTYPE records appear alongside the well-known tokens.
    style_linetypes = {
        e.get("style", {}).get("linetype") for e in canonical["entities"] if e.get("style")
    }
    assert "BYLAYER" in style_linetypes
    assert "ACAD_ISO02W100" in style_linetypes  # a named LTYPE resolved via the handle stream

    # ENC entity colour (ACI index) reaches the canonical style too.
    style_colors = {
        e.get("style", {}).get("color") for e in canonical["entities"] if e.get("style")
    }
    assert 256 in style_colors  # BYLAYER is the common default
    assert any(isinstance(c, int) and 0 < c < 256 for c in style_colors)  # an explicit ACI


def test_real_ac1032_separates_model_space_from_block_definitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Block-definition geometry (entmode==0) is grouped under its owning block
    # instead of being emitted at block-local coords in model space (the
    # pollution fix), so INSERTs expand at the correct transform.
    if not _SAMPLE.exists():
        pytest.skip(f"local AC1032 sample not present: {_SAMPLE}")
    monkeypatch.setenv(AC1032_NATIVE_OPT_IN_ENV, "1")
    from src.services.comparison.dwg_r2018_reader import read_r2018_entities

    version = DwgVersionDetector.detect_file(_SAMPLE)
    adapter = DwgNativeAc1032Adapter(fallback_adapter=DwgNativeAc1015Adapter())
    drawing = adapter.read_file(_SAMPLE, version)

    assert drawing.blocks, "expected block definitions to be emitted"
    block_entity_count = sum(len(b.entities) for b in drawing.blocks)
    assert block_entity_count > 0
    # Separation is lossless and a strict split (model is a subset of the whole).
    table = read_r2018_entities(_SAMPLE.read_bytes())
    assert len(drawing.model_space) + block_entity_count == table.decoded_count
    assert len(drawing.model_space) < table.decoded_count
    # Unique block names (anonymous *U/*D disambiguated by handle) — else ezdxf
    # cannot tell two same-named anonymous blocks apart on expansion.
    names = [b.name for b in drawing.blocks]
    assert len(names) == len(set(names))
    # The block-owned geometry reaches canonical with space="block".
    canonical = DwgImporter(adapter=adapter).import_file(_SAMPLE)
    assert canonical["blocks"]
    assert any(e.get("space") == "block" for e in canonical["entities"])


def test_build_unique_block_names_disambiguates_anonymous_and_duplicates() -> None:
    from src.services.comparison.dwg_native_ac1032_adapter import _build_unique_block_names

    unique = _build_unique_block_names(
        {0x10: "M20-2", 0x20: "*U", 0x30: "*U", 0x40: "DUP", 0x50: "DUP"}
    )
    assert unique[0x10] == "M20-2"  # named + unique keeps its name
    assert unique[0x20] != unique[0x30] and unique[0x20].startswith("*U_")  # anonymous distinct
    assert unique[0x40] != unique[0x50]  # duplicate names disambiguated by handle
    assert len(set(unique.values())) == len(unique)  # all unique


def test_adapter_entity_remaps_insert_block_name_to_unique() -> None:
    from src.services.comparison.dwg_native_ac1032_adapter import _adapter_entity
    from src.services.comparison.dwg_r2018_reader import R2018Entity

    insert = R2018Entity(
        handle=0x5,
        object_type=0x07,
        type_name="INSERT",
        geometry={
            "insert": (0.0, 0.0, 0.0),
            "scale": (1.0, 1.0, 1.0),
            "rotation_deg": 0.0,
            "block_name": "*U",
            "block_handle": 0x30,
        },
    )
    # With the unique-name map, the INSERT references the disambiguated block name.
    assert _adapter_entity(insert, {0x30: "*U_30"}).geometry["block_name"] == "*U_30"
    # Without a map, the raw name is preserved (backward compatible).
    assert _adapter_entity(insert).geometry["block_name"] == "*U"


def test_map_entity_emits_dimension_hatch_point_payloads() -> None:
    # _map_entity maps the native DIMENSION/HATCH/POINT geometry to the same
    # canonical shape the DXF importer emits (no real file needed).
    from src.services.comparison.dwg_importer import DwgAdapterEntity

    drawing = DwgAdapterDrawing(
        model_space=[
            DwgAdapterEntity(
                raw_type="POINT", geometry={"location": (1.0, 2.0, 0.0)}, layer="0", handle="1"
            ),
            DwgAdapterEntity(
                raw_type="DIMENSION",
                geometry={
                    "text_midpoint": (5.0, 6.0, 0.0),
                    "measurement": 42.5,
                    "dimtype": 0,
                    "text": "",
                },
                layer="0",
                handle="2",
            ),
            DwgAdapterEntity(
                raw_type="HATCH",
                geometry={
                    "pattern": "ansi31",
                    "solid": True,
                    "is_gradient": False,
                    "gradient_name": "LINEAR",
                    "bbox": {"min_x": 0.0, "min_y": 0.0, "max_x": 4.0, "max_y": 3.0},
                },
                layer="0",
                handle="3",
            ),
            DwgAdapterEntity(
                raw_type="ELLIPSE",
                geometry={
                    "center": (1.0, 2.0, 0.0),
                    "major_axis": (3.0, 0.0, 0.0),
                    "ratio": 0.5,
                    "start_param": 0.0,
                    "end_param": 6.283185,
                },
                layer="0",
                handle="4",
            ),
        ]
    )
    canonical = DwgImporter(adapter=DwgNativeAc1032Adapter()).import_adapter_drawing(
        drawing, version=_AC1032
    )
    by_type = {e["type"]: e for e in canonical["entities"]}
    assert by_type["point"]["geometry"]["location"] == {"x": 1.0, "y": 2.0, "z": 0.0}
    dim = by_type["dimension"]["geometry"]
    assert dim["dimension_type"] == "linear" and dim["measurement"] == 42.5
    # ELLIPSE tessellates to a canonical polyline (renders + diffs as a curve),
    # keeping ELLIPSE as the recorded source raw type.
    polyline = by_type["polyline"]
    assert polyline["source"]["raw_type"] == "ELLIPSE"
    assert len(polyline["geometry"]["vertices"]) > 8
    hatch = by_type["hatch"]
    assert hatch["geometry"]["pattern_name"] == "ANSI31"  # upper-cased, like the DXF path
    assert hatch["geometry"]["solid_fill"] is True
    assert hatch["bbox"]["max_x"] == 4.0 and hatch["bbox"]["max_y"] == 3.0


def test_native_unsupported_safety_surfaces_partial_decode() -> None:
    # S1 (graceful fallback / no silent partial): an entity the native adapter
    # cannot map to geometry (e.g. SPLINE/LEADER) must be VISIBLY flagged so the
    # viewer/compare can badge a partial decode — never silently treated as a
    # complete drawing ([[silent_fallback_pattern]]).
    from src.services.comparison.dwg_importer import DwgAdapterEntity
    from src.services.comparison.dwg_native_ac1032_adapter import native_decode_partial_summary

    supported = [
        DwgAdapterEntity(
            raw_type="LINE",
            geometry={"start": (0, 0, 0), "end": (1, 0, 0)},
            layer="0",
            handle="1",
            style={},
        ),
        DwgAdapterEntity(
            raw_type="POINT", geometry={"location": (0, 0, 0)}, layer="0", handle="2", style={}
        ),
    ]
    clean = native_decode_partial_summary(supported)
    assert clean["partial_native_decode"] is False
    assert clean["unsupported_native_entity_types"] == []

    with_unsupported = supported + [
        DwgAdapterEntity(raw_type="SPLINE", geometry={}, layer="0", handle="3", style={}),
        DwgAdapterEntity(raw_type="LEADER", geometry={}, layer="0", handle="4", style={}),
    ]
    flagged = native_decode_partial_summary(with_unsupported)
    assert flagged["partial_native_decode"] is True
    assert flagged["unsupported_native_entity_types"] == ["LEADER", "SPLINE"]


def test_real_ac1032_opt_in_product_path_diffs_and_clouds(monkeypatch: pytest.MonkeyPatch) -> None:
    # End-to-end capstone: a real AC1032 imported through the OPT-IN PRODUCT path
    # (DwgImporter + native adapter, not the diagnostic build_r2018_canonical_document)
    # feeds the real compare engine -> change zones -> revision clouds, ZERO ODA.
    if not _SAMPLE.exists():
        pytest.skip(f"local AC1032 sample not present: {_SAMPLE}")
    monkeypatch.setenv(AC1032_NATIVE_OPT_IN_ENV, "1")

    adapter = DwgNativeAc1032Adapter(fallback_adapter=DwgNativeAc1015Adapter())
    before = DwgImporter(adapter=adapter).import_file(_SAMPLE)
    assert before["import_report"]["adapter"]["name"] == "native-ac1032"  # ZERO ODA
    assert len(before["entities"]) > 200

    engine = DrawingCompareEngine(DrawingCompareOptions(include_unchanged=False))

    # (1) A drawing compared with itself produces NO changes (no false positives).
    self_diff = engine.compare(before, copy.deepcopy(before))
    self_edits = (
        self_diff.summary_counts["added"]
        + self_diff.summary_counts["removed"]
        + self_diff.summary_counts["modified"]
    )
    assert self_edits == 0, self_diff.summary_counts
    assert self_diff.summary_counts["unchanged"] == len(before["entities"])

    # (2) A single edit on the after side is detected and isolated.
    after = copy.deepcopy(before)
    moved = next(e for e in after["entities"] if e["type"] == "line")
    moved["geometry"]["end"]["x"] += 40.0
    moved["geometry"]["end"]["y"] += 30.0
    sx, ex = moved["geometry"]["start"]["x"], moved["geometry"]["end"]["x"]
    sy, ey = moved["geometry"]["start"]["y"], moved["geometry"]["end"]["y"]
    moved["bbox"] = {
        "min_x": min(sx, ex),
        "min_y": min(sy, ey),
        "max_x": max(sx, ex),
        "max_y": max(sy, ey),
    }
    centroid = ((min(sx, ex) + max(sx, ex)) / 2.0, (min(sy, ey) + max(sy, ey)) / 2.0)

    diff = engine.compare(before, after)
    edits = (
        diff.summary_counts["added"]
        + diff.summary_counts["removed"]
        + diff.summary_counts["modified"]
    )
    assert 1 <= edits <= 4, diff.summary_counts
    assert diff.summary_counts["unchanged"] >= len(before["entities"]) - 4

    # (3) The edit drives a change zone and a revision cloud that covers it.
    result = ComparisonResult(source_a="before.dwg", source_b="after.dwg")
    for record in diff.to_change_records():
        result.add_change(record)
    zones = build_change_zones(
        result, pair_id="P1", drawing_number="P1", options=ChangeZoneOptions(cluster_distance=120.0)
    )
    covering = [
        z
        for z in zones
        if z.bbox[0] <= centroid[0] <= z.bbox[2] and z.bbox[1] <= centroid[1] <= z.bbox[3]
    ]
    assert covering, f"no change zone covers the moved line at {centroid}"
    cloud = revcloud_geometry_from_bbox(covering[0].bbox)
    assert len(cloud.vertices) >= 4
    assert min(v[0] for v in cloud.vertices) <= centroid[0] <= max(v[0] for v in cloud.vertices)
    assert min(v[1] for v in cloud.vertices) <= centroid[1] <= max(v[1] for v in cloud.vertices)
