from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.comparison.native_scene_pack import (
    NativeScenePack,
    write_native_scene_pack_artifacts,
)
from src.services.comparison.viewer_manifest_v3 import ScenePackRef
from src.services.comparison.viewer_primitive_source import (
    RENDER_CONTRACT_VERSION,
    render_contract_schema_version,
    resolve_viewer_primitive_source,
)


def test_resolves_ezdxf_overview_as_contract_source(tmp_path: Path) -> None:
    source_path = tmp_path / "source.dxf"
    source_path.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")
    overview = tmp_path / "overview_lod0.json"
    overview.write_text(
        json.dumps(
            {
                "format_version": 1,
                "source_path": str(source_path),
                "world_bbox": [0.0, 0.0, 10.0, 5.0],
                "primitives": [
                    {"type": "line", "points": [0.0, 0.0, 10.0, 0.0]},
                ],
            }
        ),
        encoding="utf-8",
    )

    result = resolve_viewer_primitive_source(
        ScenePackRef(overview_lod0_path=str(overview)),
    )

    assert result.ok is True
    assert result.world_bbox == (0.0, 0.0, 10.0, 5.0)
    assert result.provenance["producer_id"] == "ezdxf_scene_pack"
    assert result.provenance["render_contract_version"] == RENDER_CONTRACT_VERSION
    assert result.payload["render_contract_version"] == RENDER_CONTRACT_VERSION
    assert result.payload["primitive_source_provenance"]["producer_id"] == "ezdxf_scene_pack"


def test_real_scene_pack_pipeline_resolves_as_ezdxf_native_branch_stays_dormant(
    tmp_path: Path,
) -> None:
    """Honest wiring contract: the REAL producer emits ezdxf, never native.

    ``test_native_scene_pack_artifacts_resolve_as_first_class_source`` below
    proves the seam *classifies* a hand-written native artifact. This proves the
    native branch stays DORMANT in the real pipeline: ``build_scene_pack`` — the
    only ``overview_lod0`` producer wired into the folder-compare / viewer flow —
    never writes a native ``source_kind``, so a real drawing always resolves
    through the ezdxf producer. NativeScenePack rendering is fixture-validated
    only; it does not fire in production until a real-pipeline native producer
    is wired in (tracked as ``viewer_lod0_real_evidence_pending``).
    """

    ezdxf = pytest.importorskip("ezdxf")
    from src.services.comparison.scene_pack_builder import build_scene_pack

    src = tmp_path / "sample.dxf"
    doc = ezdxf.new("R2018", setup=True)
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 10))
    msp.add_line((10, 10), (20, 0))
    doc.saveas(str(src))

    ref = build_scene_pack(src, tmp_path / "pack").scene_pack_ref
    overview = json.loads(Path(ref.overview_lod0_path).read_text(encoding="utf-8"))

    # The real producer must not masquerade as native.
    assert str(overview.get("source_kind") or "") != "native_cad"
    assert "native_scene_pack" not in overview

    # The seam the viewport actually calls classifies the real artifact as the
    # ezdxf producer — the native branch never fires for a real drawing.
    resolved = resolve_viewer_primitive_source(ref)
    assert resolved.ok is True
    assert resolved.provenance["producer_id"] == "ezdxf_scene_pack"


def test_native_scene_pack_artifacts_resolve_as_first_class_source(tmp_path: Path) -> None:
    pack = NativeScenePack(
        source={"path": str(tmp_path / "native.dwg"), "acad_version": "AC1015"},
        adapter={"name": "fixture"},
        display_primitives=[
            {"type": "line", "points": [1.0, 2.0, 3.0, 4.0]},
        ],
        bbox=(1.0, 2.0, 3.0, 4.0),
    )

    ref = write_native_scene_pack_artifacts(pack, tmp_path / "native_pack")
    result = resolve_viewer_primitive_source(ref)

    assert result.ok is True
    assert ref.primitive_count == 1
    assert result.provenance["producer_id"] == "native_scene_pack"
    assert result.primitives == pack.display_primitives
    assert result.payload["native_scene_pack"]["adapter"]["name"] == "fixture"


def test_missing_overview_is_badged_fail_closed(tmp_path: Path) -> None:
    result = resolve_viewer_primitive_source(
        ScenePackRef(overview_lod0_path=str(tmp_path / "missing.json"))
    )

    assert result.ok is False
    assert result.degraded is True
    assert result.render_mode == "render_failed"
    assert result.error_code == "OVERVIEW_LOD0_MISSING"
    assert result.provenance["failure_badge"] == "OVERVIEW_LOD0_MISSING"


def test_degenerate_overview_bbox_is_expanded_not_discarded(tmp_path: Path) -> None:
    overview = tmp_path / "overview_lod0.json"
    overview.write_text(
        json.dumps(
            {
                "world_bbox": [100.0, 200.0, 100.0, 200.0],
                "primitives": [{"type": "line", "points": [100.0, 200.0, 100.0, 200.0]}],
            }
        ),
        encoding="utf-8",
    )

    result = resolve_viewer_primitive_source(
        ScenePackRef(overview_lod0_path=str(overview))
    )

    assert result.ok is True
    assert result.world_bbox == (99.5, 199.5, 100.5, 200.5)


def test_missing_scene_pack_is_explicit_relative_only_fallback() -> None:
    result = resolve_viewer_primitive_source(None, empty_notice="empty")

    assert result.ok is False
    assert result.render_mode == "relative_only"
    assert result.error_code == "NO_SCENE_PACK"
    assert result.provenance["producer_id"] == "relative_only"
    assert result.provenance["render_contract_schema_version"] == render_contract_schema_version()
