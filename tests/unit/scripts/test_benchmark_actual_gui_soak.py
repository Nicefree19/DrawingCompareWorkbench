# -*- coding: utf-8 -*-
from __future__ import annotations

import contextlib
import importlib
import io
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture()
def benchmark_module():
    return importlib.import_module("scripts.benchmark_actual_gui_soak")


def _write_png(path: Path) -> None:
    image_mod = pytest.importorskip("PIL.Image")
    image = image_mod.new("RGB", (64, 64), "white")
    for x in range(8, 56):
        image.putpixel((x, x), (0, 0, 0))
        image.putpixel((x, 63 - x), (0, 0, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def _write_gui_soak_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    output_dir = tmp_path / "validation"
    viewer_root = output_dir / "viewer"
    images = viewer_root / "images"
    overlays_dir = viewer_root / "overlays"
    overlay = {
        "zone_id": "Z-1",
        "change_type": "modified",
        "severity": "medium",
        "bbox": [10, 10, 40, 40],
        "old_bbox": [11, 11, 41, 41],
        "page_a": 0,
        "page_b": 0,
    }
    pairs = []
    for idx in range(2):
        pair_id = f"pair-{idx + 1}"
        before = images / f"{pair_id}_before.png"
        after = images / f"{pair_id}_after.png"
        overlay_json = overlays_dir / f"{pair_id}.json"
        _write_png(before)
        _write_png(after)
        overlay_json.parent.mkdir(parents=True, exist_ok=True)
        overlay_json.write_text(json.dumps({"overlays": [overlay]}), encoding="utf-8")
        transform = {"min_x": 0, "min_y": 0, "max_x": 64, "max_y": 64, "width": 64, "height": 64, "dpi": 72}
        pairs.append(
            {
                "pair_id": pair_id,
                "drawing_number": pair_id,
                "before_image": str(before),
                "after_image": str(after),
                "before_transform": transform,
                "after_transform": transform,
                "overlay_json": str(overlay_json),
                "overlay_total_count": 1,
                "render_status": "rendered",
                "top_issues": [overlay],
            }
        )
    manifest_path = viewer_root / "viewer_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"schema_version": 2, "pairs": pairs}), encoding="utf-8")
    (viewer_root / "viewer_perf.jsonl").write_text("", encoding="utf-8")
    summary_path = output_dir / "validation_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "comparison": {"completed_pairs": 2},
                "viewer_package": {
                    "viewer_dir": str(viewer_root),
                    "output_paths": {
                        "viewer_dir": str(viewer_root),
                        "viewer_manifest_json": str(manifest_path),
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    customer_manifest = output_dir / "customer_evidence_manifest.json"
    customer_manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "evidence_level": "customer_grade",
                "dataset_id": "unit-gui-soak",
                "dataset_provenance": {"source_kind": "customer_grade", "approval_status": "approved"},
                "sheet_count": 20,
                "format_coverage": {"dwg_dxf": True, "pdf_pdf": True, "cad_pdf_blocked": True},
            }
        ),
        encoding="utf-8",
    )
    return summary_path, manifest_path, customer_manifest


def test_main_writes_p5_g22_actual_gui_soak_json_with_passing_gates(
    benchmark_module,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary_path, _manifest_path, customer_manifest = _write_gui_soak_fixture(tmp_path)
    output_json = tmp_path / "p5_g22_actual_gui_soak.json"
    resources = [
        {"rss_mb": 100.0, "process_handle_count": 10, "open_file_descriptor_count": None, "gdi_handle_count": 5, "user_handle_count": 6},
        {"rss_mb": 100.0, "process_handle_count": 10, "open_file_descriptor_count": None, "gdi_handle_count": 5, "user_handle_count": 6},
        {"rss_mb": 100.0, "process_handle_count": 10, "open_file_descriptor_count": None, "gdi_handle_count": 5, "user_handle_count": 6},
    ]
    fallback_resource = dict(resources[-1])
    monkeypatch.setattr(benchmark_module, "_native_resource_snapshot", lambda: resources.pop(0) if resources else dict(fallback_resource))
    monkeypatch.setattr(benchmark_module, "_process_children_worker_count", lambda: 0)

    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        exit_code = benchmark_module.main(
            [
                "--validation-summary",
                str(summary_path),
                "--customer-evidence-manifest",
                str(customer_manifest),
                "--require-customer-corpus",
                "--output-json",
                str(output_json),
                "--visits",
                "2",
                "--warmup-visits",
                "0",
                "--skip-zone-render-workers",
                "--drawing-selection-p95-target-ms",
                "60000",
                "--zone-selection-p95-target-ms",
                "60000",
                "--event-loop-gap-max-target-ms",
                "60000",
            ]
        )

    assert exit_code == 0
    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["benchmark_id"] == "p5_g22_actual_gui_soak"
    assert payload["profile"] == "actual_gui_customer_corpus_soak"
    assert payload["status"] == "passed"
    assert payload["corpus"]["evidence_level"] == "customer_grade"
    assert payload["summary"]["pair_count"] == 2
    assert payload["summary"]["gui_soak_completed"] is True
    assert payload["summary"]["zone_selection_count"] >= 1
    assert payload["summary"]["worker_cleanup_ok"] is True
    assert payload["summary"]["orphan_worker_count"] == 0
    assert payload["summary"]["native_resource_summary"]["measurement_available"] is True
    assert payload["summary"]["native_resource_summary"]["process_handle_slope"] == payload["summary"]["process_handle_slope"]
    assert payload["summary"]["native_resource_summary"]["gdi_handle_slope"] == payload["summary"]["gdi_handle_slope"]
    assert payload["summary"]["worker_tree_summary"]["cleanup_ok"] is True
    assert payload["summary"]["worker_tree_summary"]["orphan_worker_count"] == 0
    assert all(gate["passed"] for gate in payload["gates"])
    trailing = json.loads([line for line in stdout.getvalue().splitlines() if line.startswith("{")][-1])
    assert trailing["status"] == "passed"


def test_actual_gui_soak_fails_when_event_loop_gap_exceeds_gate(
    benchmark_module,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary_path, _manifest_path, _customer_manifest = _write_gui_soak_fixture(tmp_path)
    output_json = tmp_path / "failed_p5_g22.json"
    monkeypatch.setattr(
        benchmark_module,
        "_native_resource_snapshot",
        lambda: {
            "rss_mb": 100.0,
            "process_handle_count": 10,
            "open_file_descriptor_count": None,
            "gdi_handle_count": 5,
            "user_handle_count": 6,
        },
    )
    monkeypatch.setattr(benchmark_module, "_process_children_worker_count", lambda: 0)

    with contextlib.redirect_stdout(io.StringIO()):
        exit_code = benchmark_module.main(
            [
                "--validation-summary",
                str(summary_path),
                "--output-json",
                str(output_json),
                "--visits",
                "1",
                "--warmup-visits",
                "0",
                "--skip-zone-render-workers",
                "--event-loop-gap-max-target-ms",
                "0.001",
                "--no-fail-on-gate",
            ]
        )

    assert exit_code == 0
    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    failed = {gate["name"] for gate in payload["gates"] if not gate["passed"]}
    assert "p5_g22_event_loop_gap_max_ms" in failed


def test_parse_args_exposes_p5_g22_targets(benchmark_module) -> None:
    args = benchmark_module.parse_args(
        [
            "--validation-summary",
            "summary.json",
            "--visits",
            "12",
            "--min-page-navigation-count",
            "1",
            "--allow-missing-native-resources",
        ]
    )

    assert args.validation_summary == Path("summary.json")
    assert args.visits == 12
    assert args.min_page_navigation_count == 1
    assert args.allow_missing_native_resources is True
