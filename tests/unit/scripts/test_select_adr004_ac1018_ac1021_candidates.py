from __future__ import annotations

from pathlib import Path

from scripts.select_adr004_ac1018_ac1021_candidates import (
    build_report,
    classify_version,
    rank_cross_folder_candidates,
    render_markdown,
)
from scripts.select_adr004_compact_compare_candidates import collect_dwg_samples


def _dwg(path: Path, code: str, payload_size: int = 16) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(code.encode("ascii") + b"\x00" * payload_size)
    return path


def test_cross_folder_candidate_is_ranked_when_names_match_and_dates_differ(tmp_path: Path) -> None:
    root = tmp_path / "root"
    before = _dwg(root / "set-a" / "150402_structure_plan.dwg", "AC1018")
    after = _dwg(root / "set-b" / "150403_structure_plan_rev1.dwg", "AC1018")

    samples = collect_dwg_samples([root], target_versions=("AC1018",))
    candidates = rank_cross_folder_candidates(samples, target_versions=("AC1018",))

    assert len(candidates["AC1018"]) == 1
    candidate = candidates["AC1018"][0]
    assert candidate["classification"] == "confirmed_revision_pair"
    assert candidate["before"]["path"] == str(before.resolve())
    assert candidate["after"]["path"] == str(after.resolve())


def test_classifies_multiple_samples_without_evidence_as_missing_candidate() -> None:
    classification = classify_version(sample_count=2, candidates=[])

    assert classification.status == "missing_compare_candidate"
    assert "multiple samples" in classification.reason


def test_path_proximity_alone_does_not_create_revision_candidate(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _dwg(root / "same-folder" / "A31-floor-plan.dwg", "AC1018")
    _dwg(root / "same-folder" / "A33-section.dwg", "AC1018")

    samples = collect_dwg_samples([root], target_versions=("AC1018",))
    candidates = rank_cross_folder_candidates(samples, target_versions=("AC1018",), min_similarity=0.52)

    assert candidates["AC1018"] == []


def test_build_report_and_markdown_show_import_only_gap(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _dwg(root / "only-one.dwg", "AC1021")

    report = build_report([root], target_versions=("AC1021",))

    assert report["summary"]["version_counts"] == {"AC1021": 1}
    assert report["classifications"]["AC1021"]["status"] == "single_file_import_only"
    markdown = render_markdown(report)
    assert "ADR-004 AC1018/AC1021 Candidate Selection" in markdown
    assert "single_file_import_only" in markdown
