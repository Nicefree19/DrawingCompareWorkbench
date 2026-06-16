"""Experimental opt-in AC1032 native adapter — wiring + default-unchanged tests.

The real-file integration test needs the local git-ignored AC1032 corpus and
SKIPs in CI (real-file verification is local-only), matching
``test_dwg_r2018_reader``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

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


def test_opt_in_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(AC1032_NATIVE_OPT_IN_ENV, raising=False)
    assert ac1032_native_opt_in() is False
    for truthy in ("1", "true", "YES", "on"):
        monkeypatch.setenv(AC1032_NATIVE_OPT_IN_ENV, truthy)
        assert ac1032_native_opt_in() is True
    monkeypatch.setenv(AC1032_NATIVE_OPT_IN_ENV, "0")
    assert ac1032_native_opt_in() is False


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

    adapter = DwgNativeAc1032Adapter(
        fallback_adapter=DwgNativeAc1015Adapter()
    )
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
        "linear", "aligned", "angular", "diameter", "radius", "ordinate"
    }
    hatch = next(e for e in canonical["entities"] if e["type"] == "hatch")
    assert hatch["geometry"]["pattern_name"]  # non-empty, upper-cased


def test_map_entity_emits_dimension_hatch_point_payloads() -> None:
    # _map_entity maps the native DIMENSION/HATCH/POINT geometry to the same
    # canonical shape the DXF importer emits (no real file needed).
    from src.services.comparison.dwg_importer import DwgAdapterEntity

    drawing = DwgAdapterDrawing(
        model_space=[
            DwgAdapterEntity(raw_type="POINT", geometry={"location": (1.0, 2.0, 0.0)},
                             layer="0", handle="1"),
            DwgAdapterEntity(
                raw_type="DIMENSION",
                geometry={"text_midpoint": (5.0, 6.0, 0.0), "measurement": 42.5,
                          "dimtype": 0, "text": ""},
                layer="0", handle="2",
            ),
            DwgAdapterEntity(
                raw_type="HATCH",
                geometry={"pattern": "ansi31", "solid": True, "is_gradient": False,
                          "gradient_name": "LINEAR",
                          "bbox": {"min_x": 0.0, "min_y": 0.0, "max_x": 4.0, "max_y": 3.0}},
                layer="0", handle="3",
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
    hatch = by_type["hatch"]
    assert hatch["geometry"]["pattern_name"] == "ANSI31"  # upper-cased, like the DXF path
    assert hatch["geometry"]["solid_fill"] is True
    assert hatch["bbox"]["max_x"] == 4.0 and hatch["bbox"]["max_y"] == 3.0
