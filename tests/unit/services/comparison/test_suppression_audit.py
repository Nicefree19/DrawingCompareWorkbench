"""Phase Q2 (RV-20260509-002) — suppression_audit SSoT 회귀 가드.

모든 silent-drop 카운터가 audit report 에 노출되는지 검증.
"""
from __future__ import annotations

import json

import pytest

from src.services.comparison.suppression_audit import (
    SuppressionAuditReport,
    SuppressionEntry,
    audit_from_comparison_result,
    build_suppression_audit,
)


class TestExtractionStageEntries:
    """추출 단계 (Phase Q1) 카운터 → audit entry 매핑."""

    def test_unsupported_counts_a_side(self):
        report = build_suppression_audit(
            extraction_stats_a={
                "unsupported_counts": {"3DFACE": 234, "REGION": 5},
                "unsupported_total": 239,
            },
            visible_change_count=10,
        )
        ext_entries = [e for e in report.entries if e.category == "extraction"]
        assert len(ext_entries) == 1
        assert ext_entries[0].count == 239
        assert "3DFACE" in ext_entries[0].detail_ko
        assert "A 도면" in ext_entries[0].label_ko

    def test_both_sides_have_unsupported(self):
        report = build_suppression_audit(
            extraction_stats_a={"unsupported_counts": {"3DFACE": 100}},
            extraction_stats_b={"unsupported_counts": {"3DFACE": 80}},
        )
        ext_entries = [e for e in report.entries if e.category == "extraction"]
        assert len(ext_entries) == 2
        assert sum(e.count for e in ext_entries) == 180

    def test_no_unsupported_no_entry(self):
        report = build_suppression_audit(extraction_stats_a={"unsupported_counts": {}})
        assert all(e.category != "extraction" for e in report.entries)

    def test_extraction_limit_exceeded(self):
        report = build_suppression_audit(
            extraction_stats_a={"limit_exceeded": True, "max_entities": 50000},
        )
        limit_entries = [
            e for e in report.entries
            if "한계" in e.label_ko
        ]
        assert len(limit_entries) == 1


class TestComparisonStageEntries:
    """비교 단계 (Phase O P1/O2/O3) 카운터 매핑."""

    def test_modified_ignored_surfaced(self):
        report = build_suppression_audit(
            comparison_stats={"modified_ignored": 7},
        )
        e = [x for x in report.entries if "유의미" in x.label_ko][0]
        assert e.count == 7
        assert "1.0mm" in e.detail_ko

    def test_alignment_suppressed_from_stats(self):
        report = build_suppression_audit(
            comparison_stats={"alignment_suppressed": 4},
        )
        e = [x for x in report.entries if "alignment" in x.label_ko.lower()][0]
        assert e.count == 4

    def test_alignment_suppressed_from_metadata_fallback(self):
        report = build_suppression_audit(
            comparison_metadata={"alignment_suppressed_count": 3},
        )
        e = [x for x in report.entries if "alignment" in x.label_ko.lower()][0]
        assert e.count == 3

    def test_cosmetic_suppressed(self):
        report = build_suppression_audit(
            comparison_stats={"cosmetic_suppressed": 2},
        )
        e = [x for x in report.entries if "Cosmetic" in x.label_ko][0]
        assert e.count == 2


class TestZoneStageEntries:
    """Zone build 단계 (Phase O4 / P4) 카운터 매핑."""

    def test_zone_noise_suppressed(self):
        report = build_suppression_audit(
            comparison_metadata={"change_zone_noise_suppressed_count": 5},
        )
        e = [x for x in report.entries if "noise" in x.label_ko.lower() or "단일" in x.label_ko][0]
        assert e.count == 5

    def test_zone_skipped_record(self):
        report = build_suppression_audit(
            comparison_metadata={"change_zone_skipped_record_count": 1},
        )
        e = [x for x in report.entries if "좌표" in x.label_ko][0]
        assert e.count == 1


class TestResultStageEntries:
    def test_truncated_changes_metadata(self):
        report = build_suppression_audit(
            comparison_metadata={"truncated_changes": True},
        )
        e = [x for x in report.entries if "truncation" in x.label_ko.lower() or "후순위" in x.label_ko][0]
        assert e.count > 0

    def test_truncated_changes_stats(self):
        report = build_suppression_audit(
            comparison_stats={"truncated_changes": True},
        )
        assert any(e.category == "result" for e in report.entries)


class TestAggregateAndFormat:
    def test_total_suppressed_sum(self):
        report = build_suppression_audit(
            extraction_stats_a={"unsupported_counts": {"3DFACE": 5}},
            comparison_stats={"modified_ignored": 7, "alignment_suppressed": 4},
            comparison_metadata={"change_zone_noise_suppressed_count": 5},
            visible_change_count=10,
        )
        assert report.total_visible_changes == 10
        assert report.total_suppressed == 5 + 7 + 4 + 5
        assert report.has_suppression() is True

    def test_no_suppression_no_entries(self):
        report = build_suppression_audit(visible_change_count=50)
        assert report.total_suppressed == 0
        assert report.has_suppression() is False
        text = report.format_text()
        assert "가려진 변경 없음" in text

    def test_format_text_includes_categories(self):
        report = build_suppression_audit(
            extraction_stats_a={"unsupported_counts": {"3DFACE": 1}},
            comparison_stats={"modified_ignored": 1},
            comparison_metadata={"change_zone_noise_suppressed_count": 1, "truncated_changes": True},
            visible_change_count=10,
        )
        text = report.format_text()
        assert "[1] 추출 단계" in text
        assert "[2] 비교 단계" in text
        assert "[3] Zone build" in text
        assert "[4] 결과 단계" in text
        assert "표시 중인 변경: 10건" in text

    def test_to_dict_round_trip(self):
        report = build_suppression_audit(
            comparison_stats={"modified_ignored": 3},
            visible_change_count=5,
        )
        d = report.to_dict()
        assert d["total_visible_changes"] == 5
        assert d["total_suppressed"] == 3
        assert isinstance(d["entries"], list)
        # JSON serializable
        s = json.dumps(d, ensure_ascii=False)
        assert "유의미 임계 미달" in s


class TestAuditFromComparisonResult:
    def test_dict_input(self):
        result_dict = {
            "changes": [{"x": 1}, {"x": 2}],
            "stats": {"modified_ignored": 1},
            "metadata": {"alignment_suppressed_count": 2},
        }
        report = audit_from_comparison_result(
            result_dict, pair_id="test-pair"
        )
        assert report.pair_id == "test-pair"
        assert report.total_visible_changes == 2
        assert report.total_suppressed == 3

    def test_object_input(self):
        class _R:
            changes = [None] * 7
            stats = {"cosmetic_suppressed": 1}
            metadata = {}

        report = audit_from_comparison_result(_R(), pair_id="o")
        assert report.total_visible_changes == 7
        assert any(e.label_ko.startswith("Cosmetic") for e in report.entries)


class TestCodexFollowUpFixes:
    """Phase Q2 Codex follow-up (RV-20260509-002) — 4 finding regression guards."""

    def test_p2_comparison_result_metadata_fallback(self):
        """ComparisonResult (no .stats) — metadata['comparison_suppression']
        must be promoted into the audit's stats source."""

        class _Result:
            # Mimic ComparisonResult — has metadata, no .stats
            metadata = {
                "comparison_suppression": {
                    "modified_ignored": 5,
                    "alignment_suppressed": 3,
                    "cosmetic_suppressed": 2,
                },
            }
            changes: list = []

        report = audit_from_comparison_result(_Result(), pair_id="p2-1")
        labels = [e.label_ko for e in report.entries]
        # All three comparison-stage entries must surface
        assert any("유의미 임계 미달" in l for l in labels)
        assert any("alignment" in l.lower() for l in labels)
        assert any("Cosmetic" in l for l in labels)
        assert report.total_suppressed >= 10

    def test_p2_extraction_stats_default_from_metadata(self):
        """If caller does not pass extraction_stats_a/b explicitly,
        they must be auto-pulled from metadata['extraction_stats']."""
        result_dict = {
            "changes": [{"x": 1}],
            "stats": {},
            "metadata": {
                "extraction_stats": {
                    "a": {
                        "unsupported_counts": {"3DFACE": 12},
                        "unsupported_total": 12,
                    },
                    "b": {
                        "unsupported_counts": {"REGION": 5},
                        "unsupported_total": 5,
                    },
                },
            },
        }
        report = audit_from_comparison_result(result_dict, pair_id="p2-2")
        ext_entries = [e for e in report.entries if e.category == "extraction"]
        # Must surface BOTH sides without explicit kwargs
        assert len(ext_entries) == 2
        assert sum(e.count for e in ext_entries) == 17

    def test_p2_truncation_uses_omitted_change_counts(self):
        """50,001 actual changes → 1 hidden, NOT 50,000."""
        report = build_suppression_audit(
            comparison_metadata={
                "truncated_changes": True,
                "omitted_change_counts": {
                    "added": 1, "deleted": 0, "modified": 0,
                },
                "max_change_records_in_memory": 50000,
            },
        )
        e = next(x for x in report.entries if x.category == "result")
        assert e.count == 1, (
            "truncation entry should reflect actual omitted "
            f"(=1), not cap (=50000); got {e.count}"
        )

    def test_p2_truncation_fallback_when_omitted_missing(self):
        """If omitted_change_counts is missing, still emit a non-zero entry
        so the user sees a warning (not a silent 0)."""
        report = build_suppression_audit(
            comparison_metadata={
                "truncated_changes": True,
                "max_change_records_in_memory": 50000,
            },
        )
        e = next(x for x in report.entries if x.category == "result")
        assert e.count == 50000  # cap fallback

    def test_p2_truncation_fallback_no_cap_no_omitted(self):
        """No cap + no omitted → must still emit (count=1 sentinel)."""
        report = build_suppression_audit(
            comparison_metadata={"truncated_changes": True},
        )
        e = next(x for x in report.entries if x.category == "result")
        assert e.count >= 1


class TestCodexRound2Fixes:
    """Phase Q2 Codex round-2 follow-up — aggregate path correctness."""

    def test_aggregate_truncation_uses_summed_omitted(self):
        """Aggregate audit must carry omitted_change_counts so the truncation
        entry shows real omissions, not the sentinel=1 fallback.

        Simulates the Workbench aggregate path that sums
        omitted_change_counts across multiple pairs.
        """
        # Pair 1 omitted 3, Pair 2 omitted 7 → aggregate omitted = 10
        agg_meta = {
            "truncated_changes": True,
            "omitted_change_counts": {
                "added": 5, "deleted": 3, "modified": 2,
            },
            "max_change_records_in_memory": 50000,
        }
        report = build_suppression_audit(comparison_metadata=agg_meta)
        e = next(x for x in report.entries if x.category == "result")
        assert e.count == 10, (
            f"aggregate truncation should reflect 5+3+2=10 omissions, "
            f"got {e.count}"
        )

    def test_dwg_differ_surfaces_comparison_suppression(self):
        """Smoke test: dwg_differ.compare() result.metadata must carry
        comparison_suppression. Avoids real DWG/DXF files by inspecting
        the source directly."""
        from pathlib import Path
        src = Path("src/services/comparison/dwg_differ.py").read_text(
            encoding="utf-8"
        )
        assert '"comparison_suppression"' in src, (
            "dwg_differ.py must surface comparison_suppression into "
            "ComparisonResult.metadata so audit dialogs see counters"
        )
        assert "modified_ignored" in src
        assert "alignment_suppressed" in src
        assert "cosmetic_suppressed" in src


class TestCliEntryPoint:
    def test_cli_help_returns_zero(self, capsys):
        from src.services.comparison.suppression_audit import _cli
        rc = _cli(["--help"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "Usage:" in captured.out

    def test_cli_missing_file_returns_2(self, capsys):
        from src.services.comparison.suppression_audit import _cli
        rc = _cli(["nonexistent_file_xyz.json"])
        assert rc == 2

    def test_cli_with_valid_json(self, tmp_path, capsys):
        from src.services.comparison.suppression_audit import _cli
        p = tmp_path / "result.json"
        p.write_text(
            json.dumps({
                "pair_id": "cli-test",
                "changes": [{"x": 1}],
                "stats": {"modified_ignored": 3},
                "metadata": {},
            }),
            encoding="utf-8",
        )
        rc = _cli([str(p)])
        assert rc == 0
        captured = capsys.readouterr()
        assert "cli-test" in captured.out
        assert "유의미" in captured.out
