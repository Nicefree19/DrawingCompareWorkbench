from __future__ import annotations

import json
from pathlib import Path

from scripts import run_dwg_release_performance_probe as probe


def test_build_probe_writes_medium_large_progress_and_cancel_metrics(tmp_path: Path) -> None:
    report = probe.build_probe(
        out=tmp_path / "probe.json",
        work_dir=tmp_path / "work",
        medium_line_count=3,
        large_line_count=4,
        large_pair_count=3,
        compare_runner=_fake_compare_runner,
        direct_probe_runner=_fake_direct_probe_runner,
    )

    assert (tmp_path / "probe.json").exists()
    assert report["schema_version"] == probe.SCHEMA_VERSION
    assert report["medium_drawing_seconds"] == 1.25
    assert report["large_drawing_seconds"] == 1.25
    assert report["large_cad_dxf_pairs"] == 3
    assert report["progress_max_gap_s"] == 0.5
    assert report["cancel_probe"]["cancel_to_idle_s"] == 0.2
    assert len(report["large_pairs"]) == 3


def _fake_compare_runner(command: list[str] | tuple[str, ...], timeout_seconds: float) -> probe.CompareExecution:
    output = Path(command[command.index("--output") + 1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"status": "ok", "result": {"summary": {"total_changes": 1, "modified": 1}}}),
        encoding="utf-8",
    )
    return probe.CompareExecution(exit_code=0, elapsed_s=1.25)


def _fake_direct_probe_runner(
    before: Path,
    after: Path,
    max_entities: int,
    max_dxf_tokens: int,
    timeout_seconds: float,
) -> dict:
    assert before.exists()
    assert after.exists()
    return {
        "status": "passed",
        "progress_event_count": 4,
        "progress_max_gap_s": 0.5,
        "cancel_probe": {"status": "passed", "cancel_to_idle_s": 0.2},
    }
