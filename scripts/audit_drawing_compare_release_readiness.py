# -*- coding: utf-8 -*-
"""Audit drawing-compare release readiness evidence.

This audit is intentionally narrow.  It does not run comparisons or mutate
artifacts; it checks whether existing evidence is strong enough to support the
fallback-based customer-ready claim while blocking modern/native DWG overclaims.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


REQUIRED_EVIDENCE_COUNTS = {
    "pdf_pairs": 10,
    "dxf_pairs": 10,
    "large_cad_dxf_pairs": 3,
    "ac1015_native_baselines": 3,
    "ac1024_converted_dxf_fallback_pairs": 2,
    "ac1027_converted_dxf_fallback_pairs": 2,
    "ac1032_converted_dxf_fallback_pairs": 2,
    "negative_failure_samples": 5,
    "partial_import_samples": 3,
    "block_text_dimension_pairs": 5,
}

DWG_TARGET_VERSION_CODES = ("AC1009", "AC1012", "AC1014", "AC1015", "AC1018", "AC1021", "AC1024", "AC1027", "AC1032")
DWG_ALL_VERSION_AUDIT_SCHEMA = "dwg-all-version-support-audit/v1"
DWG_JSON_BRIDGE_CONTRACT_SCHEMA = "dwg-json-bridge-contract-validation/v1"
NATIVE_BRIDGE_EVIDENCE_SCOPES = {
    "native_dwg",
    "native_dwg_bridge",
    "commercial_dwg_native",
    "commercial_native",
    "commercial_sdk_native",
}

THRESHOLDS = {
    "recall": ("min", 0.90),
    "precision": ("min", 0.85),
    "false_positive_zone_rate": ("max", 0.15),
    "duplicate_zone_rate": ("max", 0.10),
    "overlay_error_px_150dpi": ("max", 10.0),
    "small_drawing_seconds": ("max", 30.0),
    "medium_drawing_seconds": ("max", 120.0),
    "progress_max_gap_s": ("max", 10.0),
    "cancel_response_s": ("max", 10.0),
    "orphan_processes": ("max", 0),
    "customer_path_oda_calls": ("max", 0),
    "exported_sensitive_path_leaks": ("max", 0),
}

FORBIDDEN_CLAIM_PATTERNS = (
    re.compile(r"\ball\s+DWG\s+versions?\s+supported\b", re.IGNORECASE),
    re.compile(r"\bmodern\s+DWG\s+native\s+support\b", re.IGNORECASE),
    re.compile(r"\bAC10(?:18|21|24|27|32)\b[^\n]{0,80}\bnative\b[^\n]{0,80}\bsupport", re.IGNORECASE),
    re.compile(r"\bAC10(?:18|21|24|27|32)\b[^\n]{0,80}\bDWG\b[^\n]{0,80}\bsupported\b", re.IGNORECASE),
    re.compile(r"\bDWG\s+fully\s+supported\b", re.IGNORECASE),
    re.compile(r"\bdefault\s+customer\s+path\b[^\n]{0,120}\bmodern\s+DWG\b[^\n]{0,80}\bnative", re.IGNORECASE),
    re.compile(r"\bODA\s+conversion\s+is\s+automatic\s+in\s+customer\s+builds\b", re.IGNORECASE),
)

FORBIDDEN_RUNTIME_TOKENS = ("oda sdk", "odafileconverter", "oda file converter", "libredwg", "gpl", "agpl")

ALLOWED_RELEASE_CLAIMS = [
    "PDF and DXF comparison are supported when evidence gates pass.",
    "AC1015 native DWG baseline is limited to the approved ODA-free path.",
    "Modern DWGs can be compared through user-provided or registered converted DXF when provenance is preserved.",
    "Explicit local/internal ODA fallback is available only when oda_converter is selected.",
]

FORBIDDEN_RELEASE_CLAIMS = [
    "All DWG versions supported.",
    "Modern DWG native support is complete.",
    "AC1018/AC1021/AC1024/AC1027/AC1032 native DWG support.",
    "AC1032 native DWG is supported.",
    "ODA conversion is automatic in customer builds.",
    "Partial imports are complete geometry parity.",
]


@dataclass(frozen=True)
class AuditCheck:
    name: str
    passed: bool
    detail: str
    severity: str = "hard"
    evidence: list[str] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "severity": self.severity,
            "detail": self.detail,
            "evidence": list(self.evidence or []),
        }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-json", type=Path, required=True)
    parser.add_argument("--run-manifest", type=Path, required=True)
    parser.add_argument("--customer-evidence-manifest", type=Path, required=True)
    parser.add_argument("--baseline-metrics", type=Path)
    parser.add_argument("--dwg-all-version-audit", type=Path)
    parser.add_argument("--native-dwg-audit", type=Path)
    parser.add_argument("--dwg-json-bridge-contract", type=Path)
    parser.add_argument("--require-native-dwg", action="store_true")
    parser.add_argument("--out", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_audit(
        result_json=args.result_json,
        run_manifest=args.run_manifest,
        customer_evidence_manifest=args.customer_evidence_manifest,
        baseline_metrics=args.baseline_metrics,
        dwg_all_version_audit=args.dwg_all_version_audit,
        native_dwg_audit=args.native_dwg_audit,
        dwg_json_bridge_contract=args.dwg_json_bridge_contract,
        require_native_dwg=args.require_native_dwg,
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report["status"] == "passed" else 1


def run_audit(
    *,
    result_json: Path,
    run_manifest: Path,
    customer_evidence_manifest: Path,
    baseline_metrics: Path | None = None,
    dwg_all_version_audit: Path | None = None,
    native_dwg_audit: Path | None = None,
    dwg_json_bridge_contract: Path | None = None,
    require_native_dwg: bool = False,
) -> dict[str, Any]:
    result = _load_json(result_json)
    run = _load_json(run_manifest)
    manifest = _load_json(customer_evidence_manifest)
    metrics = _load_json(baseline_metrics) if baseline_metrics else None
    all_version_audit = _load_json(dwg_all_version_audit) if dwg_all_version_audit else None
    native_audit = _load_json(native_dwg_audit) if native_dwg_audit else None
    bridge_contract = _load_json(dwg_json_bridge_contract) if dwg_json_bridge_contract else None

    checks: list[AuditCheck] = [
        _json_check("result_json_loadable", result, result_json),
        _json_check("run_manifest_loadable", run, run_manifest),
        _json_check("customer_evidence_manifest_loadable", manifest, customer_evidence_manifest),
    ]
    if baseline_metrics is not None:
        checks.append(_json_check("baseline_metrics_loadable", metrics, baseline_metrics))
    if dwg_all_version_audit is not None:
        checks.append(_json_check("dwg_all_version_audit_loadable", all_version_audit, dwg_all_version_audit))
    if native_dwg_audit is not None:
        checks.append(_json_check("native_dwg_audit_loadable", native_audit, native_dwg_audit))
    if dwg_json_bridge_contract is not None:
        checks.append(_json_check("dwg_json_bridge_contract_loadable", bridge_contract, dwg_json_bridge_contract))

    payloads = [payload for payload in (result, run, manifest, metrics) if isinstance(payload, dict)]
    checks.extend(
        [
            _check_evidence_counts(manifest),
            _check_release_wording(payloads),
            _check_customer_runtime_policy(payloads),
            _check_provenance(payloads),
            _check_partial_import_warnings(payloads),
            _check_timeout_cleanup_evidence(payloads),
            _check_threshold_metrics(payloads),
            _check_dwg_all_version_fallback_audit(
                all_version_audit,
                supplied=dwg_all_version_audit is not None,
            ),
            _check_native_dwg_audit(
                native_audit,
                supplied=native_dwg_audit is not None,
                required=require_native_dwg,
            ),
            _check_dwg_json_bridge_contract(
                bridge_contract,
                supplied=dwg_json_bridge_contract is not None,
                required=require_native_dwg and _native_audit_uses_json_bridge(native_audit),
            ),
            _check_dwg_json_bridge_product_evidence(
                payloads,
                required=require_native_dwg and _native_audit_uses_json_bridge(native_audit),
            ),
        ]
    )

    failed_checks = [check for check in checks if not check.passed]
    hard_failures = [check for check in failed_checks if check.severity == "hard"]
    warnings = [check for check in failed_checks if check.severity == "warning"]
    partial_reasons = _partial_reasons(payloads)
    status = "failed" if hard_failures else ("partial" if warnings or partial_reasons else "passed")

    return {
        "schema_version": 1,
        "status": status,
        "summary": {
            "passed": sum(1 for check in checks if check.passed),
            "failed": len(failed_checks),
            "hard_failed": len(hard_failures),
            "warnings": len(warnings) + len(partial_reasons),
        },
        "checks": [check.to_json() for check in checks],
        "failed_metrics": [
            {"name": check.name, "detail": check.detail}
            for check in failed_checks
            if check.name.startswith("metric_")
        ],
        "missing_evidence": [
            {"name": check.name, "detail": check.detail}
            for check in failed_checks
            if "missing" in check.detail.lower() or check.name.startswith("evidence_")
        ],
        "partial_reasons": partial_reasons,
        "allowed_release_claims": ALLOWED_RELEASE_CLAIMS,
        "forbidden_release_claims": FORBIDDEN_RELEASE_CLAIMS,
        "inputs": {
            "result_json": str(result_json),
            "run_manifest": str(run_manifest),
            "customer_evidence_manifest": str(customer_evidence_manifest),
            "baseline_metrics": str(baseline_metrics) if baseline_metrics else "",
            "dwg_all_version_audit": str(dwg_all_version_audit) if dwg_all_version_audit else "",
            "native_dwg_audit": str(native_dwg_audit) if native_dwg_audit else "",
            "dwg_json_bridge_contract": str(dwg_json_bridge_contract) if dwg_json_bridge_contract else "",
            "require_native_dwg": require_native_dwg,
        },
    }


def _json_check(name: str, payload: Any, path: Path) -> AuditCheck:
    if isinstance(payload, dict):
        return AuditCheck(name, True, "JSON object loaded", evidence=[str(path)])
    return AuditCheck(name, False, f"JSON object missing or unreadable: {path}", evidence=[str(path)])


def _check_evidence_counts(manifest: Any) -> AuditCheck:
    if not isinstance(manifest, dict):
        return AuditCheck("evidence_minimum_counts", False, "customer evidence manifest missing")
    failures: list[str] = []
    for key, required in REQUIRED_EVIDENCE_COUNTS.items():
        actual = _evidence_count(manifest, key)
        if actual < required:
            failures.append(f"{key}={actual}/{required}")
    return AuditCheck(
        "evidence_minimum_counts",
        not failures,
        "minimum evidence counts satisfied" if not failures else "; ".join(failures),
    )


def _check_release_wording(payloads: Sequence[dict[str, Any]]) -> AuditCheck:
    snippets = _claim_snippets(payloads)
    violations: list[str] = []
    for snippet in snippets:
        for pattern in FORBIDDEN_CLAIM_PATTERNS:
            if pattern.search(snippet):
                violations.append(snippet)
                break
    return AuditCheck(
        "release_wording_claims",
        not violations,
        "release wording is claim-safe" if not violations else "forbidden release wording: " + " | ".join(violations),
        evidence=violations[:10],
    )


def _check_customer_runtime_policy(payloads: Sequence[dict[str, Any]]) -> AuditCheck:
    violations: list[str] = []
    for payload in payloads:
        violations.extend(_find_forbidden_runtime(payload))
        violations.extend(_find_default_oda_calls(payload))
        violations.extend(_find_unapproved_commercial_sdk_calls(payload))
    return AuditCheck(
        "customer_runtime_policy",
        not violations,
        "default/customer runtime path is ODA/GPL/AGPL-free" if not violations else "; ".join(sorted(set(violations))),
        evidence=violations[:20],
    )


def _check_provenance(payloads: Sequence[dict[str, Any]]) -> AuditCheck:
    keys = _flatten_key_values(payloads)
    has_original = any(_key_has(key, ("original", "source", "requested")) and _looks_pathish(value) for key, value in keys)
    has_effective = any(_key_has(key, ("effective", "resolved", "converted", "actual")) and _looks_pathish(value) for key, value in keys)
    has_backend = any("backend" in key or key in {"dwg_backend_mode", "backend_mode"} for key, _ in keys)
    failures: list[str] = []
    if not has_original:
        failures.append("original/source input provenance missing")
    if not has_effective:
        failures.append("effective/resolved input provenance missing")
    if not has_backend:
        failures.append("backend mode provenance missing")
    return AuditCheck(
        "fallback_provenance",
        not failures,
        "original/effective/backend provenance present" if not failures else "; ".join(failures),
    )


def _check_partial_import_warnings(payloads: Sequence[dict[str, Any]]) -> AuditCheck:
    has_partial = any(_contains_value(payload, "partial") for payload in payloads)
    if not has_partial:
        return AuditCheck("partial_import_warnings", True, "no partial import evidence present")
    has_warning = any(_has_nonempty_warning(payload) for payload in payloads)
    if not has_warning:
        has_warning = any(_has_nonempty_warning(payload) for payload in _referenced_result_payloads(payloads))
    return AuditCheck(
        "partial_import_warnings",
        has_warning,
        "partial import warnings are present" if has_warning else "partial status exists but warnings/unsupported evidence is missing",
    )


def _check_timeout_cleanup_evidence(payloads: Sequence[dict[str, Any]]) -> AuditCheck:
    orphan_value = _metric_value(payloads, "orphan_processes")
    cleanup_present = orphan_value is not None or any(
        _key_has(key, ("cleanup", "timeout")) and _has_meaningful_value(value)
        for key, value in _flatten_key_values(payloads)
    )
    if not cleanup_present:
        return AuditCheck("timeout_cleanup_evidence", False, "timeout/orphan cleanup evidence missing")
    if orphan_value is not None and _as_float(orphan_value) != 0:
        return AuditCheck("timeout_cleanup_evidence", False, f"orphan_processes must be 0, got {orphan_value!r}")
    return AuditCheck("timeout_cleanup_evidence", True, "timeout/orphan cleanup evidence present")


def _check_threshold_metrics(payloads: Sequence[dict[str, Any]]) -> AuditCheck:
    failures: list[str] = []
    for key, (direction, threshold) in THRESHOLDS.items():
        value = _metric_value(payloads, key)
        numeric = _as_float(value)
        if numeric is None:
            failures.append(f"{key}=missing")
        elif direction == "min" and numeric < threshold:
            failures.append(f"{key}={numeric:g} < {threshold:g}")
        elif direction == "max" and numeric > threshold:
            failures.append(f"{key}={numeric:g} > {threshold:g}")

    large_seconds = _as_float(_metric_value(payloads, "large_drawing_seconds"))
    large_clear_timeout = _as_bool(_metric_value(payloads, "large_drawing_timeout_clear")) or _as_bool(
        _metric_value(payloads, "large_drawing_failure_clear")
    )
    if large_seconds is None and not large_clear_timeout:
        failures.append("large_drawing_seconds=missing and no clear timeout/failure evidence")
    elif large_seconds is not None and large_seconds > 600 and not large_clear_timeout:
        failures.append(f"large_drawing_seconds={large_seconds:g} > 600 without clear timeout/failure")

    return AuditCheck(
        "metric_release_thresholds",
        not failures,
        "release readiness metrics satisfy thresholds" if not failures else "; ".join(failures),
    )


def _check_dwg_all_version_fallback_audit(payload: Any, *, supplied: bool) -> AuditCheck:
    if not supplied:
        return AuditCheck(
            "dwg_all_version_fallback_audit",
            False,
            "all-version fallback audit not supplied",
            severity="warning",
        )
    if not isinstance(payload, dict):
        return AuditCheck("dwg_all_version_fallback_audit", False, "all-version fallback audit missing or unreadable")

    failures: list[str] = []
    if payload.get("schema_version") != DWG_ALL_VERSION_AUDIT_SCHEMA:
        failures.append(f"schema_version={payload.get('schema_version')!r}")
    if payload.get("claim_scope") != "fallback":
        failures.append(f"claim_scope={payload.get('claim_scope')!r}, expected 'fallback'")
    if payload.get("status") != "passed":
        failures.append(f"status={payload.get('status')!r}, expected 'passed'")

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    missing_versions = _string_list(summary.get("fallback_missing_versions"))
    if missing_versions:
        failures.append("fallback_missing_versions=" + ",".join(missing_versions))

    targets = _string_list(payload.get("target_versions")) or list(DWG_TARGET_VERSION_CODES)
    fallback_ready = set(_string_list(summary.get("fallback_ready_versions")))
    not_ready = [code for code in targets if code not in fallback_ready]
    if not_ready:
        failures.append("fallback_ready_versions_missing=" + ",".join(not_ready))

    version_items = payload.get("versions")
    if not isinstance(version_items, list) or not version_items:
        failures.append("versions matrix missing")
    else:
        for item in version_items:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "")
            if code in targets and not _as_bool(item.get("fallback_ready")):
                failures.append(f"{code}.fallback_ready=false")
            oda_calls = _as_int(item.get("default_customer_oda_calls"))
            if oda_calls:
                failures.append(f"{code}.default_customer_oda_calls={oda_calls}/0")

    return AuditCheck(
        "dwg_all_version_fallback_audit",
        not failures,
        "all target DWG generations are fallback-ready"
        if not failures
        else "; ".join(failures),
    )


def _check_native_dwg_audit(payload: Any, *, supplied: bool, required: bool) -> AuditCheck:
    severity = "hard" if required else "warning"
    if not supplied:
        if required:
            return AuditCheck("native_dwg_claim_gate", False, "native DWG audit required but not supplied")
        return AuditCheck("native_dwg_claim_gate", True, "native DWG claim gate not requested")
    if not isinstance(payload, dict):
        return AuditCheck("native_dwg_claim_gate", False, "native DWG audit missing or unreadable", severity=severity)

    failures: list[str] = []
    if payload.get("schema_version") != DWG_ALL_VERSION_AUDIT_SCHEMA:
        failures.append(f"schema_version={payload.get('schema_version')!r}")
    if payload.get("claim_scope") != "native":
        failures.append(f"claim_scope={payload.get('claim_scope')!r}, expected 'native'")
    if payload.get("status") != "passed":
        failures.append(f"status={payload.get('status')!r}, expected 'passed'")

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    missing_versions = _string_list(summary.get("native_missing_versions"))
    if missing_versions:
        failures.append("native_missing_versions=" + ",".join(missing_versions))

    targets = _string_list(payload.get("target_versions")) or list(DWG_TARGET_VERSION_CODES)
    native_ready = set(_string_list(summary.get("native_ready_versions")))
    not_ready = [code for code in targets if code not in native_ready]
    if not_ready:
        failures.append("native_ready_versions_missing=" + ",".join(not_ready))

    version_items = payload.get("versions")
    if not isinstance(version_items, list) or not version_items:
        failures.append("versions matrix missing")
    else:
        for item in version_items:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "")
            if code in targets and not _as_bool(item.get("native_ready")):
                failures.append(f"{code}.native_ready=false")
            oda_calls = _as_int(item.get("default_customer_oda_calls"))
            if oda_calls:
                failures.append(f"{code}.default_customer_oda_calls={oda_calls}/0")

    return AuditCheck(
        "native_dwg_claim_gate",
        not failures,
        "all target DWG generations are native-ready"
        if not failures
        else "; ".join(failures),
        severity=severity,
    )


def _check_dwg_json_bridge_contract(payload: Any, *, supplied: bool, required: bool) -> AuditCheck:
    severity = "hard" if required else "warning"
    if not supplied:
        if required:
            return AuditCheck(
                "dwg_json_bridge_contract",
                False,
                "commercial DWG JSON bridge contract required but not supplied",
            )
        return AuditCheck("dwg_json_bridge_contract", True, "DWG JSON bridge contract not required")
    if not isinstance(payload, dict):
        return AuditCheck("dwg_json_bridge_contract", False, "DWG JSON bridge contract missing or unreadable", severity=severity)

    failures: list[str] = []
    if payload.get("schema_version") != DWG_JSON_BRIDGE_CONTRACT_SCHEMA:
        failures.append(f"schema_version={payload.get('schema_version')!r}")
    if payload.get("status") != "passed":
        failures.append(f"status={payload.get('status')!r}, expected 'passed'")
    diagnostic_errors = _string_list(payload.get("diagnostic_errors"))
    if diagnostic_errors:
        failures.append("diagnostic_errors=" + ",".join(diagnostic_errors))

    adapter = payload.get("adapter") if isinstance(payload.get("adapter"), dict) else {}
    diagnostics = adapter.get("diagnostics") if isinstance(adapter.get("diagnostics"), dict) else {}
    if diagnostics.get("kind") != "commercial_dwg_json_bridge":
        failures.append(f"diagnostics.kind={diagnostics.get('kind')!r}")
    if not _as_bool(diagnostics.get("command_exists")):
        failures.append("diagnostics.command_exists=false")
    if not str(diagnostics.get("command_sha256") or "").strip():
        failures.append("diagnostics.command_sha256=missing")
    if not _string_list(diagnostics.get("supported_versions")):
        failures.append("diagnostics.supported_versions=missing")
    license_id = str(adapter.get("license_id") or diagnostics.get("license_id") or "")
    allowed = set(_string_list(payload.get("allowed_dwg_license_ids")))
    if not license_id or license_id == "COMMERCIAL-SDK-PENDING":
        failures.append(f"license_id={license_id or 'missing'}")
    elif allowed and license_id not in allowed:
        failures.append(f"license_id {license_id} not in allowed_dwg_license_ids")

    records = payload.get("records")
    if not isinstance(records, list) or not records:
        failures.append("records=missing")
    else:
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                failures.append(f"records[{index}]=non_object")
                continue
            if not _as_bool(record.get("exists")):
                failures.append(f"records[{index}].exists=false")
            if str(record.get("import_status") or "") not in {"ok", "partial"}:
                failures.append(f"records[{index}].import_status={record.get('import_status')!r}")

    return AuditCheck(
        "dwg_json_bridge_contract",
        not failures,
        "DWG JSON bridge contract evidence passed"
        if not failures
        else "; ".join(failures),
        severity=severity,
    )


def _partial_reasons(payloads: Sequence[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    for payload in payloads:
        gaps = payload.get("known_gaps") or payload.get("gaps")
        if isinstance(gaps, dict):
            for version in ("AC1018", "AC1021"):
                if version in gaps:
                    reasons.append(f"{version} real before/after compare baseline gap remains")
    return sorted(set(reasons))


def _check_dwg_json_bridge_product_evidence(payloads: Sequence[dict[str, Any]], *, required: bool) -> AuditCheck:
    if not required:
        return AuditCheck("dwg_json_bridge_product_evidence", True, "DWG JSON bridge product evidence not required")

    failures: list[str] = []
    product_modes = {
        str(payload.get("mode") or payload.get("command") or payload.get("entrypoint") or "").lower()
        for payload in payloads
        if isinstance(payload, dict)
    }
    if not ({"file", "folder", "cad_compare", "cad-compare"} & product_modes):
        failures.append("cad_compare product result/run mode missing")

    bridge_nodes = _json_bridge_product_nodes(payloads)
    if not bridge_nodes:
        failures.append("commercial_dwg_json_bridge diagnostics missing from product result/run evidence")
    bridge_metadata_nodes = _json_bridge_metadata_nodes(payloads)
    if not bridge_metadata_nodes:
        failures.append("commercial_dwg_json_bridge native provenance missing from product result/run evidence")
    for index, node in enumerate(bridge_nodes):
        diagnostics = node.get("diagnostics") if isinstance(node.get("diagnostics"), dict) else {}
        if diagnostics.get("kind") != "commercial_dwg_json_bridge":
            failures.append(f"bridge_nodes[{index}].diagnostics.kind={diagnostics.get('kind')!r}")
        if not _as_bool(diagnostics.get("command_exists")):
            failures.append(f"bridge_nodes[{index}].diagnostics.command_exists=false")
        if not str(diagnostics.get("command_sha256") or "").strip():
            failures.append(f"bridge_nodes[{index}].diagnostics.command_sha256=missing")
        if not _string_list(diagnostics.get("supported_versions")):
            failures.append(f"bridge_nodes[{index}].diagnostics.supported_versions=missing")
        implementation_status = str(node.get("implementation_status") or "")
        if implementation_status in {"", "placeholder", "plugin_load_failed"}:
            failures.append(f"bridge_nodes[{index}].implementation_status={implementation_status or 'missing'}")
        license_id = str(node.get("license_id") or diagnostics.get("license_id") or "")
        if not license_id or license_id == "COMMERCIAL-SDK-PENDING":
            failures.append(f"bridge_nodes[{index}].license_id={license_id or 'missing'}")
        backend_mode = str(node.get("backend_mode") or node.get("dwg_backend_mode") or "")
        if backend_mode != "commercial_sdk":
            failures.append(f"bridge_nodes[{index}].backend_mode={backend_mode or 'missing'}")
    for index, node in enumerate(bridge_metadata_nodes):
        if _bridge_marks_converted_dxf(node):
            failures.append(f"bridge_metadata[{index}].uses_converted_dxf=true")
        if not _bridge_marks_native_dwg(node):
            scope = str(node.get("evidence_scope") or node.get("source_kind") or "").strip()
            failures.append(f"bridge_metadata[{index}].native_evidence_scope={scope or 'missing'}")

    return AuditCheck(
        "dwg_json_bridge_product_evidence",
        not failures,
        "DWG JSON bridge product-path evidence passed" if not failures else "; ".join(failures),
    )


def _native_audit_uses_json_bridge(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    for node in _dict_nodes(payload):
        diagnostics = node.get("adapter_diagnostics") or node.get("diagnostics")
        if isinstance(diagnostics, dict) and diagnostics.get("kind") == "commercial_dwg_json_bridge":
            return True
    return False


def _json_bridge_product_nodes(payloads: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for payload in payloads:
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


def _json_bridge_metadata_nodes(payloads: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    seen: set[int] = set()
    for payload in payloads:
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


def _bridge_marks_converted_dxf(node: dict[str, Any]) -> bool:
    if _as_bool(node.get("uses_converted_dxf")):
        return True
    if node.get("converted_dxf_path") or node.get("effective_dxf_path"):
        return True
    scope = str(node.get("evidence_scope") or node.get("source_kind") or "").strip().lower()
    return any(marker in scope for marker in ("fallback", "converted_dxf", "dxf", "oda", "user_converter"))


def _bridge_marks_native_dwg(node: dict[str, Any]) -> bool:
    if _as_bool(node.get("uses_native_dwg")):
        return True
    scope = str(node.get("evidence_scope") or node.get("source_kind") or "").strip().lower()
    return scope in NATIVE_BRIDGE_EVIDENCE_SCOPES


def _load_json(path: Path | None) -> Any:
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _referenced_result_payloads(payloads: Sequence[dict[str, Any]], *, limit: int = 12) -> list[dict[str, Any]]:
    paths = _referenced_result_json_paths(payloads, limit=limit)
    results: list[dict[str, Any]] = []
    for path in paths:
        payload = _load_json(path)
        if isinstance(payload, dict):
            results.append(payload)
    return results


def _referenced_result_json_paths(payloads: Sequence[dict[str, Any]], *, limit: int = 12) -> list[Path]:
    result_keys = {"output_json", "effective_result_json", "result_json"}
    paths: list[Path] = []
    seen: set[str] = set()
    for key, value in _flatten_key_values(payloads):
        if key not in result_keys:
            continue
        for item in _string_values(value):
            path = Path(item)
            if path.suffix.lower() != ".json" or not path.is_file():
                continue
            resolved = str(path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(path)
            if len(paths) >= limit:
                return paths
    return paths


def _evidence_count(manifest: dict[str, Any], key: str) -> int:
    evidence = manifest.get("evidence_counts")
    if isinstance(evidence, dict) and key in evidence:
        return _as_int(evidence.get(key))

    composition = manifest.get("dataset_composition")
    if isinstance(composition, dict):
        strat = composition.get("stratification")
        if isinstance(strat, dict) and key in strat:
            return _as_int(strat.get(key))

    version_match = re.match(r"(ac10\d+)_converted_dxf_fallback_pairs", key)
    if version_match:
        version = version_match.group(1).upper()
        by_version = manifest.get("converted_dxf_fallback_pairs_by_version")
        if isinstance(by_version, dict):
            return _as_int(by_version.get(version))
    return 0


def _claim_snippets(payloads: Sequence[dict[str, Any]]) -> list[str]:
    keys = {
        "release_claims",
        "allowed_release_claims",
        "proposed_release_wording",
        "marketing_copy",
        "customer_claims",
        "claims",
        "readme_release_wording",
    }
    snippets: list[str] = []
    for key, value in _flatten_key_values(payloads):
        if "forbidden" in key:
            continue
        if key in keys or "release_wording" in key or key.endswith("_claim"):
            snippets.extend(_string_values(value))
    return snippets


def _find_forbidden_runtime(payload: Any) -> list[str]:
    violations: list[str] = []
    runtime_keys = {"runtime_components", "invoked_tools", "converter_invocations", "backend_calls", "tool_invocations"}
    for key, value in _flatten_key_values([payload]):
        if key not in runtime_keys and not key.endswith("_runtime_components"):
            continue
        for item in _string_values(value):
            lowered = item.lower().replace(" ", "")
            for token in FORBIDDEN_RUNTIME_TOKENS:
                if token.replace(" ", "") in lowered:
                    violations.append(f"forbidden runtime component in {key}: {item}")
    return violations


def _find_default_oda_calls(payload: Any) -> list[str]:
    violations: list[str] = []
    for node in _dict_nodes(payload):
        backend = str(node.get("backend_mode") or node.get("dwg_backend_mode") or node.get("backend") or "").lower()
        if backend != "oda_converter":
            continue
        explicit = _as_bool(node.get("explicit") if "explicit" in node else node.get("explicit_backend"))
        mode = str(node.get("mode") or node.get("path_mode") or node.get("execution_mode") or "").lower()
        customer_path = _as_bool(node.get("customer_path")) or mode in {"default", "customer", "native", "customer_default"}
        if customer_path or not explicit:
            violations.append("oda_converter used in default/customer or non-explicit path")
    return violations


def _find_unapproved_commercial_sdk_calls(payload: Any) -> list[str]:
    violations: list[str] = []
    for node in _dict_nodes(payload):
        backend = str(node.get("backend_mode") or node.get("dwg_backend_mode") or node.get("backend") or "").lower()
        if backend != "commercial_sdk":
            continue
        explicit = _as_bool(node.get("explicit") if "explicit" in node else node.get("explicit_backend"))
        implementation_status = str(node.get("implementation_status") or node.get("adapter_implementation_status") or "")
        license_id = str(node.get("license_id") or node.get("adapter_license_id") or "")
        allowed_license_ids = set(_string_list(node.get("allowed_license_ids") or node.get("license_allowlist")))
        explicit_required = (
            "dwg_backend_mode" in node
            or "explicit" in node
            or "explicit_backend" in node
            or "execution_mode" in node
            or "path_mode" in node
        )
        if explicit_required and not explicit:
            violations.append("commercial_sdk used without explicit backend selection")
        if implementation_status in {"", "placeholder", "plugin_load_failed"}:
            violations.append(f"commercial_sdk implementation_status={implementation_status or 'missing'}")
        if not license_id or license_id == "COMMERCIAL-SDK-PENDING":
            violations.append(f"commercial_sdk license_id={license_id or 'missing'}")
        if allowed_license_ids and license_id and license_id not in allowed_license_ids:
            violations.append(f"commercial_sdk license_id {license_id} not in allowed_license_ids")
    return violations


def _metric_value(payloads: Sequence[dict[str, Any]], key: str) -> Any:
    for payload in payloads:
        for metric_block_key in ("metrics", "release_readiness_metrics", "performance", "quality_metrics", "process_cleanup", "path_audit"):
            block = payload.get(metric_block_key)
            if isinstance(block, dict) and key in block:
                return block.get(key)
        if key in payload:
            return payload.get(key)
    for flat_key, value in _flatten_key_values(payloads):
        if flat_key == key:
            return value
    return None


def _flatten_key_values(payloads: Any) -> list[tuple[str, Any]]:
    pairs: list[tuple[str, Any]] = []

    def visit(node: Any, key: str = "") -> None:
        if isinstance(node, dict):
            for child_key, child_value in node.items():
                normalized = str(child_key).lower()
                pairs.append((normalized, child_value))
                visit(child_value, normalized)
        elif isinstance(node, list):
            for item in node:
                visit(item, key)

    visit(payloads)
    return pairs


def _dict_nodes(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, dict):
        yield payload
        for value in payload.values():
            yield from _dict_nodes(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _dict_nodes(item)


def _contains_value(payload: Any, needle: str) -> bool:
    lowered = needle.lower()
    if isinstance(payload, str):
        return payload.lower() == lowered
    if isinstance(payload, dict):
        return any(_contains_value(value, needle) for value in payload.values())
    if isinstance(payload, list):
        return any(_contains_value(value, needle) for value in payload)
    return False


def _has_nonempty_warning(payload: Any) -> bool:
    warning_tokens = ("warning", "partial_import", "unsupported", "skipped", "approximated")
    for key, value in _flatten_key_values([payload]):
        if any(token in key for token in warning_tokens):
            if isinstance(value, (list, tuple, dict)) and len(value) > 0:
                return True
            if isinstance(value, str) and value.strip():
                return True
    return False


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            values.extend(_string_values(item))
        return values
    if isinstance(value, dict):
        values = []
        for item in value.values():
            values.extend(_string_values(item))
        return values
    return []


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, tuple):
        return [str(item) for item in value if item is not None]
    return []


def _key_has(key: str, needles: Sequence[str]) -> bool:
    return any(needle in key for needle in needles)


def _looks_pathish(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(_looks_pathish(item) for item in value)
    if isinstance(value, dict):
        return any(_looks_pathish(item) for item in value.values())
    return value is not None


def _has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    return True


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "passed", "ok"}
    if isinstance(value, (int, float)):
        return value != 0
    return False


if __name__ == "__main__":
    sys.exit(main())
