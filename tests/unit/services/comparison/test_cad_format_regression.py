from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.cad_format_regression import (
    build_current_results,
    build_regression_report,
    compare_results,
    expanded_samples,
    main as cad_regression_main,
    validate_manifest,
)
from src.services.comparison.drawing_compare_engine import (
    CompareTolerance,
    DrawingCompareEngine,
    DrawingCompareOptions,
    result_fingerprint,
)
from src.services.comparison.drawing_normalizer import (
    DrawingNormalizer,
    NormalizationOptions,
)
from src.services.comparison.dxf_importer import DxfImporter, DxfParseError


MANIFEST_PATH = Path("tests/data/comparison/cad_samples/manifest.yaml")
GOLDEN_PATH = Path("tests/data/comparison/cad_samples/golden-results.json")
REPORT_PATH = Path("docs/CAD_FORMAT_REGRESSION_REPORT.md")
WORKFLOW_PATH = Path(".github/workflows/cad-format-regression.yml")

NORMALIZATION_OPTIONS = NormalizationOptions(
    flatten_curves=True,
    flatten_tolerance_mm=0.1,
    resolve_bylayer_byblock=True,
    normalize_text=True,
    remove_near_zero_geometry=False,
)


def _load_manifest() -> dict[str, Any]:
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


def _load_golden() -> dict[str, Any]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _sample_by_id(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {sample["id"]: sample for sample in manifest["samples"]}


def _import_sample(sample: dict[str, Any]) -> dict[str, Any]:
    source = sample["source"]
    if source["kind"] == "file":
        path = Path(source["path"])
        text = path.read_text(encoding="utf-8")
        return DxfImporter(expand_blocks=True).import_text(
            text,
            source_path=path,
            file_name=path.name,
        )
    if source["kind"] == "generated" and source["generator"] == "grid_lines":
        text = _generate_grid_dxf(
            line_count=int(source["line_count"]),
            spacing_mm=float(source["spacing_mm"]),
            layer=str(source["layer"]),
        )
        return DxfImporter(expand_blocks=True).import_text(
            text,
            file_name=f"{sample['id']}.dxf",
        )
    raise AssertionError(f"Unsupported cad-sample source: {source}")


def _normalized_sample(sample: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    imported = _import_sample(sample)
    normalized, report = DrawingNormalizer(NORMALIZATION_OPTIONS).normalize(imported)
    return imported, normalized, report.to_dict()


def _generate_grid_dxf(*, line_count: int, spacing_mm: float, layer: str) -> str:
    lines: list[str] = [
        "0", "SECTION", "2", "HEADER",
        "9", "$ACADVER", "1", "AC1032",
        "9", "$INSUNITS", "70", "4",
        "0", "ENDSEC",
        "0", "SECTION", "2", "TABLES",
        "0", "TABLE", "2", "LAYER",
        "0", "LAYER", "2", "0", "70", "0", "62", "7", "6", "Continuous",
        "0", "LAYER", "2", layer, "70", "0", "62", "8", "6", "Continuous",
        "0", "ENDTAB",
        "0", "ENDSEC",
        "0", "SECTION", "2", "BLOCKS",
        "0", "ENDSEC",
        "0", "SECTION", "2", "ENTITIES",
    ]
    for index in range(line_count):
        y = index * spacing_mm
        lines.extend(
            [
                "0", "LINE",
                "5", f"G{index:04d}",
                "8", layer,
                "10", "0",
                "20", _num(y),
                "11", "1000",
                "21", _num(y),
            ]
        )
    lines.extend(["0", "ENDSEC", "0", "EOF"])
    return "\n".join(lines) + "\n"


def _num(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _sample_snapshot(sample: dict[str, Any]) -> dict[str, Any]:
    imported, normalized, report = _normalized_sample(sample)
    warnings = list(imported["import_report"]["warnings"])
    stats = imported["import_report"]["stats"]
    return {
        "category": sample["category"],
        "format": sample["format"],
        "status": imported["import_report"]["status"],
        "entity_count": len(normalized["entities"]),
        "layer_count": len(normalized["layers"]),
        "bbox": _stable_bbox(normalized.get("extents")),
        "geometry_hash": _aggregate_geometry_hash(normalized["entities"]),
        "warning_count": len(warnings),
        "warning_codes": dict(sorted(Counter(warning["code"] for warning in warnings).items())),
        "unsupported_entity_count": stats["unsupported_entity_count"],
        "entity_types": dict(sorted(Counter(entity["type"] for entity in normalized["entities"]).items())),
        "normalization": {
            "changed_entity_count": report["changed_entity_count"],
            "removed_entity_count": report["removed_entity_count"],
            "rounded_coordinate_count": report["rounded_coordinate_count"],
            "normalized_polyline_count": report["normalized_polyline_count"],
            "flattened_curve_count": report["flattened_curve_count"],
            "normalized_text_count": report["normalized_text_count"],
            "recomputed_hash_count": report["recomputed_hash_count"],
        },
    }


def _diff_snapshot(case: dict[str, Any], samples: dict[str, dict[str, Any]]) -> dict[str, Any]:
    _, old_normalized, _ = _normalized_sample(samples[case["old_sample"]])
    _, new_normalized, _ = _normalized_sample(samples[case["new_sample"]])
    options = DrawingCompareOptions(
        tolerance=CompareTolerance(
            position_tolerance_mm=float(case["options"]["position_tolerance_mm"]),
            bbox_tolerance_mm=float(case["options"]["position_tolerance_mm"]),
        ),
        search_radius_mm=float(case["options"]["search_radius_mm"]),
        include_entity_snapshots=False,
        include_match_candidates=False,
    )
    result = DrawingCompareEngine(options).compare(old_normalized, new_normalized)
    payload = result.to_dict()
    return {
        "summary": payload["summary"],
        "change_types": [change["change_type"] for change in payload["changes"]],
        "entity_types": [change["entity_type"] for change in payload["changes"]],
        "fingerprint": result_fingerprint(result),
    }


def _stable_bbox(bbox: Any) -> dict[str, Any]:
    if not isinstance(bbox, dict):
        return {
            "min_x": 0.0,
            "min_y": 0.0,
            "min_z": 0.0,
            "max_x": 0.0,
            "max_y": 0.0,
            "max_z": 0.0,
            "quality": "missing",
        }
    return {
        "min_x": round(float(bbox.get("min_x", 0.0) or 0.0), 6),
        "min_y": round(float(bbox.get("min_y", 0.0) or 0.0), 6),
        "min_z": round(float(bbox.get("min_z", 0.0) or 0.0), 6),
        "max_x": round(float(bbox.get("max_x", 0.0) or 0.0), 6),
        "max_y": round(float(bbox.get("max_y", 0.0) or 0.0), 6),
        "max_z": round(float(bbox.get("max_z", 0.0) or 0.0), 6),
        "quality": str(bbox.get("quality") or "missing"),
    }


def _aggregate_geometry_hash(entities: list[dict[str, Any]]) -> str:
    hashes = sorted(
        str((entity.get("hashes") or {}).get("geometry_hash") or "")
        for entity in entities
    )
    payload = json.dumps(hashes, ensure_ascii=False, separators=(",", ":"))
    return "cad-geom:v1:sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _mutate_for_fuzz(case: dict[str, Any], samples: dict[str, dict[str, Any]]) -> str:
    source = samples[case["sample"]]["source"]
    text = Path(source["path"]).read_text(encoding="utf-8")
    if case["mutation"] == "truncate_bytes":
        return text[: int(case["byte_count"])]
    if case["mutation"] == "replace_first_group_code":
        lines = text.splitlines()
        lines[0] = str(case["replacement"])
        return "\n".join(lines) + "\n"
    raise AssertionError(f"Unsupported fuzz mutation: {case['mutation']}")


@pytest.mark.parametrize("sample", _load_manifest()["samples"], ids=lambda item: item["id"])
def test_cad_samples_import_normalize_and_match_golden(sample: dict[str, Any]) -> None:
    golden = _load_golden()["samples"][sample["id"]]

    assert _sample_snapshot(sample) == golden


def test_cad_diff_cases_match_golden_snapshots() -> None:
    manifest = _load_manifest()
    samples = _sample_by_id(manifest)
    golden = _load_golden()["diff_cases"]

    actual = {
        case["id"]: _diff_snapshot(case, samples)
        for case in manifest["diff_cases"]
    }

    assert actual == golden


def test_cad_fuzz_cases_fail_with_expected_parser_errors() -> None:
    manifest = _load_manifest()
    samples = _sample_by_id(manifest)

    for case in manifest["fuzz_cases"]:
        text = _mutate_for_fuzz(case, samples)
        with pytest.raises(DxfParseError):
            DxfImporter().import_text(text, file_name=f"{case['id']}.dxf")


def test_all_cad_samples_are_classified_for_regression_coverage() -> None:
    manifest = _load_manifest()
    categories = {sample["category"] for sample in expanded_samples(manifest)}

    assert categories == {
        "simple",
        "block_centered",
        "text_centered",
        "hatch_centered",
        "hatch_dimension_centered",
        "large",
        "large_synthetic",
        "customer_sanitized",
        "unsupported_objects",
        "unsupported_malformed",
    }


def test_cad_samples_manifest_is_structurally_valid() -> None:
    manifest = _load_manifest()

    assert validate_manifest(manifest) == []


def test_cad_golden_results_are_reproducible_by_cli_builder() -> None:
    manifest = _load_manifest()
    golden = _load_golden()

    actual = build_current_results(manifest)

    assert compare_results(actual, golden) == []


def test_cad_regression_report_documents_current_manifest_and_golden() -> None:
    manifest = _load_manifest()
    golden = _load_golden()
    actual = build_current_results(manifest)

    report = build_regression_report(
        manifest=manifest,
        golden=golden,
        actual=actual,
        mismatches=[],
        manifest_errors=[],
    )

    assert "Status: **PASS**" in report
    assert "Samples: 100" in report
    assert "Manifest sample definitions: 7" in report
    assert "Generated sample definitions: 7" in report
    assert "Diff cases: 1" in report
    assert "Fuzz cases: 2" in report
    assert "| unsupported_objects | unsupported_objects | dxf | partial | 0 | 2 | 3 |" in report


def test_checked_in_cad_regression_report_is_reproducible() -> None:
    manifest = _load_manifest()
    golden = _load_golden()
    actual = build_current_results(manifest)
    expected = build_regression_report(
        manifest=manifest,
        golden=golden,
        actual=actual,
        mismatches=[],
        manifest_errors=[],
    )

    assert REPORT_PATH.read_text(encoding="utf-8") == expected


def test_cad_regression_cli_check_writes_report(tmp_path: Path) -> None:
    report_path = tmp_path / "cad-format-regression-report.md"

    exit_code = cad_regression_main(["--check", "--report", str(report_path)])

    assert exit_code == 0
    assert report_path.read_text(encoding="utf-8").startswith("# CAD Format Regression Report")


def test_cad_format_regression_workflow_runs_snapshot_check_and_uploads_report() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "python scripts\\cad_format_regression.py --check --report build\\reports\\cad-format-regression-report.md" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "cad-format-regression-report" in workflow
