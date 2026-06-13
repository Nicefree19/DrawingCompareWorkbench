from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scripts.native_cad_viewer_evidence_fixture import build_fixture_viewer_evidence
from src.services.comparison.drawing_compare_engine import DrawingCompareEngine
from src.services.comparison.dwg_importer import DwgFailureCode, DwgImporter, DwgJsonFixtureAdapter, DwgVersionDetector
from src.services.comparison.native_cad_bridge import NativeCadBridgeCode
from src.services.comparison.native_cad_importer import NativeCadBridgeAdapter
from src.services.comparison.native_scene_pack import (
    NATIVE_CAD_VIEWER_EVIDENCE_SCHEMA_VERSION,
    NATIVE_SCENE_PACK_SCHEMA_VERSION,
    native_scene_viewer_evidence_payload,
)


ROOT = Path(__file__).resolve().parents[4]
FIXTURE_BRIDGE = ROOT / "tools" / "native_cad_fixture_bridge.py"


def _schema() -> dict:
    return json.loads((ROOT / "docs" / "canonical-drawing.schema.json").read_text(encoding="utf-8"))


def _validate_schema(doc: dict) -> None:
    schema = _schema()
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(doc), key=str)
    assert not errors, "\n".join(f"{list(error.path)}: {error.message}" for error in errors[:10])


def _write_dwg(path: Path, code: str = "AC1032") -> Path:
    path.write_bytes(code.encode("ascii") + b"\nfixture\n")
    return path


def _fixture_adapter() -> NativeCadBridgeAdapter:
    return NativeCadBridgeAdapter(
        command=sys.executable,
        args_template=(str(FIXTURE_BRIDGE), "{input}", "{acadver}"),
        supported_versions=("AC1032",),
        name="native-cad-fixture-adapter",
        version="1",
        timeout_seconds=120.0,
    )


def _fixture_adapter_for(code: str) -> NativeCadBridgeAdapter:
    return NativeCadBridgeAdapter(
        command=sys.executable,
        args_template=(str(FIXTURE_BRIDGE), "{input}", "{acadver}"),
        supported_versions=(code,),
        name=f"native-cad-fixture-{code.lower()}",
        version="1",
        timeout_seconds=120.0,
    )


def test_native_bridge_adapter_imports_only_when_explicitly_injected(tmp_path: Path) -> None:
    path = _write_dwg(tmp_path / "sample.dwg")

    assert DwgVersionDetector.detect_file(path).supported is False

    default_doc = DwgImporter(adapter=DwgJsonFixtureAdapter()).import_file(path)
    explicit_doc = DwgImporter(adapter=_fixture_adapter()).import_file(path)

    _validate_schema(default_doc)
    _validate_schema(explicit_doc)
    assert default_doc["import_report"]["error_code"] == DwgFailureCode.UNSUPPORTED_VERSION
    assert explicit_doc["import_report"]["status"] == "ok"
    assert explicit_doc["drawing"]["source"]["acad_version"] == "AC1032"
    assert explicit_doc["drawing"]["importer"]["backend"] == "native-cad-fixture-adapter"

    metadata = explicit_doc["metadata"]["adapter_metadata"]
    assert metadata["native_scene_pack"]["schema_version"] == NATIVE_SCENE_PACK_SCHEMA_VERSION
    assert metadata["native_scene_overview_lod0"]["primitive_count"] == 2
    assert metadata["native_cad_bridge"]["supported_versions"] == ["AC1032"]
    cache_identity = metadata["native_cad_bridge"]["cache_identity"]
    assert cache_identity["schema_version"] == "native-cad-cache-identity/v1"
    assert len(cache_identity["fingerprint"]) == 64
    assert cache_identity["dwg_version"]["code"] == "AC1032"
    assert "command_sha256" in cache_identity["bridge"]


def test_native_bridge_adapter_fails_closed_with_structured_failure(tmp_path: Path) -> None:
    path = _write_dwg(tmp_path / "sample.dwg")
    adapter = NativeCadBridgeAdapter(
        command="definitely-missing-native-cad-bridge",
        supported_versions=("AC1032",),
        timeout_seconds=5.0,
    )

    doc = DwgImporter(adapter=adapter).import_file(path)

    _validate_schema(doc)
    assert doc["import_report"]["status"] == "failed"
    assert doc["import_report"]["error_code"] == DwgFailureCode.ADAPTER_UNAVAILABLE
    details = doc["import_report"]["warnings"][0]["details"]
    assert details["native_cad_bridge"]["failure"]["code"] == NativeCadBridgeCode.SDK_UNAVAILABLE


def test_native_bridge_imported_drawings_feed_existing_compare_engine(tmp_path: Path) -> None:
    before = DwgImporter(adapter=_fixture_adapter()).import_file(_write_dwg(tmp_path / "before.dwg"))
    after = DwgImporter(adapter=_fixture_adapter()).import_file(_write_dwg(tmp_path / "after_r1.dwg"))

    _validate_schema(before)
    _validate_schema(after)
    result = DrawingCompareEngine().compare(before, after)

    modified = [change for change in result.changes if change.change_type == "modified"]
    assert result.summary["modified"] >= 1
    assert any(
        field.path == "geometry.canonical_text"
        for change in modified
        for field in ((change.geometry_diff.fields if change.geometry_diff else []))
    )


def test_native_bridge_import_builds_viewer_evidence_frame(tmp_path: Path) -> None:
    before = DwgImporter(adapter=_fixture_adapter()).import_file(_write_dwg(tmp_path / "before.dwg"))
    after = DwgImporter(adapter=_fixture_adapter()).import_file(_write_dwg(tmp_path / "after_r1.dwg"))
    diff = DrawingCompareEngine().compare(before, after)
    modified = next(change for change in diff.changes if change.change_type == "modified")

    evidence = native_scene_viewer_evidence_payload(
        after["metadata"]["adapter_metadata"]["native_scene_pack"],
        change_overlays=[
            {
                "zone_id": modified.change_id,
                "change_type": modified.change_type,
                "priority_rank": 1,
                "old_bbox": modified.old_bbox,
                "bbox": modified.new_bbox or modified.bbox,
            }
        ],
        import_report=after["import_report"],
    )

    assert evidence["schema_version"] == NATIVE_CAD_VIEWER_EVIDENCE_SCHEMA_VERSION
    assert evidence["overview_lod0"]["schema_version"] == "overview-lod0/v1"
    assert evidence["overview_lod0"]["primitive_count"] == 2
    assert evidence["viewer"]["bounded_payload"] is True
    assert evidence["viewer"]["within_primitive_budget"] is True
    assert evidence["viewer"]["within_payload_byte_budget"] is True
    assert evidence["viewer"]["world_bbox"] == [0.0, 0.0, 120.0, 60.0]
    assert evidence["change_overlay_count"] == 1
    assert evidence["primary_change_frame"]["status"] == "framed"
    assert evidence["import_report"]["status"] == "ok"

    frame = evidence["primary_change_frame"]["world_bbox"]
    assert frame[0] <= modified.bbox["min_x"]
    assert frame[1] <= modified.bbox["min_y"]
    assert frame[2] >= modified.bbox["max_x"]
    assert frame[3] >= modified.bbox["max_y"]


def test_native_viewer_evidence_fixture_writes_json_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "viewer_evidence" / "fixture_pair_evidence.json"

    evidence = build_fixture_viewer_evidence(
        code="AC1032",
        fixture_dir=tmp_path / "fixtures",
        output_path=artifact,
    )

    persisted = json.loads(artifact.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == NATIVE_CAD_VIEWER_EVIDENCE_SCHEMA_VERSION
    assert persisted["native_scene_pack"]["schema_version"] == NATIVE_SCENE_PACK_SCHEMA_VERSION
    assert persisted["overview_lod0"]["schema_version"] == "overview-lod0/v1"
    assert persisted["overview_lod0"]["primitive_count"] == 2
    assert persisted["viewer"]["world_bbox"] == [0.0, 0.0, 120.0, 60.0]
    assert persisted["viewer"]["bounded_payload"] is True
    assert persisted["viewer"]["within_primitive_budget"] is True
    assert persisted["viewer"]["within_payload_byte_budget"] is True
    assert persisted["primary_change_frame"]["status"] == "framed"
    assert persisted["primary_change_frame"]["world_bbox"]
    assert persisted["import_report"]["status"] == "ok"
    assert persisted["policy"]["default_support_expanded"] is False
    assert persisted == evidence


@pytest.mark.parametrize(
    "code",
    sorted(set(DwgVersionDetector.SUPPORTED_CODES) | set(DwgVersionDetector.KNOWN_UNSUPPORTED_CODES)),
)
def test_explicit_fixture_bridge_imports_every_target_code_without_default_broadening(
    tmp_path: Path,
    code: str,
) -> None:
    before = _write_dwg(tmp_path / f"{code}_before.dwg", code=code)
    after = _write_dwg(tmp_path / f"{code}_after_r1.dwg", code=code)

    before_doc = DwgImporter(adapter=_fixture_adapter_for(code)).import_file(before)
    after_doc = DwgImporter(adapter=_fixture_adapter_for(code)).import_file(after)

    _validate_schema(before_doc)
    _validate_schema(after_doc)
    assert before_doc["import_report"]["status"] == "ok"
    assert after_doc["import_report"]["status"] == "ok"
    assert before_doc["drawing"]["source"]["acad_version"] == code

    if code not in DwgVersionDetector.SUPPORTED_CODES:
        default_doc = DwgImporter(adapter=DwgJsonFixtureAdapter()).import_file(before)
        assert default_doc["import_report"]["error_code"] == DwgFailureCode.UNSUPPORTED_VERSION

    diff = DrawingCompareEngine().compare(before_doc, after_doc)
    assert diff.summary["modified"] >= 1
