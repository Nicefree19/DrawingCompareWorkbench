"""Generate product-path DWG JSON bridge evidence through cad_compare.

This runner executes the public ``src.cli.cad_compare file`` entrypoint for
DWG before/after pairs from an ADR-004 sample pack.  It is intentionally a
product-path check: native/commercial claims should not rely only on internal
adapter or contract probes.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import validate_adr004_version_sample_pack as sample_pack_validator  # noqa: E402
from src.services.comparison.dwg_backend import DWG_BACKEND_COMMERCIAL_SDK  # noqa: E402


SCHEMA_VERSION = "dwg-product-bridge-evidence-run/v1"
DEFAULT_ADAPTER_SPEC = "src.services.comparison.commercial_dwg_json_adapter:create_adapter"
DEFAULT_OUTPUT_DIR = Path("build/reports/dwg-product-bridge-evidence")
DEFAULT_SUMMARY_JSON = Path("build/reports/dwg-product-bridge-evidence.json")
ACCEPTED_COMPARE_STATUSES = {"ok", "partial"}
NATIVE_BRIDGE_EVIDENCE_SCOPES = {
    "native_dwg",
    "native_dwg_bridge",
    "commercial_dwg_native",
    "commercial_native",
    "commercial_sdk_native",
}


@dataclass(frozen=True)
class DwgPair:
    version: str
    before: Path
    after: Path
    index: int


def run_evidence(
    sample_pack: Path,
    *,
    adapter_spec: str = DEFAULT_ADAPTER_SPEC,
    allowed_dwg_license_ids: Sequence[str] = ("MIT", "INTERNAL"),
    bridge_command: str | None = None,
    bridge_args_json: str | None = None,
    bridge_license_id: str | None = None,
    bridge_supported_versions: str | None = None,
    bridge_timeout_seconds: float | None = None,
    pair_timeout_seconds: float = 300.0,
    max_entities: int | None = None,
    max_dxf_tokens: int | None = None,
    import_timeout_seconds: float | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    summary_json: Path = DEFAULT_SUMMARY_JSON,
    python_executable: str = sys.executable,
    cad_compare_module: str = "src.cli.cad_compare",
    only_versions: set[str] | None = None,
    max_pairs_per_version: int | None = None,
) -> dict[str, Any]:
    sample_pack = _resolve(sample_pack)
    output_dir = _resolve(output_dir)
    summary_json = _resolve(summary_json)
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_path, manifest, manifest_errors = _load_manifest(sample_pack)
    requested = {str(code).upper() for code in only_versions or set()}
    pairs, pair_errors = _collect_pairs(
        manifest_path,
        manifest,
        requested_versions=requested,
        max_pairs_per_version=max_pairs_per_version,
    )
    config_errors = _bridge_config_errors(
        bridge_command=bridge_command,
        bridge_args_json=bridge_args_json,
        bridge_license_id=bridge_license_id,
        bridge_supported_versions=bridge_supported_versions,
    )

    records: list[dict[str, Any]] = []
    if not config_errors:
        for pair in pairs:
            records.append(
                _run_pair(
                    pair,
                    adapter_spec=adapter_spec,
                    allowed_dwg_license_ids=_dedupe(("MIT", "INTERNAL", *allowed_dwg_license_ids)),
                    bridge_command=str(bridge_command),
                    bridge_args_json=str(bridge_args_json),
                    bridge_license_id=str(bridge_license_id),
                    bridge_supported_versions=str(bridge_supported_versions),
                    bridge_timeout_seconds=bridge_timeout_seconds,
                    pair_timeout_seconds=pair_timeout_seconds,
                    max_entities=max_entities,
                    max_dxf_tokens=max_dxf_tokens,
                    import_timeout_seconds=import_timeout_seconds,
                    output_dir=output_dir,
                    python_executable=python_executable,
                    cad_compare_module=cad_compare_module,
                )
            )

    errors = [*manifest_errors, *pair_errors, *config_errors]
    failed_records = [record for record in records if not _record_passed(record)]
    status = "passed" if not errors and records and not failed_records else "failed"
    bridge_reports = [
        report
        for record in records
        for report in (record.get("bridge_adapter_reports") or [])
        if isinstance(report, dict)
    ]
    versions = sorted({pair.version for pair in pairs})
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(),
        "status": status,
        "mode": "cad_compare",
        "command": "cad_compare",
        "entrypoint": cad_compare_module,
        "dwg_backend_mode": DWG_BACKEND_COMMERCIAL_SDK,
        "explicit": True,
        "customer_path": False,
        "implementation_status": "json_bridge_configured",
        "license_id": bridge_license_id or "",
        "allowed_license_ids": list(_dedupe(("MIT", "INTERNAL", *allowed_dwg_license_ids))),
        "sample_pack": str(sample_pack),
        "manifest_path": str(manifest_path) if manifest_path else "",
        "output_dir": str(output_dir),
        "summary_json": str(summary_json),
        "accepted_compare_statuses": sorted(ACCEPTED_COMPARE_STATUSES),
        "bridge": {
            "adapter_spec": adapter_spec,
            "license_id": bridge_license_id or "",
            "supported_versions": _split_versions(bridge_supported_versions),
            "timeout_seconds": bridge_timeout_seconds,
            "command": bridge_command or "",
            "args_json": bridge_args_json or "",
        },
        "process_cleanup": {
            "orphan_processes": 0,
            "pair_timeout_seconds": pair_timeout_seconds,
        },
        "provenance": {
            "original_sample_pack": str(sample_pack),
            "effective_output_dir": str(output_dir),
            "effective_summary_json": str(summary_json),
            "selected_dwg_backend_mode": DWG_BACKEND_COMMERCIAL_SDK,
        },
        "summary": {
            "version_count": len(versions),
            "pair_count": len(pairs),
            "executed_pair_count": len(records),
            "passed_pair_count": sum(1 for record in records if _record_passed(record)),
            "failed_pair_count": len(failed_records),
            "bridge_evidence_pair_count": sum(1 for record in records if record.get("bridge_evidence_present")),
            "bridge_adapter_report_count": len(bridge_reports),
            "manifest_error_count": len(manifest_errors),
            "pair_error_count": len(pair_errors),
            "config_error_count": len(config_errors),
            "versions": versions,
        },
        "diagnostic_errors": errors,
        "pairs": records,
        "bridge_adapter_reports": bridge_reports,
    }
    _write_json(summary_json, report)
    return report


def _run_pair(
    pair: DwgPair,
    *,
    adapter_spec: str,
    allowed_dwg_license_ids: Sequence[str],
    bridge_command: str,
    bridge_args_json: str,
    bridge_license_id: str,
    bridge_supported_versions: str,
    bridge_timeout_seconds: float | None,
    pair_timeout_seconds: float,
    max_entities: int | None,
    max_dxf_tokens: int | None,
    import_timeout_seconds: float | None,
    output_dir: Path,
    python_executable: str,
    cad_compare_module: str,
) -> dict[str, Any]:
    output_json = output_dir / f"{pair.version.lower()}_{pair.index:03d}.json"
    cmd = [
        python_executable,
        "-m",
        cad_compare_module,
        "file",
        str(pair.before),
        str(pair.after),
        "--dwg-backend",
        DWG_BACKEND_COMMERCIAL_SDK,
        "--dwg-commercial-adapter-spec",
        adapter_spec,
        "--dwg-bridge-command",
        bridge_command,
        "--dwg-bridge-args-json",
        bridge_args_json,
        "--dwg-bridge-license-id",
        bridge_license_id,
        "--dwg-bridge-supported-versions",
        bridge_supported_versions,
        "--output",
        str(output_json),
    ]
    for license_id in allowed_dwg_license_ids:
        cmd.extend(["--dwg-allowed-license-id", str(license_id)])
    if bridge_timeout_seconds is not None:
        cmd.extend(["--dwg-bridge-timeout-seconds", str(bridge_timeout_seconds)])
    if import_timeout_seconds is not None:
        cmd.extend(["--import-timeout", str(import_timeout_seconds)])
    if max_entities is not None:
        cmd.extend(["--max-entities", str(max_entities)])
    if max_dxf_tokens is not None:
        cmd.extend(["--max-dxf-tokens", str(max_dxf_tokens)])

    timed_out = False
    try:
        completed = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=pair_timeout_seconds,
            check=False,
        )
        returncode = completed.returncode
        stdout_tail = _tail(completed.stdout)
        stderr_tail = _tail(completed.stderr)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = None
        stdout_tail = _tail(exc.stdout)
        stderr_tail = _tail(exc.stderr)

    payload = _load_json(output_json)
    bridge_reports = _json_bridge_nodes(payload) if isinstance(payload, dict) else []
    bridge_metadata = _json_bridge_metadata_nodes(payload) if isinstance(payload, dict) else []
    bridge_errors = [
        *_bridge_evidence_errors(bridge_reports),
        *_bridge_native_provenance_errors(bridge_metadata),
    ]
    compare_status = str(payload.get("status") or "missing") if isinstance(payload, dict) else "missing"
    diagnostic_errors: list[str] = []
    if timed_out:
        diagnostic_errors.append("cad_compare_pair_timeout")
    if returncode not in (0,):
        diagnostic_errors.append(f"cad_compare_exit_code={returncode}")
    if compare_status not in ACCEPTED_COMPARE_STATUSES:
        diagnostic_errors.append(f"cad_compare_status={compare_status}")
    diagnostic_errors.extend(bridge_errors)
    return {
        "version": pair.version,
        "pair_index": pair.index,
        "status": "passed" if not diagnostic_errors else "failed",
        "cad_compare_status": compare_status,
        "exit_code": returncode,
        "timed_out": timed_out,
        "source_a": str(pair.before),
        "source_b": str(pair.after),
        "provenance": {
            "original_before_dwg": str(pair.before),
            "original_after_dwg": str(pair.after),
            "effective_result_json": str(output_json),
            "selected_dwg_backend_mode": DWG_BACKEND_COMMERCIAL_SDK,
        },
        "output_json": str(output_json),
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "bridge_evidence_present": not bridge_errors and bool(bridge_reports),
        "bridge_native_provenance_present": bool(bridge_metadata) and not _bridge_native_provenance_errors(bridge_metadata),
        "bridge_adapter_metadata": bridge_metadata,
        "bridge_adapter_reports": bridge_reports,
        "diagnostic_errors": diagnostic_errors,
    }


def _load_manifest(sample_pack: Path) -> tuple[Path | None, dict[str, Any], list[str]]:
    try:
        manifest_path, manifest = sample_pack_validator.load_manifest(sample_pack)
    except Exception as exc:
        return None, {}, [f"sample_pack_manifest_unreadable: {type(exc).__name__}: {exc}"]
    errors = sample_pack_validator.validate_manifest(manifest)
    return manifest_path, manifest, errors


def _collect_pairs(
    manifest_path: Path | None,
    manifest: dict[str, Any],
    *,
    requested_versions: set[str],
    max_pairs_per_version: int | None,
) -> tuple[list[DwgPair], list[str]]:
    if manifest_path is None:
        return [], []
    if max_pairs_per_version is not None and max_pairs_per_version <= 0:
        return [], ["max_pairs_per_version must be greater than 0"]
    versions = manifest.get("versions") if isinstance(manifest.get("versions"), dict) else {}
    errors: list[str] = []
    pairs: list[DwgPair] = []
    for missing in sorted(requested_versions - {str(code).upper() for code in versions}):
        errors.append(f"requested version not found in manifest: {missing}")
    pair_index = 1
    for code, item in sorted(versions.items()):
        version = str(code).upper()
        if requested_versions and version not in requested_versions:
            continue
        before = _resolve_manifest_path(manifest_path, item.get("sample_before_dwg"))
        after = _resolve_manifest_path(manifest_path, item.get("sample_after_dwg"))
        version_errors = []
        if before is None:
            version_errors.append(f"{version}.sample_before_dwg is missing")
        elif not before.exists():
            version_errors.append(f"{version}.sample_before_dwg missing: {before}")
        if after is None:
            version_errors.append(f"{version}.sample_after_dwg is missing")
        elif not after.exists():
            version_errors.append(f"{version}.sample_after_dwg missing: {after}")
        if version_errors:
            errors.extend(version_errors)
            continue
        pairs.append(DwgPair(version=version, before=before, after=after, index=pair_index))
        pair_index += 1
    if not pairs:
        errors.append("sample pack contains no DWG before/after pairs for product bridge evidence")
    return pairs, errors


def _resolve_manifest_path(manifest_path: Path, raw_path: Any) -> Path | None:
    if not raw_path:
        return None
    path = Path(str(raw_path))
    return path if path.is_absolute() else manifest_path.parent / path


def _bridge_config_errors(
    *,
    bridge_command: str | None,
    bridge_args_json: str | None,
    bridge_license_id: str | None,
    bridge_supported_versions: str | None,
) -> list[str]:
    errors = []
    if not str(bridge_command or "").strip():
        errors.append("bridge_command_missing")
    if not str(bridge_args_json or "").strip():
        errors.append("bridge_args_json_missing")
    else:
        try:
            parsed = json.loads(str(bridge_args_json))
        except json.JSONDecodeError:
            errors.append("bridge_args_json_invalid")
        else:
            if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
                errors.append("bridge_args_json_must_be_string_array")
    if not str(bridge_license_id or "").strip():
        errors.append("bridge_license_id_missing")
    if not _split_versions(bridge_supported_versions):
        errors.append("bridge_supported_versions_missing")
    return errors


def _record_passed(record: dict[str, Any]) -> bool:
    return (
        record.get("status") == "passed"
        and record.get("exit_code") == 0
        and str(record.get("cad_compare_status") or "") in ACCEPTED_COMPARE_STATUSES
        and bool(record.get("bridge_evidence_present"))
    )


def _json_bridge_nodes(payload: Any) -> list[dict[str, Any]]:
    nodes = []
    for node in _dict_nodes(payload):
        diagnostics = node.get("diagnostics")
        if (
            isinstance(diagnostics, dict)
            and diagnostics.get("kind") == "commercial_dwg_json_bridge"
            and _is_bridge_adapter_report(node)
        ):
            nodes.append(node)
    return nodes


def _is_bridge_adapter_report(node: dict[str, Any]) -> bool:
    return (
        "implementation_status" in node
        or "approval_required" in node
        or str(node.get("name") or "") == "commercial-dwg-json-bridge"
    )


def _json_bridge_metadata_nodes(payload: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    seen: set[int] = set()
    for node in _dict_nodes(payload):
        candidate = node.get("commercial_dwg_json_bridge")
        if not isinstance(candidate, dict) and _is_bridge_metadata_node(node):
            candidate = node
        if not isinstance(candidate, dict):
            continue
        ident = id(candidate)
        if ident in seen:
            continue
        seen.add(ident)
        nodes.append(candidate)
    return nodes


def _is_bridge_metadata_node(node: dict[str, Any]) -> bool:
    return any(
        key in node
        for key in (
            "evidence_scope",
            "source_kind",
            "uses_native_dwg",
            "uses_converted_dxf",
            "converted_dxf_path",
            "effective_dxf_path",
        )
    )


def _bridge_evidence_errors(nodes: Sequence[dict[str, Any]]) -> list[str]:
    if not nodes:
        return ["commercial_dwg_json_bridge_diagnostics_missing"]
    errors: list[str] = []
    for index, node in enumerate(nodes):
        diagnostics = node.get("diagnostics") if isinstance(node.get("diagnostics"), dict) else {}
        if diagnostics.get("kind") != "commercial_dwg_json_bridge":
            errors.append(f"bridge_nodes[{index}].diagnostics.kind={diagnostics.get('kind')!r}")
        if not bool(diagnostics.get("command_exists")):
            errors.append(f"bridge_nodes[{index}].diagnostics.command_exists=false")
        if not str(diagnostics.get("command_sha256") or "").strip():
            errors.append(f"bridge_nodes[{index}].diagnostics.command_sha256=missing")
        if not _split_versions(diagnostics.get("supported_versions")):
            errors.append(f"bridge_nodes[{index}].diagnostics.supported_versions=missing")
        implementation_status = str(node.get("implementation_status") or "")
        if implementation_status in {"", "placeholder", "plugin_load_failed"}:
            errors.append(f"bridge_nodes[{index}].implementation_status={implementation_status or 'missing'}")
        license_id = str(node.get("license_id") or diagnostics.get("license_id") or "")
        if not license_id or license_id == "COMMERCIAL-SDK-PENDING":
            errors.append(f"bridge_nodes[{index}].license_id={license_id or 'missing'}")
        backend_mode = str(node.get("backend_mode") or node.get("dwg_backend_mode") or "")
        if backend_mode != DWG_BACKEND_COMMERCIAL_SDK:
            errors.append(f"bridge_nodes[{index}].backend_mode={backend_mode or 'missing'}")
    return errors


def _bridge_native_provenance_errors(nodes: Sequence[dict[str, Any]]) -> list[str]:
    if not nodes:
        return ["commercial_dwg_json_bridge_native_provenance_missing"]
    errors: list[str] = []
    for index, node in enumerate(nodes):
        if _bridge_marks_converted_dxf(node):
            errors.append(f"bridge_metadata[{index}].uses_converted_dxf=true")
        if not _bridge_marks_native_dwg(node):
            scope = str(node.get("evidence_scope") or node.get("source_kind") or "").strip()
            errors.append(f"bridge_metadata[{index}].native_evidence_scope={scope or 'missing'}")
    return errors


def _bridge_marks_converted_dxf(node: dict[str, Any]) -> bool:
    if _truthy(node.get("uses_converted_dxf")):
        return True
    if node.get("converted_dxf_path") or node.get("effective_dxf_path"):
        return True
    scope = str(node.get("evidence_scope") or node.get("source_kind") or "").strip().lower()
    return any(marker in scope for marker in ("fallback", "converted_dxf", "dxf", "oda", "user_converter"))


def _bridge_marks_native_dwg(node: dict[str, Any]) -> bool:
    if _truthy(node.get("uses_native_dwg")):
        return True
    scope = str(node.get("evidence_scope") or node.get("source_kind") or "").strip().lower()
    return scope in NATIVE_BRIDGE_EVIDENCE_SCOPES


def _dict_nodes(value: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    if isinstance(value, dict):
        nodes.append(value)
        for child in value.values():
            nodes.extend(_dict_nodes(child))
    elif isinstance(value, list):
        for child in value:
            nodes.extend(_dict_nodes(child))
    return nodes


def _split_versions(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw = [str(item) for item in value]
    else:
        raw = str(value or "").replace(";", ",").split(",")
    return sorted({item.strip().upper() for item in raw if item.strip()})


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _tail(value: Any, *, limit: int = 4000) -> str:
    if value is None:
        return ""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
    return text[-limit:]


def _dedupe(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in result:
            result.append(item)
    return tuple(result)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sample_pack", type=Path)
    parser.add_argument("--adapter-spec", default=DEFAULT_ADAPTER_SPEC)
    parser.add_argument("--dwg-allowed-license-id", action="append", default=None)
    parser.add_argument("--bridge-command")
    parser.add_argument("--bridge-args-json")
    parser.add_argument("--bridge-license-id")
    parser.add_argument("--bridge-supported-versions")
    parser.add_argument("--bridge-timeout-seconds", type=float)
    parser.add_argument("--pair-timeout-seconds", type=float, default=300.0)
    parser.add_argument("--max-entities", type=int)
    parser.add_argument("--max-dxf-tokens", type=int)
    parser.add_argument("--import-timeout-seconds", type=float)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--cad-compare-module", default="src.cli.cad_compare")
    parser.add_argument("--version", action="append", default=None)
    parser.add_argument("--max-pairs-per-version", type=int)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_evidence(
        args.sample_pack,
        adapter_spec=args.adapter_spec,
        allowed_dwg_license_ids=tuple(args.dwg_allowed_license_id or ()),
        bridge_command=args.bridge_command,
        bridge_args_json=args.bridge_args_json,
        bridge_license_id=args.bridge_license_id,
        bridge_supported_versions=args.bridge_supported_versions,
        bridge_timeout_seconds=args.bridge_timeout_seconds,
        pair_timeout_seconds=args.pair_timeout_seconds,
        max_entities=args.max_entities,
        max_dxf_tokens=args.max_dxf_tokens,
        import_timeout_seconds=args.import_timeout_seconds,
        output_dir=args.output_dir,
        summary_json=args.summary_json,
        python_executable=args.python_executable,
        cad_compare_module=args.cad_compare_module,
        only_versions={str(code).upper() for code in args.version or []} or None,
        max_pairs_per_version=args.max_pairs_per_version,
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
