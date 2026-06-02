from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from src.services.comparison.dwg_backend import DWG_BACKEND_ENV
from src.services.comparison.dwg_importer import DwgFailureCode, DwgJsonFixtureAdapter
from src.services.comparison.cad_stability import CadStabilityLimits
from src.services.comparison.dwg_differ import DwgDiffer
from src.services.comparison.comparison_config import ComparisonConfig
from src.services.comparison.drawing_compare_engine import CompareTolerance, DrawingCompareOptions
from src.services.comparison.import_pipeline import (
    CadPipelineErrorCode,
    CadPipelineStatus,
    ComparePipeline,
    ComparePipelineOptions,
    ImportPipeline,
    ImportPipelineOptions,
    USER_CONVERTED_DXF_DEFAULT_MAX_TOKENS,
    _effective_stability_limits,
)


SAMPLES = Path("tests/data/comparison/cad_samples/dxf")


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


def _line(handle: str, layer: str, x0: float, y0: float, x1: float, y1: float) -> list[object]:
    return [
        "0", "LINE",
        "5", handle,
        "8", layer,
        "10", x0,
        "20", y0,
        "30", 0,
        "11", x1,
        "21", y1,
        "31", 0,
    ]


def _write_two_layer_dxf(path: Path, *, ignored_line_end_x: float) -> Path:
    path.write_text(
        _wrap(
            *_header(),
            *_tables("STRUCT", "IGNORE"),
            *_section(
                "BLOCKS",
            ),
            *_section(
                "ENTITIES",
                *_line("S1", "STRUCT", 0, 0, 100, 0),
                *_line("I1", "IGNORE", 0, 10, ignored_line_end_x, 10),
            ),
        ),
        encoding="utf-8",
    )
    return path


def _write_dwg_fixture(path: Path, *, version: str = "AC1015", payload: dict | None = None) -> Path:
    default_payload = {
        "header": {"$INSUNITS": 4},
        "layers": [{"name": "BEAM", "color": 3, "linetype": "Continuous"}],
        "model_space": [
            {
                "type": "LINE",
                "handle": "L1",
                "layer": "BEAM",
                "geometry": {
                    "start": {"x": 0, "y": 0, "z": 0},
                    "end": {"x": 100, "y": 0, "z": 0},
                },
            }
        ],
    }
    path.write_bytes(
        version.encode("ascii")
        + DwgJsonFixtureAdapter.MARKER
        + json.dumps(payload or default_payload, ensure_ascii=False).encode("utf-8")
    )
    return path


def test_import_pipeline_selects_dxf_importer_without_oda() -> None:
    pipeline = ImportPipeline()
    selection = pipeline.select_importer(SAMPLES / "simple_base.dxf")
    result = pipeline.import_file(SAMPLES / "simple_base.dxf")

    assert selection["importer"] == "DxfImporter"
    assert selection["version"]["code"] == "AC1032"
    assert result.status == CadPipelineStatus.OK
    assert result.source_format == "dxf"
    assert result.importer == "DxfImporter"
    assert result.error_code is None
    assert result.normalized_drawing is not None
    assert result.to_dict()["entity_count"] == 4


def test_compare_pipeline_compares_dxf_without_oda_converter() -> None:
    result = ComparePipeline(
        options=ComparePipelineOptions(
            compare_options=DrawingCompareOptions(
                tolerance=CompareTolerance(position_tolerance_mm=0.01, bbox_tolerance_mm=0.01),
                search_radius_mm=20.0,
            )
        )
    ).compare(
        SAMPLES / "simple_base.dxf",
        SAMPLES / "simple_modified.dxf",
    )

    assert result.status == CadPipelineStatus.OK
    assert result.error_code is None
    assert result.diff is not None
    assert result.diff.summary["added"] == 1
    assert result.diff.summary["modified"] == 2
    assert result.to_dict()["partial_imports"] == []


def test_comparison_result_excludes_unchanged_records_but_keeps_count() -> None:
    pipeline_result = ComparePipeline().compare(
        SAMPLES / "simple_base.dxf",
        SAMPLES / "simple_base.dxf",
    )

    result = pipeline_result.to_comparison_result()

    assert pipeline_result.diff is not None
    assert pipeline_result.diff.summary["unchanged"] > 0
    assert result.total_changes == 0
    assert result.unchanged_count == pipeline_result.diff.summary["unchanged"]
    assert result.changes == []


def test_dwg_differ_canonical_pipeline_applies_layer_filters(tmp_path: Path) -> None:
    before = _write_two_layer_dxf(tmp_path / "before.dxf", ignored_line_end_x=100)
    after = _write_two_layer_dxf(tmp_path / "after.dxf", ignored_line_end_x=120)

    unfiltered = DwgDiffer().compare(before, after)
    filtered = DwgDiffer().compare(before, after, exclude_layers=["IGNORE"])

    assert unfiltered.total_changes > 0
    assert filtered.total_changes == 0
    assert filtered.unchanged_count == 1
    assert not any("ignores legacy include/exclude" in warning for warning in filtered.warnings)


def test_import_pipeline_reports_dwg_unsupported_version(tmp_path: Path) -> None:
    path = _write_dwg_fixture(tmp_path / "old-version.dwg", version="AC1014")
    pipeline = ImportPipeline()

    selection = pipeline.select_importer(path)
    result = pipeline.import_file(path)

    assert selection["importer"] == "DwgImporter"
    assert selection["supported"] is False
    assert selection["error_code"] == DwgFailureCode.UNSUPPORTED_VERSION
    assert result.status == CadPipelineStatus.FAILED
    assert result.source_format == "dwg"
    assert result.importer == "DwgImporter"
    assert result.error_code == DwgFailureCode.UNSUPPORTED_VERSION
    assert result.version == {
        "code": "AC1014",
        "family": "AutoCAD R14",
        "release": "AutoCAD R14",
        "supported": False,
    }
    assert "not supported" in result.user_message


def test_import_pipeline_uses_injected_dwg_adapter_version_capability(tmp_path: Path) -> None:
    path = _write_dwg_fixture(tmp_path / "planned.dwg", version="AC1032")

    class PlannedVersionAdapter(DwgJsonFixtureAdapter):
        name = "planned-version-fixture"

        def supports_version(self, version) -> bool:  # type: ignore[no-untyped-def]
            return version.code == "AC1032"

    pipeline = ImportPipeline(ImportPipelineOptions(dwg_adapter=PlannedVersionAdapter()))

    selection = pipeline.select_importer(path)
    result = pipeline.import_file(path)

    assert selection["supported"] is True
    assert selection["error_code"] is None
    assert selection["version"]["supported"] is False
    assert result.status == CadPipelineStatus.OK
    assert result.import_report["adapter"]["name"] == "planned-version-fixture"


def test_import_pipeline_explicit_backend_mode_uses_fail_closed_placeholder(tmp_path: Path) -> None:
    path = _write_dwg_fixture(tmp_path / "planned.dwg", version="AC1032")
    pipeline = ImportPipeline(ImportPipelineOptions(dwg_backend_mode="commercial_sdk"))

    selection = pipeline.select_importer(path)
    result = pipeline.import_file(path)

    assert selection["supported"] is False
    assert selection["error_code"] == DwgFailureCode.UNSUPPORTED_VERSION
    assert result.status == CadPipelineStatus.FAILED
    assert result.import_report["adapter"]["name"] == "commercial-sdk-placeholder"
    assert result.import_report["adapter"]["backend_mode"] == "commercial_sdk"
    assert result.import_report["adapter"]["approval_required"] is True


def test_user_converter_mode_raises_default_dxf_token_budget_only_for_default_limits() -> None:
    default_options = ImportPipelineOptions(dwg_backend_mode="user_converter")
    native_options = ImportPipelineOptions()
    custom_options = ImportPipelineOptions(
        dwg_backend_mode="user_converter",
        stability_limits=CadStabilityLimits(max_dxf_tokens=3_000_000),
    )

    assert _effective_stability_limits(default_options).max_dxf_tokens == USER_CONVERTED_DXF_DEFAULT_MAX_TOKENS
    assert _effective_stability_limits(native_options).max_dxf_tokens == CadStabilityLimits().max_dxf_tokens
    assert _effective_stability_limits(custom_options).max_dxf_tokens == 3_000_000


def test_compare_pipeline_user_converter_mode_uses_converted_dxf_pair(tmp_path: Path) -> None:
    source_a = _write_dwg_fixture(tmp_path / "detail.dwg", version="AC1032")
    source_b = _write_dwg_fixture(tmp_path / "detail_r1.dwg", version="AC1032")
    before_dir = tmp_path / "dxf_registered" / "before"
    after_dir = tmp_path / "dxf_registered" / "after"
    before_dir.mkdir(parents=True)
    after_dir.mkdir(parents=True)
    fallback_a = _write_two_layer_dxf(before_dir / "detail.dxf", ignored_line_end_x=100)
    fallback_b = _write_two_layer_dxf(after_dir / "detail_r1.dxf", ignored_line_end_x=120)

    result = ComparePipeline(
        ComparePipelineOptions(
            import_options=ImportPipelineOptions(dwg_backend_mode="user_converter")
        )
    ).compare(source_a, source_b)
    payload = result.to_dict()

    assert result.status == CadPipelineStatus.OK
    assert result.error_code is None
    assert result.imports["a"].source_path == str(fallback_a.resolve())
    assert result.imports["b"].source_path == str(fallback_b.resolve())
    assert result.imports["a"].source_format == "dxf"
    assert result.input_resolution["used"] is True
    assert result.input_resolution["reason"] == "unsupported_dwg_version_with_converted_dxf"
    assert payload["input_resolution"]["effective_source_a"] == str(fallback_a.resolve())
    assert payload["warnings"][-1]["code"] == "DWG_CONVERTED_DXF_FALLBACK"
    comparison_result = result.to_comparison_result()
    assert comparison_result.metadata["dwg_dxf_fallback"]["used"] is True


def test_compare_pipeline_env_user_converter_mode_uses_converted_dxf_pair(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(DWG_BACKEND_ENV, "user_converter")
    source_a = _write_dwg_fixture(tmp_path / "detail.dwg", version="AC1024")
    source_b = _write_dwg_fixture(tmp_path / "detail_r1.dwg", version="AC1024")
    before_dir = tmp_path / "dxf_registered" / "before"
    after_dir = tmp_path / "dxf_registered" / "after"
    before_dir.mkdir(parents=True)
    after_dir.mkdir(parents=True)
    fallback_a = _write_two_layer_dxf(before_dir / "detail.dxf", ignored_line_end_x=100)
    fallback_b = _write_two_layer_dxf(after_dir / "detail_r1.dxf", ignored_line_end_x=120)

    result = ComparePipeline().compare(source_a, source_b)

    assert result.status == CadPipelineStatus.OK
    assert result.input_resolution["used"] is True
    assert result.input_resolution["diagnostics"]["dwg_versions"]["a"]["code"] == "AC1024"
    assert result.imports["a"].source_path == str(fallback_a.resolve())
    assert result.imports["b"].source_path == str(fallback_b.resolve())


def test_dwg_differ_user_converter_mode_uses_converted_dxf_pair(tmp_path: Path) -> None:
    source_a = _write_dwg_fixture(tmp_path / "detail.dwg", version="AC1027")
    source_b = _write_dwg_fixture(tmp_path / "detail_r3.dwg", version="AC1027")
    before_dir = tmp_path / "dxf_registered" / "before"
    after_dir = tmp_path / "dxf_registered" / "after"
    before_dir.mkdir(parents=True)
    after_dir.mkdir(parents=True)
    fallback_a = _write_two_layer_dxf(before_dir / "detail.dxf", ignored_line_end_x=100)
    fallback_b = _write_two_layer_dxf(after_dir / "detail_r3.dxf", ignored_line_end_x=120)

    result = DwgDiffer(config={"dwg_backend_mode": "user_converter"}).compare(source_a, source_b)

    assert result.metadata["pipeline_status"] == CadPipelineStatus.OK
    assert result.metadata["dwg_dxf_fallback"]["used"] is True
    assert result.metadata["dwg_dxf_fallback"]["diagnostics"]["dwg_versions"]["a"]["code"] == "AC1027"
    assert result.metadata["imports"]["a"]["source_path"] == str(fallback_a.resolve())
    assert result.metadata["imports"]["b"]["source_path"] == str(fallback_b.resolve())
    assert any("DWG_CONVERTED_DXF_FALLBACK" in warning for warning in result.warnings)


def test_compare_pipeline_default_does_not_auto_use_converted_dxf_pair(tmp_path: Path) -> None:
    source_a = _write_dwg_fixture(tmp_path / "detail.dwg", version="AC1032")
    source_b = _write_dwg_fixture(tmp_path / "detail_r1.dwg", version="AC1032")
    before_dir = tmp_path / "dxf_registered" / "before"
    after_dir = tmp_path / "dxf_registered" / "after"
    before_dir.mkdir(parents=True)
    after_dir.mkdir(parents=True)
    _write_two_layer_dxf(before_dir / "detail.dxf", ignored_line_end_x=100)
    _write_two_layer_dxf(after_dir / "detail_r1.dxf", ignored_line_end_x=120)

    result = ComparePipeline().compare(source_a, source_b)

    assert result.status == CadPipelineStatus.FAILED
    assert result.error_code == CadPipelineErrorCode.COMPARE_IMPORT_FAILED
    assert result.input_resolution == {}
    assert result.imports["a"].source_format == "dwg"
    assert result.imports["a"].error_code == DwgFailureCode.UNSUPPORTED_VERSION


def test_compare_pipeline_surfaces_import_side_failure(tmp_path: Path) -> None:
    ok = _write_dwg_fixture(tmp_path / "ok.dwg")
    failed = _write_dwg_fixture(tmp_path / "old.dwg", version="AC1014")

    result = ComparePipeline().compare(ok, failed)

    assert result.status == CadPipelineStatus.FAILED
    assert result.error_code == CadPipelineErrorCode.COMPARE_IMPORT_FAILED
    assert result.imports["a"].status == CadPipelineStatus.OK
    assert result.imports["b"].status == CadPipelineStatus.FAILED
    assert result.imports["b"].error_code == DwgFailureCode.UNSUPPORTED_VERSION
    assert result.diff is None


def test_compare_pipeline_allows_partial_dwg_import_and_exposes_warning(tmp_path: Path) -> None:
    before = _write_dwg_fixture(
        tmp_path / "before.dwg",
        payload={
            "header": {"$INSUNITS": 4},
            "layers": [{"name": "BEAM", "color": 3}],
            "model_space": [
                {
                    "type": "LINE",
                    "handle": "L1",
                    "layer": "BEAM",
                    "geometry": {
                        "start": {"x": 0, "y": 0, "z": 0},
                        "end": {"x": 100, "y": 0, "z": 0},
                    },
                },
                {"type": "3DSOLID", "handle": "S1", "layer": "BEAM", "geometry": {}},
            ],
        },
    )
    after = _write_dwg_fixture(tmp_path / "after.dwg")

    result = ComparePipeline().compare(before, after)
    payload = result.to_dict()

    assert result.status == CadPipelineStatus.PARTIAL
    assert result.diff is not None
    assert payload["partial_imports"] == ["a"]
    assert payload["imports"]["a"]["status"] == CadPipelineStatus.PARTIAL
    assert payload["imports"]["a"]["warnings"][0]["code"] == DwgFailureCode.UNSUPPORTED_ENTITY
    assert payload["imports"]["a"]["import_report"]["unsupported_entities"][0]["raw_type"] == "3DSOLID"


def test_oda_fallback_is_disabled_by_default_for_dwg_failures(tmp_path: Path) -> None:
    path = _write_dwg_fixture(tmp_path / "old-version.dwg", version="AC1014")

    result = ImportPipeline(
        ImportPipelineOptions(allow_oda_fallback=False)
    ).import_file(path)

    assert result.status == CadPipelineStatus.FAILED
    assert result.error_code == DwgFailureCode.UNSUPPORTED_VERSION
    assert result.importer == "DwgImporter"


def test_dwg_differ_default_uses_canonical_pipeline_without_oda_converter() -> None:
    with patch("src.services.comparison.dwg_differ.DwgConverter") as converter_class:
        result = DwgDiffer().compare(
            SAMPLES / "simple_base.dxf",
            SAMPLES / "simple_modified.dxf",
        )

    converter_class.assert_not_called()
    assert result.metadata["canonical_pipeline"] is True
    assert result.metadata["comparison_type"] == "CAD_CANONICAL"
    assert result.total_changes > 0


def test_dwg_differ_with_comparison_config_still_uses_canonical_pipeline_without_oda_converter() -> None:
    with patch("src.services.comparison.dwg_differ.DwgConverter") as converter_class:
        result = DwgDiffer(comparison_config=ComparisonConfig.get_default()).compare(
            SAMPLES / "simple_base.dxf",
            SAMPLES / "simple_modified.dxf",
        )

    converter_class.assert_not_called()
    assert result.metadata["canonical_pipeline"] is True
    assert result.metadata["comparison_type"] == "CAD_CANONICAL"


def test_dwg_differ_status_does_not_probe_oda_installation() -> None:
    with patch("src.services.comparison.dwg_differ.DwgConverter") as converter_class:
        converter_class.check_installation.side_effect = AssertionError("must not probe ODA")
        status = DwgDiffer.get_status()

    converter_class.check_installation.assert_not_called()
    assert status["canonical_pipeline"] is True
    assert status["oda_converter"] is False
    assert status["oda_required"] is False
    assert status["legacy_oda_fallback"] == "disabled_by_default"


def test_dwg_differ_returns_partial_import_metadata_for_dwg(tmp_path: Path) -> None:
    before = _write_dwg_fixture(
        tmp_path / "before.dwg",
        payload={
            "header": {"$INSUNITS": 4},
            "layers": [{"name": "BEAM", "color": 3}],
            "model_space": [
                {
                    "type": "LINE",
                    "handle": "L1",
                    "layer": "BEAM",
                    "geometry": {
                        "start": {"x": 0, "y": 0, "z": 0},
                        "end": {"x": 100, "y": 0, "z": 0},
                    },
                },
                {"type": "3DSOLID", "handle": "S1", "layer": "BEAM", "geometry": {}},
            ],
        },
    )
    after = _write_dwg_fixture(tmp_path / "after.dwg")

    result = DwgDiffer().compare(before, after)

    assert result.metadata["pipeline_status"] == CadPipelineStatus.PARTIAL
    assert result.metadata["partial_imports"] == ["a"]
    assert result.metadata["imports"]["a"]["warnings"][0]["code"] == DwgFailureCode.UNSUPPORTED_ENTITY
    assert any("부분 가져오기" in warning for warning in result.warnings)
