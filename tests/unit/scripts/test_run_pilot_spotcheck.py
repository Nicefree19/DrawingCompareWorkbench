"""Deterministic tests for the pilot spotcheck runner.

Two layers:
  * pure transform tests on a fixed detection fixture (fast, no pipeline) — they
    lock the spotcheck-sheet / ground-truth shape and the no-fabrication contract;
  * one real-pipeline golden integration (skipif-guarded, mirroring the e2e
    smoke) proving the runner wires to ``FolderComparePipeline`` and lists the
    known change.
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest

import scripts.run_pilot_spotcheck as run_mod
from scripts.run_pilot_spotcheck import (
    DWG_BACKEND_ODA_CONVERTER,
    GROUND_TRUTH_HEADER,
    PilotSpotcheckError,
    build_ground_truth_rows,
    build_spotcheck_md,
    emit_spotcheck_artifacts,
    run_pilot_spotcheck,
)

# A fixed slice of a real golden-02 ``top_issues`` row (captured 2026-06-27),
# so the transform tests are deterministic without running the heavy pipeline.
_FIXTURE_ISSUE = {
    "display_label": "after",
    "category": "member",
    "change_type": "mixed",
    "change_type_ko": "혼합",
    "severity_ko": "높음",
    "change_summary_ko": "구조 부재 표기 변경: 구조 검토가 필요한 변경 후보입니다.",
    "major_layers": "BEAM",
    "bbox_text": "-30.0, 345.0 - 1030.0, 460.0",
    "added_count": 1,
    "deleted_count": 1,
    "modified_count": 0,
    "source_format": "cad",
    "detection_source": "cad_entity",
    "bbox_status": "exact",
}

_GOLDEN = (
    Path(__file__).resolve().parents[3] / "tests/data/comparison/golden/dxf/02_single_modification"
)


def test_spotcheck_md_lists_detected_change_with_operator_columns() -> None:
    md = build_spotcheck_md("before → after", [_FIXTURE_ISSUE])
    assert "총 검출 변경(top_issues): **1**" in md
    # detected row carries location + type + Korean summary + add/delete/modify
    assert "BEAM" in md
    assert "혼합·높음" in md
    assert "구조 부재 표기 변경" in md
    assert "+1/-1/~0" in md
    # blank operator columns are present for the dry-run
    for column in ("아는변경?", "검출됨?(Y/N)", "위치정확?(Y/N)", "비고"):
        assert column in md
    # judgement criterion + miss-capture section both exist
    assert "누락" in md


def test_spotcheck_md_handles_zero_detections() -> None:
    md = build_spotcheck_md("a → b", [])
    assert "총 검출 변경(top_issues): **0**" in md
    assert "검출 0" in md


def test_ground_truth_rows_use_existing_schema_facts_only() -> None:
    rows = build_ground_truth_rows([_FIXTURE_ISSUE])
    assert len(rows) == 1
    row = dict(zip(GROUND_TRUTH_HEADER, rows[0]))
    assert row["drawing_label"] == "after"
    assert row["category"] == "member"
    # detection-derived match tokens (facts only), not fabricated semantics
    assert row["summary_contains"] == "BEAM;mixed"
    assert row["source_format"] == "cad"
    assert row["detection_source"] == "cad_entity"
    assert row["bbox_status"] == "exact"
    # honesty: provenance flagged so the row is not mistaken for approved truth
    assert "스켈레톤" in row["notes"]


def test_ground_truth_header_matches_canonical_schema() -> None:
    assert GROUND_TRUTH_HEADER[:6] == [
        "drawing_label",
        "category",
        "summary_contains",
        "source_format",
        "detection_source",
        "bbox_status",
    ]


@pytest.mark.skipif(
    not (_GOLDEN / "before.dxf").exists() or not (_GOLDEN / "after.dxf").exists(),
    reason="golden pair 02_single_modification not present",
)
def test_real_pipeline_golden_emits_spotcheck_and_truth(tmp_path: Path) -> None:
    summary = run_pilot_spotcheck(_GOLDEN / "before.dxf", _GOLDEN / "after.dxf", tmp_path / "run")
    out = Path(summary["output_dir"])

    assert summary["detected_count"] >= 1
    md = (out / "pilot_spotcheck.md").read_text(encoding="utf-8")
    # the known single modification sits on the BEAM layer
    assert "BEAM" in md

    csv_path = out / "review_ground_truth.csv"
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        data_rows = list(reader)
    assert header == GROUND_TRUTH_HEADER
    assert data_rows  # at least one detection-derived skeleton row
    assert any("BEAM" in cell for row in data_rows for cell in row)


# --- Folder batch (PB1) ---------------------------------------------------

_FIXTURE_ISSUE_ALPHA = {**_FIXTURE_ISSUE, "display_label": "alpha"}
_FIXTURE_ISSUE_BETA = {
    **_FIXTURE_ISSUE,
    "display_label": "beta",
    "major_layers": "기둥-1F",
    "severity_ko": "보통",
}


def test_spotcheck_md_groups_folder_batch_by_pair() -> None:
    md = build_spotcheck_md("배치", [_FIXTURE_ISSUE_ALPHA, _FIXTURE_ISSUE_BETA])
    assert "총 검출 변경(top_issues): **2**" in md
    assert "비교 쌍: **2**" in md
    # one detected-changes section per pair, labelled by display_label
    assert "### 쌍: alpha (검출 1)" in md
    assert "### 쌍: beta (검출 1)" in md
    # each pair's layer surfaces under its own section
    assert "BEAM" in md and "기둥-1F" in md


def test_spotcheck_md_single_pair_keeps_pr56_shape() -> None:
    # A single pair must NOT grow per-pair headers (PR#56 output preserved).
    md = build_spotcheck_md("before → after", [_FIXTURE_ISSUE])
    assert "### 쌍:" not in md
    assert "비교 쌍:" not in md


def test_ground_truth_rows_distinguish_batch_pairs() -> None:
    rows = build_ground_truth_rows([_FIXTURE_ISSUE_ALPHA, _FIXTURE_ISSUE_BETA])
    assert [row[0] for row in rows] == ["alpha", "beta"]


# --- emit_spotcheck_artifacts (GS1): shared by CLI runner + GUI ------------


def test_emit_spotcheck_artifacts_from_dashboard(tmp_path: Path) -> None:
    """The extracted emitter writes both files from a pre-written dashboard
    (no pipeline run) — this is the function the GUI compare-completion path
    reuses so a reviewer gets the sheet without a dev checkout."""
    out = tmp_path / "run"
    (out / "artifacts").mkdir(parents=True)
    (out / "artifacts" / "review_dashboard.json").write_text(
        json.dumps({"top_issues": [_FIXTURE_ISSUE]}, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = emit_spotcheck_artifacts(out, "테스트 비교")

    assert summary["detected_count"] == 1
    md = (out / "pilot_spotcheck.md").read_text(encoding="utf-8")
    assert "테스트 비교" in md
    assert "BEAM" in md
    with (out / "review_ground_truth.csv").open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == GROUND_TRUTH_HEADER
    assert any("BEAM" in cell for row in rows[1:] for cell in row)


def test_emit_spotcheck_artifacts_handles_missing_dashboard(tmp_path: Path) -> None:
    # No artifacts/review_dashboard.json → 0 detections, still writes the sheet
    # (so the GUI emit never crashes on a degenerate run).
    out = tmp_path / "run"
    out.mkdir()
    summary = emit_spotcheck_artifacts(out)
    assert summary["detected_count"] == 0
    assert (out / "pilot_spotcheck.md").exists()
    assert (out / "review_ground_truth.csv").exists()


# --- DWG on-ramp (PB2) ----------------------------------------------------


def test_inputs_include_dwg_detects_file_and_folder(tmp_path: Path) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()
    (before / "p1.dxf").write_text("x", encoding="utf-8")
    (after / "p1.dxf").write_text("x", encoding="utf-8")
    assert run_mod._inputs_include_dwg(before, after) is False
    # a DWG anywhere in the inputs flips detection on
    (after / "p2.dwg").write_text("x", encoding="utf-8")
    assert run_mod._inputs_include_dwg(before, after) is True
    # single-file DWG input is also detected
    dwg = tmp_path / "x.dwg"
    dwg.write_text("x", encoding="utf-8")
    assert run_mod._inputs_include_dwg(dwg, before / "p1.dxf") is True


def test_resolve_dwg_backend_mode_dxf_returns_none(tmp_path: Path) -> None:
    a = tmp_path / "a.dxf"
    b = tmp_path / "b.dxf"
    a.write_text("x", encoding="utf-8")
    b.write_text("x", encoding="utf-8")
    assert run_mod._resolve_dwg_backend_mode(a, b) is None


def test_resolve_dwg_backend_mode_wires_converter_when_installed(
    tmp_path: Path, monkeypatch
) -> None:
    dwg = tmp_path / "a.dwg"
    other = tmp_path / "b.dxf"
    dwg.write_text("x", encoding="utf-8")
    other.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        run_mod,
        "converter_installation_status",
        lambda: {"installed": True, "message": "ok"},
    )
    assert run_mod._resolve_dwg_backend_mode(dwg, other) == DWG_BACKEND_ODA_CONVERTER


def test_resolve_dwg_backend_mode_fails_loud_without_converter(tmp_path: Path, monkeypatch) -> None:
    dwg = tmp_path / "a.dwg"
    other = tmp_path / "b.dxf"
    dwg.write_text("x", encoding="utf-8")
    other.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        run_mod,
        "converter_installation_status",
        lambda: {"installed": False, "message": "not found"},
    )
    with pytest.raises(PilotSpotcheckError) as excinfo:
        run_mod._resolve_dwg_backend_mode(dwg, other)
    # fail-loud message tells the operator to pre-convert — not a silent empty run
    assert "DXF로 변환" in str(excinfo.value)


# --- Real-ODA end-to-end (DF3): proves the DWG on-ramp, not just the wiring ---
#
# These run the REAL ODA conversion (AC1032 DWG -> DXF -> compare), which the
# tests above only mock. Committed AC1032 fixtures live next to the golden DXF
# pair. Skipped when no converter is installed (e.g. CI) — honestly gated, not
# silently inert; a dev machine with ODA runs them and proves the path.

_DWG = _GOLDEN / "dwg"


def _oda_installed() -> bool:
    try:
        return bool(run_mod.converter_installation_status().get("installed"))
    except Exception:  # pragma: no cover - defensive
        return False


_dwg_e2e_skip = pytest.mark.skipif(
    not (_DWG / "before.dwg").exists() or not _oda_installed(),
    reason="real DWG on-ramp not exercised: ODA converter not installed or AC1032 fixture missing",
)


@_dwg_e2e_skip
def test_real_dwg_single_pair_converts_and_detects(tmp_path: Path) -> None:
    summary = run_pilot_spotcheck(_DWG / "before.dwg", _DWG / "after.dwg", tmp_path / "run")
    assert summary["detected_count"] >= 1
    md = (Path(summary["output_dir"]) / "pilot_spotcheck.md").read_text(encoding="utf-8")
    assert "BEAM" in md  # the known single modification is on the BEAM layer


@_dwg_e2e_skip
def test_real_dwg_folder_batch_converts_and_detects(tmp_path: Path) -> None:
    # Folder input with an AC1032 DWG used to fail preflight (unsupported version)
    # because the pipeline's folder path never converted per-file. Now pre-converted.
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()
    shutil.copy(_DWG / "before.dwg", before / "p1.dwg")
    shutil.copy(_DWG / "after.dwg", after / "p1.dwg")
    summary = run_pilot_spotcheck(before, after, tmp_path / "run")
    assert summary["detected_count"] >= 1
    md = (Path(summary["output_dir"]) / "pilot_spotcheck.md").read_text(encoding="utf-8")
    assert "BEAM" in md
