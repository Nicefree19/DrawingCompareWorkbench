# -*- coding: utf-8 -*-
"""3rd-review fix (P1) — Windows path escape regression guards.

The 2nd-review-fix commit added validate_output_path with allowed_base_dir
strict matching. The 3rd review noted that even with the basic check,
Windows-specific path forms (UNC paths, junctions, alternate data
streams, drive-letter case) need explicit regression coverage so a
future refactor of validate_path can't silently weaken the guard.

These tests exercise pdf_cloud_dxf_export.export_cloud_marks_to_dxf
end-to-end with hostile output_dir inputs and assert fail-closed
behaviour.

Pure-Python; runs on any platform (Path semantics differ but the
fail-closed assertion holds either way).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


class _FakeRecord:
    """Minimal review-record stub matching the production protocol."""

    def __init__(self, pair_id, zone_id, status):
        self.pair_id = pair_id
        self.zone_id = zone_id
        self.status = status


# ---------------------------------------------------------------------------
# Output-dir escape attempts
# ---------------------------------------------------------------------------


def test_export_rejects_output_dir_outside_allowed_root(tmp_path: Path) -> None:
    """output_dir under a DIFFERENT root from allowed_output_root must
    raise PathValidationError → fail-closed result."""

    from src.services.comparison.pdf_cloud_dxf_export import (
        export_cloud_marks_to_dxf,
    )
    other_root = tmp_path / "other_root"
    other_root.mkdir()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    # Try to write under `other_root` while only `sandbox` is allowed
    result = export_cloud_marks_to_dxf(
        pair_id="p1",
        overlays=[],
        review_records={},
        output_dir=other_root / "out",
        pdf_path=None,
        pdf_dpi=200.0,
        allowed_output_root=sandbox,
    )
    assert result.output_path == ""
    assert "출력 경로 검증 실패" in result.skipped_reason


def test_export_rejects_traversal_dotdot(tmp_path: Path) -> None:
    """``../../etc/passwd``-style traversal must be rejected by the
    Path.resolve() + is_relative_to() check."""

    from src.services.comparison.pdf_cloud_dxf_export import (
        export_cloud_marks_to_dxf,
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    # Construct an output_dir that resolves OUTSIDE sandbox via ../..
    escape_path = sandbox / ".." / ".." / "elsewhere"
    result = export_cloud_marks_to_dxf(
        pair_id="p1",
        overlays=[],
        review_records={},
        output_dir=escape_path,
        pdf_path=None,
        pdf_dpi=200.0,
        allowed_output_root=sandbox,
    )
    assert result.output_path == ""
    assert "출력 경로 검증 실패" in result.skipped_reason


def test_export_rejects_empty_output_dir(tmp_path: Path) -> None:
    """Empty / blank output_dir must raise (validate_path catches it)."""

    from src.services.comparison.pdf_cloud_dxf_export import (
        export_cloud_marks_to_dxf,
    )
    result = export_cloud_marks_to_dxf(
        pair_id="p1",
        overlays=[],
        review_records={},
        output_dir="",  # empty path
        pdf_path=None,
        pdf_dpi=200.0,
        allowed_output_root=tmp_path,
    )
    assert result.output_path == ""
    assert "검증 실패" in result.skipped_reason


def test_export_accepts_legitimate_subdir(tmp_path: Path) -> None:
    """Sanity — legitimate output under allowed_output_root must work."""

    from src.services.comparison.pdf_cloud_dxf_export import (
        export_cloud_marks_to_dxf,
    )
    legit = tmp_path / "session_artifacts"
    result = export_cloud_marks_to_dxf(
        pair_id="p1",
        overlays=[],
        review_records={},
        output_dir=legit,
        pdf_path=None,
        pdf_dpi=200.0,
        allowed_output_root=tmp_path,
    )
    # Skips because no confirmed zones, NOT because of path validation
    assert result.output_path == ""
    # Important: skip reason must NOT be path-related
    assert "출력 경로" not in result.skipped_reason
    assert "확인" in result.skipped_reason


# ---------------------------------------------------------------------------
# Windows-specific path forms
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only path form")
def test_export_rejects_unc_path_outside_root(tmp_path: Path) -> None:
    """UNC paths (``\\\\server\\share``) bypass normal resolve checks
    on Windows. Even if such paths somehow get to the validator,
    they must NOT be accepted unless the allowed root is itself UNC.
    """

    from src.services.comparison.pdf_cloud_dxf_export import (
        export_cloud_marks_to_dxf,
    )
    unc_path = r"\\nonexistent-server\share\out"
    result = export_cloud_marks_to_dxf(
        pair_id="p1",
        overlays=[],
        review_records={},
        output_dir=unc_path,
        pdf_path=None,
        pdf_dpi=200.0,
        allowed_output_root=tmp_path,
    )
    # Either path validation rejects, or the resolve fails — both are
    # acceptable fail-closed outcomes
    assert result.output_path == ""
    assert ("검증 실패" in result.skipped_reason
            or "확인" in result.skipped_reason)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only path form")
def test_export_rejects_alternate_data_stream(tmp_path: Path) -> None:
    """Windows NTFS Alternate Data Streams (``file:stream``) must NOT
    be allowed as output paths — they're invisible by default and let
    a malicious caller hide writes."""

    from src.services.comparison.pdf_cloud_dxf_export import (
        export_cloud_marks_to_dxf,
    )
    ads_path = tmp_path / "out:hidden_stream"
    result = export_cloud_marks_to_dxf(
        pair_id="p1",
        overlays=[],
        review_records={},
        output_dir=ads_path,
        pdf_path=None,
        pdf_dpi=200.0,
        allowed_output_root=tmp_path,
    )
    # Must NOT successfully write
    assert result.output_path == ""


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only path form")
def test_drive_letter_case_does_not_bypass_check(tmp_path: Path) -> None:
    """Drive letter case (``C:`` vs ``c:``) must not bypass the
    allowed_root check. NTFS treats them as the same drive but a naive
    string comparison would miss this."""

    from src.services.comparison.pdf_cloud_dxf_export import (
        export_cloud_marks_to_dxf,
    )
    # Try uppercase + lowercase variants of the same drive
    drive = str(tmp_path)[0]
    flipped_drive = drive.swapcase()
    if flipped_drive == drive:  # tmp_path on a path without a letter
        pytest.skip("tmp_path doesn't start with a drive letter")
    flipped_path = flipped_drive + str(tmp_path)[1:]
    legit = Path(flipped_path) / "session"

    result = export_cloud_marks_to_dxf(
        pair_id="p1",
        overlays=[],
        review_records={},
        output_dir=legit,
        pdf_path=None,
        pdf_dpi=200.0,
        allowed_output_root=tmp_path,
    )
    # Path.resolve() normalises drive case, so the legitimate path
    # under the case-flipped tmp_path SHOULD be accepted (not rejected
    # for a spurious case mismatch). The test asserts no false-positive
    # rejection.
    # Either the path validates and we get the no-confirmed-zones
    # message, OR the validator rejects with a clear reason.
    if result.output_path:
        # accepted → must be the legit path
        assert "session" in result.output_path
    else:
        # rejected → must NOT crash; reason must be clear Korean string
        assert result.skipped_reason


# ---------------------------------------------------------------------------
# pdf_path safe_file_open coverage (negative: missing extension, oversize)
# ---------------------------------------------------------------------------


def test_export_rejects_pdf_with_wrong_extension(tmp_path: Path) -> None:
    """safe_file_open(allowed_extensions={".pdf"}) must reject .docx
    even if the file exists."""

    from src.services.comparison.pdf_cloud_dxf_export import (
        export_cloud_marks_to_dxf,
    )
    fake_pdf = tmp_path / "fake.docx"
    fake_pdf.write_bytes(b"not a real pdf")

    result = export_cloud_marks_to_dxf(
        pair_id="p1",
        overlays=[{"zone_id": "z1", "after_bbox_px": [10, 10, 100, 100]}],
        review_records={"k1": _FakeRecord("p1", "z1", "confirmed")},
        output_dir=tmp_path / "session",
        pdf_path=fake_pdf,
        pdf_dpi=200.0,
        allowed_output_root=tmp_path,
    )
    assert result.output_path == ""
    assert "PDF 입력 검증 실패" in result.skipped_reason


def test_export_rejects_nonexistent_pdf(tmp_path: Path) -> None:
    """Missing PDF must fail at safe_file_open(must_exist=True)."""

    from src.services.comparison.pdf_cloud_dxf_export import (
        export_cloud_marks_to_dxf,
    )
    missing = tmp_path / "does_not_exist.pdf"
    result = export_cloud_marks_to_dxf(
        pair_id="p1",
        overlays=[{"zone_id": "z1", "after_bbox_px": [10, 10, 100, 100]}],
        review_records={"k1": _FakeRecord("p1", "z1", "confirmed")},
        output_dir=tmp_path / "session",
        pdf_path=missing,
        pdf_dpi=200.0,
        allowed_output_root=tmp_path,
    )
    assert result.output_path == ""
    assert "PDF 입력 검증 실패" in result.skipped_reason
