from __future__ import annotations

import json
from pathlib import Path

from src.services.comparison.native_scene_pack import (
    BRIDGE_RESULT_SCHEMA_VERSION,
    NATIVE_SCENE_PACK_SCHEMA_VERSION,
    NativeScenePack,
    bridge_payload_from_scene_pack,
    source_signature,
    write_native_scene_pack_artifacts,
)


def test_scene_pack_round_trips_unknown_fields_and_overview_payload() -> None:
    pack = NativeScenePack.from_dict(
        {
            "schema_version": NATIVE_SCENE_PACK_SCHEMA_VERSION,
            "source": {"path": "sample.dwg", "acad_version": "AC1015"},
            "adapter": {"name": "fixture"},
            "layers": [{"name": "STEEL"}],
            "display_primitives": [
                {"id": "p1", "type": "lines", "geometry": [[0.0, 0.0, 10.0, 0.0]]}
            ],
            "bbox": [0.0, 0.0, 10.0, 5.0],
            "future_field": {"kept": True},
        }
    )

    assert pack.metadata["unknown_fields"]["future_field"] == {"kept": True}
    assert pack.to_dict()["schema_version"] == NATIVE_SCENE_PACK_SCHEMA_VERSION

    overview = pack.overview_lod0_payload()
    assert overview["schema_version"] == "overview-lod0/v1"
    assert overview["source_kind"] == "native_cad"
    assert overview["world_bbox"] == [0.0, 0.0, 10.0, 5.0]
    assert overview["primitive_count"] == 1
    assert overview["primitives"][0]["type"] == "lines"


def test_bridge_payload_wraps_scene_pack_and_optional_drawing() -> None:
    pack = NativeScenePack(
        source={"path": "sample.dwg"},
        adapter={"name": "fixture"},
        bbox=(0.0, 0.0, 1.0, 1.0),
    )

    payload = bridge_payload_from_scene_pack(pack, drawing={"model_space": []})

    assert payload["schema_version"] == BRIDGE_RESULT_SCHEMA_VERSION
    assert payload["scene_pack"]["schema_version"] == NATIVE_SCENE_PACK_SCHEMA_VERSION
    assert payload["drawing"] == {"model_space": []}


def test_write_native_scene_pack_artifacts_returns_scene_pack_ref(tmp_path: Path) -> None:
    pack = NativeScenePack(
        source={"path": "sample.dwg"},
        adapter={"name": "fixture"},
        display_primitives=[
            {"id": "p1", "type": "line", "points": [0.0, 0.0, 1.0, 1.0]}
        ],
        bbox=(0.0, 0.0, 1.0, 1.0),
    )

    ref = write_native_scene_pack_artifacts(pack, tmp_path / "native")
    overview = json.loads(Path(ref.overview_lod0_path).read_text(encoding="utf-8"))

    assert Path(ref.json_path).exists()
    assert overview["source_kind"] == "native_cad"
    assert overview["primitive_count"] == 1
    assert ref.notes == "native_scene_pack"


def test_source_signature_is_stable_for_existing_and_missing_files(tmp_path: Path) -> None:
    path = tmp_path / "sample.dwg"
    path.write_bytes(b"AC1032 fixture")

    signature = source_signature(path)
    missing = source_signature(tmp_path / "missing.dwg")

    assert signature["exists"] is True
    assert signature["size"] == len(b"AC1032 fixture")
    assert len(signature["sha256"]) == 64
    assert missing["exists"] is False
    assert missing["sha256"] == ""
