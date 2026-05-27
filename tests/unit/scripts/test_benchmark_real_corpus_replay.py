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
    return importlib.import_module("scripts.benchmark_real_corpus_replay")


def _write_png(path: Path, *, blank: bool) -> None:
    image_mod = pytest.importorskip("PIL.Image")
    image = image_mod.new("RGB", (64, 64), "white")
    if not blank:
        for x in range(8, 56):
            image.putpixel((x, x), (0, 0, 0))
            image.putpixel((x, 63 - x), (0, 0, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def _write_validation_fixture(
    tmp_path: Path,
    *,
    blank: bool = False,
    stale_event: bool = False,
    fallback_missing_reason: bool = False,
) -> tuple[Path, Path, Path]:
    output_dir = tmp_path / "realset_out"
    viewer_root = output_dir / "viewer"
    pair_dir = viewer_root / "zone_crops" / "pair-1" / "cache-key-1"
    before = pair_dir / "Z-1_before.png"
    after = pair_dir / "Z-1_after.png"
    _write_png(before, blank=blank)
    _write_png(after, blank=blank)
    (viewer_root / "images").mkdir(parents=True, exist_ok=True)
    _write_png(viewer_root / "images" / "pair-1_page-0.png", blank=False)

    lifecycle = "fallback_visible" if fallback_missing_reason else "ready"
    fidelity = "relative_overlay" if fallback_missing_reason else "pdf_render"
    reason_code = "" if fallback_missing_reason else ""
    (pair_dir / "render_result.json").write_text(
        json.dumps(
            {
                "pair_uuid": "pair-1",
                "zone_id": "Z-1",
                "before_image": "Z-1_before.png",
                "after_image": "Z-1_after.png",
                "renderer_backend": "pdf-image-crop",
                "visual_fidelity": fidelity,
                "render_lifecycle": lifecycle,
                "reason_code": reason_code,
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    events = [
        {
            "event": "zone_crop_render",
            "cache_hit": False,
            "render_ms": 12.0,
            "render_lifecycle": lifecycle,
            "visual_fidelity": fidelity,
            "renderer_backend": "pdf-image-crop",
            "reason_code": reason_code,
            "stale_result_visible": stale_event,
            "pdf_display_list_cache_total_estimated_bytes": 100,
            "pdf_display_list_cache_byte_limit": 1000,
            "dxf_index_cache_total_estimated_bytes": 0,
            "dxf_index_cache_byte_limit": 0,
        }
    ]
    (viewer_root / "viewer_perf.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )
    summary = {
        "output_dir": str(output_dir),
        "comparison": {"completed_pairs": 1},
        "runtime_budget": {"sampler_active": True, "sample_count": 4},
        "viewer_package": {
            "viewer_dir": str(viewer_root),
            "output_paths": {
                "viewer_dir": str(viewer_root),
                "viewer_tiles_manifest_json": str(viewer_root / "tiles_manifest.json"),
            },
        },
    }
    summary_path = output_dir / "validation_summary.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    manifest_path = output_dir / "customer_evidence_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "evidence_level": "customer_grade",
                "dataset_id": "unit-real-corpus",
                "dataset_provenance": {
                    "source_kind": "customer_approved",
                    "approval_status": "approved",
                },
                "sheet_count": 20,
                "format_coverage": {
                    "dwg_dxf": True,
                    "pdf_pdf": True,
                    "cad_pdf_blocked": True,
                },
            }
        ),
        encoding="utf-8",
    )
    return summary_path, viewer_root, manifest_path


def test_main_writes_p5_g16_replay_json_with_passing_gates(
    benchmark_module,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary_path, _viewer_root, manifest_path = _write_validation_fixture(tmp_path)
    output_json = tmp_path / "p5_g16.json"
    rss_values = iter([100.0, 100.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(benchmark_module, "_process_rss_mb", lambda: next(rss_values, 100.0))

    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        exit_code = benchmark_module.main(
            [
                "--validation-summary",
                str(summary_path),
                "--output-json",
                str(output_json),
                "--customer-evidence-manifest",
                str(manifest_path),
                "--require-customer-corpus",
                "--visits",
                "4",
                "--warmup-visits",
                "1",
            ]
        )

    assert exit_code == 0
    assert output_json.exists()
    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["benchmark_id"] == "p5_g16_real_corpus_replay"
    assert payload["profile"] == "real_corpus_artifact_replay"
    assert payload["status"] == "passed"
    assert payload["corpus"]["evidence_level"] == "customer_grade"
    assert payload["corpus"]["sheet_count"] == 20
    assert payload["environment"]["allow_missing_psutil"] is False
    assert payload["summary"]["zone_render_artifact_count"] == 1
    assert payload["summary"]["page_artifact_count"] == 1
    assert payload["summary"]["replay_completed"] is True
    assert payload["summary"]["blank_zone_output_count"] == 0
    assert payload["summary"]["stale_result_visible_count"] == 0
    assert payload["summary"]["rss_measurement_available"] is True
    assert payload["summary"]["rss_slope"]["slope_mb_per_100_visits"] == 0.0
    assert all(gate["passed"] for gate in payload["gates"])
    trailing = json.loads([line for line in stdout.getvalue().splitlines() if line][-1])
    assert trailing["status"] == "passed"


def test_replay_json_fails_blank_stale_and_missing_fallback_reason(
    benchmark_module,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary_path, _viewer_root, _manifest_path = _write_validation_fixture(
        tmp_path,
        blank=True,
        stale_event=True,
        fallback_missing_reason=True,
    )
    output_json = tmp_path / "p5_g16_failed.json"
    monkeypatch.setattr(benchmark_module, "_process_rss_mb", lambda: 100.0)

    with contextlib.redirect_stdout(io.StringIO()):
        exit_code = benchmark_module.main(
            [
                "--validation-summary",
                str(summary_path),
                "--output-json",
                str(output_json),
                "--visits",
                "2",
                "--warmup-visits",
                "0",
                "--no-fail-on-gate",
            ]
        )

    assert exit_code == 0
    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["summary"]["blank_zone_output_count"] == 1
    assert payload["summary"]["stale_result_visible_count"] == 1
    assert payload["summary"]["fallback_missing_reason_count"] == 1
    failed = {gate["name"] for gate in payload["gates"] if not gate["passed"]}
    assert "blank_zone_output_count" in failed
    assert "stale_result_visible_count" in failed
    assert "fallback_missing_reason_count" in failed


def test_replay_respects_zero_page_artifact_limit(
    benchmark_module,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary_path, _viewer_root, _manifest_path = _write_validation_fixture(tmp_path)
    output_json = tmp_path / "p5_g16_zone_only.json"
    monkeypatch.setattr(benchmark_module, "_process_rss_mb", lambda: 100.0)

    with contextlib.redirect_stdout(io.StringIO()):
        exit_code = benchmark_module.main(
            [
                "--validation-summary",
                str(summary_path),
                "--output-json",
                str(output_json),
                "--visits",
                "2",
                "--max-page-artifacts",
                "0",
                "--min-page-artifacts",
                "0",
            ]
        )

    assert exit_code == 0
    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["summary"]["zone_render_artifact_count"] == 1
    assert payload["summary"]["page_artifact_count"] == 0
    assert payload["summary"]["replay_artifact_count"] == 1


def test_parse_args_exposes_real_corpus_replay_targets(benchmark_module) -> None:
    args = benchmark_module.parse_args(
        [
            "--validation-summary",
            "summary.json",
            "--visits",
            "12",
            "--rss-slope-target-mb-per-100",
            "3.5",
            "--allow-rss-unavailable",
            "--require-customer-corpus",
        ]
    )

    assert args.validation_summary == Path("summary.json")
    assert args.visits == 12
    assert args.rss_slope_target_mb_per_100 == 3.5
    assert args.allow_rss_unavailable is True
    assert args.require_customer_corpus is True
