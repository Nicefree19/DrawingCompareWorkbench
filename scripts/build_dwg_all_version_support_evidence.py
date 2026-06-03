"""Build aggregated DWG all-version support evidence from validation summaries.

This script consumes existing ``validate_adr004_version_sample_pack.py`` JSON
reports and writes the compact evidence manifest consumed by
``audit_dwg_all_version_support.py``.  It does not run converters or imports.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_dwg_all_version_support import TARGET_DWG_CODES  # noqa: E402


NATIVE_BRIDGE_EVIDENCE_SCOPES = {
    "commercial_dwg_native",
    "commercial_native",
    "commercial_sdk_native",
    "native_dwg",
    "native_dwg_bridge",
}


@dataclass
class _VersionAccumulator:
    code: str
    sample_paths: set[str] = field(default_factory=set)
    real_pair_keys: set[str] = field(default_factory=set)
    converted_baseline_keys: set[str] = field(default_factory=set)
    native_baseline_keys: set[str] = field(default_factory=set)
    native_backend_modes: set[str] = field(default_factory=set)
    fallback_candidate_count: int = 0
    sources: set[str] = field(default_factory=set)

    def add_record(self, source: Path, record: dict[str, Any], *, limits: dict[str, Any]) -> None:
        dwg_paths = _valid_dwg_paths(self.code, record)
        if _is_duplicated_record(record) and dwg_paths:
            self.sample_paths.add(_duplicated_sample_key(record, dwg_paths))
        else:
            self.sample_paths.update(dwg_paths)
        if _has_converted_dxf_pair(self.code, record):
            self.fallback_candidate_count += 1
        if _is_real_pair(record, dwg_paths):
            pair_key = _pair_key(dwg_paths)
            self.real_pair_keys.add(pair_key)
            if _is_compare_baseline_ready(record):
                self.converted_baseline_keys.add(pair_key)
            if _is_native_baseline_ready(record, limits=limits):
                self.native_baseline_keys.add(pair_key)
                self.native_backend_modes.add(str(limits.get("dwg_backend_mode") or ""))
        self.sources.add(str(source))

    def to_json(self) -> dict[str, Any]:
        return {
            "sample_count": len(self.sample_paths),
            "real_pair_count": len(self.real_pair_keys),
            "converted_dxf_baseline_count": len(self.converted_baseline_keys),
            "fallback_supported": self.fallback_candidate_count > 0,
            "fallback_candidate_count": self.fallback_candidate_count,
            "native_supported": len(self.native_baseline_keys) > 0,
            "native_baseline_count": len(self.native_baseline_keys),
            "default_customer_oda_calls": 0,
            "sample_paths": sorted(self.sample_paths),
            "real_pair_keys": sorted(self.real_pair_keys),
            "converted_baseline_keys": sorted(self.converted_baseline_keys),
            "native_baseline_keys": sorted(self.native_baseline_keys),
            "native_backend_modes": sorted(mode for mode in self.native_backend_modes if mode),
            "sources": sorted(self.sources),
        }


def build_report(summary_paths: Sequence[Path], *, root: Path = ROOT) -> dict[str, Any]:
    accumulators = {code: _VersionAccumulator(code) for code in TARGET_DWG_CODES}
    source_summaries: list[dict[str, Any]] = []
    for path in summary_paths:
        resolved = _resolve(root, path)
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        source_summaries.append(
            {
                "path": str(resolved),
                "status": payload.get("status"),
                "sample_pack": payload.get("sample_pack"),
                "compare_source": (payload.get("limits") or {}).get("compare_source"),
                "dwg_backend_mode": (payload.get("limits") or {}).get("dwg_backend_mode"),
            }
        )
        limits = payload.get("limits") or {}
        for record in payload.get("versions") or []:
            code = str(record.get("version") or "")
            if code in accumulators and isinstance(record, dict):
                accumulators[code].add_record(resolved, record, limits=limits)

    versions = {code: accumulator.to_json() for code, accumulator in accumulators.items()}
    return {
        "schema_version": "dwg-all-version-support-evidence/v1",
        "generated_at": datetime.now().isoformat(),
        "source_policy": "aggregated from local validation summaries; DWG files are referenced, not copied",
        "source_summaries": source_summaries,
        "versions": versions,
        "summary": {
            "source_summary_count": len(source_summaries),
            "version_count": len(versions),
            "versions_with_real_pairs": [
                code for code, item in versions.items() if int(item.get("real_pair_count") or 0) > 0
            ],
            "versions_with_converted_baselines": [
                code for code, item in versions.items() if int(item.get("converted_dxf_baseline_count") or 0) > 0
            ],
            "versions_with_native_baselines": [
                code for code, item in versions.items() if int(item.get("native_baseline_count") or 0) > 0
            ],
        },
    }


def _valid_dwg_paths(code: str, record: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    inputs = record.get("dwg_inputs") or {}
    if not isinstance(inputs, dict):
        return paths
    for side in ("before", "after"):
        item = inputs.get(side) or {}
        path = str(item.get("path") or "")
        if path and item.get("exists") and item.get("header_matches_version") and str(item.get("detected_header") or code) == code:
            paths.add(path)
    return paths


def _has_converted_dxf_pair(code: str, record: dict[str, Any]) -> bool:
    outputs = record.get("outputs") or {}
    if not isinstance(outputs, dict):
        return False
    return all(_side_has_valid_dxf(code, outputs.get(side) or []) for side in ("before", "after"))


def _side_has_valid_dxf(code: str, outputs: Any) -> bool:
    if not isinstance(outputs, list):
        return False
    for item in outputs:
        if (
            isinstance(item, dict)
            and item.get("exists")
            and item.get("header_matches_expected")
            and str(item.get("detected_acadver") or "") == code
        ):
            return True
    return False


def _is_real_pair(record: dict[str, Any], dwg_paths: set[str]) -> bool:
    if _is_duplicated_record(record):
        return False
    return len(dwg_paths) >= 2


def _is_duplicated_record(record: dict[str, Any]) -> bool:
    pair_kind = str(record.get("pair_kind") or "")
    return "duplicated" in pair_kind or "single_file" in pair_kind


def _duplicated_sample_key(record: dict[str, Any], paths: set[str]) -> str:
    version = str(record.get("version") or "")
    basename = sorted(Path(path).name for path in paths)[0]
    return f"{version}:{basename}:duplicated-import-only"


def _is_compare_baseline_ready(record: dict[str, Any]) -> bool:
    compare = record.get("compare") or {}
    imports = record.get("imports") or {}
    import_statuses = [str((imports.get(side) or {}).get("status") or "") for side in ("before", "after")]
    return (
        str(compare.get("status") or "") in {"ok", "partial"}
        and all(status in {"ok", "partial"} for status in import_statuses)
        and _has_converted_dxf_pair(str(record.get("version") or ""), record)
    )


def _is_native_baseline_ready(record: dict[str, Any], *, limits: dict[str, Any]) -> bool:
    compare_source = str(limits.get("compare_source") or "")
    backend_mode = str(limits.get("dwg_backend_mode") or "")
    if compare_source != "dwg" or not _is_native_backend_mode(backend_mode):
        return False
    if _uses_converted_dxf_bridge(record):
        return False
    if not _has_required_native_bridge_scope(record):
        return False
    compare = record.get("compare") or {}
    imports = compare.get("imports") or {}
    import_statuses = [
        str((imports.get(side) or {}).get("status") or "")
        for side in ("a", "b", "before", "after")
        if side in imports
    ]
    if not import_statuses or any(status not in {"ok", "partial"} for status in import_statuses):
        return False
    return (
        str(compare.get("status") or "") in {"ok", "partial"}
        and _has_converted_dxf_pair(str(record.get("version") or ""), record)
    )


def _is_native_backend_mode(mode: str) -> bool:
    normalized = mode.strip().lower().replace("-", "_")
    return normalized in {"cleanroom_native", "native", "commercial_sdk", "commercial"}


def _uses_converted_dxf_bridge(record: dict[str, Any]) -> bool:
    compare = record.get("compare") or {}
    imports = compare.get("imports") or {}
    if not isinstance(imports, dict):
        return False
    for item in imports.values():
        if not isinstance(item, dict):
            continue
        if _truthy((item.get("fallback") or {}).get("user_converter")):
            return True
        adapter_metadata = item.get("adapter_metadata") or {}
        if not isinstance(adapter_metadata, dict):
            continue
        bridge = adapter_metadata.get("commercial_dwg_json_bridge") or {}
        if _bridge_marks_converted_dxf(bridge):
            return True
    return False


def _has_required_native_bridge_scope(record: dict[str, Any]) -> bool:
    """Require positive native provenance for commercial JSON bridge outputs.

    Direct in-process adapters have no ``commercial_dwg_json_bridge`` metadata
    and remain eligible. JSON bridge outputs must explicitly declare native DWG
    evidence so converted-DXF wrappers cannot pass by omission.
    """

    compare = record.get("compare") or {}
    imports = compare.get("imports") or {}
    if not isinstance(imports, dict):
        return True
    saw_bridge = False
    for item in imports.values():
        if not isinstance(item, dict):
            continue
        adapter_metadata = item.get("adapter_metadata") or {}
        if not isinstance(adapter_metadata, dict):
            continue
        bridge = adapter_metadata.get("commercial_dwg_json_bridge") or {}
        if not bridge:
            continue
        saw_bridge = True
        if not _bridge_marks_native_dwg(bridge):
            return False
    return True


def _bridge_marks_converted_dxf(bridge: Any) -> bool:
    if not isinstance(bridge, dict):
        return False
    if _truthy(bridge.get("uses_converted_dxf")):
        return True
    if bridge.get("converted_dxf_path") or bridge.get("effective_dxf_path"):
        return True
    scope = str(bridge.get("evidence_scope") or bridge.get("source_kind") or "").strip().lower()
    return any(marker in scope for marker in ("fallback", "converted_dxf", "dxf", "oda", "user_converter"))


def _bridge_marks_native_dwg(bridge: Any) -> bool:
    if not isinstance(bridge, dict):
        return False
    if _truthy(bridge.get("uses_native_dwg")):
        return True
    scope = str(bridge.get("evidence_scope") or bridge.get("source_kind") or "").strip().lower()
    return scope in NATIVE_BRIDGE_EVIDENCE_SCOPES


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _pair_key(paths: set[str]) -> str:
    return " | ".join(sorted(paths))


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", action="append", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args.summary)
    _write_json(args.out, report)
    print(
        "dwg all-version support evidence: "
        f"sources={report['summary']['source_summary_count']} "
        f"versions_with_baselines={report['summary']['versions_with_converted_baselines']} "
        f"out={Path(args.out).resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
