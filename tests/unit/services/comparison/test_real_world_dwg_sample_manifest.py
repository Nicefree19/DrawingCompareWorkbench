from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.comparison.dwg_diagnostics import diagnose_dwg_file
from src.services.comparison.dwg_importer import DwgVersionDetector


MANIFEST_PATH = Path("tests/data/comparison/real_world/local-dwg-samples.manifest.json")


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _source_root(manifest: dict) -> Path:
    return Path(str(manifest["source_root"]))


def test_real_world_dwg_manifest_schema_and_unique_ids() -> None:
    manifest = _load_manifest()

    assert manifest["schema_version"] == "cad-real-world-local/v1"
    sample_ids = [sample["id"] for sample in manifest["samples"]]
    assert len(sample_ids) == len(set(sample_ids))
    assert {sample["format"] for sample in manifest["samples"]} == {"dwg"}
    assert manifest["pairs"]


def test_local_real_world_dwg_headers_match_manifest() -> None:
    manifest = _load_manifest()
    root = _source_root(manifest)
    if not root.exists():
        pytest.skip(f"local real-world DWG sample root is unavailable: {root}")

    for sample in manifest["samples"]:
        path = root / sample["path"]
        assert path.is_file(), path
        assert path.stat().st_size == sample["expected_size_bytes"]
        version = DwgVersionDetector.detect_file(path)
        assert version.code == sample["expected_version"]
        assert version.supported is (version.code == "AC1015")


def test_local_real_world_descriptor_cache_covers_legacy_pair() -> None:
    manifest = _load_manifest()
    cache_dir = Path(str(manifest["cache_dir"]))
    if not cache_dir.exists():
        pytest.skip(f"local descriptor cache is unavailable: {cache_dir}")

    pair = next(item for item in manifest["pairs"] if item["id"] == "legacy_pair_1_vs_2")
    cache_files = pair["descriptor_cache_files"]
    descriptors = []
    for name in cache_files:
        payload = json.loads((cache_dir / name).read_text(encoding="utf-8"))
        descriptor = payload["descriptor"]
        descriptors.append(descriptor)
        assert descriptor["kind"] == "cad"
        assert descriptor["extension"] == ".dwg"
        assert descriptor["entity_counts"]
        assert len(descriptor["layers"]) >= 1

    assert Path(descriptors[0]["path"]).name == "1.dwg"
    assert Path(descriptors[1]["path"]).name == "2.dwg"
    assert descriptors[0]["content_fingerprint"] != descriptors[1]["content_fingerprint"]


def test_local_real_world_dwg_native_diagnostics_pin_blocking_stage() -> None:
    manifest = _load_manifest()
    root = _source_root(manifest)
    if not root.exists():
        pytest.skip(f"local real-world DWG sample root is unavailable: {root}")

    diagnostics = [
        diagnose_dwg_file(root / sample["path"]).to_dict()
        for sample in manifest["samples"]
    ]

    assert {item["status"] for item in diagnostics} == {"unsupported_version"}
    assert {
        item["blocking_stage"]
        for item in diagnostics
    } == {"section_map_decoder"}
    assert {
        item["version"]["code"]
        for item in diagnostics
    } == {"AC1024", "AC1032"}
    for diagnostic in diagnostics:
        stages = {stage["name"]: stage for stage in diagnostic["stages"]}
        metrics = stages["section_map_decoder"]["metrics"]
        assert metrics["blocking_stage_detail"] == "approved_format_contract_required"
        assert metrics["approved_reference_available"] is False
