from __future__ import annotations

from scripts.release_environment_check import (
    _console_summary,
    _oda_status,
    collect_environment_report,
)


def test_release_environment_reports_oda_as_optional_legacy_fallback() -> None:
    status = _oda_status()

    assert status["required"] is False
    assert "legacy fallback" in status["policy"]


def test_release_environment_keeps_pymupdf_out_of_required_runtime_modules() -> None:
    report = collect_environment_report()

    assert "fitz" not in report["runtime_modules"]
    assert report["optional_or_licensed_modules"]["fitz"]["required"] is False
    assert "disabled unless separately licensed" in report["optional_or_licensed_modules"]["fitz"]["policy"]

    summary = _console_summary(report)
    assert "Optional/licensed modules:" in summary
    assert "required=no" in summary
    assert "ODA Converter: MISSING" not in summary
    assert "required=yes" not in summary
