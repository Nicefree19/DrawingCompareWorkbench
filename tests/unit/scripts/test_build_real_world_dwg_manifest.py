from __future__ import annotations

import json
from pathlib import Path

from scripts.build_real_world_dwg_manifest import build_manifest, main
from scripts.validate_real_world_dwg_samples import validate_manifest


def _dwg(path: Path, code: bytes = b"AC1032") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(code + b"\0" * 32)
    return path


def test_build_manifest_scans_local_dwg_folder_and_pairs_revision(tmp_path: Path) -> None:
    source_root = tmp_path / "samples"
    _dwg(source_root / "detail.dwg")
    _dwg(source_root / "detail_r1.dwg")

    manifest = build_manifest(source_root)

    assert validate_manifest(manifest) == []
    assert manifest["source_policy"] == "local DWG files are referenced, not copied"
    assert len(manifest["samples"]) == 2
    assert {sample["expected_version"] for sample in manifest["samples"]} == {"AC1032"}
    assert manifest["pairs"] == [
        {
            "id": "pair-0001",
            "old_sample": "dwg-0001",
            "new_sample": "dwg-0002",
            "pair_key": "detail",
            "current_import_expectation": "unsupported_version_until_native_reader_expands_beyond_AC1015",
        }
    ]


def test_build_manifest_pairs_before_after_folder_structure(tmp_path: Path) -> None:
    source_root = tmp_path / "samples"
    before = _dwg(source_root / "before" / "detail.dwg", b"AC1018")
    after = _dwg(source_root / "after" / "detail.dwg", b"AC1018")

    manifest = build_manifest(source_root)

    assert validate_manifest(manifest) == []
    assert len(manifest["pairs"]) == 1
    by_id = {sample["id"]: sample for sample in manifest["samples"]}
    pair = manifest["pairs"][0]
    assert by_id[pair["old_sample"]]["path"] == before.relative_to(source_root).as_posix()
    assert by_id[pair["new_sample"]]["path"] == after.relative_to(source_root).as_posix()
    assert pair["pair_key"] == "detail"


def test_build_manifest_skips_generated_dirs_by_default(tmp_path: Path) -> None:
    source_root = tmp_path / "samples"
    _dwg(source_root / "customer" / "detail.dwg")
    _dwg(source_root / "out" / "generated.dwg")
    _dwg(source_root / "build" / "generated.dwg")

    manifest = build_manifest(source_root)

    assert [sample["path"] for sample in manifest["samples"]] == ["customer/detail.dwg"]
    assert "out" in manifest["excluded_dir_names"]
    assert "build" in manifest["excluded_dir_names"]


def test_build_manifest_can_include_generated_dirs_when_requested(tmp_path: Path) -> None:
    source_root = tmp_path / "samples"
    _dwg(source_root / "customer" / "detail.dwg")
    _dwg(source_root / "out" / "generated.dwg")

    manifest = build_manifest(source_root, include_generated=True)

    assert [sample["path"] for sample in manifest["samples"]] == [
        "customer/detail.dwg",
        "out/generated.dwg",
    ]
    assert manifest["excluded_dir_names"] == []


def test_build_manifest_does_not_pair_ambiguous_revisions(tmp_path: Path) -> None:
    source_root = tmp_path / "samples"
    _dwg(source_root / "detail.dwg")
    _dwg(source_root / "detail_r1.dwg")
    _dwg(source_root / "detail_rev2.dwg")

    manifest = build_manifest(source_root)

    assert len(manifest["samples"]) == 3
    assert manifest["pairs"] == []


def test_build_manifest_cli_writes_validation_compatible_manifest(tmp_path: Path) -> None:
    source_root = tmp_path / "samples"
    _dwg(source_root / "old.dwg", b"AC1015")
    out = tmp_path / "manifest.json"

    exit_code = main([str(source_root), "--manifest", str(out)])

    assert exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert validate_manifest(payload) == []
    assert payload["samples"][0]["expected_version"] == "AC1015"
    assert payload["samples"][0]["detected_supported"] is True
