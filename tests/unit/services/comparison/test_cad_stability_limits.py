from __future__ import annotations

import json
import math
from pathlib import Path

from src.services.comparison.cad_stability import CadLimitCode, CadStabilityLimits
from src.services.comparison.drawing_compare_engine import DrawingCompareEngine, DrawingCompareOptions
from src.services.comparison.dwg_importer import DwgFailureCode, DwgImporter, DwgJsonFixtureAdapter
from src.services.comparison.dxf_importer import DxfImportLimitError, DxfImporter
from src.services.comparison.import_pipeline import (
    CadPipelineErrorCode,
    CadPipelineStatus,
    ImportPipeline,
    ImportPipelineOptions,
)


SIMPLE_DXF = Path("tests/data/comparison/cad_samples/dxf/simple_base.dxf")


def _write_dwg_fixture(path: Path, entity_count: int = 2) -> Path:
    payload = {
        "header": {"$INSUNITS": 4},
        "layers": [{"name": "BEAM", "color": 3}],
        "model_space": [
            {
                "type": "LINE",
                "handle": f"L{idx}",
                "layer": "BEAM",
                "geometry": {
                    "start": {"x": 0, "y": idx, "z": 0},
                    "end": {"x": 100, "y": idx, "z": 0},
                },
            }
            for idx in range(entity_count)
        ],
    }
    path.write_bytes(
        b"AC1015"
        + DwgJsonFixtureAdapter.MARKER
        + json.dumps(payload).encode("utf-8")
    )
    return path


def test_dxf_importer_stops_at_entity_limit() -> None:
    text = SIMPLE_DXF.read_text(encoding="utf-8")

    try:
        DxfImporter(max_entities=1).import_text(text, file_name="limit.dxf")
    except DxfImportLimitError as exc:
        assert exc.code == CadLimitCode.ENTITY_LIMIT_EXCEEDED
        assert exc.details["max_entities"] == 1
    else:
        raise AssertionError("DXF entity limit must stop import")


def test_dxf_importer_stops_at_token_limit() -> None:
    text = SIMPLE_DXF.read_text(encoding="utf-8")

    try:
        DxfImporter(max_tokens=4).import_text(text, file_name="token-limit.dxf")
    except DxfImportLimitError as exc:
        assert exc.code == CadLimitCode.TOKEN_LIMIT_EXCEEDED
    else:
        raise AssertionError("DXF token limit must stop import")


def test_dxf_importer_cancel_callback_stops_tokenization() -> None:
    text = SIMPLE_DXF.read_text(encoding="utf-8")

    try:
        DxfImporter(cancel_callback=lambda: True).import_text(text, file_name="cancel.dxf")
    except DxfImportLimitError as exc:
        assert exc.code == CadLimitCode.IMPORT_CANCELLED
    else:
        raise AssertionError("DXF cancel callback must stop import")


def test_dxf_importer_timeout_stops_tokenization() -> None:
    text = SIMPLE_DXF.read_text(encoding="utf-8")

    try:
        DxfImporter(timeout_seconds=1e-30).import_text(text, file_name="timeout.dxf")
    except DxfImportLimitError as exc:
        assert exc.code == CadLimitCode.IMPORT_TIMEOUT
    else:
        raise AssertionError("DXF timeout must stop import")


def test_import_pipeline_returns_failed_result_for_limits_and_malformed_dxf(tmp_path: Path) -> None:
    limited = ImportPipeline(
        ImportPipelineOptions(stability_limits=CadStabilityLimits(max_entities=1))
    ).import_file(SIMPLE_DXF)

    assert limited.status == CadPipelineStatus.FAILED
    assert limited.error_code == CadLimitCode.ENTITY_LIMIT_EXCEEDED

    malformed_path = tmp_path / "malformed.dxf"
    malformed_path.write_text("XX\nSECTION\n", encoding="utf-8")
    malformed = ImportPipeline().import_file(malformed_path)
    assert malformed.status == CadPipelineStatus.FAILED
    assert malformed.error_code == CadPipelineErrorCode.DXF_PARSE_ERROR

    bad_text_path = SIMPLE_DXF.parent / "bad-temp-do-not-exist.dxf"
    result = ImportPipeline().import_file(bad_text_path)
    assert result.status == CadPipelineStatus.FAILED
    assert result.error_code == CadPipelineErrorCode.READ_FAILED


def test_dwg_importer_returns_failed_result_for_entity_limit_and_malformed_header(tmp_path: Path) -> None:
    path = _write_dwg_fixture(tmp_path / "many.dwg", entity_count=2)
    limited = DwgImporter(adapter=DwgJsonFixtureAdapter(), max_entities=1).import_file(path)
    assert limited["import_report"]["status"] == "failed"
    assert limited["import_report"]["error_code"] == DwgFailureCode.ENTITY_LIMIT_EXCEEDED

    corrupted = tmp_path / "corrupted.dwg"
    corrupted.write_bytes(b"DWG")
    failed = ImportPipeline().import_file(corrupted)
    assert failed.status == CadPipelineStatus.FAILED
    assert failed.error_code == DwgFailureCode.CORRUPTED


def test_compare_engine_handles_non_finite_malformed_bbox_without_crashing() -> None:
    entity = {
        "id": "line:bad",
        "type": "line",
        "layer_id": "layer:0",
        "geometry": {
            "type": "line",
            "start": {"x": 0, "y": 0, "z": 0},
            "end": {"x": 1, "y": 0, "z": 0},
        },
        "bbox": {"min_x": -math.inf, "min_y": 0, "max_x": math.inf, "max_y": 1},
        "hashes": {"geometry_hash": "geom:v1:sha256:bad"},
        "style": {},
        "metadata": {},
    }
    drawing = {
        "drawing": {"id": "drawing:test", "source": {"format": "test"}},
        "layers": [{"id": "layer:0", "name": "0"}],
        "entities": [entity],
    }

    result = DrawingCompareEngine(
        DrawingCompareOptions(max_spatial_cells_per_entity=1)
    ).compare(drawing, drawing)

    assert result.summary["unchanged"] == 1
