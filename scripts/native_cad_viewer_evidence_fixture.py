"""Write a deterministic native-CAD lightweight-viewer evidence artifact."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.comparison.drawing_compare_engine import DrawingCompareEngine  # noqa: E402
from src.services.comparison.dwg_importer import DwgImporter  # noqa: E402
from src.services.comparison.native_cad_importer import NativeCadBridgeAdapter  # noqa: E402
from src.services.comparison.native_scene_pack import native_scene_viewer_evidence_payload  # noqa: E402


DEFAULT_CODE = "AC1032"
DEFAULT_ARTIFACT_PATH = Path(".local/native_cad_viewer_evidence/fixture_pair_evidence.json")
DEFAULT_FIXTURE_DIR = Path(".local/native_cad_viewer_evidence/fixtures/AC1032")
FIXTURE_BRIDGE_PATH = Path("tools/native_cad_fixture_bridge.py")


def build_fixture_viewer_evidence(
    *,
    code: str = DEFAULT_CODE,
    fixture_dir: Path = DEFAULT_FIXTURE_DIR,
    output_path: Path | None = DEFAULT_ARTIFACT_PATH,
    primitive_budget: int = 5000,
    payload_byte_budget: int = 2_000_000,
) -> dict[str, Any]:
    before = fixture_dir / "before.dwg"
    after = fixture_dir / "after_r1.dwg"
    _write_fixture_dwg(before, code)
    _write_fixture_dwg(after, code)

    adapter = NativeCadBridgeAdapter(
        command=sys.executable,
        args_template=(str(REPO_ROOT / FIXTURE_BRIDGE_PATH), "{input}", "{acadver}"),
        supported_versions=(code,),
        name="native-cad-viewer-evidence-fixture",
        version="1",
        timeout_seconds=120.0,
        adapter_id="native-cad-viewer-evidence-fixture",
    )
    before_doc = DwgImporter(adapter=adapter).import_file(before)
    after_doc = DwgImporter(adapter=adapter).import_file(after)
    diff = DrawingCompareEngine().compare(before_doc, after_doc)
    selected_change = _primary_modified_change(diff.to_dict())
    if selected_change is None:
        raise RuntimeError("fixture pair did not produce a modified change for viewer evidence")

    evidence = native_scene_viewer_evidence_payload(
        after_doc["metadata"]["adapter_metadata"]["native_scene_pack"],
        change_overlays=[
            {
                "zone_id": selected_change["change_id"],
                "change_type": selected_change["change_type"],
                "priority_rank": 1,
                "old_bbox": selected_change.get("old_bbox"),
                "bbox": selected_change.get("new_bbox") or selected_change.get("bbox"),
            }
        ],
        import_report=after_doc.get("import_report") or {},
        primitive_budget=primitive_budget,
        payload_byte_budget=payload_byte_budget,
    )
    evidence["artifact"] = {
        "generator": "scripts/native_cad_viewer_evidence_fixture.py",
        "fixture_code": code,
        "before_fixture": str(before),
        "after_fixture": str(after),
        "output_path": str(output_path or ""),
    }
    evidence["comparison"] = {
        "summary": diff.summary,
        "selected_change_id": selected_change["change_id"],
        "selected_change_type": selected_change["change_type"],
    }
    evidence["policy"] = {
        "fixture_evidence_only": True,
        "default_support_expanded": False,
        "broad_dwg_support_claim": False,
    }

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return evidence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", default=DEFAULT_CODE)
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_ARTIFACT_PATH)
    parser.add_argument("--primitive-budget", type=int, default=5000)
    parser.add_argument("--payload-byte-budget", type=int, default=2_000_000)
    args = parser.parse_args(argv)

    evidence = build_fixture_viewer_evidence(
        code=args.code,
        fixture_dir=args.fixture_dir,
        output_path=args.output,
        primitive_budget=args.primitive_budget,
        payload_byte_budget=args.payload_byte_budget,
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    frame_status = (evidence.get("primary_change_frame") or {}).get("status")
    import_status = (evidence.get("import_report") or {}).get("status")
    viewer = evidence.get("viewer") or {}
    ok = (
        evidence.get("schema_version") == "native-cad-viewer-evidence/v1"
        and frame_status == "framed"
        and import_status == "ok"
        and viewer.get("within_primitive_budget") is True
        and viewer.get("within_payload_byte_budget") is True
    )
    return 0 if ok else 1


def _primary_modified_change(diff_payload: dict[str, Any]) -> dict[str, Any] | None:
    for change in diff_payload.get("changes") or []:
        if isinstance(change, dict) and change.get("change_type") == "modified":
            return change
    return None


def _write_fixture_dwg(path: Path, code: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(str(code).encode("ascii") + b"\nviewer-evidence:" + path.stem.encode("ascii") + b"\n")


if __name__ == "__main__":
    raise SystemExit(main())
