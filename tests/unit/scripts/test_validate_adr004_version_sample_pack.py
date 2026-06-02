from __future__ import annotations

import json
from pathlib import Path

from scripts.validate_adr004_version_sample_pack import (
    build_report,
    detect_dwg_header,
    detect_dxf_acadver,
    render_markdown,
    validate_manifest,
)


def _write_dxf(path: Path, acadver: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "0",
                "SECTION",
                "2",
                "HEADER",
                "9",
                "$ACADVER",
                "1",
                acadver,
                "0",
                "ENDSEC",
                "0",
                "EOF",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _write_dwg(path: Path, code: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(code.encode("ascii") + b"\x00sample")
    return path


def _sample_pack(tmp_path: Path, *, output_acadver: str = "AC1024", manifest_acadver: str = "AC1024") -> Path:
    before_dxf = _write_dxf(tmp_path / "AC1024/dxf_registered/before/before.dxf", output_acadver)
    after_dxf = _write_dxf(tmp_path / "AC1024/dxf_registered/after/after.dxf", output_acadver)
    before_dwg = _write_dwg(tmp_path / "AC1024/before/before.dwg", "AC1024")
    after_dwg = _write_dwg(tmp_path / "AC1024/after/after.dwg", "AC1024")
    manifest = {
        "schema_version": 1,
        "versions": {
            "AC1024": {
                "dwg_code": "AC1024",
                "dxf_output_version": "ACAD2010",
                "pair_kind": "unit_test_pair",
                "sample_before_dwg": str(before_dwg),
                "sample_after_dwg": str(after_dwg),
                "outputs": {
                    "before": [
                        {
                            "path": str(before_dxf),
                            "size": before_dxf.stat().st_size,
                            "sha256": "before",
                            "acadver": manifest_acadver,
                        }
                    ],
                    "after": [
                        {
                            "path": str(after_dxf),
                            "size": after_dxf.stat().st_size,
                            "sha256": "after",
                            "acadver": manifest_acadver,
                        }
                    ],
                },
            }
        },
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path


def test_detects_dxf_acadver_and_dwg_header(tmp_path: Path) -> None:
    dxf = _write_dxf(tmp_path / "sample.dxf", "AC1032")
    dwg = _write_dwg(tmp_path / "sample.dwg", "AC1032")

    assert detect_dxf_acadver(dxf) == "AC1032"
    assert detect_dwg_header(dwg) == "AC1032"


def test_validate_manifest_rejects_missing_outputs() -> None:
    errors = validate_manifest({"schema_version": 1, "versions": {"AC1024": {"dwg_code": "AC1024"}}})

    assert "versions.AC1024.outputs is required" in errors
    assert "versions.AC1024.outputs.before must be a non-empty list" in errors


def test_build_report_checks_headers_without_running_smoke(tmp_path: Path) -> None:
    sample_pack = _sample_pack(tmp_path)

    report = build_report(sample_pack, run_import=False, run_compare=False, root=Path.cwd())

    assert report["status"] == "ok"
    assert report["summary"]["header_mismatch_count"] == 0
    assert report["summary"]["import_status_counts"] == {"skipped": 2}
    assert report["summary"]["compare_status_counts"] == {"skipped": 1}
    markdown = render_markdown(report)
    assert "ADR-004 Version Sample Pack Validation" in markdown
    assert "| AC1024 | before | AC1024 | AC1024 |" in markdown


def test_build_report_fails_on_dxf_acadver_mismatch(tmp_path: Path) -> None:
    sample_pack = _sample_pack(tmp_path, output_acadver="AC1032", manifest_acadver="AC1024")

    report = build_report(sample_pack, run_import=False, run_compare=False, root=Path.cwd())

    assert report["status"] == "failed"
    assert report["summary"]["header_mismatch_count"] == 2
    assert any("DXF $ACADVER mismatch" in error for error in report["validation_errors"])
