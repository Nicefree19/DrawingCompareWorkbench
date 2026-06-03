"""Validate the commercial DWG JSON bridge contract against DWG files.

This is a pre-sample-pack smoke gate for approved commercial/internal SDK
wrappers. It verifies that the configured wrapper can feed DwgAdapterDrawing
JSON into the commercial_sdk import path and records the bridge provenance.
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

from src.services.comparison.commercial_dwg_json_adapter import (  # noqa: E402
    ARGS_JSON_ENV,
    COMMAND_ENV,
    LICENSE_ID_ENV,
    SUPPORTED_VERSIONS_ENV,
    TIMEOUT_SECONDS_ENV,
)
from src.services.comparison.dwg_backend import (  # noqa: E402
    COMMERCIAL_SDK_ADAPTER_ENV,
    DWG_BACKEND_COMMERCIAL_SDK,
)
from src.services.comparison.dwg_importer import DwgImporter, DwgVersionDetector  # noqa: E402


DEFAULT_ADAPTER_SPEC = "src.services.comparison.commercial_dwg_json_adapter:create_adapter"
DEFAULT_JSON_REPORT = Path("build/reports/dwg-json-bridge-contract.json")
ACCEPTED_IMPORT_STATUSES = {"ok", "partial"}


def validate_contract(
    dwg_paths: Sequence[Path],
    *,
    adapter_spec: str = DEFAULT_ADAPTER_SPEC,
    allowed_dwg_license_ids: Sequence[str] = ("MIT", "INTERNAL"),
    bridge_command: str | None = None,
    bridge_args_json: str | None = None,
    bridge_license_id: str | None = None,
    bridge_supported_versions: str | None = None,
    bridge_timeout_seconds: float | None = None,
    json_report: Path = DEFAULT_JSON_REPORT,
) -> dict[str, Any]:
    inputs = [_resolve(path) for path in dwg_paths]
    allowed = _dedupe(("MIT", "INTERNAL", *allowed_dwg_license_ids))
    env_updates = {
        COMMERCIAL_SDK_ADAPTER_ENV: adapter_spec,
        COMMAND_ENV: bridge_command,
        ARGS_JSON_ENV: bridge_args_json,
        LICENSE_ID_ENV: bridge_license_id,
        SUPPORTED_VERSIONS_ENV: bridge_supported_versions,
        TIMEOUT_SECONDS_ENV: str(bridge_timeout_seconds) if bridge_timeout_seconds else None,
    }
    with _temporary_env(env_updates):
        importer = DwgImporter(
            backend_mode=DWG_BACKEND_COMMERCIAL_SDK,
            allowed_license_ids=allowed,
        )
    records = [_validate_one(importer, path) for path in inputs]
    adapter_report = importer._adapter_report()

    missing = [record for record in records if not record["exists"]]
    failed = [record for record in records if record.get("import_status") not in ACCEPTED_IMPORT_STATUSES]
    diagnostics = adapter_report.get("diagnostics") or {}
    diagnostic_errors = []
    if not records:
        diagnostic_errors.append("bridge_input_missing")
    if not diagnostics.get("command_exists"):
        diagnostic_errors.append("bridge_command_missing")
    if not diagnostics.get("command_sha256"):
        diagnostic_errors.append("bridge_command_sha256_missing")
    if not diagnostics.get("supported_versions"):
        diagnostic_errors.append("bridge_supported_versions_missing")
    if adapter_report.get("license_id") not in set(allowed):
        diagnostic_errors.append("bridge_license_not_allowed")

    status = "passed" if not missing and not failed and not diagnostic_errors else "failed"
    report = {
        "schema_version": "dwg-json-bridge-contract-validation/v1",
        "generated_at": datetime.now().isoformat(),
        "status": status,
        "summary": {
            "input_count": len(records),
            "accepted_import_count": len(records) - len(failed),
            "failed_import_count": len(failed),
            "missing_input_count": len(missing),
            "diagnostic_error_count": len(diagnostic_errors),
        },
        "adapter": adapter_report,
        "allowed_dwg_license_ids": list(allowed),
        "diagnostic_errors": diagnostic_errors,
        "records": records,
    }
    _write_json(_resolve(json_report), report)
    return report


def _validate_one(importer: DwgImporter, path: Path) -> dict[str, Any]:
    exists = path.exists() and path.is_file()
    detected_version: dict[str, Any] | None = None
    if exists:
        try:
            detected_version = DwgVersionDetector.detect_file(path).to_dict()
        except Exception as exc:
            detected_version = {"error": f"{type(exc).__name__}: {exc}"}
    doc = importer.import_file(path) if exists else None
    import_report = (doc or {}).get("import_report") or {}
    return {
        "path": str(path),
        "exists": exists,
        "detected_version": detected_version,
        "import_status": import_report.get("status") if import_report else "missing",
        "error_code": import_report.get("error_code"),
        "message": import_report.get("message") or import_report.get("error_message"),
        "warning_count": len(import_report.get("warnings") or []),
        "unsupported_entity_count": sum(
            int(item.get("count") or 0) for item in import_report.get("unsupported_entities") or []
        ),
        "entity_count": len((doc or {}).get("entities") or []),
        "adapter": import_report.get("adapter") or {},
    }


@contextmanager
def _temporary_env(updates: dict[str, str | None]) -> Iterator[None]:
    old_values = {key: os.environ.get(key) for key in updates}
    try:
        for key, value in updates.items():
            if value is None:
                continue
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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dwg", nargs="+", type=Path)
    parser.add_argument("--adapter-spec", default=DEFAULT_ADAPTER_SPEC)
    parser.add_argument("--dwg-allowed-license-id", action="append", default=None)
    parser.add_argument("--bridge-command")
    parser.add_argument("--bridge-args-json")
    parser.add_argument("--bridge-license-id")
    parser.add_argument("--bridge-supported-versions")
    parser.add_argument("--bridge-timeout-seconds", type=float)
    parser.add_argument("--json-report", type=Path, default=DEFAULT_JSON_REPORT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = validate_contract(
        args.dwg,
        adapter_spec=args.adapter_spec,
        allowed_dwg_license_ids=tuple(args.dwg_allowed_license_id or ()),
        bridge_command=args.bridge_command,
        bridge_args_json=args.bridge_args_json,
        bridge_license_id=args.bridge_license_id,
        bridge_supported_versions=args.bridge_supported_versions,
        bridge_timeout_seconds=args.bridge_timeout_seconds,
        json_report=args.json_report,
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
