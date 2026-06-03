from __future__ import annotations

import json
from pathlib import Path

from scripts import run_dwg_release_supplemental_probe as probe


def test_build_probe_writes_pdf_negative_focused_and_overlay_evidence(tmp_path: Path) -> None:
    report = probe.build_probe(
        out=tmp_path / "supplemental.json",
        work_dir=tmp_path / "work",
        pdf_pair_count=2,
        negative_sample_count=2,
        block_text_dimension_pair_count=2,
        pdf_compare_runner=_fake_pdf_compare_runner,
        cad_compare_runner=_fake_cad_compare_runner,
    )

    assert (tmp_path / "supplemental.json").exists()
    assert report["schema_version"] == probe.SCHEMA_VERSION
    assert report["evidence_counts"] == {
        "pdf_pairs": 2,
        "negative_failure_samples": 2,
        "block_text_dimension_pairs": 2,
    }
    assert report["metrics"]["overlay_error_px_150dpi"] <= 10.0
    assert len(report["pdf_pairs"]) == 2
    assert len(report["negative_samples"]) == 2
    assert len(report["block_text_dimension_pairs"]) == 2


def _fake_pdf_compare_runner(before: Path, after: Path, result_json: Path, timeout_seconds: float) -> dict:
    assert before.exists()
    assert after.exists()
    payload = {
        "mode": "pdf_file",
        "status": "ok",
        "result": {
            "summary": {"added": 0, "deleted": 0, "modified": 1, "unchanged": 0, "total_changes": 1},
            "metadata": {"comparison_type": "PDF", "pages_compared": 1},
        },
    }
    result_json.write_text(json.dumps(payload), encoding="utf-8")
    return {
        "status": "passed",
        "elapsed_s": 0.1,
        "summary": payload["result"]["summary"],
        "metadata": payload["result"]["metadata"],
    }


def _fake_cad_compare_runner(command: list[str] | tuple[str, ...], timeout_seconds: float) -> probe.CompareExecution:
    output = Path(command[command.index("--output") + 1])
    output.parent.mkdir(parents=True, exist_ok=True)
    if "negative" in str(output):
        output.write_text(
            json.dumps(
                {
                    "status": "failed",
                    "result": {
                        "summary": {"total_changes": 0},
                        "metadata": {"error_code": "COMPARE_IMPORT_FAILED"},
                    },
                }
            ),
            encoding="utf-8",
        )
        return probe.CompareExecution(exit_code=2, elapsed_s=0.1)

    output.write_text(
        json.dumps(
            {
                "status": "ok",
                "result": {
                    "summary": {"added": 0, "deleted": 0, "modified": 1, "total_changes": 1},
                    "metadata": {"pipeline_status": "ok"},
                },
            }
        ),
        encoding="utf-8",
    )
    return probe.CompareExecution(exit_code=0, elapsed_s=0.1)
