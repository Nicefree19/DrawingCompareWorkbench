# -*- coding: utf-8 -*-
"""Unit tests for the PDF report settings persistence + branding integration."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from src.services.comparison.report_settings import (
    DEFAULT_ACCENT_COLOR_HEX,
    DEFAULT_COMPANY_NAME,
    REPORT_SETTINGS_FILENAME,
    ReportSettings,
    load_report_settings,
    save_report_settings,
)
from src.services.comparison.review_project import ReviewStateRecord
from src.services.comparison.review_report_pdf import (
    ReviewReportInput,
    generate_review_report_pdf,
)


def test_default_settings_have_expected_brand_color_and_company() -> None:
    settings = ReportSettings()
    assert settings.accent_color_hex == DEFAULT_ACCENT_COLOR_HEX
    assert settings.company_name == DEFAULT_COMPANY_NAME
    rgb = settings.accent_color_rgb
    assert all(0.0 <= channel <= 1.0 for channel in rgb)


def test_accent_color_rgb_falls_back_for_invalid_hex() -> None:
    settings = ReportSettings(accent_color_hex="not-a-color")
    rgb = settings.accent_color_rgb
    # Falls back to fallback red roughly
    assert rgb[0] > 0.5 and rgb[1] < 0.3 and rgb[2] < 0.3


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    settings = ReportSettings(
        company_name="테스트사",
        reviewer_name="홍길동",
        reviewer_title="과장",
        reviewer_department="구조설계팀",
        reviewer_contact="hong@test.co.kr",
        accent_color_hex="#0AA864",
        footer_note="계약 #ABC-2026-001",
    )
    path = tmp_path / REPORT_SETTINGS_FILENAME
    save_report_settings(path, settings)
    assert path.exists()

    loaded = load_report_settings(path)
    assert loaded.company_name == "테스트사"
    assert loaded.reviewer_name == "홍길동"
    assert loaded.reviewer_title == "과장"
    assert loaded.accent_color_hex == "#0AA864"
    assert loaded.footer_note == "계약 #ABC-2026-001"


def test_load_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    settings = load_report_settings(tmp_path / "nope.json")
    assert isinstance(settings, ReportSettings)
    assert settings.company_name == DEFAULT_COMPANY_NAME


def test_load_handles_corrupt_json(tmp_path: Path) -> None:
    bad_path = tmp_path / REPORT_SETTINGS_FILENAME
    bad_path.write_text("{not valid json", encoding="utf-8")
    settings = load_report_settings(bad_path)
    assert settings.company_name == DEFAULT_COMPANY_NAME


def test_reviewer_one_line_combines_name_title_department() -> None:
    settings = ReportSettings(
        reviewer_name="김철수",
        reviewer_title="부장",
        reviewer_department="구조설계1팀",
    )
    assert settings.reviewer_one_line() == "김철수 · 부장 · 구조설계1팀"


def test_reviewer_one_line_falls_back_to_placeholder_when_empty() -> None:
    settings = ReportSettings()
    assert "미입력" in settings.reviewer_one_line()


def test_pdf_report_includes_signoff_page_when_reviewer_configured(tmp_path: Path) -> None:
    """When ReportSettings has a reviewer name, the PDF gains a sign-off page."""

    confirmed_dir = tmp_path / "confirmed_clouds"
    confirmed_dir.mkdir()
    Image.new("RGB", (800, 600), color=(255, 255, 255)).save(
        confirmed_dir / "pair_test_confirmed.png"
    )

    rec = ReviewStateRecord(pair_id="pair_test", pair_uuid="pair_test", zone_id="z1", status="confirmed", note="검토 완료")

    settings = ReportSettings(
        company_name="테스트 시공사",
        reviewer_name="홍길동",
        reviewer_title="책임 검토자",
        reviewer_department="설계관리팀",
        reviewer_contact="hong@test.co.kr",
        accent_color_hex="#1E40AF",
    )

    inputs = ReviewReportInput(
        project_label="프로젝트 A",
        run_started_at="2026-05-02T19:30:00",
        source_a="C:/test/A",
        source_b="C:/test/B",
        drawing_rows=[{"pair_id": "pair_test", "drawing_number": "S-101"}],
        review_records={rec.key: rec},
        confirmed_cloud_dir=confirmed_dir,
        overlays_by_pair={},
        settings=settings,
    )
    output_path = tmp_path / "branded.pdf"
    result = generate_review_report_pdf(inputs=inputs, output_path=output_path)

    assert output_path.exists()
    # Cover (1) + 1 confirmed pair page + appendix (1) + sign-off (1) = 4 pages
    assert result.page_count == 4


def test_pdf_report_skips_signoff_page_without_reviewer(tmp_path: Path) -> None:
    """No reviewer configured → cover + appendix only, no sign-off page."""

    rec = ReviewStateRecord(pair_id="pair_test", pair_uuid="pair_test", zone_id="z1", status="confirmed", note="")
    inputs = ReviewReportInput(
        project_label="프로젝트 B",
        run_started_at="2026-05-02T19:30:00",
        source_a="C:/test/A",
        source_b="C:/test/B",
        drawing_rows=[],
        review_records={rec.key: rec},
        confirmed_cloud_dir=None,
        overlays_by_pair={},
        settings=ReportSettings(),  # defaults — no reviewer name
    )
    output_path = tmp_path / "no_signoff.pdf"
    result = generate_review_report_pdf(inputs=inputs, output_path=output_path)
    assert output_path.exists()
    # Cover + 1 pair page + appendix = 3 (no sign-off because reviewer_name empty)
    assert result.page_count == 3


def test_pdf_report_handles_missing_settings_argument(tmp_path: Path) -> None:
    """Backwards compat — ReviewReportInput.settings can be None."""

    inputs = ReviewReportInput(
        project_label="레거시 호출",
        run_started_at="2026-05-02T19:30:00",
        source_a="C:/x",
        source_b="C:/y",
        drawing_rows=[],
        review_records={},
        confirmed_cloud_dir=None,
        overlays_by_pair={},
        settings=None,
    )
    output_path = tmp_path / "no_settings.pdf"
    result = generate_review_report_pdf(inputs=inputs, output_path=output_path)
    assert output_path.exists()
    assert result.page_count >= 2
