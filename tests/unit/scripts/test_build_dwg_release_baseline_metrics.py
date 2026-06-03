from __future__ import annotations

import json
from pathlib import Path

from scripts import build_dwg_release_baseline_metrics as builder


def test_build_metrics_calculates_golden_precision_recall_and_policy_metrics(tmp_path: Path) -> None:
    manifest = _write_golden_fixture(tmp_path)
    product = tmp_path / "product.json"
    product.write_text(json.dumps({"process_cleanup": {"orphan_processes": 0}}), encoding="utf-8")
    fallback = tmp_path / "fallback.json"
    fallback.write_text(
        json.dumps({"versions": [{"code": "AC1032", "default_customer_oda_calls": 0}]}),
        encoding="utf-8",
    )
    sharable = tmp_path / "sharable_path_audit.json"
    sharable.write_text(json.dumps({"passed": True, "leak_count": 0}), encoding="utf-8")
    out = tmp_path / "metrics.json"

    report = builder.build_metrics(
        golden_manifest=manifest,
        result_dir=tmp_path / "results",
        output_json=out,
        product_evidence_json=product,
        fallback_audit_json=fallback,
        sharable_path_audits=[sharable],
        compare_runner=_fake_compare_runner,
    )

    assert out.exists()
    metrics = report["metrics"]
    assert metrics["recall"] == 1.0
    assert metrics["precision"] == 1.0
    assert metrics["false_positive_zone_rate"] == 0.0
    assert metrics["duplicate_zone_rate"] == 0.0
    assert metrics["small_drawing_seconds"] == 1.25
    assert metrics["orphan_processes"] == 0
    assert metrics["customer_path_oda_calls"] == 0
    assert metrics["exported_sensitive_path_leaks"] == 0
    assert "overlay_error_px_150dpi" in report["known_missing_metrics"]
    assert report["evidence_counts"]["dxf_pairs"] == 1


def test_build_metrics_keeps_false_positive_debt_visible(tmp_path: Path) -> None:
    manifest = _write_golden_fixture(tmp_path)

    report = builder.build_metrics(
        golden_manifest=manifest,
        result_dir=tmp_path / "results",
        output_json=tmp_path / "metrics.json",
        compare_runner=_noisy_compare_runner,
    )

    metrics = report["metrics"]
    assert metrics["recall"] == 1.0
    assert metrics["precision"] == 0.5
    assert metrics["false_positive_zone_rate"] == 0.5


def test_build_metrics_reads_performance_probe_and_fallback_counts(tmp_path: Path) -> None:
    manifest = _write_golden_fixture(tmp_path)
    fallback = tmp_path / "fallback.json"
    fallback.write_text(
        json.dumps(
            {
                "versions": [
                    {"code": "AC1024", "converted_dxf_baseline_count": 2, "default_customer_oda_calls": 0},
                    {"code": "AC1027", "converted_dxf_baseline_count": 2, "default_customer_oda_calls": 0},
                    {"code": "AC1032", "converted_dxf_baseline_count": 2, "default_customer_oda_calls": 0},
                ]
            }
        ),
        encoding="utf-8",
    )
    probe = tmp_path / "performance-probe.json"
    probe.write_text(
        json.dumps(
            {
                "medium_drawing_seconds": 12.5,
                "large_drawing_seconds": 55.0,
                "progress_max_gap_s": 1.5,
                "cancel_probe": {"cancel_to_idle_s": 0.8},
                "large_cad_dxf_pairs": 3,
            }
        ),
        encoding="utf-8",
    )

    report = builder.build_metrics(
        golden_manifest=manifest,
        result_dir=tmp_path / "results",
        output_json=tmp_path / "metrics.json",
        fallback_audit_json=fallback,
        large_dwg_probe=probe,
        compare_runner=_fake_compare_runner,
    )

    metrics = report["metrics"]
    assert metrics["medium_drawing_seconds"] == 12.5
    assert metrics["large_drawing_seconds"] == 55.0
    assert metrics["progress_max_gap_s"] == 1.5
    assert metrics["cancel_response_s"] == 0.8
    evidence = report["evidence_counts"]
    assert evidence["large_cad_dxf_pairs"] == 3
    assert evidence["ac1024_converted_dxf_fallback_pairs"] == 2
    assert evidence["ac1027_converted_dxf_fallback_pairs"] == 2
    assert evidence["ac1032_converted_dxf_fallback_pairs"] == 2


def test_build_metrics_reads_supplemental_release_probe(tmp_path: Path) -> None:
    manifest = _write_golden_fixture(tmp_path)
    supplemental = tmp_path / "supplemental.json"
    supplemental.write_text(
        json.dumps(
            {
                "evidence_counts": {
                    "pdf_pairs": 10,
                    "negative_failure_samples": 2,
                    "block_text_dimension_pairs": 2,
                },
                "metrics": {"overlay_error_px_150dpi": 4.25},
            }
        ),
        encoding="utf-8",
    )

    report = builder.build_metrics(
        golden_manifest=manifest,
        result_dir=tmp_path / "results",
        output_json=tmp_path / "metrics.json",
        supplemental_probe=supplemental,
        compare_runner=_fake_compare_runner,
    )

    assert report["metrics"]["overlay_error_px_150dpi"] == 4.25
    assert "overlay_error_px_150dpi" not in report["known_missing_metrics"]
    evidence = report["evidence_counts"]
    assert evidence["pdf_pairs"] == 10
    assert evidence["negative_failure_samples"] == 2
    assert evidence["block_text_dimension_pairs"] == 2


def test_build_metrics_matches_text_added_deleted_by_insert_midpoint(tmp_path: Path) -> None:
    manifest = _write_text_golden_fixture(tmp_path)

    report = builder.build_metrics(
        golden_manifest=manifest,
        result_dir=tmp_path / "results",
        output_json=tmp_path / "metrics.json",
        compare_runner=_text_added_deleted_runner,
    )

    metrics = report["metrics"]
    assert metrics["recall"] == 1.0
    assert metrics["precision"] == 1.0


def test_build_metrics_collapses_added_deleted_truth_into_zone(tmp_path: Path) -> None:
    manifest = _write_custom_manifest(
        tmp_path,
        "zone",
        [
            {"location": [500.0, 400.0], "change_type": "deleted", "layer": "GRID", "tolerance_mm": 50.0},
            {"location": [500.0, 400.0], "change_type": "added", "layer": "DIAG", "tolerance_mm": 50.0},
        ],
    )

    report = builder.build_metrics(
        golden_manifest=manifest,
        result_dir=tmp_path / "results",
        output_json=tmp_path / "metrics.json",
        compare_runner=_single_modified_zone_runner,
    )

    assert report["pairs"][0]["raw_truth_count"] == 2
    assert report["pairs"][0]["truth_count"] == 1
    assert report["metrics"]["recall"] == 1.0
    assert report["metrics"]["precision"] == 1.0


def test_build_metrics_normalizes_block_attribute_text_delta(tmp_path: Path) -> None:
    manifest = _write_custom_manifest(
        tmp_path,
        "attrib",
        [
            {
                "location": [500.0, 400.0],
                "change_type": "modified",
                "layer": "TEXT_LAYER",
                "entity_type": "ATTRIB",
                "tolerance_mm": 1.0,
            }
        ],
    )

    report = builder.build_metrics(
        golden_manifest=manifest,
        result_dir=tmp_path / "results",
        output_json=tmp_path / "metrics.json",
        compare_runner=_block_attribute_runner,
    )

    assert report["metrics"]["recall"] == 1.0
    assert report["metrics"]["precision"] == 1.0


def _write_golden_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "golden"
    pair_dir = root / "dxf" / "one"
    pair_dir.mkdir(parents=True)
    (pair_dir / "before.dxf").write_text("0\nEOF\n", encoding="utf-8")
    (pair_dir / "after.dxf").write_text("0\nEOF\n", encoding="utf-8")
    (pair_dir / "truth.json").write_text(
        json.dumps(
            {
                "expected_changes": [
                    {
                        "location": [500.0, 402.5],
                        "change_type": "modified",
                        "layer": "BEAM",
                        "entity_type": "LINE",
                        "tolerance_mm": 50.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "pairs": [
                    {
                        "pair_id": "one",
                        "before_path": "dxf/one/before.dxf",
                        "after_path": "dxf/one/after.dxf",
                        "expected_changes_path": "dxf/one/truth.json",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _write_custom_manifest(tmp_path: Path, pair_id: str, expected_changes: list[dict]) -> Path:
    root = tmp_path / "golden"
    pair_dir = root / "dxf" / pair_id
    pair_dir.mkdir(parents=True)
    (pair_dir / "before.dxf").write_text("0\nEOF\n", encoding="utf-8")
    (pair_dir / "after.dxf").write_text("0\nEOF\n", encoding="utf-8")
    (pair_dir / "truth.json").write_text(
        json.dumps({"expected_changes": expected_changes}),
        encoding="utf-8",
    )
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "pairs": [
                    {
                        "pair_id": pair_id,
                        "before_path": f"dxf/{pair_id}/before.dxf",
                        "after_path": f"dxf/{pair_id}/after.dxf",
                        "expected_changes_path": f"dxf/{pair_id}/truth.json",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _write_text_golden_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "golden"
    pair_dir = root / "dxf" / "text"
    pair_dir.mkdir(parents=True)
    (pair_dir / "before.dxf").write_text("0\nEOF\n", encoding="utf-8")
    (pair_dir / "after.dxf").write_text("0\nEOF\n", encoding="utf-8")
    (pair_dir / "truth.json").write_text(
        json.dumps(
            {
                "expected_changes": [
                    {
                        "location": [215.0, 100.0],
                        "change_type": "modified",
                        "layer": "DIM",
                        "entity_type": "TEXT",
                        "tolerance_mm": 50.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "pairs": [
                    {
                        "pair_id": "text",
                        "before_path": "dxf/text/before.dxf",
                        "after_path": "dxf/text/after.dxf",
                        "expected_changes_path": "dxf/text/truth.json",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _fake_compare_runner(command: list[str] | tuple[str, ...], timeout_seconds: float) -> builder.CompareExecution:
    output = Path(command[command.index("--output") + 1])
    _write_compare_result(
        output,
        [
            _change("deleted", 500.0, 400.0, "BEAM"),
            _change("added", 500.0, 405.0, "BEAM"),
        ],
    )
    return builder.CompareExecution(exit_code=0, elapsed_s=1.25)


def _noisy_compare_runner(command: list[str] | tuple[str, ...], timeout_seconds: float) -> builder.CompareExecution:
    output = Path(command[command.index("--output") + 1])
    _write_compare_result(
        output,
        [
            _change("deleted", 500.0, 400.0, "BEAM"),
            _change("added", 500.0, 405.0, "BEAM"),
            _change("added", 900.0, 900.0, "BEAM"),
        ],
    )
    return builder.CompareExecution(exit_code=0, elapsed_s=1.0)


def _text_added_deleted_runner(command: list[str] | tuple[str, ...], timeout_seconds: float) -> builder.CompareExecution:
    output = Path(command[command.index("--output") + 1])
    _write_compare_result(
        output,
        [
            _text_change("deleted", 260.0, 125.0, "old_value", 200.0, 100.0),
            _text_change("added", 290.0, 125.0, "new_value", 230.0, 100.0),
        ],
    )
    return builder.CompareExecution(exit_code=0, elapsed_s=1.0)


def _single_modified_zone_runner(command: list[str] | tuple[str, ...], timeout_seconds: float) -> builder.CompareExecution:
    output = Path(command[command.index("--output") + 1])
    _write_compare_result(output, [_change("modified", 500.0, 400.0, "DIAG")])
    return builder.CompareExecution(exit_code=0, elapsed_s=1.0)


def _block_attribute_runner(command: list[str] | tuple[str, ...], timeout_seconds: float) -> builder.CompareExecution:
    output = Path(command[command.index("--output") + 1])
    _write_compare_result(
        output,
        [
            {
                "change_type": "modified",
                "field_name": "block_reference",
                "location": "505.0,400.0",
                "metadata": {"x": 505.0, "y": 400.0, "layer": "0", "entity_type": "block_reference"},
                "old_value": _block_value("DOWEL BAR @100"),
                "new_value": _block_value("DOWEL BAR @200"),
            }
        ],
    )
    return builder.CompareExecution(exit_code=0, elapsed_s=1.0)


def _write_compare_result(path: Path, changes: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"status": "partial", "result": {"summary": {"total_changes": len(changes)}, "changes": changes}}),
        encoding="utf-8",
    )


def _change(change_type: str, x: float, y: float, layer: str) -> dict:
    return {
        "change_type": change_type,
        "field_name": "line",
        "location": f"{x},{y}",
        "metadata": {"x": x, "y": y, "layer": layer, "entity_type": "line"},
    }


def _text_change(change_type: str, x: float, y: float, value_key: str, insert_x: float, insert_y: float) -> dict:
    change = {
        "change_type": change_type,
        "field_name": "text",
        "location": f"{x},{y}",
        "metadata": {"x": x, "y": y, "layer": "DIM", "entity_type": "text"},
    }
    change[value_key] = {
        "geometry": {
            "type": "text",
            "insert": {"x": insert_x, "y": insert_y, "z": 0.0},
            "canonical_text": "1500" if change_type == "deleted" else "1550",
        }
    }
    return change


def _block_value(text: str) -> dict:
    return {
        "geometry": {
            "type": "block_reference",
            "insert": {"x": 500.0, "y": 400.0, "z": 0.0},
            "attributes": [
                {
                    "tag": "DOWEL",
                    "text": text,
                    "canonical_text": text,
                    "insert": {"x": 0.0, "y": 0.0, "z": 0.0},
                }
            ],
        }
    }
