"""Diagnose approved native/commercial DWG JSON bridge readiness.

This is a fast preflight for the final DWG product gate.  It does not make a
native-support claim by itself; it verifies that the configured bridge command
is present, license-allowed, declares support for target DWG versions, and can
optionally emit positive native DWG provenance for probe inputs.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.comparison.commercial_dwg_json_adapter import CommercialDwgJsonBridgeAdapter  # noqa: E402
from src.services.comparison.dwg_importer import DwgImportError, DwgVersionDetector  # noqa: E402


SCHEMA_VERSION = "dwg-native-bridge-doctor/v1"
TARGET_DWG_CODES = ("AC1009", "AC1012", "AC1014", "AC1015", "AC1018", "AC1021", "AC1024", "AC1027", "AC1032")
NATIVE_BRIDGE_EVIDENCE_SCOPES = {
    "native_dwg",
    "native_dwg_bridge",
    "commercial_dwg_native",
    "commercial_native",
    "commercial_sdk_native",
}


@dataclass(frozen=True)
class DoctorCheck:
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


def run_doctor(
    *,
    bridge_command: str | None = None,
    bridge_args_json: str | None = None,
    bridge_license_id: str | None = None,
    bridge_supported_versions: str | None = None,
    allowed_dwg_license_ids: Sequence[str] = ("MIT", "INTERNAL"),
    target_versions: Sequence[str] = TARGET_DWG_CODES,
    probe_inputs: Sequence[Path] = (),
    require_probe: bool = False,
    bridge_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    target_codes = tuple(_normalize_versions(target_versions) or TARGET_DWG_CODES)
    allowed_ids = tuple(_dedupe(("MIT", "INTERNAL", *allowed_dwg_license_ids)))
    args_template, args_error = _parse_args_template(bridge_args_json)
    adapter = CommercialDwgJsonBridgeAdapter(
        command=bridge_command,
        args_template=args_template,
        license_id=bridge_license_id,
        supported_versions=bridge_supported_versions,
        timeout_seconds=bridge_timeout_seconds,
    )
    diagnostics = adapter.diagnostics()
    supported = set(_normalize_versions(diagnostics.get("supported_versions")))
    missing_versions = [code for code in target_codes if code not in supported and "*" not in supported]

    checks = [
        _check_args_template(args_template, args_error),
        _check_command(diagnostics),
        _check_license(adapter.license_id, allowed_ids),
        _check_supported_versions(missing_versions, target_codes),
        _check_adapter_available(adapter),
        *_probe_checks(adapter, probe_inputs, require_probe=require_probe),
    ]
    failed = [check for check in checks if not check.passed]
    hard_failed = [check for check in failed if check.severity == "hard"]
    warnings = [check for check in failed if check.severity == "warning"]
    status = "failed" if hard_failed else ("partial" if warnings else "passed")

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(),
        "status": status,
        "target_versions": list(target_codes),
        "allowed_dwg_license_ids": list(allowed_ids),
        "bridge": {
            "command": bridge_command or "",
            "args_json": bridge_args_json or "",
            "license_id": bridge_license_id or "",
            "supported_versions": _normalize_versions(bridge_supported_versions),
            "timeout_seconds": bridge_timeout_seconds,
        },
        "diagnostics": diagnostics,
        "summary": {
            "passed": sum(1 for check in checks if check.passed),
            "failed": len(failed),
            "hard_failed": len(hard_failed),
            "warnings": len(warnings),
            "missing_versions": missing_versions,
            "probe_count": len(probe_inputs),
        },
        "checks": [check.to_json() for check in checks],
        "next_actions": _next_actions(checks, missing_versions),
    }


def _check_args_template(args_template: tuple[str, ...] | None, args_error: str | None) -> DoctorCheck:
    if args_error:
        return DoctorCheck("bridge_args_template", False, args_error)
    if not args_template:
        return DoctorCheck("bridge_args_template", False, "bridge_args_json missing or empty")
    if "{input}" not in " ".join(args_template) and "{path}" not in " ".join(args_template):
        return DoctorCheck("bridge_args_template", False, "args template must include {input} or {path}")
    return DoctorCheck("bridge_args_template", True, "bridge args template is explicit")


def _check_command(diagnostics: dict[str, Any]) -> DoctorCheck:
    if not str(diagnostics.get("command") or "").strip():
        return DoctorCheck("bridge_command", False, "bridge command is not configured")
    if not diagnostics.get("command_exists"):
        return DoctorCheck("bridge_command", False, "bridge command does not exist", evidence=[str(diagnostics.get("command") or "")])
    if not str(diagnostics.get("command_sha256") or "").strip():
        return DoctorCheck("bridge_command", False, "bridge command SHA-256 is missing")
    return DoctorCheck("bridge_command", True, "bridge command exists and is fingerprinted", evidence=[str(diagnostics.get("resolved_command") or "")])


def _check_license(license_id: str, allowed: Sequence[str]) -> DoctorCheck:
    if not license_id or license_id == "COMMERCIAL-SDK-PENDING":
        return DoctorCheck("bridge_license", False, f"bridge license_id={license_id or 'missing'}")
    if allowed and license_id not in allowed:
        return DoctorCheck("bridge_license", False, f"bridge license_id {license_id} not in allowed_dwg_license_ids")
    return DoctorCheck("bridge_license", True, "bridge license is present and allowed")


def _check_supported_versions(missing_versions: Sequence[str], targets: Sequence[str]) -> DoctorCheck:
    if missing_versions:
        return DoctorCheck("bridge_supported_versions", False, "missing target versions: " + ",".join(missing_versions))
    return DoctorCheck("bridge_supported_versions", True, "bridge declares support for target versions", evidence=list(targets))


def _check_adapter_available(adapter: CommercialDwgJsonBridgeAdapter) -> DoctorCheck:
    if not adapter.is_available():
        return DoctorCheck("bridge_adapter_available", False, "adapter is not available")
    return DoctorCheck("bridge_adapter_available", True, "adapter is available")


def _probe_checks(
    adapter: CommercialDwgJsonBridgeAdapter,
    probe_inputs: Sequence[Path],
    *,
    require_probe: bool,
) -> list[DoctorCheck]:
    if not probe_inputs:
        severity = "hard" if require_probe else "warning"
        return [
            DoctorCheck(
                "native_provenance_probe",
                False,
                "native provenance probe input not supplied",
                severity=severity,
            )
        ]
    checks: list[DoctorCheck] = []
    detector = DwgVersionDetector()
    for index, path in enumerate(probe_inputs):
        candidate = Path(path)
        try:
            version = detector.detect_file(candidate)
            drawing = adapter.read_file(candidate, version)
        except DwgImportError as exc:
            checks.append(
                DoctorCheck(
                    f"native_provenance_probe[{index}]",
                    False,
                    f"{exc.code}: {exc}",
                    evidence=[str(candidate)],
                )
            )
            continue
        except Exception as exc:
            checks.append(
                DoctorCheck(
                    f"native_provenance_probe[{index}]",
                    False,
                    f"{type(exc).__name__}: {exc}",
                    evidence=[str(candidate)],
                )
            )
            continue
        bridge = drawing.metadata.get("commercial_dwg_json_bridge") if isinstance(drawing.metadata, dict) else None
        provenance_errors = _native_provenance_errors(bridge)
        checks.append(
            DoctorCheck(
                f"native_provenance_probe[{index}]",
                not provenance_errors,
                "native bridge provenance present" if not provenance_errors else "; ".join(provenance_errors),
                evidence=[str(candidate)],
            )
        )
    return checks


def _native_provenance_errors(bridge: Any) -> list[str]:
    if not isinstance(bridge, dict):
        return ["commercial_dwg_json_bridge metadata missing"]
    errors: list[str] = []
    if _bridge_marks_converted_dxf(bridge):
        errors.append("uses_converted_dxf=true")
    if not _bridge_marks_native_dwg(bridge):
        scope = str(bridge.get("evidence_scope") or bridge.get("source_kind") or "").strip()
        errors.append(f"native_evidence_scope={scope or 'missing'}")
    return errors


def _bridge_marks_converted_dxf(bridge: dict[str, Any]) -> bool:
    if _truthy(bridge.get("uses_converted_dxf")):
        return True
    if bridge.get("converted_dxf_path") or bridge.get("effective_dxf_path"):
        return True
    scope = str(bridge.get("evidence_scope") or bridge.get("source_kind") or "").strip().lower()
    return any(marker in scope for marker in ("fallback", "converted_dxf", "dxf", "oda", "user_converter"))


def _bridge_marks_native_dwg(bridge: dict[str, Any]) -> bool:
    if _truthy(bridge.get("uses_native_dwg")):
        return True
    scope = str(bridge.get("evidence_scope") or bridge.get("source_kind") or "").strip().lower()
    return scope in NATIVE_BRIDGE_EVIDENCE_SCOPES


def _next_actions(checks: Sequence[DoctorCheck], missing_versions: Sequence[str]) -> list[dict[str, Any]]:
    failed_names = {check.name for check in checks if not check.passed}
    actions: list[dict[str, Any]] = []
    if "bridge_command" in failed_names or "bridge_adapter_available" in failed_names:
        actions.append(
            {
                "priority": "P0",
                "action": "configure_available_bridge_command",
                "detail": "Provide an approved DWG native/commercial bridge command that exists on this machine.",
            }
        )
    if "bridge_license" in failed_names:
        actions.append(
            {
                "priority": "P0",
                "action": "approve_bridge_license",
                "detail": "Set a non-placeholder bridge license id and include it in --dwg-allowed-license-id.",
            }
        )
    if missing_versions:
        actions.append(
            {
                "priority": "P0",
                "action": "extend_bridge_supported_versions",
                "detail": "Bridge must explicitly support every target version: " + ",".join(missing_versions),
            }
        )
    if any(check.name.startswith("native_provenance_probe") and not check.passed for check in checks):
        actions.append(
            {
                "priority": "P0",
                "action": "emit_native_bridge_provenance",
                "detail": "Bridge output must include commercial_dwg_json_bridge metadata with uses_native_dwg=true or an approved native evidence_scope.",
            }
        )
    return actions


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge-command")
    parser.add_argument("--bridge-args-json")
    parser.add_argument("--bridge-license-id")
    parser.add_argument("--bridge-supported-versions")
    parser.add_argument("--dwg-allowed-license-id", action="append", default=[])
    parser.add_argument("--target-version", action="append", dest="target_versions")
    parser.add_argument("--probe-input", action="append", type=Path, default=[])
    parser.add_argument("--require-probe", action="store_true")
    parser.add_argument("--bridge-timeout-seconds", type=float)
    parser.add_argument("--out", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_doctor(
        bridge_command=args.bridge_command,
        bridge_args_json=args.bridge_args_json,
        bridge_license_id=args.bridge_license_id,
        bridge_supported_versions=args.bridge_supported_versions,
        allowed_dwg_license_ids=args.dwg_allowed_license_id,
        target_versions=args.target_versions or TARGET_DWG_CODES,
        probe_inputs=args.probe_input,
        require_probe=args.require_probe,
        bridge_timeout_seconds=args.bridge_timeout_seconds,
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report["status"] == "passed" else 1


def _parse_args_template(raw: str | None) -> tuple[tuple[str, ...] | None, str | None]:
    if not str(raw or "").strip():
        return None, "bridge_args_json missing"
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        return None, f"bridge_args_json invalid: {exc}"
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        return None, "bridge_args_json must be a JSON array of strings"
    if not parsed:
        return None, "bridge_args_json must not be empty"
    return tuple(parsed), None


def _normalize_versions(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = value.replace(";", ",").split(",")
    elif isinstance(value, Sequence):
        raw = [str(item) for item in value]
    else:
        raw = []
    return sorted({item.strip().upper() for item in raw if item and item.strip()})


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


if __name__ == "__main__":
    raise SystemExit(main())
