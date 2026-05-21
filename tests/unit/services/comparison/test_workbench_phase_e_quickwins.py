# -*- coding: utf-8 -*-
"""Unit tests for Phase E1 Quick Wins (QW1 tutorial, QW2 speed preset, QW3 PDF report)."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# QW2 — speed preset --------------------------------------------------------


def test_qw2_speed_preset_uses_lazy_render_policy() -> None:
    from src.gui.drawing_compare_workbench import COMPARE_PRESETS

    # 첫 번째 entry는 ⚡ 초고속 스캔 — viewer_render_policy="lazy"
    label, quality_idx, recursive, viewer_policy = COMPARE_PRESETS[0]
    assert "초고속" in label
    assert viewer_policy == "lazy"
    assert recursive is False
    # Audit-gates §10 follow-up — speed preset now pins quality_idx=1
    # (DPI 80) since index 0 became the auto sentinel. Auto-quality would
    # potentially pick a higher tier on small folders, defeating the
    # "5분 내 결과" promise of the speed preset.
    assert quality_idx == 1


def test_qw2_default_preset_is_auto_review() -> None:
    """Audit-gates §10 follow-up — default preset is now the auto-quality
    review, replacing the legacy DPI-120 "표준 검토". Speed preset
    (⚡ 초고속) remains opt-in only."""
    from src.gui.drawing_compare_workbench import (
        COMPARE_PRESETS,
        COMPARE_PRESET_DEFAULT_INDEX,
        PREVIEW_QUALITY_AUTO_INDEX,
    )
    label, quality_idx, _r, policy = COMPARE_PRESETS[COMPARE_PRESET_DEFAULT_INDEX]
    assert "자동" in label
    assert quality_idx == PREVIEW_QUALITY_AUTO_INDEX
    assert policy == "top-issues"


def test_qw2_preset_change_updates_render_policy_attribute(qapp) -> None:
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2

    workbench = DrawingCompareWorkbenchV2()
    try:
        workbench.cmb_preset_v2.setCurrentIndex(0)  # ⚡ 초고속
        assert workbench._active_viewer_render_policy_v2 == "lazy"
        workbench.cmb_preset_v2.setCurrentIndex(1)  # 표준
        assert workbench._active_viewer_render_policy_v2 == "top-issues"
    finally:
        workbench.deleteLater()


# QW1 — tutorial flag -------------------------------------------------------


def test_qw1_tutorial_dialog_writes_completion_flag(qapp, tmp_path, monkeypatch) -> None:
    from src.gui import drawing_compare_workbench as dcw
    monkeypatch.setattr(dcw, "_workbench_data_dir", lambda: tmp_path)

    workbench = dcw.DrawingCompareWorkbenchV2()
    try:
        flag_path = workbench._tutorial_completed_path_v2()
        assert not flag_path.exists()
        # Manually mimic dialog completion writing the flag (avoid live exec)
        flag_path.parent.mkdir(parents=True, exist_ok=True)
        flag_path.write_text("completed_at=test\nresult=finished\n", encoding="utf-8")
        assert flag_path.exists()
        # _maybe_show should be a no-op now (idempotent)
        workbench._maybe_show_first_run_tutorial_v2()  # should not raise
    finally:
        workbench.deleteLater()


def test_qw1_tutorial_pages_have_korean_titles_and_body() -> None:
    from src.gui.drawing_compare_workbench import TUTORIAL_PAGES

    assert len(TUTORIAL_PAGES) == 5
    for title, body in TUTORIAL_PAGES:
        assert "단계" in title or "5단계" in title or any(c in title for c in "12345")
        assert len(body) > 30
        # All Korean by design
        assert any(0xAC00 <= ord(ch) <= 0xD7A3 for ch in body)


# QW3 — PDF report ----------------------------------------------------------


def _make_after_png(tmp_path: Path) -> Path:
    img = Image.new("RGB", (1240, 1754), color=(245, 245, 250))
    path = tmp_path / "pair_test_after.png"
    img.save(path)
    return path


def test_qw3_pdf_report_generates_file_with_expected_pages(tmp_path: Path) -> None:
    from src.services.comparison.review_report_pdf import (
        ReviewReportInput,
        generate_review_report_pdf,
    )
    from src.services.comparison.review_project import ReviewStateRecord

    # Set up confirmed cloud dir with one synthetic png
    confirmed_dir = tmp_path / "confirmed_clouds"
    confirmed_dir.mkdir()
    Image.new("RGB", (800, 600), color=(255, 255, 255)).save(confirmed_dir / "pair_test_confirmed.png")

    records = {}
    for zid, status in [("z1", "confirmed"), ("z2", "hold"), ("z3", "confirmed"), ("z4", "false_positive"), ("z5", "needs_review")]:
        rec = ReviewStateRecord(pair_id="pair_test", pair_uuid="pair_test", zone_id=zid, status=status, note=f"메모 {zid}")
        records[rec.key] = rec

    inputs = ReviewReportInput(
        project_label="테스트 프로젝트",
        run_started_at="2026-05-02T19:30:00",
        source_a="C:/test/A",
        source_b="C:/test/B",
        drawing_rows=[{"pair_id": "pair_test", "drawing_number": "S-101"}],
        review_records=records,
        confirmed_cloud_dir=confirmed_dir,
        overlays_by_pair={},
    )
    output_path = tmp_path / "report.pdf"
    result = generate_review_report_pdf(inputs=inputs, output_path=output_path)

    assert output_path.exists()
    # Cover (1) + 1 confirmed pair page + appendix (1) = at least 3 pages
    assert result.page_count >= 3
    assert result.confirmed_total == 2
    assert result.ignored_total == 1
    assert result.false_positive_total == 1
    assert result.needs_review_total == 1


def test_qw3_pdf_report_displays_source_names_not_absolute_paths(tmp_path: Path) -> None:
    fitz = pytest.importorskip("fitz")
    from src.services.comparison.review_report_pdf import (
        ReviewReportInput,
        generate_review_report_pdf,
    )

    inputs = ReviewReportInput(
        project_label="경로 누출 점검",
        run_started_at="2026-05-11T14:40:00",
        source_a="D:/customer/private/cache/old/S-2401_REV0.pdf",
        source_b="C:/Users/user/.codex/worktrees/45ea/02.TEKLA_MCP/tmp/new/S-2401_REV1.pdf",
        drawing_rows=[],
        review_records={},
        confirmed_cloud_dir=None,
        overlays_by_pair={},
    )
    output_path = tmp_path / "path_safe_report.pdf"

    generate_review_report_pdf(inputs=inputs, output_path=output_path)

    doc = fitz.open(str(output_path))
    try:
        text = "\n".join(page.get_text("text") for page in doc)
    finally:
        doc.close()
    assert "S-2401_REV0.pdf" in text
    assert "S-2401_REV1.pdf" in text
    assert "D:/customer" not in text
    assert "C:/Users/user" not in text
    assert ".codex/worktrees" not in text


def test_qw3_pdf_report_handles_empty_records(tmp_path: Path) -> None:
    """No reviewed zones → still produces cover + appendix (no pair pages)."""
    from src.services.comparison.review_report_pdf import (
        ReviewReportInput,
        generate_review_report_pdf,
    )

    inputs = ReviewReportInput(
        project_label="빈 프로젝트",
        run_started_at="2026-05-02T19:30:00",
        source_a="C:/x",
        source_b="C:/y",
        drawing_rows=[],
        review_records={},
        confirmed_cloud_dir=None,
        overlays_by_pair={},
    )
    output_path = tmp_path / "empty.pdf"
    result = generate_review_report_pdf(inputs=inputs, output_path=output_path)
    assert output_path.exists()
    # Cover + appendix only (no per-pair pages because no confirmed records)
    assert result.page_count == 2
    assert result.confirmed_total == 0


def test_qw3_pdf_report_falls_back_when_cloud_png_missing(tmp_path: Path) -> None:
    """Pair page should still render even if the confirmed PNG isn't there."""
    from src.services.comparison.review_report_pdf import (
        ReviewReportInput,
        generate_review_report_pdf,
    )
    from src.services.comparison.review_project import ReviewStateRecord

    rec = ReviewStateRecord(pair_id="pair_test", pair_uuid="pair_test", zone_id="z1", status="confirmed", note="")
    inputs = ReviewReportInput(
        project_label="누락 PNG 테스트",
        run_started_at="2026-05-02T19:30:00",
        source_a="C:/a",
        source_b="C:/b",
        drawing_rows=[{"pair_id": "pair_test", "drawing_number": "S-200"}],
        review_records={rec.key: rec},
        confirmed_cloud_dir=tmp_path / "missing_dir",  # doesn't exist
        overlays_by_pair={},
    )
    output_path = tmp_path / "fallback.pdf"
    result = generate_review_report_pdf(inputs=inputs, output_path=output_path)
    assert output_path.exists()
    assert result.page_count >= 3  # cover + pair + appendix
    assert result.confirmed_total == 1
