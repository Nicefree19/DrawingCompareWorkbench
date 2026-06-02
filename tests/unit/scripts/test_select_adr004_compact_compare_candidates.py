from __future__ import annotations

from pathlib import Path

from scripts.select_adr004_compact_compare_candidates import (
    build_report,
    collect_dwg_samples,
    detect_dwg_code,
    normalize_candidate_name,
    rank_candidates,
    render_markdown,
)


def _dwg(path: Path, code: str, payload_size: int = 16) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(code.encode("ascii") + b"\x00" * payload_size)
    return path


def test_detects_dwg_code_from_header(tmp_path: Path) -> None:
    path = _dwg(tmp_path / "sample.dwg", "AC1027")

    assert detect_dwg_code(path) == "AC1027"


def test_normalize_candidate_name_removes_dates_and_revision_tokens() -> None:
    assert normalize_candidate_name("230530_P5_detail.dwg") == "p5detail"
    assert normalize_candidate_name("230531_P5_detail_Rev1.dwg") == "p5detail"
    assert normalize_candidate_name("240110_P5_detail_R1.dwg") == "p5detail"


def test_rank_candidates_prefers_small_revision_pair(tmp_path: Path) -> None:
    root = tmp_path / "root"
    before = _dwg(root / "230530_P5_detail.dwg", "AC1024", payload_size=10)
    after = _dwg(root / "230531_P5_detail_Rev1.dwg", "AC1024", payload_size=12)
    _dwg(root / "231000_unrelated.dwg", "AC1024", payload_size=10)

    samples = collect_dwg_samples([root], target_versions=("AC1024",))
    candidates = rank_candidates(samples, target_versions=("AC1024",))

    assert len(candidates["AC1024"]) == 1
    candidate = candidates["AC1024"][0]
    assert candidate["before"]["path"] == str(before.resolve())
    assert candidate["after"]["path"] == str(after.resolve())
    assert candidate["similarity"] == 1.0


def test_build_report_and_markdown(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _dwg(root / "230530_P5_detail.dwg", "AC1024")
    _dwg(root / "230531_P5_detail_Rev1.dwg", "AC1024")

    report = build_report([root], target_versions=("AC1024",))

    assert report["summary"]["sample_count"] == 2
    assert report["summary"]["candidate_counts"] == {"AC1024": 1}
    markdown = render_markdown(report)
    assert "ADR-004 Compact Compare Candidate Selection" in markdown
    assert "AC1024" in markdown
