"""Generate a native CAD version-row evidence packet."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jsonschema import Draft202012Validator  # noqa: E402

from scripts.native_cad_version_matrix import DEFAULT_MATRIX_PATH, load_matrix  # noqa: E402
from src.services.comparison.drawing_compare_engine import DrawingCompareEngine  # noqa: E402
from src.services.comparison.dwg_importer import DwgImporter, DwgVersionDetector  # noqa: E402
from src.services.comparison.native_cad_importer import NativeCadBridgeAdapter  # noqa: E402


CANONICAL_SCHEMA_PATH = Path("docs/canonical-drawing.schema.json")
DEFAULT_FIXTURE_ROOT = Path(".local/native_cad_fixture_rows")
DEFAULT_ARTIFACT_ROOT = Path(".local/native_cad_evidence")
FIXTURE_BRIDGE_PATH = Path("tools/native_cad_fixture_bridge.py")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", required=True)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--after", type=Path)
    parser.add_argument("--bridge-command")
    parser.add_argument("--bridge-args-json", default="")
    parser.add_argument("--fixture-row", action="store_true")
    parser.add_argument("--fixture-root", default=str(DEFAULT_FIXTURE_ROOT))
    parser.add_argument("--artifact-dir", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--matrix", default=str(DEFAULT_MATRIX_PATH))
    parser.add_argument("--lod0-primitive-budget", type=int, default=5000)
    parser.add_argument("--json-out")
    args = parser.parse_args(argv)

    if args.fixture_row:
        json_out = Path(args.json_out) if args.json_out else Path(args.artifact_dir) / f"{args.code}.json"
        packet = build_fixture_evidence_packet(
            code=args.code,
            fixture_root=Path(args.fixture_root),
            artifact_path=json_out,
            matrix_path=Path(args.matrix),
            lod0_primitive_budget=args.lod0_primitive_budget,
        )
    else:
        if args.before is None or args.after is None or not args.bridge_command:
            parser.error("--before, --after, and --bridge-command are required unless --fixture-row is set")
        packet = build_evidence_packet(
            code=args.code,
            before=args.before,
            after=args.after,
            bridge_command=args.bridge_command,
            bridge_args_template=_args_template(args.bridge_args_json),
            matrix_path=Path(args.matrix),
            lod0_primitive_budget=args.lod0_primitive_budget,
        )
        json_out = Path(args.json_out) if args.json_out else None
    text = json.dumps(packet, ensure_ascii=False, indent=2)
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if packet["status"] == "PASS" else 1


def build_evidence_packet(
    *,
    code: str,
    before: Path,
    after: Path,
    bridge_command: str,
    bridge_args_template: Sequence[str],
    matrix_path: Path = DEFAULT_MATRIX_PATH,
    lod0_primitive_budget: int = 5000,
) -> dict[str, Any]:
    matrix = load_matrix(matrix_path)
    row = _row_for_code(matrix, code)
    version = DwgVersionDetector.detect_file(before)
    if version.code != code:
        return _failed_packet(code, row, f"before file header {version.code!r} does not match {code!r}")
    after_version = DwgVersionDetector.detect_file(after)
    if after_version.code != code:
        return _failed_packet(code, row, f"after file header {after_version.code!r} does not match {code!r}")

    adapter = NativeCadBridgeAdapter(
        command=bridge_command,
        args_template=tuple(bridge_args_template),
        supported_versions=(code,),
        name=f"native-cad-evidence-{code.lower()}",
        timeout_seconds=30.0,
        adapter_id=f"native-cad-evidence-{code.lower()}",
    )
    before_doc = DwgImporter(adapter=adapter).import_file(before)
    after_doc = DwgImporter(adapter=adapter).import_file(after)
    schema_result = {
        "before": _schema_errors(before_doc),
        "after": _schema_errors(after_doc),
    }
    compare = DrawingCompareEngine().compare(before_doc, after_doc)
    before_meta = before_doc.get("metadata", {}).get("adapter_metadata", {})
    after_meta = after_doc.get("metadata", {}).get("adapter_metadata", {})
    before_bridge = before_meta.get("native_cad_bridge", {})
    after_bridge = after_meta.get("native_cad_bridge", {})
    before_lod0 = before_meta.get("native_scene_overview_lod0", {})
    after_lod0 = after_meta.get("native_scene_overview_lod0", {})
    packet = {
        "schema_version": 1,
        "status": "PASS",
        "code": code,
        "release_family": row.get("release_family"),
        "matrix_state": row.get("state"),
        "bridge_adapter": adapter.name,
        "license_id": adapter.license_id,
        "sample_corpus": {
            "before": _source_summary(before_doc),
            "after": _source_summary(after_doc),
        },
        "canonical_schema_result": {
            "before_valid": not schema_result["before"],
            "after_valid": not schema_result["after"],
            "errors": schema_result,
        },
        "compare_expected_result": {
            "summary": compare.summary,
            "has_modified_text": _has_modified_text(compare.to_dict()),
        },
        "viewer_lod0_budget": {
            "budget": lod0_primitive_budget,
            "before_primitive_count": before_lod0.get("primitive_count"),
            "after_primitive_count": after_lod0.get("primitive_count"),
            "before_within_budget": int(before_lod0.get("primitive_count") or 0) <= lod0_primitive_budget,
            "after_within_budget": int(after_lod0.get("primitive_count") or 0) <= lod0_primitive_budget,
            "before_world_bbox": before_lod0.get("world_bbox"),
            "after_world_bbox": after_lod0.get("world_bbox"),
        },
        "unsupported_entities": {
            "before": before_doc.get("import_report", {}).get("unsupported_entities", []),
            "after": after_doc.get("import_report", {}).get("unsupported_entities", []),
        },
        "failure_code_tests": {
            "structured_bridge_failures": True,
            "not_exercised_by_packet": ["timeout", "encrypted", "corrupted"],
        },
        "cache_identity_fields": {
            "before": _cache_identity_summary(before_bridge),
            "after": _cache_identity_summary(after_bridge),
            "fingerprints_differ_for_different_sources": (
                _cache_fingerprint(before_bridge) != _cache_fingerprint(after_bridge)
            ),
        },
        "policy_gate_result": "not_run_by_packet",
        "fallback_test_result": "not_run_by_packet",
        "promotion_decision": "evidence_packet_only",
    }
    if not packet["canonical_schema_result"]["before_valid"] or not packet["canonical_schema_result"]["after_valid"]:
        packet["status"] = "FAIL"
    if not packet["compare_expected_result"]["has_modified_text"]:
        packet["status"] = "FAIL"
    if not packet["viewer_lod0_budget"]["before_within_budget"] or not packet["viewer_lod0_budget"]["after_within_budget"]:
        packet["status"] = "FAIL"
    if not packet["cache_identity_fields"]["fingerprints_differ_for_different_sources"]:
        packet["status"] = "FAIL"
    return packet


def build_fixture_evidence_packet(
    *,
    code: str,
    fixture_root: Path = DEFAULT_FIXTURE_ROOT,
    artifact_path: Path | None = None,
    matrix_path: Path = DEFAULT_MATRIX_PATH,
    lod0_primitive_budget: int = 5000,
) -> dict[str, Any]:
    row_dir = fixture_root / code
    before = row_dir / "before.dwg"
    after = row_dir / "after_r1.dwg"
    _write_fixture_dwg(before, code)
    _write_fixture_dwg(after, code)
    packet = build_evidence_packet(
        code=code,
        before=before,
        after=after,
        bridge_command=sys.executable,
        bridge_args_template=(str(REPO_ROOT / FIXTURE_BRIDGE_PATH), "{input}", "{acadver}"),
        matrix_path=matrix_path,
        lod0_primitive_budget=lod0_primitive_budget,
    )
    packet["sample_corpus"]["origin"] = "local_fixture_row"
    packet["sample_corpus"]["fixture_root"] = str(row_dir)
    packet["promotion_decision"] = "fixture_evidence_only"
    if artifact_path is not None:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return packet


def _failed_packet(code: str, row: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "FAIL",
        "code": code,
        "release_family": row.get("release_family"),
        "matrix_state": row.get("state"),
        "reason": reason,
    }


def _row_for_code(matrix: Mapping[str, Any], code: str) -> Mapping[str, Any]:
    for row in matrix.get("rows", []):
        if isinstance(row, Mapping) and row.get("code") == code:
            return row
    raise ValueError(f"{code!r} is not present in the native CAD version matrix.")


def _schema_errors(doc: dict[str, Any]) -> list[str]:
    schema = json.loads((REPO_ROOT / CANONICAL_SCHEMA_PATH).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    return [f"{list(error.path)}: {error.message}" for error in sorted(validator.iter_errors(doc), key=str)[:10]]


def _source_summary(doc: Mapping[str, Any]) -> dict[str, Any]:
    source = ((doc.get("drawing") or {}).get("source") or {}) if isinstance(doc, Mapping) else {}
    return {
        "path": source.get("path"),
        "file_name": source.get("file_name"),
        "sha256": source.get("sha256"),
        "acad_version": source.get("acad_version"),
        "status": (doc.get("import_report") or {}).get("status") if isinstance(doc, Mapping) else None,
    }


def _cache_identity_summary(bridge_metadata: Mapping[str, Any]) -> dict[str, Any]:
    identity = bridge_metadata.get("cache_identity") if isinstance(bridge_metadata, Mapping) else {}
    if not isinstance(identity, Mapping):
        return {"present": False}
    return {
        "present": True,
        "schema_version": identity.get("schema_version"),
        "fingerprint": identity.get("fingerprint"),
        "fields": sorted(key for key in identity if key != "fingerprint"),
    }


def _cache_fingerprint(bridge_metadata: Mapping[str, Any]) -> str:
    identity = bridge_metadata.get("cache_identity") if isinstance(bridge_metadata, Mapping) else {}
    return str(identity.get("fingerprint") or "") if isinstance(identity, Mapping) else ""


def _has_modified_text(result: Mapping[str, Any]) -> bool:
    for change in result.get("changes", []):
        if not isinstance(change, Mapping) or change.get("change_type") != "modified":
            continue
        for field in (change.get("geometry_diff") or {}).get("fields", []):
            if isinstance(field, Mapping) and field.get("path") == "geometry.canonical_text":
                return True
    return False


def _args_template(raw: str) -> tuple[str, ...]:
    if not raw:
        return ("{input}", "{acadver}")
    parsed = json.loads(raw)
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise SystemExit("--bridge-args-json must be a JSON string array")
    return tuple(parsed)


def _write_fixture_dwg(path: Path, code: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(str(code).encode("ascii") + b"\nfixture:" + path.stem.encode("utf-8") + b"\n")


if __name__ == "__main__":
    raise SystemExit(main())
