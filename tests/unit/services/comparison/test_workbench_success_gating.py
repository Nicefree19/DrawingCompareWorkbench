# -*- coding: utf-8 -*-
"""Tests for the run-completion gate used by the Workbench when loading results.

The Workbench refuses to claim "완료" until ``_SUCCESS`` exists AND its run_id
matches the run manifest. This guards against partial outputs being treated as
completed (a known foot-gun when a previous failed run leaves stale artifacts).
"""

from __future__ import annotations

import json
from pathlib import Path

from src.services.comparison.run_contract import RunManifestWriter, validate_run_completion


def _start_and_complete_run(out_dir: Path) -> RunManifestWriter:
    writer = RunManifestWriter(out_dir, run_id="run_test_1")
    writer.start(inputs={}, paths={}, preflight={"status": "ok"})
    writer.complete(counts={"pair_count": 1}, outputs={}, warnings=[])
    return writer


def test_validate_run_completion_returns_ok_when_sentinel_and_manifest_align(tmp_path: Path) -> None:
    writer = _start_and_complete_run(tmp_path)
    result = validate_run_completion(str(writer.path), str(writer.success_path))
    assert result["valid"] is True
    assert result["status"] == "ok"
    assert result["run_id"] == "run_test_1"


def test_complete_finalizes_any_running_stages(tmp_path: Path) -> None:
    writer = RunManifestWriter(tmp_path, run_id="run_stage_close")
    writer.start(inputs={}, paths={})
    writer.stage("artifact", "running")
    writer.complete(counts={"pair_count": 1}, outputs={}, warnings=[])

    payload = json.loads(writer.path.read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert payload["stages"]["artifact"]["status"] == "completed"
    assert payload["stages"]["artifact"]["auto_finalized"] is True


def test_validate_run_completion_flags_missing_sentinel(tmp_path: Path) -> None:
    writer = RunManifestWriter(tmp_path, run_id="run_partial")
    writer.start(inputs={}, paths={})
    # Intentionally no .complete() call — sentinel never written
    result = validate_run_completion(str(writer.path), str(tmp_path / "_SUCCESS"))
    assert result["valid"] is False
    assert result["status"] == "missing_sentinel"
    assert "_SUCCESS" in result["message"]


def test_validate_run_completion_detects_stale_sentinel_after_failure(tmp_path: Path) -> None:
    writer = _start_and_complete_run(tmp_path)
    # A subsequent failed run wipes _SUCCESS via .start()/fail().
    writer2 = RunManifestWriter(tmp_path, run_id="run_test_2")
    writer2.start(inputs={}, paths={})
    writer2.fail("compare", "boom")
    result = validate_run_completion(str(writer2.path), str(writer2.success_path))
    assert result["valid"] is False
    assert result["status"] == "missing_sentinel"


def test_validate_run_completion_detects_run_id_mismatch(tmp_path: Path) -> None:
    writer = _start_and_complete_run(tmp_path)
    # Hand-tamper the sentinel to simulate cross-run pollution.
    writer.success_path.write_text(
        json.dumps({"run_id": "run_other", "completed_at": "2026-01-01", "run_manifest": str(writer.path)}),
        encoding="utf-8",
    )
    result = validate_run_completion(str(writer.path), str(writer.success_path))
    assert result["valid"] is False
    assert result["status"] == "run_id_mismatch"


def test_validate_run_completion_handles_missing_manifest(tmp_path: Path) -> None:
    sentinel = tmp_path / "_SUCCESS"
    sentinel.write_text(json.dumps({"run_id": "run_x"}), encoding="utf-8")
    result = validate_run_completion(str(tmp_path / "missing_manifest.json"), str(sentinel))
    assert result["valid"] is False
    assert result["status"] == "manifest_missing"
    assert result["run_id"] == "run_x"


def test_validate_run_completion_handles_unreadable_manifest(tmp_path: Path) -> None:
    sentinel = tmp_path / "_SUCCESS"
    sentinel.write_text(json.dumps({"run_id": "run_x"}), encoding="utf-8")
    manifest = tmp_path / "run_manifest.json"
    manifest.write_text("not json", encoding="utf-8")
    result = validate_run_completion(str(manifest), str(sentinel))
    assert result["valid"] is False
    assert result["status"] == "manifest_unreadable"


def test_validate_run_completion_returns_missing_sentinel_for_empty_paths() -> None:
    result = validate_run_completion(None, None)
    assert result["valid"] is False
    assert result["status"] == "missing_sentinel"
