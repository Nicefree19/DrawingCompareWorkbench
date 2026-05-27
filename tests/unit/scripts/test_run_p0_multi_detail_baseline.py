from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import p0_multi_detail_baseline_runner as runner


class _Completed:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _output_dir_from_command(command: list[str]) -> Path:
    return Path(command[command.index("--out") + 1])


def _write_nonblank_png(path: Path) -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (2, 1), (255, 255, 255))
    image.putpixel((1, 0), (0, 0, 0))
    image.save(path)


def _write_valid_p0_evidence(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "_SUCCESS").write_text("", encoding="utf-8")
    _write_json(output_dir / "run_manifest.json", {"run_id": "run-p0", "status": "completed"})
    _write_json(
        output_dir / "validation_summary.json",
        {
            "status": "passed",
            "runtime_budget": {"peak_rss_mb": 100.0, "sample_count": 1},
            "outputs": {
                "selected_zone_evidence_json": "viewer/selected_zone_evidence.json",
                "perf_events_summary_json": "perf_events_summary.json",
            },
        },
    )
    _write_json(
        output_dir / "artifacts" / "region_detection_summary.json",
        {
            "region_count": 4,
            "results": [
                {"side": "before", "status": "passed", "region_count": 2},
                {"side": "after", "status": "passed", "region_count": 2},
            ],
        },
    )
    _write_json(
        output_dir / "artifacts" / "region_match_summary.json",
        {
            "summaries": [
                {
                    "auto_matched_count": 2,
                    "manual_matched_count": 0,
                    "review_required_count": 0,
                }
            ]
        },
    )
    _write_json(
        output_dir / "artifacts" / "localized_compare_summary.json",
        {"summaries": [{"total_zones": 2, "assigned_zones": 2}]},
    )
    _write_json(
        output_dir / "artifacts" / "localized_region_compare_results.json",
        {"status": "passed", "compared_region_count": 2, "unsupported_pair_count": 0},
    )
    _write_json(
        output_dir / "artifacts" / "localized_change_zones_v2.json",
        {"status": "passed", "primary_enabled": True, "zones": [{"zone_id": "R-001"}]},
    )
    _write_json(
        output_dir / "artifacts" / "multi_frame_validation.json",
        {"status": "passed", "detected_region_count": 4},
    )
    _write_json(
        output_dir / "artifacts" / "region_aware_status.json",
        {"feature_mode": "review_gate", "localized_assigned_zones": 2},
    )
    _write_json(
        output_dir / "artifacts" / "region_viewer" / "region_viewer_manifest.json",
        {
            "entry_count": 1,
            "entries": [
                {
                    "before": {"render_status": "rendered"},
                    "after": {"render_status": "rendered"},
                }
            ],
        },
    )
    _write_json(
        output_dir / "viewer" / "selected_zone_evidence.json",
        {"status": "passed", "failure_count": 0, "renders": [{"render_ms": 20.0}]},
    )
    (output_dir / "viewer").mkdir(parents=True, exist_ok=True)
    (output_dir / "viewer" / "viewer_perf.jsonl").write_text('{"event":"zone_crop_render"}\n', encoding="utf-8")
    (output_dir / "perf_events.jsonl").write_text('{"stage":"validation","event":"done","elapsed_ms":1}\n', encoding="utf-8")
    _write_nonblank_png(output_dir / "viewer" / "focus_tiles" / "pair-1" / "R-001.png")


def test_build_validation_command_contains_p0_evidence_options(tmp_path: Path) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    out = tmp_path / "out"
    args = runner.parse_args(
        [
            "--before",
            str(before),
            "--after",
            str(after),
            "--out",
            str(out),
            "--recursive",
            "--selected-zone-evidence-per-pair",
            "2",
            "--viewer-render-timeout-seconds",
            "15",
            "--python-executable",
            "python",
        ]
    )

    command = runner.build_validation_command(args, before.resolve(), after.resolve(), out.resolve())

    assert command[:2] == ["python", str(runner.ROOT / "scripts" / "validate_drawing_compare_realset.py")]
    assert "--measure-runtime-budget" in command
    assert "--change-zone-report" in command
    assert "--review-dashboard" in command
    assert "--executive-review" in command
    assert "--export-viewer-package" in command
    assert "--viewer-perf-log" in command
    assert "--render-selected-zone-evidence" in command
    assert command[command.index("--selected-zone-evidence-per-pair") + 1] == "2"
    assert command[command.index("--viewer-render-policy") + 1] == "top-issues"
    assert command[command.index("--viewer-render-timeout-seconds") + 1] == "15"
    assert "--recursive" in command


def test_dry_run_writes_plan_without_subprocess(tmp_path: Path, monkeypatch) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    out = tmp_path / "out"
    before.mkdir()
    after.mkdir()

    def fail_run(*_args, **_kwargs):
        raise AssertionError("subprocess.run should not be called in dry-run mode")

    monkeypatch.setattr(runner.subprocess, "run", fail_run)

    exit_code = runner.main(
        [
            "--before",
            str(before),
            "--after",
            str(after),
            "--out",
            str(out),
            "--dry-run",
        ]
    )

    assert exit_code == 0
    payload = json.loads((out / "p0_multi_detail_baseline_run.json").read_text(encoding="utf-8"))
    assert payload["status"] == "dry_run"
    assert payload["env_overrides"] == runner.REGION_ENV
    assert "--render-selected-zone-evidence" in payload["validation_command"]
    assert "--fail-on-incomplete" in payload["inventory_command"]


def test_main_runs_validation_then_inventory_with_region_env(tmp_path: Path, monkeypatch) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    out = tmp_path / "out"
    before.mkdir()
    after.mkdir()
    calls = []

    def fake_run(command, *, cwd, env):
        calls.append({"command": command, "cwd": cwd, "env": env})
        if str(command[1]).endswith("validate_drawing_compare_realset.py"):
            _write_valid_p0_evidence(_output_dir_from_command(command))
        return _Completed(0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    exit_code = runner.main(
        [
            "--before",
            str(before),
            "--after",
            str(after),
            "--out",
            str(out),
            "--python-executable",
            "python",
        ]
    )

    assert exit_code == 0
    assert len(calls) == 2
    assert calls[0]["cwd"] == runner.ROOT
    assert calls[0]["env"]["DRAWING_COMPARE_MULTI_FRAME"] == "auto"
    assert calls[0]["env"]["DRAWING_COMPARE_AUTO_REGION_COMPARE"] == "1"
    assert calls[0]["command"][1].endswith("validate_drawing_compare_realset.py")
    assert calls[1]["command"][1].endswith("inventory_performance_baselines.py")
    payload = json.loads((out / "p0_multi_detail_baseline_run.json").read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
    assert payload["validation_returncode"] == 0
    assert payload["inventory_returncode"] == 0
    assert payload["postprocess"]["nonblank_pixel_probe_status"] == "passed"
    assert payload["p0_contract"]["passed"] is True
    assert (out / "perf_events_summary.json").exists()
    assert (out / "nonblank_pixel_probe.json").exists()


def test_main_fails_before_inventory_when_p0_contract_is_incomplete(tmp_path: Path, monkeypatch) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    out = tmp_path / "out"
    before.mkdir()
    after.mkdir()
    calls = []

    def fake_run(command, *, cwd, env):
        calls.append(command)
        return _Completed(0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    exit_code = runner.main(["--before", str(before), "--after", str(after), "--out", str(out)])

    assert exit_code == runner.P0_CONTRACT_FAILURE_RETURN_CODE
    assert len(calls) == 1
    payload = json.loads((out / "p0_multi_detail_baseline_run.json").read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["error"] == "P0 multi-detail evidence contract failed"
    assert payload["p0_contract"]["passed"] is False
    assert "artifacts/region_detection_summary.json" in payload["p0_contract"]["missing"]


def test_allow_incomplete_inventory_does_not_bypass_p0_contract(tmp_path: Path, monkeypatch) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    out = tmp_path / "out"
    before.mkdir()
    after.mkdir()
    calls = []

    def fake_run(command, *, cwd, env):
        calls.append(command)
        return _Completed(0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    exit_code = runner.main(
        [
            "--before",
            str(before),
            "--after",
            str(after),
            "--out",
            str(out),
            "--allow-incomplete-inventory",
        ]
    )

    assert exit_code == runner.P0_CONTRACT_FAILURE_RETURN_CODE
    assert len(calls) == 1
    payload = json.loads((out / "p0_multi_detail_baseline_run.json").read_text(encoding="utf-8"))
    assert "--fail-on-incomplete" not in payload["inventory_command"]
    assert payload["error"] == "P0 multi-detail evidence contract failed"


def test_p0_contract_rejects_single_region_smoke_run(tmp_path: Path) -> None:
    _write_valid_p0_evidence(tmp_path)
    _write_json(
        tmp_path / "artifacts" / "region_detection_summary.json",
        {
            "region_count": 2,
            "results": [
                {"side": "before", "status": "passed", "region_count": 1},
                {"side": "after", "status": "passed", "region_count": 1},
            ],
        },
    )
    runner.postprocess_evidence(tmp_path)

    contract = runner.evaluate_p0_evidence_contract(tmp_path)

    assert contract["passed"] is False
    assert "real multi-detail evidence requires at least 2 detected regions per side" in contract["failures"]


def test_main_stops_before_inventory_when_validation_fails(tmp_path: Path, monkeypatch) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    out = tmp_path / "out"
    before.mkdir()
    after.mkdir()
    calls = []

    def fake_run(command, *, cwd, env):
        calls.append(command)
        return _Completed(7)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    exit_code = runner.main(["--before", str(before), "--after", str(after), "--out", str(out)])

    assert exit_code == 7
    assert len(calls) == 1
    payload = json.loads((out / "p0_multi_detail_baseline_run.json").read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["error"] == "validation command failed"
    assert payload["validation_returncode"] == 7


def test_postprocess_evidence_writes_nonblank_pixel_probe(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    image_path = tmp_path / "viewer" / "focus_tiles" / "pair-1" / "R-001.png"
    image_path.parent.mkdir(parents=True)
    image = Image.new("RGB", (2, 1), (255, 255, 255))
    image.putpixel((1, 0), (0, 0, 0))
    image.save(image_path)

    payload = runner.postprocess_evidence(tmp_path)

    assert payload["status"] == "passed"
    probe = json.loads((tmp_path / "nonblank_pixel_probe.json").read_text(encoding="utf-8"))
    assert probe["status"] == "passed"
    assert probe["images"][0]["path"] == "viewer/focus_tiles/pair-1/R-001.png"
    assert (tmp_path / "perf_events_summary.json").exists()
