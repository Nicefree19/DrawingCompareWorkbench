"""Audit evidence for broad DWG version support claims.

This gate separates two very different claims:

* fallback readiness: every target DWG generation can be processed through an
  approved native or user/registered converted-DXF path with provenance;
* native readiness: every target DWG generation has native-reader evidence.

The script does not run converters or compare drawings. It aggregates existing
JSON evidence and returns a version-by-version blocker matrix.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
TARGET_DWG_CODES = ("AC1009", "AC1012", "AC1014", "AC1015", "AC1018", "AC1021", "AC1024", "AC1027", "AC1032")
MIN_REAL_PAIR_COUNT = 2
MIN_CONVERTED_BASELINE_COUNT = 2
MIN_NATIVE_BASELINE_COUNT = 2
NATIVE_CLAIM_PATTERNS = (
    re.compile(r"\bmodern\s+DWG\s+native\s+support\b", re.IGNORECASE),
    re.compile(r"\bAC10(?:09|12|14|15|18|21|24|27|32)\b[^\n]{0,100}\bnative\b[^\n]{0,60}\bsupport", re.IGNORECASE),
)
ALL_VERSION_CLAIM_PATTERNS = (
    re.compile(r"\ball\s+DWG\s+versions?\s+supported\b", re.IGNORECASE),
    re.compile(r"\bDWG\s+fully\s+supported\b", re.IGNORECASE),
)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class VersionEvidence:
    code: str
    sample_count: int = 0
    real_pair_count: int = 0
    converted_dxf_baseline_count: int = 0
    fallback_supported: bool = False
    fallback_candidate_count: int = 0
    native_supported: bool = False
    native_baseline_count: int = 0
    default_customer_oda_calls: int = 0
    sources: list[str] = field(default_factory=list)

    def merge(self, **values: Any) -> None:
        self.sample_count = max(self.sample_count, _int(values.get("sample_count")))
        self.real_pair_count = max(self.real_pair_count, _int(values.get("real_pair_count")))
        self.converted_dxf_baseline_count = max(
            self.converted_dxf_baseline_count,
            _int(values.get("converted_dxf_baseline_count")),
        )
        self.fallback_candidate_count = max(self.fallback_candidate_count, _int(values.get("fallback_candidate_count")))
        self.native_baseline_count = max(self.native_baseline_count, _int(values.get("native_baseline_count")))
        self.default_customer_oda_calls = max(self.default_customer_oda_calls, _int(values.get("default_customer_oda_calls")))
        self.fallback_supported = self.fallback_supported or bool(values.get("fallback_supported"))
        self.native_supported = self.native_supported or bool(values.get("native_supported"))
        source = values.get("source")
        if source:
            self.sources.append(str(source))

    @property
    def fallback_ready(self) -> bool:
        has_conversion_path = self.fallback_supported or self.fallback_candidate_count > 0
        has_supported_native_path = self.native_supported and self.native_baseline_count >= MIN_NATIVE_BASELINE_COUNT
        return (
            self.default_customer_oda_calls == 0
            and self.sample_count >= MIN_REAL_PAIR_COUNT
            and self.real_pair_count >= MIN_REAL_PAIR_COUNT
            and (
                has_supported_native_path
                or (
                    has_conversion_path
                    and self.converted_dxf_baseline_count >= MIN_CONVERTED_BASELINE_COUNT
                )
            )
        )

    @property
    def native_ready(self) -> bool:
        return (
            self.default_customer_oda_calls == 0
            and self.sample_count >= MIN_REAL_PAIR_COUNT
            and self.real_pair_count >= MIN_REAL_PAIR_COUNT
            and self.converted_dxf_baseline_count >= MIN_CONVERTED_BASELINE_COUNT
            and self.native_supported
            and self.native_baseline_count >= MIN_NATIVE_BASELINE_COUNT
        )

    def fallback_blockers(self) -> list[str]:
        blockers: list[str] = []
        has_conversion_path = self.fallback_supported or self.fallback_candidate_count > 0
        has_supported_native_path = self.native_supported and self.native_baseline_count >= MIN_NATIVE_BASELINE_COUNT
        if self.default_customer_oda_calls:
            blockers.append(f"default_customer_oda_calls={self.default_customer_oda_calls}/0")
        if self.sample_count < MIN_REAL_PAIR_COUNT:
            blockers.append(f"sample_count={self.sample_count}/{MIN_REAL_PAIR_COUNT}")
        if self.real_pair_count < MIN_REAL_PAIR_COUNT:
            blockers.append(f"real_pair_count={self.real_pair_count}/{MIN_REAL_PAIR_COUNT}")
        if not (has_conversion_path or self.native_supported):
            blockers.append("approved fallback/native route missing")
        if self.native_supported and not has_supported_native_path and not has_conversion_path:
            blockers.append(f"native_baseline_count={self.native_baseline_count}/{MIN_NATIVE_BASELINE_COUNT}")
        if not has_supported_native_path and has_conversion_path and self.converted_dxf_baseline_count < MIN_CONVERTED_BASELINE_COUNT:
            blockers.append(f"converted_dxf_baseline_count={self.converted_dxf_baseline_count}/{MIN_CONVERTED_BASELINE_COUNT}")
        return blockers

    def native_blockers(self) -> list[str]:
        blockers: list[str] = []
        if self.default_customer_oda_calls:
            blockers.append(f"default_customer_oda_calls={self.default_customer_oda_calls}/0")
        if self.sample_count < MIN_REAL_PAIR_COUNT:
            blockers.append(f"sample_count={self.sample_count}/{MIN_REAL_PAIR_COUNT}")
        if self.real_pair_count < MIN_REAL_PAIR_COUNT:
            blockers.append(f"real_pair_count={self.real_pair_count}/{MIN_REAL_PAIR_COUNT}")
        if self.converted_dxf_baseline_count < MIN_CONVERTED_BASELINE_COUNT:
            blockers.append(f"converted_dxf_baseline_count={self.converted_dxf_baseline_count}/{MIN_CONVERTED_BASELINE_COUNT}")
        if not self.native_supported:
            blockers.append("native_supported=false")
        if self.native_baseline_count < MIN_NATIVE_BASELINE_COUNT:
            blockers.append(f"native_baseline_count={self.native_baseline_count}/{MIN_NATIVE_BASELINE_COUNT}")
        return blockers

    def to_json(self) -> dict[str, Any]:
        fallback_actions = self.fallback_next_actions()
        native_actions = self.native_next_actions()
        return {
            "code": self.code,
            "sample_count": self.sample_count,
            "real_pair_count": self.real_pair_count,
            "converted_dxf_baseline_count": self.converted_dxf_baseline_count,
            "fallback_supported": self.fallback_supported,
            "fallback_candidate_count": self.fallback_candidate_count,
            "native_supported": self.native_supported,
            "native_baseline_count": self.native_baseline_count,
            "default_customer_oda_calls": self.default_customer_oda_calls,
            "fallback_ready": self.fallback_ready,
            "native_ready": self.native_ready,
            "fallback_blockers": self.fallback_blockers(),
            "native_blockers": self.native_blockers(),
            "fallback_next_actions": fallback_actions,
            "native_next_actions": native_actions,
            "sources": sorted(set(self.sources)),
        }

    def fallback_next_actions(self) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        has_conversion_path = self.fallback_supported or self.fallback_candidate_count > 0
        has_supported_native_path = self.native_supported and self.native_baseline_count >= MIN_NATIVE_BASELINE_COUNT
        if self.default_customer_oda_calls:
            actions.append(
                _action(
                    self.code,
                    "fallback",
                    "P0",
                    "remove_default_customer_oda_calls",
                    "Remove ODA usage from the default/customer path; keep ODA only in explicit local/internal mode.",
                    self.default_customer_oda_calls,
                    0,
                )
            )
        if self.sample_count < MIN_REAL_PAIR_COUNT:
            actions.append(
                _action(
                    self.code,
                    "fallback",
                    "P1",
                    "collect_real_dwg_samples",
                    "Collect local/customer-approved real DWG samples for this version without copying private drawings into source control.",
                    self.sample_count,
                    MIN_REAL_PAIR_COUNT,
                )
            )
        if self.real_pair_count < MIN_REAL_PAIR_COUNT:
            actions.append(
                _action(
                    self.code,
                    "fallback",
                    "P1",
                    "confirm_before_after_pairs",
                    "Confirm real before/after revision pairs for this version and record them in a local evidence manifest.",
                    self.real_pair_count,
                    MIN_REAL_PAIR_COUNT,
                )
            )
        if not (has_conversion_path or self.native_supported):
            actions.append(
                _action(
                    self.code,
                    "fallback",
                    "P1",
                    "establish_approved_route",
                    "Provide registered converted-DXF baselines, an explicit user_converter path, or an approved native/commercial backend.",
                    int(has_conversion_path or self.native_supported),
                    1,
                )
            )
        if not has_supported_native_path and has_conversion_path and self.converted_dxf_baseline_count < MIN_CONVERTED_BASELINE_COUNT:
            actions.append(
                _action(
                    self.code,
                    "fallback",
                    "P1",
                    "capture_converted_dxf_baselines",
                    "Generate and validate converted-DXF before/after baselines for this DWG version.",
                    self.converted_dxf_baseline_count,
                    MIN_CONVERTED_BASELINE_COUNT,
                )
            )
        return actions

    def native_next_actions(self) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        if self.default_customer_oda_calls:
            actions.append(
                _action(
                    self.code,
                    "native",
                    "P0",
                    "remove_default_customer_oda_calls",
                    "Remove ODA usage from the default/customer path before making native-support claims.",
                    self.default_customer_oda_calls,
                    0,
                )
            )
        if self.sample_count < MIN_REAL_PAIR_COUNT:
            actions.append(
                _action(
                    self.code,
                    "native",
                    "P1",
                    "collect_native_gate_samples",
                    "Collect at least two real before/after DWG samples for the native reader exit gate.",
                    self.sample_count,
                    MIN_REAL_PAIR_COUNT,
                )
            )
        if self.real_pair_count < MIN_REAL_PAIR_COUNT:
            actions.append(
                _action(
                    self.code,
                    "native",
                    "P1",
                    "confirm_native_compare_pairs",
                    "Confirm before/after revision pairs for native-vs-baseline comparison.",
                    self.real_pair_count,
                    MIN_REAL_PAIR_COUNT,
                )
            )
        if self.converted_dxf_baseline_count < MIN_CONVERTED_BASELINE_COUNT:
            actions.append(
                _action(
                    self.code,
                    "native",
                    "P1",
                    "capture_native_oracle_baselines",
                    "Capture converted-DXF compare baselines to use as the native-reader oracle.",
                    self.converted_dxf_baseline_count,
                    MIN_CONVERTED_BASELINE_COUNT,
                )
            )
        if not self.native_supported:
            actions.append(
                _action(
                    self.code,
                    "native",
                    "P2",
                    "implement_or_license_native_backend",
                    "Implement an approved clean-room reader or wire an approved commercial SDK backend for this version.",
                    0,
                    1,
                )
            )
        if self.native_baseline_count < MIN_NATIVE_BASELINE_COUNT:
            actions.append(
                _action(
                    self.code,
                    "native",
                    "P2",
                    "capture_native_backend_baselines",
                    "Capture successful native backend imports/compares and compare them against converted-DXF baselines.",
                    self.native_baseline_count,
                    MIN_NATIVE_BASELINE_COUNT,
                )
            )
        return actions


def run_audit(
    *,
    evidence_manifest: Path | None = None,
    phase0_inventory: Path | None = None,
    phase0c_baselines: Path | None = None,
    real_world_validation: Path | Sequence[Path] | None = None,
    claim_scope: str = "fallback",
    target_versions: Sequence[str] = TARGET_DWG_CODES,
) -> dict[str, Any]:
    records = {code: VersionEvidence(code) for code in target_versions}
    inputs: dict[str, str] = {}
    payloads: list[dict[str, Any]] = []

    if evidence_manifest is not None:
        payload = _load_json(evidence_manifest)
        inputs["evidence_manifest"] = str(evidence_manifest)
        payloads.append(payload)
        _merge_support_evidence(records, payload, source=evidence_manifest)
    if phase0_inventory is not None:
        payload = _load_json(phase0_inventory)
        inputs["phase0_inventory"] = str(phase0_inventory)
        payloads.append(payload)
        _merge_phase0_inventory(records, payload, source=phase0_inventory)
    if phase0c_baselines is not None:
        payload = _load_json(phase0c_baselines)
        inputs["phase0c_baselines"] = str(phase0c_baselines)
        payloads.append(payload)
        _merge_phase0c_baselines(records, payload, source=phase0c_baselines)
    real_world_paths = _path_list(real_world_validation)
    if real_world_paths:
        inputs["real_world_validation"] = [str(path) for path in real_world_paths]
        for path in real_world_paths:
            payload = _load_json(path)
            payloads.append(payload)
            _merge_real_world_validation(records, payload, source=path)

    version_items = [records[code].to_json() for code in target_versions]
    fallback_missing = [item["code"] for item in version_items if not item["fallback_ready"]]
    native_missing = [item["code"] for item in version_items if not item["native_ready"]]
    claim_violations = _claim_violations(payloads, fallback_missing=fallback_missing, native_missing=native_missing)
    requested_ready = not (native_missing if claim_scope == "native" else fallback_missing)
    status = "passed" if requested_ready and not claim_violations else "failed"

    return {
        "schema_version": "dwg-all-version-support-audit/v1",
        "generated_at": datetime.now().isoformat(),
        "status": status,
        "claim_scope": claim_scope,
        "target_versions": list(target_versions),
        "summary": {
            "fallback_ready_versions": [item["code"] for item in version_items if item["fallback_ready"]],
            "fallback_missing_versions": fallback_missing,
            "native_ready_versions": [item["code"] for item in version_items if item["native_ready"]],
            "native_missing_versions": native_missing,
            "claim_violation_count": len(claim_violations),
        },
        "claim_violations": claim_violations,
        "next_actions": _next_actions(version_items, claim_scope=claim_scope),
        "versions": version_items,
        "inputs": inputs,
    }


def _merge_support_evidence(records: dict[str, VersionEvidence], payload: dict[str, Any], *, source: Path) -> None:
    versions = payload.get("versions") or {}
    if isinstance(versions, dict):
        items = versions.items()
    elif isinstance(versions, list):
        items = ((str(item.get("dwg_code") or item.get("code") or ""), item) for item in versions if isinstance(item, dict))
    else:
        items = ()
    for code, item in items:
        if code not in records or not isinstance(item, dict):
            continue
        records[code].merge(
            sample_count=item.get("sample_count"),
            real_pair_count=item.get("real_pair_count") or item.get("real_before_after_pair_count"),
            converted_dxf_baseline_count=item.get("converted_dxf_baseline_count")
            or item.get("converted_dxf_baseline_pairs"),
            fallback_supported=item.get("fallback_supported") or item.get("user_converter_supported"),
            fallback_candidate_count=item.get("fallback_candidate_count"),
            native_supported=item.get("native_supported"),
            native_baseline_count=item.get("native_baseline_count") or item.get("native_baseline_pairs"),
            default_customer_oda_calls=item.get("default_customer_oda_calls"),
            source=source,
        )


def _merge_phase0_inventory(records: dict[str, VersionEvidence], payload: dict[str, Any], *, source: Path) -> None:
    version_counts = payload.get("version_counts") or {}
    for code, count in version_counts.items():
        if code in records:
            records[code].merge(sample_count=count, source=source)
    for summary in payload.get("root_summaries") or []:
        if not isinstance(summary, dict) or not summary.get("converted_dxf_fallback_ready"):
            continue
        for code, count in (summary.get("version_counts") or {}).items():
            if code in records:
                records[code].merge(
                    sample_count=count,
                    fallback_supported=True,
                    fallback_candidate_count=1,
                    source=source,
                )


def _merge_phase0c_baselines(records: dict[str, VersionEvidence], payload: dict[str, Any], *, source: Path) -> None:
    versions = payload.get("versions") or {}
    if isinstance(versions, dict):
        items = versions.items()
    else:
        items = ((str(item.get("version") or item.get("code") or ""), item) for item in versions if isinstance(item, dict))
    for code, record in items:
        if code not in records or not isinstance(record, dict):
            continue
        baseline_ready = bool(record.get("compare_baseline_ready")) or str(record.get("phase0c_status")) == "compare_ready"
        imported_side_count = _imported_side_count(record)
        duplicated = "duplicated" in str(record.get("pair_kind") or "") or "single_file" in str(record.get("pair_kind") or "")
        sample_count = 1 if duplicated and imported_side_count else imported_side_count
        records[code].merge(
            sample_count=sample_count,
            converted_dxf_baseline_count=1 if baseline_ready else 0,
            real_pair_count=1 if baseline_ready else 0,
            fallback_supported=baseline_ready,
            source=source,
        )


def _imported_side_count(record: dict[str, Any]) -> int:
    counts = record.get("import_entity_counts") or {}
    if isinstance(counts, dict):
        sides = [side for side in ("before", "after") if counts.get(side) is not None]
        if sides:
            return len(sides)
    statuses = record.get("import_statuses") or {}
    if isinstance(statuses, dict):
        sides = [side for side in ("before", "after") if statuses.get(side)]
        if sides:
            return len(sides)
    return 0


def _merge_real_world_validation(records: dict[str, VersionEvidence], payload: dict[str, Any], *, source: Path) -> None:
    samples_by_id: dict[str, str] = {}
    counts: dict[str, int] = {}
    for sample in payload.get("samples") or []:
        if not isinstance(sample, dict):
            continue
        code = str(sample.get("detected_version") or sample.get("expected_version") or "")
        sample_id = str(sample.get("id") or "")
        if sample_id:
            samples_by_id[sample_id] = code
        if code in records:
            counts[code] = counts.get(code, 0) + 1
    pair_counts: dict[str, int] = {}
    for pair in payload.get("pairs") or []:
        if not isinstance(pair, dict):
            continue
        old_code = samples_by_id.get(str(pair.get("old_sample") or ""))
        new_code = samples_by_id.get(str(pair.get("new_sample") or ""))
        if old_code and old_code == new_code and old_code in records:
            pair_counts[old_code] = pair_counts.get(old_code, 0) + 1
    for code in records:
        if code in counts or code in pair_counts:
            records[code].merge(sample_count=counts.get(code, 0), real_pair_count=pair_counts.get(code, 0), source=source)


def _claim_violations(
    payloads: Sequence[dict[str, Any]],
    *,
    fallback_missing: Sequence[str],
    native_missing: Sequence[str],
) -> list[dict[str, str]]:
    fallback_blocked = bool(fallback_missing)
    native_blocked = bool(native_missing)
    violations: list[dict[str, str]] = []
    for snippet in _claim_snippets(payloads):
        if fallback_blocked and any(pattern.search(snippet) for pattern in ALL_VERSION_CLAIM_PATTERNS):
            violations.append({"scope": "fallback", "claim": snippet, "missing_versions": ",".join(fallback_missing)})
        if native_blocked and any(pattern.search(snippet) for pattern in NATIVE_CLAIM_PATTERNS):
            violations.append({"scope": "native", "claim": snippet, "missing_versions": ",".join(native_missing)})
    return violations


def _next_actions(version_items: Sequence[dict[str, Any]], *, claim_scope: str) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    action_key = "native_next_actions" if claim_scope == "native" else "fallback_next_actions"
    for item in version_items:
        for action in item.get(action_key) or []:
            if isinstance(action, dict):
                actions.append(action)
    return sorted(actions, key=lambda item: (str(item.get("priority", "")), str(item.get("code", "")), str(item.get("action", ""))))


def _action(
    code: str,
    scope: str,
    priority: str,
    action: str,
    detail: str,
    current: int,
    target: int,
) -> dict[str, Any]:
    return {
        "code": code,
        "scope": scope,
        "priority": priority,
        "action": action,
        "current": current,
        "target": target,
        "remaining": max(0, target - current),
        "detail": detail,
    }


def _claim_snippets(payloads: Sequence[dict[str, Any]]) -> list[str]:
    snippets: list[str] = []
    for payload in payloads:
        for key, value in _walk(payload):
            leaf = key.rsplit(".", 1)[-1].lower()
            if "claim" in leaf or "wording" in leaf or "release" in leaf:
                if isinstance(value, str):
                    snippets.append(value.strip())
                elif isinstance(value, list):
                    snippets.extend(str(item).strip() for item in value if isinstance(item, str))
    return [item for item in snippets if item]


def _walk(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_key = f"{prefix}.{key}" if prefix else str(key)
            yield from _walk(child, child_key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{prefix}[{index}]")
    else:
        yield prefix, value


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _path_list(value: Path | Sequence[Path] | None) -> list[Path]:
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        return [Path(value)]
    return [Path(item) for item in value]


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-manifest", type=Path)
    parser.add_argument("--phase0-inventory", type=Path)
    parser.add_argument("--phase0c-baselines", type=Path)
    parser.add_argument("--real-world-validation", type=Path, action="append", default=None)
    parser.add_argument("--claim-scope", choices=("fallback", "native"), default="fallback")
    parser.add_argument("--out", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_audit(
        evidence_manifest=args.evidence_manifest,
        phase0_inventory=args.phase0_inventory,
        phase0c_baselines=args.phase0c_baselines,
        real_world_validation=args.real_world_validation,
        claim_scope=args.claim_scope,
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
