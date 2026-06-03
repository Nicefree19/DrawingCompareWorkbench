"""Run approved native/commercial DWG backend validation end to end.

This orchestrates the ADR-004 sample-pack validator, all-version evidence
aggregation, and native-scope audit. It does not approve a backend by itself;
the selected backend must already be available and license-allowed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import audit_dwg_all_version_support as native_audit  # noqa: E402
from scripts import build_dwg_all_version_support_evidence as evidence_builder  # noqa: E402
from scripts import run_dwg_product_bridge_evidence as product_bridge_evidence  # noqa: E402
from scripts import validate_adr004_version_sample_pack as validate_sample_pack  # noqa: E402
from scripts import validate_dwg_json_bridge_contract as bridge_contract_validator  # noqa: E402
from src.services.comparison.dwg_backend import (  # noqa: E402
    COMMERCIAL_SDK_ADAPTER_ENV,
    DWG_BACKEND_COMMERCIAL_SDK,
    create_dwg_backend_selection,
    normalize_dwg_backend_mode,
)
from src.services.comparison.commercial_dwg_json_adapter import (  # noqa: E402
    ARGS_JSON_ENV,
    COMMAND_ENV,
    LICENSE_ID_ENV,
    SUPPORTED_VERSIONS_ENV,
    TIMEOUT_SECONDS_ENV,
)
from src.services.comparison.dwg_importer import DwgVersionDetector, DwgVersionInfo  # noqa: E402


DEFAULT_VALIDATION_JSON = Path("build/reports/dwg-native-backend-validation.json")
DEFAULT_VALIDATION_MD = Path("build/reports/dwg-native-backend-validation.md")
DEFAULT_EVIDENCE_JSON = Path("build/reports/dwg-native-backend-evidence.json")
DEFAULT_AUDIT_JSON = Path("build/reports/dwg-all-version-native-audit.json")
DEFAULT_BRIDGE_CONTRACT_JSON = Path("build/reports/dwg-json-bridge-contract.json")
DEFAULT_PRODUCT_EVIDENCE_JSON = Path("build/reports/dwg-product-bridge-evidence.json")
DEFAULT_PRODUCT_EVIDENCE_OUTPUT_DIR = Path("build/reports/dwg-product-bridge-evidence")


def run_validation(
    sample_pack: Path,
    *,
    dwg_backend_mode: str = DWG_BACKEND_COMMERCIAL_SDK,
    adapter_spec: str | None = None,
    allowed_dwg_license_ids: Sequence[str] = ("MIT", "INTERNAL"),
    validation_json: Path = DEFAULT_VALIDATION_JSON,
    validation_md: Path = DEFAULT_VALIDATION_MD,
    evidence_json: Path = DEFAULT_EVIDENCE_JSON,
    audit_json: Path = DEFAULT_AUDIT_JSON,
    bridge_contract_json: Path | None = None,
    bridge_command: str | None = None,
    bridge_args_json: str | None = None,
    bridge_license_id: str | None = None,
    bridge_supported_versions: str | None = None,
    bridge_timeout_seconds: float | None = None,
    product_evidence_json: Path | None = None,
    product_evidence_output_dir: Path = DEFAULT_PRODUCT_EVIDENCE_OUTPUT_DIR,
    product_pair_timeout_seconds: float = 300.0,
    product_max_pairs_per_version: int | None = None,
    max_entities: int = validate_sample_pack.DEFAULT_MAX_ENTITIES,
    max_dxf_tokens: int = validate_sample_pack.DEFAULT_MAX_DXF_TOKENS,
    import_timeout_seconds: float = validate_sample_pack.DEFAULT_IMPORT_TIMEOUT_SECONDS,
    compare_timeout_seconds: float = validate_sample_pack.DEFAULT_COMPARE_TIMEOUT_SECONDS,
    skip_compare_over_dxf_mb: float = validate_sample_pack.DEFAULT_SKIP_COMPARE_OVER_DXF_MB,
    only_versions: set[str] | None = None,
) -> dict[str, Any]:
    backend_mode = normalize_dwg_backend_mode(dwg_backend_mode)
    allowed = _dedupe(("MIT", "INTERNAL", *allowed_dwg_license_ids))
    sample_pack = _resolve(sample_pack)
    validation_json = _resolve(validation_json)
    validation_md = _resolve(validation_md)
    evidence_json = _resolve(evidence_json)
    audit_json = _resolve(audit_json)
    bridge_contract_json = _resolve(bridge_contract_json) if bridge_contract_json is not None else None
    product_evidence_json = _resolve(product_evidence_json) if product_evidence_json is not None else None
    product_evidence_output_dir = _resolve(product_evidence_output_dir)
    target_versions = tuple(sorted(only_versions)) if only_versions else native_audit.TARGET_DWG_CODES

    with _temporary_env(
        {
            COMMERCIAL_SDK_ADAPTER_ENV: adapter_spec,
            COMMAND_ENV: bridge_command,
            ARGS_JSON_ENV: bridge_args_json,
            LICENSE_ID_ENV: bridge_license_id,
            SUPPORTED_VERSIONS_ENV: bridge_supported_versions,
            TIMEOUT_SECONDS_ENV: str(bridge_timeout_seconds) if bridge_timeout_seconds is not None else None,
        }
    ):
        backend_check = _backend_check(backend_mode, allowed, required_versions=target_versions)
        bridge_contract = _bridge_contract_check(
            sample_pack,
            backend_check=backend_check,
            adapter_spec=adapter_spec,
            allowed_dwg_license_ids=allowed,
            bridge_contract_json=bridge_contract_json,
            only_versions=only_versions,
        )
        if backend_check["passed"] and bridge_contract.get("passed", True):
            validation_report = validate_sample_pack.build_report(
                sample_pack,
                run_import=True,
                run_compare=True,
                compare_source="dwg",
                dwg_backend_mode=backend_mode,
                allowed_dwg_license_ids=allowed,
                max_entities=max_entities,
                max_dxf_tokens=max_dxf_tokens,
                import_timeout_seconds=import_timeout_seconds,
                compare_timeout_seconds=compare_timeout_seconds,
                skip_compare_over_dxf_mb=skip_compare_over_dxf_mb,
                only_versions=only_versions,
            )
        else:
            failed_check = dict(backend_check)
            if not bridge_contract.get("passed", True):
                failed_check["errors"] = [
                    *list(failed_check.get("errors") or []),
                    "bridge_contract_failed",
                ]
            failed_check["bridge_contract"] = bridge_contract
            validation_report = _failed_validation_report(
                sample_pack,
                backend_check=failed_check,
                backend_mode=backend_mode,
                allowed_dwg_license_ids=allowed,
                max_entities=max_entities,
                max_dxf_tokens=max_dxf_tokens,
                import_timeout_seconds=import_timeout_seconds,
                compare_timeout_seconds=compare_timeout_seconds,
                skip_compare_over_dxf_mb=skip_compare_over_dxf_mb,
                only_versions=only_versions,
            )

    product_evidence = _product_evidence_check(
        sample_pack,
        backend_check=backend_check,
        adapter_spec=adapter_spec,
        allowed_dwg_license_ids=allowed,
        bridge_command=bridge_command,
        bridge_args_json=bridge_args_json,
        bridge_license_id=bridge_license_id,
        bridge_supported_versions=bridge_supported_versions,
        bridge_timeout_seconds=bridge_timeout_seconds,
        product_evidence_json=product_evidence_json,
        product_evidence_output_dir=product_evidence_output_dir,
        product_pair_timeout_seconds=product_pair_timeout_seconds,
        product_max_pairs_per_version=product_max_pairs_per_version,
        max_entities=max_entities,
        max_dxf_tokens=max_dxf_tokens,
        import_timeout_seconds=import_timeout_seconds,
        only_versions=only_versions,
    )

    validation_report["backend_check"] = backend_check
    validation_report["bridge_contract"] = bridge_contract
    validation_report["product_evidence"] = product_evidence
    _write_json(validation_json, validation_report)
    _write_text(validation_md, validate_sample_pack.render_markdown(validation_report))

    evidence_report = evidence_builder.build_report([validation_json])
    evidence_report["backend_check"] = backend_check
    evidence_report["bridge_contract"] = bridge_contract
    evidence_report["product_evidence"] = product_evidence
    _write_json(evidence_json, evidence_report)

    audit_report = native_audit.run_audit(
        evidence_manifest=evidence_json,
        claim_scope="native",
        target_versions=target_versions,
    )
    audit_report["backend_check"] = backend_check
    audit_report["bridge_contract"] = bridge_contract
    audit_report["product_evidence"] = product_evidence
    _write_json(audit_json, audit_report)

    bridge_contract_failed = bridge_contract_json is not None and bridge_contract.get("status") == "failed"
    product_evidence_failed = product_evidence_json is not None and product_evidence.get("status") == "failed"
    return {
        "schema_version": "dwg-native-backend-validation-run/v1",
        "generated_at": datetime.now().isoformat(),
        "status": "passed"
        if backend_check["passed"] and not bridge_contract_failed and not product_evidence_failed and audit_report["status"] == "passed"
        else "failed",
        "backend_check": backend_check,
        "bridge_contract": bridge_contract,
        "product_evidence": product_evidence,
        "paths": {
            "validation_json": str(validation_json),
            "validation_md": str(validation_md),
            "evidence_json": str(evidence_json),
            "audit_json": str(audit_json),
            "bridge_contract_json": str(bridge_contract_json) if bridge_contract_json else "",
            "product_evidence_json": str(product_evidence_json) if product_evidence_json else "",
            "product_evidence_output_dir": str(product_evidence_output_dir) if product_evidence_json else "",
        },
        "validation_status": validation_report.get("status"),
        "native_audit_status": audit_report.get("status"),
        "product_evidence_status": product_evidence.get("status"),
        "native_ready_versions": (audit_report.get("summary") or {}).get("native_ready_versions") or [],
        "native_missing_versions": (audit_report.get("summary") or {}).get("native_missing_versions") or [],
        "target_versions": list(target_versions),
    }


def _backend_check(
    backend_mode: str,
    allowed_dwg_license_ids: Sequence[str],
    *,
    required_versions: Sequence[str] = (),
) -> dict[str, Any]:
    try:
        selection = create_dwg_backend_selection(backend_mode)
    except Exception as exc:
        return {
            "passed": False,
            "backend_mode": backend_mode,
            "error_code": "DWG_BACKEND_SELECTION_FAILED",
            "message": str(exc),
        }
    adapter = selection.adapter
    license_id = str(getattr(adapter, "license_id", "") or "")
    implementation_status = str(getattr(adapter, "implementation_status", selection.implementation_status) or "")
    availability_error = ""
    try:
        available = bool(adapter.is_available())
    except Exception as exc:
        available = False
        availability_error = f"{type(exc).__name__}: {exc}"
    adapter_diagnostics = _adapter_diagnostics(adapter)
    target_versions = tuple(str(code).upper() for code in required_versions if str(code or "").strip())
    version_support: dict[str, bool] = {}
    version_support_errors: dict[str, str] = {}
    unsupported_required_versions: list[str] = []
    for code in target_versions:
        version_info = _version_info_for_code(code)
        try:
            supported = bool(adapter.supports_version(version_info))
        except Exception as exc:
            supported = False
            version_support_errors[code] = f"{type(exc).__name__}: {exc}"
        version_support[code] = supported
        if not supported:
            unsupported_required_versions.append(code)
    details = selection.to_dict()
    details.update(
        {
            "license_id": license_id,
            "allowed_dwg_license_ids": list(allowed_dwg_license_ids),
            "available": available,
            "required_versions": list(target_versions),
            "version_support": version_support,
            "adapter_diagnostics": adapter_diagnostics,
        }
    )
    errors = []
    if not available:
        errors.append("adapter_unavailable")
    if availability_error:
        errors.append("adapter_availability_check_failed")
    if implementation_status in {"placeholder", "plugin_load_failed"}:
        errors.append(f"implementation_status={implementation_status}")
    if license_id not in set(allowed_dwg_license_ids):
        errors.append("license_not_allowed")
    if backend_mode == DWG_BACKEND_COMMERCIAL_SDK and not os.environ.get(COMMERCIAL_SDK_ADAPTER_ENV):
        errors.append(f"{COMMERCIAL_SDK_ADAPTER_ENV}_missing")
    if version_support_errors:
        errors.append("adapter_version_support_probe_failed")
    if unsupported_required_versions:
        errors.append("adapter_missing_required_versions")
    return {
        "passed": not errors,
        "backend_mode": backend_mode,
        "adapter": adapter.name,
        "adapter_version": adapter.version,
        "implementation_status": implementation_status,
        "approval_required": bool(getattr(adapter, "approval_required", selection.approval_required)),
        "license_id": license_id,
        "source": selection.source,
        "errors": errors,
        "availability_error": availability_error,
        "required_versions": list(target_versions),
        "supported_required_versions": [
            code for code in target_versions if version_support.get(code)
        ],
        "unsupported_required_versions": unsupported_required_versions,
        "version_support": version_support,
        "version_support_errors": version_support_errors,
        "adapter_diagnostics": adapter_diagnostics,
        "details": details,
    }


def _failed_validation_report(
    sample_pack: Path,
    *,
    backend_check: dict[str, Any],
    backend_mode: str,
    allowed_dwg_license_ids: Sequence[str],
    max_entities: int,
    max_dxf_tokens: int,
    import_timeout_seconds: float,
    compare_timeout_seconds: float,
    skip_compare_over_dxf_mb: float,
    only_versions: set[str] | None,
) -> dict[str, Any]:
    return {
        "schema_version": "adr004-version-sample-pack-validation/v1",
        "generated_at": datetime.now().isoformat(),
        "sample_pack": str(sample_pack),
        "manifest_path": "",
        "status": "failed",
        "limits": {
            "max_entities": max_entities,
            "max_dxf_tokens": max_dxf_tokens,
            "import_timeout_seconds": import_timeout_seconds,
            "compare_timeout_seconds": compare_timeout_seconds,
            "skip_compare_over_dxf_mb": skip_compare_over_dxf_mb,
            "compare_source": "dwg",
            "dwg_backend_mode": backend_mode,
            "allowed_dwg_license_ids": list(allowed_dwg_license_ids),
            "only_versions": sorted(only_versions or []),
        },
        "summary": {
            "version_count": 0,
            "manifest_error_count": 1,
            "validation_error_count": 0,
            "header_mismatch_count": 0,
            "import_status_counts": {},
            "compare_status_counts": {},
        },
        "manifest_errors": [
            "DWG native backend check failed: " + ", ".join(backend_check.get("errors") or ["unknown"])
        ],
        "validation_errors": [],
        "versions": [],
    }


def _bridge_contract_check(
    sample_pack: Path,
    *,
    backend_check: dict[str, Any],
    adapter_spec: str | None,
    allowed_dwg_license_ids: Sequence[str],
    bridge_contract_json: Path | None,
    only_versions: set[str] | None,
) -> dict[str, Any]:
    if bridge_contract_json is None:
        return {"status": "not_requested", "passed": True, "path": ""}
    if not backend_check.get("passed"):
        return {
            "status": "skipped",
            "passed": True,
            "path": str(bridge_contract_json),
            "reason": "backend_check_failed",
        }
    if not _is_json_bridge_backend(backend_check):
        return {
            "status": "not_applicable",
            "passed": True,
            "path": str(bridge_contract_json),
            "reason": "adapter_is_not_commercial_dwg_json_bridge",
        }

    dwg_paths, errors = _collect_sample_pack_dwgs(sample_pack, only_versions=only_versions)
    if errors:
        report = _failed_bridge_contract_report(sample_pack, errors=errors)
        _write_json(bridge_contract_json, report)
        return {
            "status": "failed",
            "passed": False,
            "path": str(bridge_contract_json),
            "diagnostic_errors": report["diagnostic_errors"],
            "input_count": 0,
        }

    contract_adapter_spec = (
        adapter_spec
        or os.environ.get(COMMERCIAL_SDK_ADAPTER_ENV)
        or bridge_contract_validator.DEFAULT_ADAPTER_SPEC
    )
    report = bridge_contract_validator.validate_contract(
        dwg_paths,
        adapter_spec=contract_adapter_spec,
        allowed_dwg_license_ids=allowed_dwg_license_ids,
        json_report=bridge_contract_json,
    )
    summary = report.get("summary") or {}
    return {
        "status": report.get("status"),
        "passed": report.get("status") == "passed",
        "path": str(bridge_contract_json),
        "input_count": summary.get("input_count", len(dwg_paths)),
        "accepted_import_count": summary.get("accepted_import_count"),
        "failed_import_count": summary.get("failed_import_count"),
        "diagnostic_errors": report.get("diagnostic_errors") or [],
    }


def _is_json_bridge_backend(backend_check: dict[str, Any]) -> bool:
    diagnostics = backend_check.get("adapter_diagnostics")
    return isinstance(diagnostics, dict) and diagnostics.get("kind") == "commercial_dwg_json_bridge"


def _product_evidence_check(
    sample_pack: Path,
    *,
    backend_check: dict[str, Any],
    adapter_spec: str | None,
    allowed_dwg_license_ids: Sequence[str],
    bridge_command: str | None,
    bridge_args_json: str | None,
    bridge_license_id: str | None,
    bridge_supported_versions: str | None,
    bridge_timeout_seconds: float | None,
    product_evidence_json: Path | None,
    product_evidence_output_dir: Path,
    product_pair_timeout_seconds: float,
    product_max_pairs_per_version: int | None,
    max_entities: int,
    max_dxf_tokens: int,
    import_timeout_seconds: float,
    only_versions: set[str] | None,
) -> dict[str, Any]:
    if product_evidence_json is None:
        return {"status": "not_requested", "passed": True, "path": ""}
    if not backend_check.get("passed"):
        return {
            "status": "skipped",
            "passed": True,
            "path": str(product_evidence_json),
            "output_dir": str(product_evidence_output_dir),
            "reason": "backend_check_failed",
        }
    if not _is_json_bridge_backend(backend_check):
        return {
            "status": "not_applicable",
            "passed": True,
            "path": str(product_evidence_json),
            "output_dir": str(product_evidence_output_dir),
            "reason": "adapter_is_not_commercial_dwg_json_bridge",
        }

    product_adapter_spec = (
        adapter_spec
        or os.environ.get(COMMERCIAL_SDK_ADAPTER_ENV)
        or product_bridge_evidence.DEFAULT_ADAPTER_SPEC
    )
    report = product_bridge_evidence.run_evidence(
        sample_pack,
        adapter_spec=product_adapter_spec,
        allowed_dwg_license_ids=allowed_dwg_license_ids,
        bridge_command=bridge_command,
        bridge_args_json=bridge_args_json,
        bridge_license_id=bridge_license_id,
        bridge_supported_versions=bridge_supported_versions,
        bridge_timeout_seconds=bridge_timeout_seconds,
        pair_timeout_seconds=product_pair_timeout_seconds,
        max_entities=max_entities,
        max_dxf_tokens=max_dxf_tokens,
        import_timeout_seconds=import_timeout_seconds,
        output_dir=product_evidence_output_dir,
        summary_json=product_evidence_json,
        only_versions=only_versions,
        max_pairs_per_version=product_max_pairs_per_version,
    )
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    return {
        "status": report.get("status"),
        "passed": report.get("status") == "passed",
        "path": str(product_evidence_json),
        "output_dir": str(product_evidence_output_dir),
        "pair_count": summary.get("pair_count"),
        "executed_pair_count": summary.get("executed_pair_count"),
        "passed_pair_count": summary.get("passed_pair_count"),
        "failed_pair_count": summary.get("failed_pair_count"),
        "bridge_evidence_pair_count": summary.get("bridge_evidence_pair_count"),
        "diagnostic_errors": report.get("diagnostic_errors") or [],
    }


def _collect_sample_pack_dwgs(
    sample_pack: Path,
    *,
    only_versions: set[str] | None,
) -> tuple[list[Path], list[str]]:
    try:
        manifest_path, manifest = validate_sample_pack.load_manifest(sample_pack)
    except Exception as exc:
        return [], [f"sample_pack_manifest_unreadable: {type(exc).__name__}: {exc}"]

    requested = {str(code).upper() for code in only_versions or set()}
    versions = manifest.get("versions") or {}
    errors: list[str] = []
    paths: list[Path] = []
    for missing in sorted(requested - {str(code).upper() for code in versions}):
        errors.append(f"requested version not found in manifest: {missing}")
    for code, item in sorted(versions.items()):
        if requested and str(code).upper() not in requested:
            continue
        for field in ("sample_before_dwg", "sample_after_dwg"):
            raw = item.get(field)
            if not raw:
                errors.append(f"{code}.{field} is missing")
                continue
            path = Path(str(raw))
            if not path.is_absolute():
                path = manifest_path.parent / path
            paths.append(path)
    if not paths:
        errors.append("sample pack contains no DWG inputs for bridge contract validation")
    return _dedupe_paths(paths), errors


def _dedupe_paths(paths: Sequence[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def _failed_bridge_contract_report(sample_pack: Path, *, errors: Sequence[str]) -> dict[str, Any]:
    return {
        "schema_version": "dwg-json-bridge-contract-validation/v1",
        "generated_at": datetime.now().isoformat(),
        "status": "failed",
        "sample_pack": str(sample_pack),
        "summary": {
            "input_count": 0,
            "accepted_import_count": 0,
            "failed_import_count": 0,
            "missing_input_count": 0,
            "diagnostic_error_count": len(errors),
        },
        "adapter": {},
        "allowed_dwg_license_ids": [],
        "diagnostic_errors": list(errors),
        "records": [],
    }


@contextmanager
def _temporary_env(updates: dict[str, str | None]) -> Iterator[None]:
    old_values = {key: os.environ.get(key) for key in updates}
    try:
        for key, value in updates.items():
            if value is not None:
                os.environ[key] = value
        yield
    finally:
        for key, old_value in old_values.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


def _dedupe(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in result:
            result.append(item)
    return tuple(result)


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def _adapter_diagnostics(adapter: Any) -> dict[str, Any]:
    diagnostics = getattr(adapter, "diagnostics", None)
    if not callable(diagnostics):
        return {}
    try:
        payload = diagnostics()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return payload if isinstance(payload, dict) else {"value": payload}


def _version_info_for_code(code: str) -> DwgVersionInfo:
    normalized = str(code).upper()
    if normalized in DwgVersionDetector.SUPPORTED_CODES:
        family, release = DwgVersionDetector.SUPPORTED_CODES[normalized]
        return DwgVersionInfo(code=normalized, family=family, release=release, supported=True)
    if normalized in DwgVersionDetector.KNOWN_UNSUPPORTED_CODES:
        family, release = DwgVersionDetector.KNOWN_UNSUPPORTED_CODES[normalized]
        return DwgVersionInfo(code=normalized, family=family, release=release, supported=False)
    return DwgVersionInfo(
        code=normalized,
        family="Unknown AutoCAD DWG",
        release="unsupported",
        supported=False,
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sample_pack", type=Path)
    parser.add_argument("--dwg-backend", default=DWG_BACKEND_COMMERCIAL_SDK)
    parser.add_argument("--adapter-spec", default=None)
    parser.add_argument("--dwg-allowed-license-id", action="append", default=None)
    parser.add_argument("--validation-json", type=Path, default=DEFAULT_VALIDATION_JSON)
    parser.add_argument("--validation-md", type=Path, default=DEFAULT_VALIDATION_MD)
    parser.add_argument("--evidence-json", type=Path, default=DEFAULT_EVIDENCE_JSON)
    parser.add_argument("--audit-json", type=Path, default=DEFAULT_AUDIT_JSON)
    parser.add_argument("--bridge-contract-json", type=Path, default=None)
    parser.add_argument("--bridge-command")
    parser.add_argument("--bridge-args-json")
    parser.add_argument("--bridge-license-id")
    parser.add_argument("--bridge-supported-versions")
    parser.add_argument("--bridge-timeout-seconds", type=float)
    parser.add_argument("--product-evidence-json", type=Path, default=None)
    parser.add_argument("--product-evidence-output-dir", type=Path, default=DEFAULT_PRODUCT_EVIDENCE_OUTPUT_DIR)
    parser.add_argument("--product-pair-timeout-seconds", type=float, default=300.0)
    parser.add_argument("--product-max-pairs-per-version", type=int)
    parser.add_argument("--max-entities", type=int, default=validate_sample_pack.DEFAULT_MAX_ENTITIES)
    parser.add_argument("--max-dxf-tokens", type=int, default=validate_sample_pack.DEFAULT_MAX_DXF_TOKENS)
    parser.add_argument("--import-timeout-seconds", type=float, default=validate_sample_pack.DEFAULT_IMPORT_TIMEOUT_SECONDS)
    parser.add_argument("--compare-timeout-seconds", type=float, default=validate_sample_pack.DEFAULT_COMPARE_TIMEOUT_SECONDS)
    parser.add_argument("--skip-compare-over-dxf-mb", type=float, default=validate_sample_pack.DEFAULT_SKIP_COMPARE_OVER_DXF_MB)
    parser.add_argument("--version", action="append", default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_validation(
        args.sample_pack,
        dwg_backend_mode=args.dwg_backend,
        adapter_spec=args.adapter_spec,
        allowed_dwg_license_ids=tuple(["MIT", "INTERNAL", *(args.dwg_allowed_license_id or [])]),
        validation_json=args.validation_json,
        validation_md=args.validation_md,
        evidence_json=args.evidence_json,
        audit_json=args.audit_json,
        bridge_contract_json=args.bridge_contract_json,
        bridge_command=args.bridge_command,
        bridge_args_json=args.bridge_args_json,
        bridge_license_id=args.bridge_license_id,
        bridge_supported_versions=args.bridge_supported_versions,
        bridge_timeout_seconds=args.bridge_timeout_seconds,
        product_evidence_json=args.product_evidence_json,
        product_evidence_output_dir=args.product_evidence_output_dir,
        product_pair_timeout_seconds=args.product_pair_timeout_seconds,
        product_max_pairs_per_version=args.product_max_pairs_per_version,
        max_entities=args.max_entities,
        max_dxf_tokens=args.max_dxf_tokens,
        import_timeout_seconds=args.import_timeout_seconds,
        compare_timeout_seconds=args.compare_timeout_seconds,
        skip_compare_over_dxf_mb=args.skip_compare_over_dxf_mb,
        only_versions={str(code).upper() for code in args.version or []} or None,
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
