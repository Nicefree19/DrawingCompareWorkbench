"""Run the end-to-end all-version DWG product release gate.

This orchestrates the approved native/commercial DWG backend validation, JSON
bridge contract, product ``cad_compare file`` evidence, and release readiness
audit.  It does not make a DWG support claim by itself; it produces the
evidence bundle that must pass before such a claim is safe.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import audit_drawing_compare_release_readiness as release_audit  # noqa: E402
from scripts import validate_dwg_native_backend as native_validator  # noqa: E402


SCHEMA_VERSION = "dwg-product-release-gate/v1"
DEFAULT_SUMMARY_JSON = Path("build/reports/dwg-product-release-gate.json")
DEFAULT_RELEASE_AUDIT_JSON = Path("build/reports/dwg-native-release-readiness-audit.json")


def run_gate(
    sample_pack: Path,
    *,
    extra_sample_packs: Sequence[Path] = (),
    customer_evidence_manifest: Path,
    baseline_metrics: Path,
    dwg_all_version_audit: Path,
    adapter_spec: str = native_validator.product_bridge_evidence.DEFAULT_ADAPTER_SPEC,
    allowed_dwg_license_ids: Sequence[str] = ("MIT", "INTERNAL"),
    bridge_command: str | None,
    bridge_args_json: str | None,
    bridge_license_id: str | None,
    bridge_supported_versions: str | None,
    bridge_timeout_seconds: float | None = None,
    validation_json: Path = native_validator.DEFAULT_VALIDATION_JSON,
    validation_md: Path = native_validator.DEFAULT_VALIDATION_MD,
    evidence_json: Path = native_validator.DEFAULT_EVIDENCE_JSON,
    native_audit_json: Path = native_validator.DEFAULT_AUDIT_JSON,
    bridge_contract_json: Path = native_validator.DEFAULT_BRIDGE_CONTRACT_JSON,
    product_evidence_json: Path = native_validator.DEFAULT_PRODUCT_EVIDENCE_JSON,
    product_evidence_output_dir: Path = native_validator.DEFAULT_PRODUCT_EVIDENCE_OUTPUT_DIR,
    product_pair_timeout_seconds: float = 300.0,
    product_max_pairs_per_version: int | None = None,
    release_audit_json: Path = DEFAULT_RELEASE_AUDIT_JSON,
    summary_json: Path = DEFAULT_SUMMARY_JSON,
    max_entities: int = native_validator.validate_sample_pack.DEFAULT_MAX_ENTITIES,
    max_dxf_tokens: int = native_validator.validate_sample_pack.DEFAULT_MAX_DXF_TOKENS,
    import_timeout_seconds: float = native_validator.validate_sample_pack.DEFAULT_IMPORT_TIMEOUT_SECONDS,
    compare_timeout_seconds: float = native_validator.validate_sample_pack.DEFAULT_COMPARE_TIMEOUT_SECONDS,
    skip_compare_over_dxf_mb: float = native_validator.validate_sample_pack.DEFAULT_SKIP_COMPARE_OVER_DXF_MB,
    only_versions: set[str] | None = None,
) -> dict[str, Any]:
    sample_pack = _resolve(sample_pack)
    sample_packs = [sample_pack, *[_resolve(path) for path in extra_sample_packs]]
    customer_evidence_manifest = _resolve(customer_evidence_manifest)
    baseline_metrics = _resolve(baseline_metrics)
    dwg_all_version_audit = _resolve(dwg_all_version_audit)
    validation_json = _resolve(validation_json)
    validation_md = _resolve(validation_md)
    evidence_json = _resolve(evidence_json)
    native_audit_json = _resolve(native_audit_json)
    bridge_contract_json = _resolve(bridge_contract_json)
    product_evidence_json = _resolve(product_evidence_json)
    product_evidence_output_dir = _resolve(product_evidence_output_dir)
    release_audit_json = _resolve(release_audit_json)
    summary_json = _resolve(summary_json)
    allowed = _dedupe(("MIT", "INTERNAL", *allowed_dwg_license_ids))

    if len(sample_packs) == 1:
        native_report = native_validator.run_validation(
            sample_pack,
            adapter_spec=adapter_spec,
            allowed_dwg_license_ids=allowed,
            validation_json=validation_json,
            validation_md=validation_md,
            evidence_json=evidence_json,
            audit_json=native_audit_json,
            bridge_contract_json=bridge_contract_json,
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
            compare_timeout_seconds=compare_timeout_seconds,
            skip_compare_over_dxf_mb=skip_compare_over_dxf_mb,
            only_versions=only_versions,
        )
    else:
        native_report = _run_multi_sample_pack_validation(
            sample_packs,
            adapter_spec=adapter_spec,
            allowed_dwg_license_ids=allowed,
            bridge_command=bridge_command,
            bridge_args_json=bridge_args_json,
            bridge_license_id=bridge_license_id,
            bridge_supported_versions=bridge_supported_versions,
            bridge_timeout_seconds=bridge_timeout_seconds,
            validation_json=validation_json,
            validation_md=validation_md,
            evidence_json=evidence_json,
            native_audit_json=native_audit_json,
            bridge_contract_json=bridge_contract_json,
            product_evidence_json=product_evidence_json,
            product_evidence_output_dir=product_evidence_output_dir,
            product_pair_timeout_seconds=product_pair_timeout_seconds,
            product_max_pairs_per_version=product_max_pairs_per_version,
            max_entities=max_entities,
            max_dxf_tokens=max_dxf_tokens,
            import_timeout_seconds=import_timeout_seconds,
            compare_timeout_seconds=compare_timeout_seconds,
            skip_compare_over_dxf_mb=skip_compare_over_dxf_mb,
            only_versions=only_versions,
            fallback_audit_json=dwg_all_version_audit,
        )

    if native_report.get("status") == "passed":
        release_report = release_audit.run_audit(
            result_json=product_evidence_json,
            run_manifest=product_evidence_json,
            customer_evidence_manifest=customer_evidence_manifest,
            baseline_metrics=baseline_metrics,
            dwg_all_version_audit=dwg_all_version_audit,
            native_dwg_audit=native_audit_json,
            dwg_json_bridge_contract=bridge_contract_json,
            require_native_dwg=True,
        )
    else:
        release_report = {
            "schema_version": 1,
            "status": "skipped",
            "reason": "native_validation_failed",
            "native_validation_status": native_report.get("status"),
        }
    _write_json(release_audit_json, release_report)
    native_audit_payload = _load_json(native_audit_json)
    fallback_audit_payload = _load_json(dwg_all_version_audit)

    status = "passed" if native_report.get("status") == "passed" and release_report.get("status") == "passed" else "failed"
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(),
        "status": status,
        "sample_pack": str(sample_pack),
        "sample_packs": [str(path) for path in sample_packs],
        "adapter_spec": adapter_spec,
        "allowed_dwg_license_ids": list(allowed),
        "target_versions": list(native_report.get("target_versions") or []),
        "native_validation": _compact_native_report(native_report),
        "native_audit_matrix": _compact_native_audit(native_audit_payload, fallback_audit_payload=fallback_audit_payload),
        "fallback_audit_matrix": _compact_fallback_audit(fallback_audit_payload),
        "release_audit": _compact_release_report(release_report),
        "next_actions": _next_actions(
            native_report,
            release_report,
            native_audit_payload,
            fallback_audit_payload=fallback_audit_payload,
        ),
        "paths": {
            "summary_json": str(summary_json),
            "validation_json": str(validation_json),
            "validation_md": str(validation_md),
            "evidence_json": str(evidence_json),
            "native_audit_json": str(native_audit_json),
            "bridge_contract_json": str(bridge_contract_json),
            "product_evidence_json": str(product_evidence_json),
            "product_evidence_output_dir": str(product_evidence_output_dir),
            "release_audit_json": str(release_audit_json),
            "customer_evidence_manifest": str(customer_evidence_manifest),
            "baseline_metrics": str(baseline_metrics),
            "dwg_all_version_audit": str(dwg_all_version_audit),
        },
    }
    _write_json(summary_json, report)
    return report


def _run_multi_sample_pack_validation(
    sample_packs: Sequence[Path],
    *,
    adapter_spec: str,
    allowed_dwg_license_ids: Sequence[str],
    bridge_command: str | None,
    bridge_args_json: str | None,
    bridge_license_id: str | None,
    bridge_supported_versions: str | None,
    bridge_timeout_seconds: float | None,
    validation_json: Path,
    validation_md: Path,
    evidence_json: Path,
    native_audit_json: Path,
    bridge_contract_json: Path,
    product_evidence_json: Path,
    product_evidence_output_dir: Path,
    product_pair_timeout_seconds: float,
    product_max_pairs_per_version: int | None,
    max_entities: int,
    max_dxf_tokens: int,
    import_timeout_seconds: float,
    compare_timeout_seconds: float,
    skip_compare_over_dxf_mb: float,
    only_versions: set[str] | None,
    fallback_audit_json: Path | None = None,
) -> dict[str, Any]:
    target_versions = tuple(sorted(only_versions)) if only_versions else native_validator.native_audit.TARGET_DWG_CODES
    validation_paths: list[Path] = []
    pack_reports: list[dict[str, Any]] = []
    contract_payloads: list[dict[str, Any]] = []
    product_payloads: list[dict[str, Any]] = []

    for index, pack in enumerate(sample_packs, start=1):
        pack_versions = _sample_pack_target_versions(pack, only_versions=only_versions)
        if not pack_versions:
            pack_reports.append(
                {
                    "sample_pack": str(pack),
                    "status": "skipped",
                    "reason": "no_target_versions_in_sample_pack",
                    "target_versions": [],
                }
            )
            continue

        paths = _indexed_output_paths(
            index,
            validation_json=validation_json,
            validation_md=validation_md,
            evidence_json=evidence_json,
            native_audit_json=native_audit_json,
            bridge_contract_json=bridge_contract_json,
            product_evidence_json=product_evidence_json,
            product_evidence_output_dir=product_evidence_output_dir,
        )
        report = native_validator.run_validation(
            pack,
            adapter_spec=adapter_spec,
            allowed_dwg_license_ids=allowed_dwg_license_ids,
            validation_json=paths["validation_json"],
            validation_md=paths["validation_md"],
            evidence_json=paths["evidence_json"],
            audit_json=paths["native_audit_json"],
            bridge_contract_json=paths["bridge_contract_json"],
            bridge_command=bridge_command,
            bridge_args_json=bridge_args_json,
            bridge_license_id=bridge_license_id,
            bridge_supported_versions=bridge_supported_versions,
            bridge_timeout_seconds=bridge_timeout_seconds,
            product_evidence_json=paths["product_evidence_json"],
            product_evidence_output_dir=paths["product_evidence_output_dir"],
            product_pair_timeout_seconds=product_pair_timeout_seconds,
            product_max_pairs_per_version=product_max_pairs_per_version,
            max_entities=max_entities,
            max_dxf_tokens=max_dxf_tokens,
            import_timeout_seconds=import_timeout_seconds,
            compare_timeout_seconds=compare_timeout_seconds,
            skip_compare_over_dxf_mb=skip_compare_over_dxf_mb,
            only_versions=set(pack_versions),
        )
        validation_paths.append(paths["validation_json"])
        validation_payload = _load_json(paths["validation_json"])
        contract_payload = _load_json(paths["bridge_contract_json"])
        product_payload = _load_json(paths["product_evidence_json"])
        if isinstance(contract_payload, dict):
            contract_payloads.append(contract_payload)
        if isinstance(product_payload, dict):
            product_payloads.append(product_payload)
        pack_status = _pack_validation_status(
            report,
            validation_payload=validation_payload if isinstance(validation_payload, dict) else {},
            contract_payload=contract_payload if isinstance(contract_payload, dict) else {},
            product_payload=product_payload if isinstance(product_payload, dict) else {},
        )
        pack_reports.append(
            {
                "sample_pack": str(pack),
                "status": pack_status,
                "native_run_status": report.get("status"),
                "validation_status": validation_payload.get("status") if isinstance(validation_payload, dict) else "",
                "target_versions": list(report.get("target_versions") or pack_versions),
                "validation_json": str(paths["validation_json"]),
                "native_audit_json": str(paths["native_audit_json"]),
                "bridge_contract_json": str(paths["bridge_contract_json"]),
                "product_evidence_json": str(paths["product_evidence_json"]),
                "native_ready_versions": report.get("native_ready_versions") or [],
                "native_missing_versions": report.get("native_missing_versions") or [],
                "backend_check": _compact_backend_check(report.get("backend_check") or {}),
                "bridge_contract": report.get("bridge_contract") or {},
                "product_evidence": report.get("product_evidence") or {},
            }
        )

    if validation_paths:
        evidence_report = native_validator.evidence_builder.build_report(validation_paths)
    else:
        evidence_report = {
            "schema_version": "dwg-all-version-support-evidence/v1",
            "generated_at": datetime.now().isoformat(),
            "source_policy": "no validation summaries were produced",
            "source_summaries": [],
            "versions": {},
            "summary": {
                "source_summary_count": 0,
                "version_count": 0,
                "versions_with_real_pairs": [],
                "versions_with_converted_baselines": [],
                "versions_with_native_baselines": [],
            },
        }
    combined_contract = _combine_bridge_contracts(contract_payloads, allowed_dwg_license_ids=allowed_dwg_license_ids)
    combined_product = _combine_product_evidence(
        product_payloads,
        sample_packs=sample_packs,
        bridge_license_id=bridge_license_id,
        allowed_dwg_license_ids=allowed_dwg_license_ids,
    )
    _merge_fallback_oracle_evidence(
        evidence_report,
        fallback_audit_json=fallback_audit_json,
        target_versions=target_versions,
    )
    _merge_product_bridge_native_evidence(
        evidence_report,
        product_evidence=combined_product,
        target_versions=target_versions,
    )
    evidence_report["sample_packs"] = [str(path) for path in sample_packs]
    evidence_report["pack_reports"] = pack_reports
    evidence_report["bridge_contract"] = combined_contract
    evidence_report["product_evidence"] = combined_product
    _write_json(evidence_json, evidence_report)

    audit_report = native_validator.native_audit.run_audit(
        evidence_manifest=evidence_json,
        claim_scope="native",
        target_versions=target_versions,
    )
    audit_report["bridge_contract"] = combined_contract
    audit_report["product_evidence"] = combined_product
    _write_json(native_audit_json, audit_report)
    _write_json(bridge_contract_json, combined_contract)
    _write_json(product_evidence_json, combined_product)

    failed_pack_reports = [item for item in pack_reports if item.get("status") != "passed"]
    product_failed = combined_product.get("status") == "failed"
    contract_failed = combined_contract.get("status") == "failed"
    status = (
        "passed"
        if not failed_pack_reports
        and not contract_failed
        and not product_failed
        and audit_report.get("status") == "passed"
        else "failed"
    )
    report = {
        "schema_version": "dwg-native-backend-validation-run/v1",
        "generated_at": datetime.now().isoformat(),
        "status": status,
        "validation_status": "passed" if not failed_pack_reports else "failed",
        "native_audit_status": audit_report.get("status"),
        "product_evidence_status": combined_product.get("status"),
        "target_versions": list(target_versions),
        "sample_packs": [str(path) for path in sample_packs],
        "pack_reports": pack_reports,
        "backend_check": _combine_backend_checks(pack_reports),
        "bridge_contract": {
            "status": combined_contract.get("status"),
            "path": str(bridge_contract_json),
            "diagnostic_errors": combined_contract.get("diagnostic_errors") or [],
        },
        "product_evidence": {
            "status": combined_product.get("status"),
            "path": str(product_evidence_json),
            "diagnostic_errors": combined_product.get("diagnostic_errors") or [],
            "pair_count": (combined_product.get("summary") or {}).get("pair_count"),
        },
        "paths": {
            "validation_json": str(validation_json),
            "validation_md": str(validation_md),
            "evidence_json": str(evidence_json),
            "audit_json": str(native_audit_json),
            "bridge_contract_json": str(bridge_contract_json),
            "product_evidence_json": str(product_evidence_json),
            "product_evidence_output_dir": str(product_evidence_output_dir),
        },
        "native_ready_versions": (audit_report.get("summary") or {}).get("native_ready_versions") or [],
        "native_missing_versions": (audit_report.get("summary") or {}).get("native_missing_versions") or [],
    }
    _write_json(validation_json, report)
    _write_text(
        validation_md,
        "# DWG native product gate validation\n\n"
        f"- Status: `{status}`\n"
        f"- Sample packs: `{len(sample_packs)}`\n"
        f"- Native audit: `{audit_report.get('status')}`\n"
        f"- Product evidence: `{combined_product.get('status')}`\n",
    )
    return report


def _pack_validation_status(
    report: dict[str, Any],
    *,
    validation_payload: dict[str, Any],
    contract_payload: dict[str, Any],
    product_payload: dict[str, Any],
) -> str:
    """Return whether one sample pack produced usable evidence for aggregation.

    In multi-pack mode a single pack often cannot satisfy the native audit's
    minimum baseline count by itself.  That is expected; the aggregate audit is
    checked after all pack summaries are merged.  The per-pack gate should only
    fail on backend, sample validation, bridge contract, or product evidence
    failures local to that pack.
    """

    backend_check = report.get("backend_check") or {}
    if backend_check and not backend_check.get("passed"):
        return "failed"
    if str(validation_payload.get("status") or "") not in {"ok", "passed", "partial"}:
        return "failed"
    bridge_contract = report.get("bridge_contract") or {}
    product_evidence = report.get("product_evidence") or {}
    if str(bridge_contract.get("status") or contract_payload.get("status") or "") == "failed":
        return "failed"
    if str(product_evidence.get("status") or product_payload.get("status") or "") == "failed":
        return "failed"
    return "passed"


def _compact_native_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report.get("status"),
        "validation_status": report.get("validation_status"),
        "native_audit_status": report.get("native_audit_status"),
        "product_evidence_status": report.get("product_evidence_status"),
        "native_ready_versions": report.get("native_ready_versions") or [],
        "native_missing_versions": report.get("native_missing_versions") or [],
        "backend_check": {
            "passed": (report.get("backend_check") or {}).get("passed"),
            "backend_mode": (report.get("backend_check") or {}).get("backend_mode"),
            "implementation_status": (report.get("backend_check") or {}).get("implementation_status"),
            "license_id": (report.get("backend_check") or {}).get("license_id"),
            "errors": (report.get("backend_check") or {}).get("errors") or [],
        },
        "bridge_contract": report.get("bridge_contract") or {},
        "product_evidence": report.get("product_evidence") or {},
    }


def _compact_backend_check(check: dict[str, Any]) -> dict[str, Any]:
    return {
        "passed": check.get("passed"),
        "backend_mode": check.get("backend_mode"),
        "implementation_status": check.get("implementation_status"),
        "license_id": check.get("license_id"),
        "errors": check.get("errors") or [],
    }


def _compact_release_report(report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    return {
        "status": report.get("status"),
        "reason": report.get("reason") or "",
        "passed": summary.get("passed"),
        "failed": summary.get("failed"),
        "hard_failed": summary.get("hard_failed"),
        "warnings": summary.get("warnings"),
        "failed_checks": [
            {"name": check.get("name"), "detail": check.get("detail")}
            for check in report.get("checks") or []
            if isinstance(check, dict) and not check.get("passed")
        ],
    }


def _compact_native_audit(payload: Any, *, fallback_audit_payload: Any | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"status": "missing", "summary": {}, "blocked_versions": [], "next_actions": []}
    fallback_by_version = _version_map(fallback_audit_payload)
    blocked_versions = []
    for item in payload.get("versions") or []:
        if not isinstance(item, dict) or item.get("native_ready"):
            continue
        code = str(item.get("code") or "")
        fallback_item = fallback_by_version.get(code, {})
        blocked_versions.append(
            {
                "code": code,
                "native_blockers": item.get("native_blockers") or [],
                "effective_native_blockers": _effective_native_blockers(item, fallback_item),
                "native_next_actions": item.get("native_next_actions") or [],
                "fallback_evidence": _compact_version_evidence(fallback_item),
            }
        )
    return {
        "status": payload.get("status"),
        "claim_scope": payload.get("claim_scope"),
        "summary": payload.get("summary") if isinstance(payload.get("summary"), dict) else {},
        "blocked_versions": blocked_versions,
        "next_actions": payload.get("next_actions") or [],
    }


def _compact_fallback_audit(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"status": "missing", "claim_scope": "", "summary": {}, "ready_versions": [], "blocked_versions": []}
    versions = [item for item in payload.get("versions") or [] if isinstance(item, dict)]
    return {
        "status": payload.get("status"),
        "claim_scope": payload.get("claim_scope"),
        "summary": payload.get("summary") if isinstance(payload.get("summary"), dict) else {},
        "ready_versions": [str(item.get("code") or "") for item in versions if item.get("fallback_ready")],
        "blocked_versions": [
            {
                "code": item.get("code"),
                "fallback_blockers": item.get("fallback_blockers") or [],
                "fallback_next_actions": item.get("fallback_next_actions") or [],
            }
            for item in versions
            if not item.get("fallback_ready")
        ],
    }


def _next_actions(
    native_report: dict[str, Any],
    release_report: dict[str, Any],
    native_audit_payload: Any,
    *,
    fallback_audit_payload: Any | None = None,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    backend_check = native_report.get("backend_check") if isinstance(native_report.get("backend_check"), dict) else {}
    backend_errors = [str(error) for error in backend_check.get("errors") or [] if str(error or "").strip()]
    if any("adapter_unavailable" in error or "bridge" in error for error in backend_errors):
        actions.append(
            {
                "priority": "P0",
                "action": "configure_approved_dwg_bridge",
                "detail": "Provide an approved commercial/native DWG bridge command or backend that is available, license-allowed, and supports every target DWG version.",
                "errors": backend_errors,
            }
        )
    if native_report.get("status") != "passed":
        actions.append(
            {
                "priority": "P0",
                "action": "make_native_validation_pass",
                "detail": "Run the approved native/commercial DWG backend validation until it passes for every target version.",
                "errors": backend_check.get("errors") or [],
            }
        )
    if release_report.get("status") not in {"passed", "skipped"}:
        actions.append(
            {
                "priority": "P0",
                "action": "make_release_readiness_audit_pass",
                "detail": "Fix hard release readiness failures before making all-version/native DWG product claims.",
            }
        )
    fallback_by_version = _version_map(fallback_audit_payload)
    if isinstance(native_audit_payload, dict):
        for action in native_audit_payload.get("next_actions") or []:
            if isinstance(action, dict) and not _is_redundant_with_fallback_evidence(action, fallback_by_version):
                actions.append(action)
    return _dedupe_actions(actions)


def _version_map(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}
    versions = payload.get("versions")
    if not isinstance(versions, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in versions:
        if isinstance(item, dict):
            code = str(item.get("code") or "")
            if code:
                result[code] = item
    return result


def _compact_version_evidence(item: dict[str, Any]) -> dict[str, Any]:
    if not item:
        return {}
    return {
        "fallback_ready": bool(item.get("fallback_ready")),
        "sample_count": item.get("sample_count"),
        "real_pair_count": item.get("real_pair_count"),
        "converted_dxf_baseline_count": item.get("converted_dxf_baseline_count"),
        "fallback_blockers": item.get("fallback_blockers") or [],
    }


def _effective_native_blockers(native_item: dict[str, Any], fallback_item: dict[str, Any]) -> list[str]:
    blockers = [str(item) for item in native_item.get("native_blockers") or []]
    if not fallback_item:
        return blockers
    if _fallback_corpus_ready(fallback_item):
        blockers = [
            blocker
            for blocker in blockers
            if not (
                blocker.startswith("sample_count=")
                or blocker.startswith("real_pair_count=")
                or blocker.startswith("converted_dxf_baseline_count=")
            )
        ]
    return blockers


def _is_redundant_with_fallback_evidence(action: dict[str, Any], fallback_by_version: dict[str, dict[str, Any]]) -> bool:
    redundant_actions = {
        "collect_native_gate_samples",
        "confirm_native_compare_pairs",
        "capture_native_oracle_baselines",
    }
    action_name = str(action.get("action") or "")
    if action_name not in redundant_actions:
        return False
    code = str(action.get("code") or "")
    return _fallback_corpus_ready(fallback_by_version.get(code, {}))


def _fallback_corpus_ready(item: dict[str, Any]) -> bool:
    return bool(item.get("fallback_ready")) or (
        _as_int(item.get("sample_count")) >= 2
        and _as_int(item.get("real_pair_count")) >= 2
        and _as_int(item.get("converted_dxf_baseline_count")) >= 2
    )


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _dedupe_actions(actions: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for action in actions:
        key = (
            str(action.get("priority") or ""),
            str(action.get("code") or ""),
            str(action.get("action") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(action)
    return result


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _sample_pack_target_versions(sample_pack: Path, *, only_versions: set[str] | None) -> tuple[str, ...]:
    requested = {str(code).upper() for code in only_versions} if only_versions else set(native_validator.native_audit.TARGET_DWG_CODES)
    try:
        _, manifest = native_validator.validate_sample_pack.load_manifest(sample_pack)
    except Exception:
        return tuple(sorted(requested))
    versions = manifest.get("versions") if isinstance(manifest.get("versions"), dict) else {}
    available = {str(code).upper() for code in versions}
    return tuple(code for code in native_validator.native_audit.TARGET_DWG_CODES if code in requested and code in available)


def _indexed_output_paths(
    index: int,
    *,
    validation_json: Path,
    validation_md: Path,
    evidence_json: Path,
    native_audit_json: Path,
    bridge_contract_json: Path,
    product_evidence_json: Path,
    product_evidence_output_dir: Path,
) -> dict[str, Path]:
    suffix = f"pack{index:02d}"
    return {
        "validation_json": _with_suffix_token(validation_json, suffix),
        "validation_md": _with_suffix_token(validation_md, suffix),
        "evidence_json": _with_suffix_token(evidence_json, suffix),
        "native_audit_json": _with_suffix_token(native_audit_json, suffix),
        "bridge_contract_json": _with_suffix_token(bridge_contract_json, suffix),
        "product_evidence_json": _with_suffix_token(product_evidence_json, suffix),
        "product_evidence_output_dir": product_evidence_output_dir / suffix,
    }


def _with_suffix_token(path: Path, token: str) -> Path:
    return path.with_name(f"{path.stem}.{token}{path.suffix}")


def _combine_backend_checks(pack_reports: Sequence[dict[str, Any]]) -> dict[str, Any]:
    checks = [item.get("backend_check") for item in pack_reports if isinstance(item.get("backend_check"), dict)]
    errors = [
        error
        for index, check in enumerate(checks, start=1)
        for error in check.get("errors") or []
        if str(error or "").strip()
    ]
    first = next((check for check in checks if check), {})
    return {
        "passed": bool(checks) and all(bool(check.get("passed")) for check in checks),
        "backend_mode": first.get("backend_mode"),
        "implementation_status": first.get("implementation_status"),
        "license_id": first.get("license_id"),
        "errors": errors,
    }


def _combine_bridge_contracts(
    reports: Sequence[dict[str, Any]],
    *,
    allowed_dwg_license_ids: Sequence[str],
) -> dict[str, Any]:
    records = [record for report in reports for record in report.get("records") or [] if isinstance(record, dict)]
    errors = [
        f"pack{index:02d}:{error}"
        for index, report in enumerate(reports, start=1)
        for error in report.get("diagnostic_errors") or []
    ]
    failed = [f"pack{index:02d}:status={report.get('status')}" for index, report in enumerate(reports, start=1) if report.get("status") != "passed"]
    adapter = next((report.get("adapter") for report in reports if isinstance(report.get("adapter"), dict) and report.get("adapter")), {})
    missing = [record for record in records if not record.get("exists")]
    failed_imports = [record for record in records if str(record.get("import_status") or "") not in {"ok", "partial"}]
    diagnostic_errors = [*errors, *failed]
    if not reports:
        diagnostic_errors.append("bridge_contract_reports_missing")
    if not records:
        diagnostic_errors.append("bridge_contract_records_missing")
    status = "passed" if reports and not diagnostic_errors and not missing and not failed_imports else "failed"
    return {
        "schema_version": "dwg-json-bridge-contract-validation/v1",
        "generated_at": datetime.now().isoformat(),
        "status": status,
        "summary": {
            "input_count": len(records),
            "accepted_import_count": len(records) - len(failed_imports),
            "failed_import_count": len(failed_imports),
            "missing_input_count": len(missing),
            "diagnostic_error_count": len(diagnostic_errors),
            "source_report_count": len(reports),
        },
        "adapter": adapter,
        "allowed_dwg_license_ids": list(_dedupe(allowed_dwg_license_ids)),
        "diagnostic_errors": diagnostic_errors,
        "records": records,
        "source_reports": [report.get("path") or report.get("sample_pack") or "" for report in reports],
    }


def _combine_product_evidence(
    reports: Sequence[dict[str, Any]],
    *,
    sample_packs: Sequence[Path],
    bridge_license_id: str | None,
    allowed_dwg_license_ids: Sequence[str],
) -> dict[str, Any]:
    pairs = [pair for report in reports for pair in report.get("pairs") or [] if isinstance(pair, dict)]
    bridge_reports = [
        item
        for report in reports
        for item in report.get("bridge_adapter_reports") or []
        if isinstance(item, dict)
    ]
    errors = [
        f"pack{index:02d}:{error}"
        for index, report in enumerate(reports, start=1)
        for error in report.get("diagnostic_errors") or []
    ]
    failed = [f"pack{index:02d}:status={report.get('status')}" for index, report in enumerate(reports, start=1) if report.get("status") != "passed"]
    diagnostic_errors = [*errors, *failed]
    if not reports:
        diagnostic_errors.append("product_evidence_reports_missing")
    if not pairs:
        diagnostic_errors.append("product_evidence_pairs_missing")
    versions = sorted({str(pair.get("version") or "").upper() for pair in pairs if str(pair.get("version") or "").strip()})
    failed_pairs = [pair for pair in pairs if pair.get("status") != "passed"]
    status = "passed" if reports and pairs and not diagnostic_errors and not failed_pairs else "failed"
    process_cleanup = _combine_process_cleanup(reports)
    return {
        "schema_version": native_validator.product_bridge_evidence.SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(),
        "status": status,
        "mode": "cad_compare",
        "command": "cad_compare",
        "entrypoint": "multi_sample_pack",
        "dwg_backend_mode": "commercial_sdk",
        "explicit": True,
        "customer_path": False,
        "implementation_status": "json_bridge_configured",
        "license_id": bridge_license_id or "",
        "allowed_license_ids": list(_dedupe(allowed_dwg_license_ids)),
        "sample_packs": [str(path) for path in sample_packs],
        "summary": {
            "version_count": len(versions),
            "pair_count": len(pairs),
            "executed_pair_count": len(pairs),
            "passed_pair_count": len(pairs) - len(failed_pairs),
            "failed_pair_count": len(failed_pairs),
            "bridge_evidence_pair_count": sum(1 for pair in pairs if pair.get("bridge_evidence_present")),
            "bridge_adapter_report_count": len(bridge_reports),
            "source_report_count": len(reports),
            "versions": versions,
        },
        "diagnostic_errors": diagnostic_errors,
        "process_cleanup": process_cleanup,
        "pairs": pairs,
        "bridge_adapter_reports": bridge_reports,
    }


def _combine_process_cleanup(reports: Sequence[dict[str, Any]]) -> dict[str, Any]:
    orphan_values: list[int] = []
    timeout_values: list[float] = []
    for report in reports:
        cleanup = report.get("process_cleanup") if isinstance(report.get("process_cleanup"), dict) else {}
        if "orphan_processes" in cleanup:
            orphan_values.append(_as_int(cleanup.get("orphan_processes")))
        timeout = _as_float(cleanup.get("pair_timeout_seconds"))
        if timeout is not None:
            timeout_values.append(timeout)

    result: dict[str, Any] = {"source_report_count": len(reports)}
    if orphan_values:
        result["orphan_processes"] = max(orphan_values)
    if timeout_values:
        result["pair_timeout_seconds"] = max(timeout_values)
    return result


def _merge_fallback_oracle_evidence(
    evidence_report: dict[str, Any],
    *,
    fallback_audit_json: Path | None,
    target_versions: Sequence[str],
) -> None:
    if fallback_audit_json is None:
        return
    fallback_payload = _load_json(fallback_audit_json)
    fallback_by_version = _version_map(fallback_payload)
    versions = evidence_report.setdefault("versions", {})
    if not isinstance(versions, dict):
        return
    source = str(fallback_audit_json)
    for code in target_versions:
        fallback_item = fallback_by_version.get(str(code), {})
        if not _fallback_corpus_ready(fallback_item):
            continue
        item = versions.setdefault(str(code), _empty_version_evidence())
        if not isinstance(item, dict):
            continue
        _max_int_field(item, "sample_count", fallback_item.get("sample_count"))
        _max_int_field(item, "real_pair_count", fallback_item.get("real_pair_count"))
        _max_int_field(item, "converted_dxf_baseline_count", fallback_item.get("converted_dxf_baseline_count"))
        _max_int_field(item, "fallback_candidate_count", fallback_item.get("fallback_candidate_count"))
        item["fallback_supported"] = bool(item.get("fallback_supported")) or bool(fallback_item.get("fallback_supported"))
        item.setdefault("oracle_baseline_sources", [])
        if isinstance(item["oracle_baseline_sources"], list) and source not in item["oracle_baseline_sources"]:
            item["oracle_baseline_sources"].append(source)
        item.setdefault("sources", [])
        if isinstance(item["sources"], list) and source not in item["sources"]:
            item["sources"].append(source)

    summary = evidence_report.setdefault("summary", {})
    if isinstance(summary, dict):
        ready = set(summary.get("versions_with_converted_baselines") or [])
        for code, item in versions.items():
            if isinstance(item, dict) and _as_int(item.get("converted_dxf_baseline_count")) > 0:
                ready.add(str(code))
        summary["versions_with_converted_baselines"] = sorted(ready)


def _merge_product_bridge_native_evidence(
    evidence_report: dict[str, Any],
    *,
    product_evidence: dict[str, Any],
    target_versions: Sequence[str],
) -> None:
    if not isinstance(product_evidence, dict) or product_evidence.get("status") != "passed":
        return
    versions = evidence_report.setdefault("versions", {})
    if not isinstance(versions, dict):
        return
    target_set = {str(code).upper() for code in target_versions}
    source = "combined_product_bridge_evidence"
    for pair in product_evidence.get("pairs") or []:
        if not isinstance(pair, dict) or pair.get("status") != "passed":
            continue
        code = str(pair.get("version") or pair.get("version_code") or "").upper()
        if code not in target_set or not _product_pair_has_native_bridge_provenance(pair):
            continue
        item = versions.setdefault(code, _empty_version_evidence())
        if not isinstance(item, dict):
            continue
        pair_key = _product_pair_key(pair, code)
        sample_paths = _string_values(pair.get("source_a"), pair.get("source_b"))
        _extend_unique(item, "sample_paths", sample_paths)
        _extend_unique(item, "real_pair_keys", [pair_key])
        _extend_unique(item, "native_baseline_keys", [pair_key])
        backend_mode = _product_pair_backend_mode(pair) or str(product_evidence.get("dwg_backend_mode") or "commercial_sdk")
        _extend_unique(item, "native_backend_modes", [backend_mode])
        _extend_unique(item, "sources", [source])
        item["native_supported"] = True
        _max_int_field(item, "sample_count", len(item.get("sample_paths") or []))
        _max_int_field(item, "real_pair_count", len(item.get("real_pair_keys") or []))
        _max_int_field(item, "native_baseline_count", len(item.get("native_baseline_keys") or []))

    summary = evidence_report.setdefault("summary", {})
    if isinstance(summary, dict):
        native_ready = set(summary.get("versions_with_native_baselines") or [])
        real_ready = set(summary.get("versions_with_real_pairs") or [])
        for code, item in versions.items():
            if not isinstance(item, dict):
                continue
            if _as_int(item.get("native_baseline_count")) > 0:
                native_ready.add(str(code))
            if _as_int(item.get("real_pair_count")) > 0:
                real_ready.add(str(code))
        summary["versions_with_native_baselines"] = sorted(native_ready)
        summary["versions_with_real_pairs"] = sorted(real_ready)


def _product_pair_has_native_bridge_provenance(pair: dict[str, Any]) -> bool:
    if pair.get("bridge_native_provenance_present") is True:
        return True
    for metadata in pair.get("bridge_adapter_metadata") or []:
        if not isinstance(metadata, dict):
            continue
        if _bridge_metadata_marks_native(metadata):
            return True
    return False


def _bridge_metadata_marks_native(metadata: dict[str, Any]) -> bool:
    if _truthy(metadata.get("uses_converted_dxf")):
        return False
    scope = str(metadata.get("evidence_scope") or metadata.get("source_kind") or "").strip().lower()
    native_scopes = native_validator.evidence_builder.NATIVE_BRIDGE_EVIDENCE_SCOPES
    return _truthy(metadata.get("uses_native_dwg")) or scope in native_scopes


def _product_pair_key(pair: dict[str, Any], code: str) -> str:
    source_a = str(pair.get("source_a") or pair.get("before_path") or "")
    source_b = str(pair.get("source_b") or pair.get("after_path") or "")
    index = str(pair.get("pair_index") or pair.get("pair_id") or "")
    output = str(pair.get("output_json") or "")
    return "|".join(part for part in (code, source_a, source_b, index, output) if part)


def _product_pair_backend_mode(pair: dict[str, Any]) -> str:
    provenance = pair.get("provenance") if isinstance(pair.get("provenance"), dict) else {}
    return str(
        provenance.get("selected_dwg_backend_mode")
        or pair.get("dwg_backend_mode")
        or pair.get("backend")
        or ""
    )


def _extend_unique(item: dict[str, Any], key: str, values: Sequence[str]) -> None:
    existing = item.setdefault(key, [])
    if not isinstance(existing, list):
        existing = []
        item[key] = existing
    seen = {str(value) for value in existing}
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            existing.append(text)
            seen.add(text)


def _string_values(*values: Any) -> list[str]:
    return [str(value) for value in values if str(value or "").strip()]


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _empty_version_evidence() -> dict[str, Any]:
    return {
        "sample_count": 0,
        "real_pair_count": 0,
        "converted_dxf_baseline_count": 0,
        "fallback_supported": False,
        "fallback_candidate_count": 0,
        "native_supported": False,
        "native_baseline_count": 0,
        "default_customer_oda_calls": 0,
        "sample_paths": [],
        "real_pair_keys": [],
        "converted_baseline_keys": [],
        "native_baseline_keys": [],
        "native_backend_modes": [],
        "sources": [],
    }


def _max_int_field(item: dict[str, Any], key: str, value: Any) -> None:
    item[key] = max(_as_int(item.get(key)), _as_int(value))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _dedupe(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in result:
            result.append(item)
    return tuple(result)


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sample_pack", type=Path)
    parser.add_argument("--extra-sample-pack", action="append", type=Path, default=None)
    parser.add_argument("--customer-evidence-manifest", type=Path, required=True)
    parser.add_argument("--baseline-metrics", type=Path, required=True)
    parser.add_argument("--dwg-all-version-audit", type=Path, required=True)
    parser.add_argument("--adapter-spec", default=native_validator.product_bridge_evidence.DEFAULT_ADAPTER_SPEC)
    parser.add_argument("--dwg-allowed-license-id", action="append", default=None)
    parser.add_argument("--bridge-command", required=True)
    parser.add_argument("--bridge-args-json", required=True)
    parser.add_argument("--bridge-license-id", required=True)
    parser.add_argument("--bridge-supported-versions", required=True)
    parser.add_argument("--bridge-timeout-seconds", type=float)
    parser.add_argument("--validation-json", type=Path, default=native_validator.DEFAULT_VALIDATION_JSON)
    parser.add_argument("--validation-md", type=Path, default=native_validator.DEFAULT_VALIDATION_MD)
    parser.add_argument("--evidence-json", type=Path, default=native_validator.DEFAULT_EVIDENCE_JSON)
    parser.add_argument("--native-audit-json", type=Path, default=native_validator.DEFAULT_AUDIT_JSON)
    parser.add_argument("--bridge-contract-json", type=Path, default=native_validator.DEFAULT_BRIDGE_CONTRACT_JSON)
    parser.add_argument("--product-evidence-json", type=Path, default=native_validator.DEFAULT_PRODUCT_EVIDENCE_JSON)
    parser.add_argument("--product-evidence-output-dir", type=Path, default=native_validator.DEFAULT_PRODUCT_EVIDENCE_OUTPUT_DIR)
    parser.add_argument("--product-pair-timeout-seconds", type=float, default=300.0)
    parser.add_argument("--product-max-pairs-per-version", type=int)
    parser.add_argument("--release-audit-json", type=Path, default=DEFAULT_RELEASE_AUDIT_JSON)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    parser.add_argument("--max-entities", type=int, default=native_validator.validate_sample_pack.DEFAULT_MAX_ENTITIES)
    parser.add_argument("--max-dxf-tokens", type=int, default=native_validator.validate_sample_pack.DEFAULT_MAX_DXF_TOKENS)
    parser.add_argument("--import-timeout-seconds", type=float, default=native_validator.validate_sample_pack.DEFAULT_IMPORT_TIMEOUT_SECONDS)
    parser.add_argument("--compare-timeout-seconds", type=float, default=native_validator.validate_sample_pack.DEFAULT_COMPARE_TIMEOUT_SECONDS)
    parser.add_argument("--skip-compare-over-dxf-mb", type=float, default=native_validator.validate_sample_pack.DEFAULT_SKIP_COMPARE_OVER_DXF_MB)
    parser.add_argument("--version", action="append", default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_gate(
        args.sample_pack,
        extra_sample_packs=tuple(args.extra_sample_pack or ()),
        customer_evidence_manifest=args.customer_evidence_manifest,
        baseline_metrics=args.baseline_metrics,
        dwg_all_version_audit=args.dwg_all_version_audit,
        adapter_spec=args.adapter_spec,
        allowed_dwg_license_ids=tuple(args.dwg_allowed_license_id or ()),
        bridge_command=args.bridge_command,
        bridge_args_json=args.bridge_args_json,
        bridge_license_id=args.bridge_license_id,
        bridge_supported_versions=args.bridge_supported_versions,
        bridge_timeout_seconds=args.bridge_timeout_seconds,
        validation_json=args.validation_json,
        validation_md=args.validation_md,
        evidence_json=args.evidence_json,
        native_audit_json=args.native_audit_json,
        bridge_contract_json=args.bridge_contract_json,
        product_evidence_json=args.product_evidence_json,
        product_evidence_output_dir=args.product_evidence_output_dir,
        product_pair_timeout_seconds=args.product_pair_timeout_seconds,
        product_max_pairs_per_version=args.product_max_pairs_per_version,
        release_audit_json=args.release_audit_json,
        summary_json=args.summary_json,
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
