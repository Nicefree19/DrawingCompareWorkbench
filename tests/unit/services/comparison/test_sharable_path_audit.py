# -*- coding: utf-8 -*-
"""Tests for the sharable export profile path-leak audit.

The validation runner walks all .json artifacts after redaction and looks for
absolute paths or unredacted sensitive keys. Any leak is recorded in
``validation_summary.json`` and forces ``quality_gate.passed=False`` so CI can
block on packaged artifacts that would expose internal filesystem layout.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.validate_drawing_compare_realset import (
    _looks_like_absolute_path,
    audit_sharable_paths,
)
from src.services.comparison.export_profiles import (
    apply_export_profile_to_csv,
    apply_export_profile_to_file,
    apply_export_profile_to_xlsx,
    audit_sharable_paths as service_audit_sharable_paths,
)


def test_looks_like_absolute_recognizes_windows_posix_unc() -> None:
    assert _looks_like_absolute_path("C:\\Users\\nobody\\thing.dxf") is True
    assert _looks_like_absolute_path("/home/runner/build.dxf") is True
    assert _looks_like_absolute_path("\\\\server\\share\\file.dxf") is True


def test_looks_like_absolute_ignores_relative_and_redacted() -> None:
    assert _looks_like_absolute_path("artifacts/change_zones.csv") is False
    assert _looks_like_absolute_path("<redacted>/file.dxf") is False
    assert _looks_like_absolute_path("/redacted/file.dxf") is False
    assert _looks_like_absolute_path("") is False
    assert _looks_like_absolute_path("just a string") is False


def test_audit_returns_empty_when_no_json_files(tmp_path: Path) -> None:
    assert audit_sharable_paths(tmp_path) == []


def test_audit_returns_empty_when_payloads_are_clean(tmp_path: Path) -> None:
    (tmp_path / "viewer_manifest.json").write_text(
        json.dumps(
            {
                "pairs": [
                    {
                        "pair_id": "p1",
                        "source_a": "<redacted>/before.dxf",
                        "source_b": "<redacted>/after.dxf",
                        "overlay_json": "viewer/overlays/p1.json",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert audit_sharable_paths(tmp_path) == []


def test_audit_flags_unredacted_sensitive_key(tmp_path: Path) -> None:
    (tmp_path / "leaky.json").write_text(
        json.dumps({"source_a": "C:\\Users\\nobody\\drawings\\A.dxf"}),
        encoding="utf-8",
    )
    leaks = audit_sharable_paths(tmp_path)
    assert len(leaks) == 1
    leak = leaks[0]
    assert leak["key"] == "source_a"
    assert leak["reason"] == "sensitive_key_not_redacted"
    assert "nobody" in leak["value"]


def test_service_audit_flags_unredacted_sensitive_key(tmp_path: Path) -> None:
    (tmp_path / "leaky.json").write_text(
        json.dumps({"source_a": "D:\\work\\customer\\A.dwg"}),
        encoding="utf-8",
    )

    leaks = service_audit_sharable_paths(tmp_path)

    assert len(leaks) == 1
    assert leaks[0]["key"] == "source_a"
    assert leaks[0]["reason"] == "sensitive_key_not_redacted"


def test_service_audit_accepts_redacted_and_relative_paths(tmp_path: Path) -> None:
    (tmp_path / "clean.json").write_text(
        json.dumps(
            {
                "source_a": "<redacted>/A.dwg",
                "review_project": "review_project.json",
                "outputs": ["artifacts/review_dashboard.json"],
            }
        ),
        encoding="utf-8",
    )

    assert service_audit_sharable_paths(tmp_path) == []


def test_service_audit_flags_csv_and_xlsx_path_leaks(tmp_path: Path) -> None:
    (tmp_path / "review_priority.csv").write_text(
        "source_a,source_b\nD:\\work\\old.dxf,D:\\work\\new.dxf\n",
        encoding="utf-8-sig",
    )
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    workbook.active.append(["Source A"])
    workbook.active.append(["C:\\cache\\old.dxf"])
    workbook.save(tmp_path / "change_register.xlsx")

    leaks = service_audit_sharable_paths(tmp_path)

    assert {leak["file"] for leak in leaks} == {"review_priority.csv", "change_register.xlsx"}


def test_service_audit_flags_pdf_text_path_leaks(tmp_path: Path) -> None:
    fitz = pytest.importorskip("fitz")
    pdf_path = tmp_path / "review_report.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "source: C:/Users/user/.codex/worktrees/45ea/secret/S-101.pdf")
    doc.save(str(pdf_path))
    doc.close()

    leaks = service_audit_sharable_paths(tmp_path)

    assert len(leaks) == 1
    assert leaks[0]["file"] == "review_report.pdf"
    assert leaks[0]["key"] == "pdf_text"
    assert leaks[0]["reason"] == "absolute_path_in_text"


def test_apply_export_profile_redacts_csv_and_xlsx_cells(tmp_path: Path) -> None:
    csv_path = tmp_path / "review_priority.csv"
    csv_path.write_text(
        "source_a,before_image\nD:\\customer\\old.dxf,"
        + str(tmp_path / "viewer" / "before.png")
        + "\n",
        encoding="utf-8-sig",
    )
    (tmp_path / "viewer").mkdir()
    (tmp_path / "viewer" / "before.png").write_text("x", encoding="utf-8")
    openpyxl = pytest.importorskip("openpyxl")
    xlsx_path = tmp_path / "change_register.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active.append(["Source A"])
    workbook.active.append(["C:\\cache\\old.dxf"])
    workbook.save(xlsx_path)

    apply_export_profile_to_csv(csv_path, profile="sharable", package_root=tmp_path)
    apply_export_profile_to_xlsx(xlsx_path, profile="sharable", package_root=tmp_path)

    assert service_audit_sharable_paths(tmp_path) == []
    csv_text = csv_path.read_text(encoding="utf-8-sig")
    assert "<redacted>/old.dxf" in csv_text
    assert "viewer/before.png" in csv_text


def test_apply_export_profile_redacts_embedded_csv_json_paths(tmp_path: Path) -> None:
    csv_path = tmp_path / "match_candidates.csv"
    csv_path.write_text(
        'status,alternates_json\n'
        'review_required,"[{""b_path"": ""D:\\\\customer\\\\new\\\\S-2407_REV1.pdf"", '
        '""score"": 0.84}]"\n',
        encoding="utf-8-sig",
    )

    apply_export_profile_to_csv(csv_path, profile="sharable", package_root=tmp_path)

    text = csv_path.read_text(encoding="utf-8-sig")
    assert "D:\\customer" not in text
    assert "<redacted>/S-2407_REV1.pdf" in text
    assert service_audit_sharable_paths(tmp_path) == []


def test_apply_export_profile_redacts_html_and_text_paths(tmp_path: Path) -> None:
    html_path = tmp_path / "validation_report.html"
    html_path.write_text(
        '<a href="D:\\customer\\old.dxf">old</a><span>/tmp/cache/state.json</span>',
        encoding="utf-8",
    )

    apply_export_profile_to_file(html_path, profile="sharable", package_root=tmp_path)

    assert service_audit_sharable_paths(tmp_path) == []
    text = html_path.read_text(encoding="utf-8")
    assert "<redacted>/old.dxf" in text
    assert "<redacted>/state.json" in text


def test_apply_export_profile_redacts_success_sentinel_paths(tmp_path: Path) -> None:
    success_path = tmp_path / "_SUCCESS"
    success_path.write_text(
        '{"run_manifest":"C:\\\\work\\\\run_manifest.json"}',
        encoding="utf-8",
    )

    assert service_audit_sharable_paths(tmp_path)

    apply_export_profile_to_file(success_path, profile="sharable", package_root=tmp_path)

    assert service_audit_sharable_paths(tmp_path) == []
    assert "<redacted>/run_manifest.json" in success_path.read_text(encoding="utf-8")


def test_audit_flags_absolute_paths_in_string_values(tmp_path: Path) -> None:
    # Audit detects absolute paths at the start of string values (the dominant
    # leak shape for output_paths-style fields). Embedded paths in prose are not
    # flagged to avoid false positives on log-style messages.
    (tmp_path / "warnings.json").write_text(
        json.dumps(
            {
                "outputs": [
                    "/var/log/run/output.log",
                    "C:\\Temp\\thing.txt",
                ]
            }
        ),
        encoding="utf-8",
    )
    leaks = audit_sharable_paths(tmp_path)
    assert len(leaks) == 2
    assert all(leak["file"] == "warnings.json" for leak in leaks)
    assert {leak["reason"] for leak in leaks} == {"absolute_path_in_string_value"}


def test_audit_walks_nested_lists_and_dicts(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "nested.json"
    nested.parent.mkdir()
    nested.write_text(
        json.dumps(
            {
                "outer": {
                    "items": [
                        {"safe": "rel/path.json"},
                        {"compare_state_dir": "/tmp/state"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    leaks = audit_sharable_paths(tmp_path)
    assert len(leaks) == 1
    leak = leaks[0]
    assert leak["file"] == "deep/nested.json"
    assert leak["key"] == "outer.items[1].compare_state_dir"
    assert leak["reason"] == "sensitive_key_not_redacted"


def test_audit_does_not_flag_relative_path_under_sensitive_key(tmp_path: Path) -> None:
    # Sensitive keys often hold simple relative directory names ("dxf_cache",
    # "compare_state") inside artifact manifests. Those are safe to ship and
    # should not be reported as leaks even though the key itself is sensitive.
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "dxf_cache_dir": "dxf_cache",
                "compare_state_dir": "compare_state",
            }
        ),
        encoding="utf-8",
    )
    assert audit_sharable_paths(tmp_path) == []


def test_audit_skips_unparseable_files_without_failing(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("not json", encoding="utf-8")
    (tmp_path / "ok.json").write_text(json.dumps({"value": "rel/x.json"}), encoding="utf-8")
    leaks = audit_sharable_paths(tmp_path)
    assert leaks == []
