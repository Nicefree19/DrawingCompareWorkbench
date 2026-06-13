from __future__ import annotations

import hashlib
import struct
from pathlib import Path

from scripts import native_cad_version_matrix as matrix
from scripts.native_cad_real_sample_smoke import (
    MANIFEST_SCHEMA,
    build_report,
    load_sample_manifest,
    render_markdown,
    validate_manifest_shape,
)
from src.services.comparison.dwg_object_decoder import DwgObjectDecoder


ROOT = Path(__file__).resolve().parents[3]
MATRIX_PATH = ROOT / "docs" / "collab" / "native_cad_version_matrix.json"


def _write_dwg(root: Path, code: str, name: str | None = None) -> dict[str, object]:
    path = root / ".local" / "native_cad_real_samples" / (name or f"{code}.dwg")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(code.encode("ascii") + b"\npublic-sample\n")
    payload = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "file_name": path.name,
        "size_bytes": len(payload),
        "header_code": code,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "source": "unit-test-source",
        "source_url": "https://example.invalid/sample.dwg",
        "license_note": "local evidence only",
    }


def _manifest(root: Path, codes: list[str]) -> dict[str, object]:
    items = [_write_dwg(root, code) for code in codes]
    return {
        "schema": MANIFEST_SCHEMA,
        "generated_at_utc": "2026-06-12T00:00:00Z",
        "root": ".local/native_cad_real_samples",
        "total_files": len(items),
        "source_summary": [{"source": "unit-test-source", "count": len(items)}],
        "items": items,
    }


def _mchar(value: int, *, signed: bool = False) -> bytes:
    negative = signed and value < 0
    value = abs(value)
    chunks = []
    while True:
        chunks.append(value & 0x7F)
        value >>= 7
        if not value:
            break
    if signed and not negative and (chunks[-1] & 0x40):
        chunks.append(0)
    if negative:
        chunks[-1] |= 0x40
    for index in range(len(chunks) - 1):
        chunks[index] |= 0x80
    return bytes(chunks)


def _ac1015_bad_payload_dwg(root: Path) -> dict[str, object]:
    data = bytearray(b"\x00" * (0x15 + 4 + 9))
    data[:6] = b"AC1015"
    struct.pack_into("<H", data, 0x13, 30)
    struct.pack_into("<I", data, 0x15, 1)
    object_offset = len(data)
    data += b"REALDWG!" + b"\x00" * 24
    object_map_offset = len(data)
    body = _mchar(0x10) + _mchar(object_offset, signed=True)
    object_map = struct.pack(">H", len(body) + 2) + body + b"\x00\x00" + b"\x00\x02\x00\x00"
    data += object_map
    struct.pack_into("<BII", data, 0x15 + 4, 2, object_map_offset, len(object_map))

    path = root / ".local" / "native_cad_real_samples" / "bad_ac1015_payload.dwg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {
        "path": path.relative_to(root).as_posix(),
        "file_name": path.name,
        "size_bytes": len(data),
        "header_code": "AC1015",
        "sha256": hashlib.sha256(data).hexdigest(),
        "source": "unit-test-source",
        "source_url": "https://example.invalid/sample.dwg",
        "license_note": "local evidence only",
    }


def test_report_passes_when_manifest_covers_every_target_code(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, sorted(matrix.expected_versions()))

    report = build_report(
        manifest,
        repo_root=tmp_path,
        matrix_path=MATRIX_PATH,
        run_import=False,
    )

    assert report["status"] == "PASS"
    assert report["summary"]["target_codes_covered"] == len(matrix.expected_versions())
    assert report["missing_target_codes"] == []
    assert report["summary"]["import_probe_count"] == 0
    assert "Native CAD Real Sample Smoke" in render_markdown(report)


def test_report_fails_when_a_target_code_is_missing(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, ["AC1032"])

    report = build_report(
        manifest,
        repo_root=tmp_path,
        matrix_path=MATRIX_PATH,
        run_import=False,
    )

    assert report["status"] == "FAIL"
    assert "AC1009" in report["missing_target_codes"]


def test_report_detects_hash_mismatch(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, sorted(matrix.expected_versions()))
    manifest["items"][0]["sha256"] = "0" * 64

    report = build_report(
        manifest,
        repo_root=tmp_path,
        matrix_path=MATRIX_PATH,
        run_import=False,
    )

    assert report["status"] == "FAIL"
    assert report["summary"]["integrity_failure_count"] == 1


def test_import_probe_records_parse_outcome_separately_from_coverage(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, sorted(matrix.expected_versions()))

    report = build_report(
        manifest,
        repo_root=tmp_path,
        matrix_path=MATRIX_PATH,
        import_samples_per_code=1,
        run_import=True,
    )

    assert report["status"] == "PASS"
    assert report["summary"]["import_probe_count"] == len(matrix.expected_versions())
    assert report["import_probe"]["status_counts"]
    assert report["summary"]["blocked_parse_codes"]
    ac1015 = next(item for item in report["import_probe"]["results"] if item["code"] == "AC1015")
    assert ac1015["failure_stage"] == "section read"
    assert ac1015["reader_error_type"]


def test_import_probe_carries_object_decode_diagnostics(tmp_path: Path) -> None:
    items = [_write_dwg(tmp_path, code) for code in sorted(matrix.expected_versions()) if code != "AC1015"]
    items.append(_ac1015_bad_payload_dwg(tmp_path))
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "generated_at_utc": "2026-06-12T00:00:00Z",
        "root": ".local/native_cad_real_samples",
        "total_files": len(items),
        "source_summary": [{"source": "unit-test-source", "count": len(items)}],
        "items": items,
    }

    report = build_report(
        manifest,
        repo_root=tmp_path,
        matrix_path=MATRIX_PATH,
        import_samples_per_code=1,
        run_import=True,
    )

    ac1015 = next(item for item in report["import_probe"]["results"] if item["code"] == "AC1015")
    assert ac1015["failure_stage"] == "object decode"
    assert ac1015["object_handle"] == "10"
    assert ac1015["object_payload_prefix_hex"].startswith(b"REALDWG!".hex())
    assert ac1015["object_payload_prefix_hex"] != DwgObjectDecoder.MVP_OBJECT_MAGIC.hex()


def test_inventory_only_headers_are_not_required_targets(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, [*sorted(matrix.expected_versions()), "AC1006"])

    report = build_report(
        manifest,
        repo_root=tmp_path,
        matrix_path=MATRIX_PATH,
        run_import=False,
    )

    assert report["status"] == "PASS"
    assert report["inventory_only_headers"] == ["AC1006"]


def test_manifest_shape_requires_source_metadata(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, ["AC1032"])
    del manifest["items"][0]["source_url"]

    assert "items[0].source_url is required" in validate_manifest_shape(manifest)


def test_load_sample_manifest_accepts_utf8_bom(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"schema":"native-cad-real-sample-manifest/v1"}', encoding="utf-8-sig")

    manifest = load_sample_manifest(manifest_path)

    assert manifest["schema"] == MANIFEST_SCHEMA
