"""Validate and normalize local DWG accuracy evidence.

The evidence directory is intentionally local-only: raw DWG files may point to
customer or generated output folders, but this script validates metadata and
truth labels without copying those files into the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_DIR = Path(".local/accuracy-evidence")
DEFAULT_MANIFEST = DEFAULT_EVIDENCE_DIR / "corpus_manifest.json"
DEFAULT_NORMALIZED_MANIFEST = DEFAULT_EVIDENCE_DIR / "corpus_manifest_normalized.json"
DEFAULT_TRUTH = DEFAULT_EVIDENCE_DIR / "truth_reviewed_agent_draft.json"
DEFAULT_NORMALIZED_TRUTH = DEFAULT_EVIDENCE_DIR / "truth_normalized.json"
DEFAULT_REPORT_JSON = DEFAULT_EVIDENCE_DIR / "accuracy_evidence_validation.json"
DEFAULT_REPORT_MD = DEFAULT_EVIDENCE_DIR / "accuracy_evidence_validation.md"

MANIFEST_SCHEMA_VERSION = "dwg-accuracy-corpus-manifest/v1"
TRUTH_SCHEMA_VERSION = "dwg-accuracy-truth/v1"
REPORT_SCHEMA_VERSION = "dwg-accuracy-evidence-validation/v1"

REQUIRED_FILE_FIELDS = (
    "file_id",
    "absolute_path",
    "sha256",
    "file_size_bytes",
    "dwg_version",
    "source_type",
    "confidentiality",
)
REQUIRED_PAIR_FIELDS = (
    "pair_id",
    "before_file_id",
    "after_file_id",
    "pair_type",
    "expected_changed",
    "expected_change_count",
    "expected_changes",
    "reviewer_status",
    "confidence",
)
RISK_PATTERNS = (
    "timeout",
    "timed out",
    "skipped",
    "dxf_size_over_limit",
    "cannot verify truth",
    "failed/skipped",
)
NEGATIVE_PAIR_TYPES = {"identical", "version_resave", "non_structural_noise"}
ACTIVE_STATUS = "active"
EXCLUDED_STATUS = "excluded"
CONFIDENTIALITY_RANK = {
    "public": 0,
    "internal": 1,
    "customer_confidential": 2,
    "unknown": 3,
}
DWG_VERSION_ORDER = (
    "AC1009",
    "AC1012",
    "AC1014",
    "AC1015",
    "AC1018",
    "AC1021",
    "AC1024",
    "AC1027",
    "AC1032",
)
DWG_VERSION_RANK = {version: index for index, version in enumerate(DWG_VERSION_ORDER)}
TARGET_PROFILES = {
    "internal_pilot": {
        "min_active_pairs": 50,
        "required_pair_types": {
            "identical": 1,
            "version_resave": 1,
            "non_structural_noise": 3,
            "block_transform_case": 2,
            "import_edge_case": 2,
            "structural_change": 1,
        },
    },
    "limited_customer_release": {
        "min_active_pairs": 100,
        "required_pair_types": {
            "identical": 10,
            "version_resave": 10,
            "non_structural_noise": 10,
            "block_transform_case": 5,
            "import_edge_case": 5,
            "structural_change": 10,
        },
        "requires_accuracy_metrics": True,
    },
}


def load_records(path: Path, *, key: str) -> list[dict[str, Any]]:
    payload = json.loads(_resolve(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        records = payload.get(key)
        if isinstance(records, list):
            return [dict(item) for item in records if isinstance(item, dict)]
    raise ValueError(f"{path} must be a JSON list or an object with a {key!r} list")


def load_manifest(path: Path = DEFAULT_MANIFEST) -> list[dict[str, Any]]:
    return load_records(path, key="files")


def load_truth(path: Path = DEFAULT_TRUTH) -> list[dict[str, Any]]:
    return load_records(path, key="pairs")


def normalize_manifest_records(files: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in files:
        groups[str(item.get("file_id") or "")].append(dict(item))

    normalized: list[dict[str, Any]] = []
    for file_id in sorted(groups):
        group = groups[file_id]
        if len(group) == 1:
            normalized.append(group[0])
            continue
        chosen = max(group, key=_manifest_preference)
        chosen = dict(chosen)
        chosen["alternate_paths"] = sorted(
            {
                str(item.get("absolute_path") or "")
                for item in group
                if str(item.get("absolute_path") or "") != str(chosen.get("absolute_path") or "")
            }
        )
        chosen["normalization_actions"] = ["merged_duplicate_file_id"]
        if len({str(item.get("sha256") or "") for item in group}) > 1:
            chosen.setdefault("normalization_warnings", []).append("duplicate_file_id_sha256_conflict")
        if len({str(item.get("file_size_bytes") or "") for item in group}) > 1:
            chosen.setdefault("normalization_warnings", []).append("duplicate_file_id_size_conflict")
        normalized.append(chosen)
    return normalized


def build_manifest_payload(
    files: Sequence[dict[str, Any]],
    *,
    source_manifest: Path,
    extra_source_manifests: Sequence[Path] = (),
) -> dict[str, Any]:
    normalized = normalize_manifest_records(files)
    source_paths = [source_manifest, *extra_source_manifests]
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(),
        "source_manifest": str(_resolve(source_manifest)),
        "source_manifest_sha256": sha256_file(_resolve(source_manifest)),
        "source_manifests": [
            {"path": str(_resolve(path)), "sha256": sha256_file(_resolve(path))}
            for path in source_paths
        ],
        "summary": {
            "source_file_count": len(files),
            "file_count": len(normalized),
            "merged_duplicate_count": len(files) - len(normalized),
            "dwg_version_counts": dict(
                sorted(Counter(str(item.get("dwg_version") or "unknown") for item in normalized).items())
            ),
            "confidentiality_counts": dict(
                sorted(Counter(str(item.get("confidentiality") or "unknown") for item in normalized).items())
            ),
        },
        "files": normalized,
    }


def normalize_truth_records(
    files: Sequence[dict[str, Any]],
    pairs: Sequence[dict[str, Any]],
    *,
    add_identical_controls: bool = True,
    add_version_resave_controls: bool = True,
) -> list[dict[str, Any]]:
    files_by_id = _files_by_id(files)
    normalized: list[dict[str, Any]] = []
    for pair in pairs:
        item = dict(pair)
        item.setdefault("normalization_actions", [])
        item.setdefault("exclusion_reasons", [])
        before = files_by_id.get(str(item.get("before_file_id") or ""))
        after = files_by_id.get(str(item.get("after_file_id") or ""))
        if _should_swap_pair(item, before, after):
            item["before_file_id"], item["after_file_id"] = item["after_file_id"], item["before_file_id"]
            item["normalization_actions"].append("swapped_before_after")
            before, after = after, before

        version = _pair_version(before, after)
        if version:
            item["dwg_version"] = version
        item["source_confidentiality"] = _pair_confidentiality(before, after)
        item["accuracy_status"] = ACTIVE_STATUS
        if _is_risky_pair(item):
            item["accuracy_status"] = EXCLUDED_STATUS
            item["exclusion_reasons"].append("validation_not_completed")
        normalized.append(item)

    _exclude_duplicate_active_pairs(normalized)
    if add_identical_controls:
        normalized.extend(_build_identical_controls(files))
    if add_version_resave_controls:
        normalized.extend(_build_version_resave_controls(files, normalized))
    return normalized


def build_truth_payload(
    files: Sequence[dict[str, Any]],
    pairs: Sequence[dict[str, Any]],
    *,
    source_manifest: Path,
    source_truth: Path,
    extra_source_truths: Sequence[Path] = (),
    add_identical_controls: bool = True,
    add_version_resave_controls: bool = True,
) -> dict[str, Any]:
    normalized = normalize_truth_records(
        files,
        pairs,
        add_identical_controls=add_identical_controls,
        add_version_resave_controls=add_version_resave_controls,
    )
    truth_paths = [source_truth, *extra_source_truths]
    return {
        "schema_version": TRUTH_SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(),
        "source_manifest": str(_resolve(source_manifest)),
        "source_truth": str(_resolve(source_truth)),
        "source_manifest_sha256": sha256_file(_resolve(source_manifest)),
        "source_truth_sha256": sha256_file(_resolve(source_truth)),
        "source_truths": [
            {"path": str(_resolve(path)), "sha256": sha256_file(_resolve(path))}
            for path in truth_paths
        ],
        "summary": _pair_summary(normalized),
        "pairs": normalized,
    }


def build_report(
    files: Sequence[dict[str, Any]],
    pairs: Sequence[dict[str, Any]],
    *,
    manifest_path: Path | None = None,
    truth_path: Path | None = None,
    verify_file_hashes: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    files_by_id = _files_by_id(files)

    errors.extend(_validate_files(files, verify_file_hashes=verify_file_hashes))
    errors.extend(_validate_pairs(pairs, files_by_id=files_by_id))
    _append_coverage_warnings(pairs, warnings)

    active_pairs = [pair for pair in pairs if pair.get("accuracy_status", ACTIVE_STATUS) == ACTIVE_STATUS]
    excluded_pairs = [pair for pair in pairs if pair.get("accuracy_status") == EXCLUDED_STATUS]
    pair_types = Counter(str(pair.get("pair_type") or "unknown") for pair in active_pairs)
    versions = Counter(str(pair.get("dwg_version") or _pair_version_from_ids(pair, files_by_id) or "unknown") for pair in active_pairs)
    expected_changed = Counter(str(pair.get("expected_changed")) for pair in active_pairs)

    summary = {
        "file_count": len(files),
        "pair_count": len(pairs),
        "active_pair_count": len(active_pairs),
        "excluded_pair_count": len(excluded_pairs),
        "negative_control_count": sum(pair_types.get(kind, 0) for kind in NEGATIVE_PAIR_TYPES),
        "expected_changed_counts": dict(sorted(expected_changed.items())),
        "pair_type_counts": dict(sorted(pair_types.items())),
        "version_counts": dict(sorted(versions.items())),
        "confidential_file_count": _confidential_file_count(files),
    }
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(),
        "status": "failed" if errors else "passed",
        "manifest_path": str(_resolve(manifest_path)) if manifest_path else None,
        "truth_path": str(_resolve(truth_path)) if truth_path else None,
        "manifest_sha256": sha256_file(_resolve(manifest_path)) if manifest_path and _resolve(manifest_path).exists() else None,
        "truth_sha256": sha256_file(_resolve(truth_path)) if truth_path and _resolve(truth_path).exists() else None,
        "summary": summary,
        "target_assessment": _target_assessment(summary),
        "errors": errors,
        "warnings": warnings,
        "excluded_pairs": [
            {
                "pair_id": pair.get("pair_id"),
                "reasons": pair.get("exclusion_reasons") or [],
                "notes": pair.get("notes") or "",
            }
            for pair in excluded_pairs
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Accuracy Evidence Validation",
        "",
        f"Status: **{report.get('status')}**",
        "",
        "## Summary",
        "",
        f"- Files: `{summary.get('file_count')}`",
        f"- Pairs: `{summary.get('pair_count')}`",
        f"- Active pairs: `{summary.get('active_pair_count')}`",
        f"- Excluded pairs: `{summary.get('excluded_pair_count')}`",
        f"- Negative controls: `{summary.get('negative_control_count')}`",
        f"- Confidential files: `{summary.get('confidential_file_count')}`",
        "",
        "## Pair Types",
        "",
        "| pair type | active count |",
        "| --- | ---: |",
    ]
    for key, value in (summary.get("pair_type_counts") or {}).items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(["", "## Versions", "", "| DWG version | active count |", "| --- | ---: |"])
    for key, value in (summary.get("version_counts") or {}).items():
        lines.append(f"| `{key}` | {value} |")
    if report.get("target_assessment"):
        lines.extend(["", "## Target Assessment", "", "| profile | status | blockers |", "| --- | --- | --- |"])
        for key, assessment in (report.get("target_assessment") or {}).items():
            blockers = ", ".join(assessment.get("blockers") or [])
            lines.append(f"| `{key}` | `{assessment.get('status')}` | {blockers} |")
    if report.get("errors"):
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {error}" for error in report["errors"])
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in report["warnings"])
    if report.get("excluded_pairs"):
        lines.extend(["", "## Excluded Pairs", "", "| pair | reasons |", "| --- | --- |"])
        for pair in report["excluded_pairs"]:
            reasons = ", ".join(pair.get("reasons") or [])
            lines.append(f"| `{pair.get('pair_id')}` | {reasons} |")
    lines.append("")
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--extra-manifest", type=Path, action="append", default=[])
    parser.add_argument("--write-normalized-manifest", type=Path, default=None)
    parser.add_argument("--truth", type=Path, default=DEFAULT_TRUTH)
    parser.add_argument("--extra-truth", type=Path, action="append", default=[])
    parser.add_argument("--write-normalized", type=Path, default=None)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    parser.add_argument("--verify-file-hashes", action="store_true")
    parser.add_argument("--no-identical-controls", action="store_true")
    parser.add_argument("--no-version-resave-controls", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    files = load_manifest(args.manifest)
    for path in args.extra_manifest:
        files.extend(load_manifest(path))
    manifest_for_report = args.manifest
    if args.write_normalized_manifest:
        manifest_payload = build_manifest_payload(
            files,
            source_manifest=args.manifest,
            extra_source_manifests=args.extra_manifest,
        )
        _write_json(args.write_normalized_manifest, manifest_payload)
        files = manifest_payload["files"]
        manifest_for_report = args.write_normalized_manifest
    pairs = load_truth(args.truth)
    for path in args.extra_truth:
        pairs.extend(load_truth(path))
    if args.write_normalized:
        payload = build_truth_payload(
            files,
            pairs,
            source_manifest=manifest_for_report,
            source_truth=args.truth,
            extra_source_truths=args.extra_truth,
            add_identical_controls=not args.no_identical_controls,
            add_version_resave_controls=not args.no_version_resave_controls,
        )
        _write_json(args.write_normalized, payload)
        pairs = payload["pairs"]

    report = build_report(
        files,
        pairs,
        manifest_path=manifest_for_report,
        truth_path=args.write_normalized or args.truth,
        verify_file_hashes=args.verify_file_hashes,
    )
    _write_json(args.report_json, report)
    _write_text(args.report_md, render_markdown(report))
    print(f"status={report['status']}")
    print(f"active_pairs={report['summary']['active_pair_count']}")
    print(f"excluded_pairs={report['summary']['excluded_pair_count']}")
    print(f"negative_controls={report['summary']['negative_control_count']}")
    return 0 if report["status"] == "passed" else 1


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_files(files: Sequence[dict[str, Any]], *, verify_file_hashes: bool) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(files):
        prefix = f"files[{index}]"
        for field in REQUIRED_FILE_FIELDS:
            if field not in item:
                errors.append(f"{prefix}.{field} is required")
        file_id = str(item.get("file_id") or "")
        if file_id in seen:
            errors.append(f"{prefix}.file_id duplicates {file_id}")
        seen.add(file_id)
        sha = str(item.get("sha256") or "")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
            errors.append(f"{prefix}.sha256 must be a 64-character hex digest")
        path = Path(str(item.get("absolute_path") or ""))
        if not path.exists():
            errors.append(f"{prefix}.absolute_path does not exist: {path}")
            continue
        if verify_file_hashes and sha and sha256_file(path).lower() != sha.lower():
            errors.append(f"{prefix}.sha256 does not match file contents: {file_id}")
    return errors


def _validate_pairs(pairs: Sequence[dict[str, Any]], *, files_by_id: dict[str, dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen_pair_ids: set[str] = set()
    active_keys: dict[tuple[str, str], str] = {}
    for index, pair in enumerate(pairs):
        prefix = f"pairs[{index}]"
        for field in REQUIRED_PAIR_FIELDS:
            if field not in pair:
                errors.append(f"{prefix}.{field} is required")
        pair_id = str(pair.get("pair_id") or "")
        if pair_id in seen_pair_ids:
            errors.append(f"{prefix}.pair_id duplicates {pair_id}")
        seen_pair_ids.add(pair_id)
        before_id = str(pair.get("before_file_id") or "")
        after_id = str(pair.get("after_file_id") or "")
        before = files_by_id.get(before_id)
        after = files_by_id.get(after_id)
        if before is None:
            errors.append(f"{prefix}.before_file_id references an unknown file: {before_id}")
        if after is None:
            errors.append(f"{prefix}.after_file_id references an unknown file: {after_id}")
        if pair.get("accuracy_status", ACTIVE_STATUS) == ACTIVE_STATUS:
            if _looks_reversed(before_id, after_id, before, after):
                errors.append(f"{prefix} appears to have before/after reversed: {pair_id}")
            if _is_risky_pair(pair):
                errors.append(f"{prefix} is risky but still active: {pair_id}")
            key = (before_id, after_id)
            if key in active_keys:
                errors.append(f"{prefix} duplicates active pair direction with {active_keys[key]}: {pair_id}")
            active_keys[key] = pair_id
    return errors


def _append_coverage_warnings(pairs: Sequence[dict[str, Any]], warnings: list[str]) -> None:
    active_pairs = [pair for pair in pairs if pair.get("accuracy_status", ACTIVE_STATUS) == ACTIVE_STATUS]
    types = Counter(str(pair.get("pair_type") or "unknown") for pair in active_pairs)
    if len(active_pairs) < 50:
        warnings.append(f"active pair count is below pilot target: {len(active_pairs)}/50")
    if types.get("identical", 0) == 0:
        warnings.append("identical negative controls are missing")
    if types.get("version_resave", 0) == 0:
        warnings.append("version_resave negative controls are missing")
    if types.get("non_structural_noise", 0) == 0:
        warnings.append("non_structural_noise controls are missing")
    if types.get("block_transform_case", 0) == 0:
        warnings.append("block_transform_case coverage is missing")
    if types.get("import_edge_case", 0) == 0:
        warnings.append("import_edge_case coverage is missing")


def _target_assessment(summary: dict[str, Any]) -> dict[str, Any]:
    assessments: dict[str, Any] = {}
    pair_types = summary.get("pair_type_counts") or {}
    active_pair_count = int(summary.get("active_pair_count") or 0)
    for profile, target in TARGET_PROFILES.items():
        blockers: list[str] = []
        min_active = int(target["min_active_pairs"])
        if active_pair_count < min_active:
            blockers.append(f"active_pair_count={active_pair_count}/{min_active}")
        for pair_type, minimum in (target.get("required_pair_types") or {}).items():
            actual = int(pair_types.get(pair_type, 0))
            if actual < int(minimum):
                blockers.append(f"{pair_type}={actual}/{minimum}")
        if target.get("requires_accuracy_metrics"):
            blockers.append("accuracy_metrics_not_connected")
            blockers.append("performance_metrics_not_connected")
        assessments[profile] = {
            "status": "passed" if not blockers else "blocked",
            "blockers": blockers,
        }
    return assessments


def _exclude_duplicate_active_pairs(pairs: list[dict[str, Any]]) -> None:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for pair in pairs:
        if pair.get("accuracy_status") == ACTIVE_STATUS:
            groups[(str(pair.get("before_file_id")), str(pair.get("after_file_id")))].append(pair)
    for group in groups.values():
        if len(group) <= 1:
            continue
        keep = max(group, key=_duplicate_preference)
        for pair in group:
            if pair is keep:
                pair.setdefault("normalization_actions", []).append("kept_duplicate_preferred_label")
                continue
            pair["accuracy_status"] = EXCLUDED_STATUS
            pair.setdefault("exclusion_reasons", []).append("duplicate_normalized_pair")


def _duplicate_preference(pair: dict[str, Any]) -> tuple[int, int, str]:
    pair_type_score = 2 if pair.get("pair_type") == "structural_change" else 1
    confidence_score = {"high": 3, "medium": 2, "low": 1}.get(str(pair.get("confidence")), 0)
    return pair_type_score, confidence_score, str(pair.get("pair_id") or "")


def _manifest_preference(item: dict[str, Any]) -> tuple[int, int, str]:
    confidentiality = str(item.get("confidentiality") or "unknown")
    confidentiality_score = CONFIDENTIALITY_RANK.get(confidentiality, CONFIDENTIALITY_RANK["unknown"])
    source_score = 1 if str(item.get("source_type") or "") == "customer" else 0
    return confidentiality_score, source_score, str(item.get("absolute_path") or "")


def _build_identical_controls(files: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    by_version: dict[str, dict[str, Any]] = {}
    for item in sorted(files, key=lambda value: str(value.get("file_id") or "")):
        version = str(item.get("dwg_version") or "")
        confidentiality = str(item.get("confidentiality") or "")
        if not version or confidentiality == "customer_confidential":
            continue
        by_version.setdefault(version, item)
    controls = []
    for index, version in enumerate(sorted(by_version), start=1):
        file_id = by_version[version]["file_id"]
        controls.append(
            {
                "pair_id": f"{version}_identical_control_{index:03d}",
                "before_file_id": file_id,
                "after_file_id": file_id,
                "pair_type": "identical",
                "expected_changed": False,
                "expected_change_count": 0,
                "expected_changes": [],
                "reviewer_status": "agent_draft",
                "confidence": "high",
                "dwg_version": version,
                "source_confidentiality": by_version[version].get("confidentiality"),
                "accuracy_status": ACTIVE_STATUS,
                "synthetic_control": True,
                "normalization_actions": ["added_identical_negative_control"],
                "exclusion_reasons": [],
                "notes": "Same-file negative control generated from the corpus manifest; no raw DWG was copied.",
            }
        )
    return controls


def _build_version_resave_controls(
    files: Sequence[dict[str, Any]],
    existing_pairs: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    existing_keys = {
        (str(pair.get("before_file_id") or ""), str(pair.get("after_file_id") or ""))
        for pair in existing_pairs
        if pair.get("accuracy_status", ACTIVE_STATUS) == ACTIVE_STATUS
    }
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in files:
        confidentiality = str(item.get("confidentiality") or "")
        if confidentiality == "customer_confidential":
            continue
        group_key = _version_resave_group_key(item)
        if group_key:
            groups[group_key].append(dict(item))

    controls: list[dict[str, Any]] = []
    for group_key in sorted(groups):
        group = sorted(
            groups[group_key],
            key=lambda item: DWG_VERSION_RANK.get(str(item.get("dwg_version") or ""), 999),
        )
        for before, after in zip(group, group[1:]):
            before_id = str(before.get("file_id") or "")
            after_id = str(after.get("file_id") or "")
            before_version = str(before.get("dwg_version") or "")
            after_version = str(after.get("dwg_version") or "")
            if not before_id or not after_id or before_version == after_version:
                continue
            if (before_id, after_id) in existing_keys:
                continue
            existing_keys.add((before_id, after_id))
            pair_id = f"version_resave_{_slug(group_key)}_{before_version}_to_{after_version}"
            controls.append(
                {
                    "pair_id": pair_id,
                    "before_file_id": before_id,
                    "after_file_id": after_id,
                    "pair_type": "version_resave",
                    "expected_changed": False,
                    "expected_change_count": 0,
                    "expected_changes": [],
                    "reviewer_status": "agent_draft",
                    "confidence": "medium",
                    "dwg_version": f"{before_version}->{after_version}",
                    "dwg_versions": [before_version, after_version],
                    "source_confidentiality": _pair_confidentiality(before, after),
                    "accuracy_status": ACTIVE_STATUS,
                    "synthetic_control": True,
                    "version_resave_group_key": group_key,
                    "normalization_actions": ["added_version_resave_negative_control"],
                    "exclusion_reasons": [],
                    "notes": (
                        "Cross-version same-side negative control generated from an existing "
                        "version matrix group; no raw DWG was copied."
                    ),
                }
            )
    return controls


def _version_resave_group_key(item: dict[str, Any]) -> str | None:
    path = Path(str(item.get("absolute_path") or ""))
    parts = list(path.parts)
    version_index = next(
        (index for index, part in enumerate(parts) if re.fullmatch(r"AC\d{4}", part.upper())),
        None,
    )
    if version_index is None or version_index + 1 >= len(parts):
        return None
    side = parts[version_index + 1].lower()
    if side not in {"before", "after"}:
        return None
    # Keep this conservative: only use matrix folders where the same basename
    # appears under each DWG version. Customer-realistic one-off pairs are not
    # assumed to be version-resave equivalents.
    root_text = "\\".join(parts[:version_index])
    if "version_matrix" not in root_text.lower():
        return None
    return f"{root_text}|{side}|{path.name.lower()}"


def _slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return value[-80:] if len(value) > 80 else value


def _should_swap_pair(
    pair: dict[str, Any],
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> bool:
    before_id = str(pair.get("before_file_id") or "")
    after_id = str(pair.get("after_file_id") or "")
    if _looks_reversed(before_id, after_id, before, after):
        return True
    before_rank = _version_rank(before)
    after_rank = _version_rank(after)
    return before_rank is not None and after_rank is not None and before_rank > after_rank


def _looks_reversed(
    before_id: str,
    after_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> bool:
    before_text = " ".join(filter(None, [before_id, _path_name(before)]))
    after_text = " ".join(filter(None, [after_id, _path_name(after)]))
    return _has_token(before_text, "after") and _has_token(after_text, "before")


def _has_token(text: str, token: str) -> bool:
    lowered = text.lower()
    return bool(re.search(rf"(^|[_\-.\\/\s]){re.escape(token)}([_\-.\\/\s]|$)", lowered))


def _version_rank(file_item: dict[str, Any] | None) -> tuple[int, int] | None:
    if not file_item:
        return None
    text = f"{file_item.get('file_id') or ''} {_path_name(file_item)}".lower()
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    if not match:
        return None
    date_rank = int(match.group(1))
    revision_rank = 0
    if re.search(r"(rev\.?\s*0?1|rev1|_r1|[^a-z]r1[^a-z]|치수수정)", text):
        revision_rank = 1
    return date_rank, revision_rank


def _pair_version(before: dict[str, Any] | None, after: dict[str, Any] | None) -> str | None:
    before_version = before.get("dwg_version") if before else None
    after_version = after.get("dwg_version") if after else None
    if before_version and before_version == after_version:
        return str(before_version)
    if before_version or after_version:
        return f"{before_version or 'unknown'}->{after_version or 'unknown'}"
    return None


def _pair_version_from_ids(pair: dict[str, Any], files_by_id: dict[str, dict[str, Any]]) -> str | None:
    before = files_by_id.get(str(pair.get("before_file_id") or ""))
    after = files_by_id.get(str(pair.get("after_file_id") or ""))
    return _pair_version(before, after)


def _pair_confidentiality(before: dict[str, Any] | None, after: dict[str, Any] | None) -> str | None:
    values = [str(item.get("confidentiality") or "") for item in (before, after) if item]
    if "customer_confidential" in values:
        return "customer_confidential"
    if "internal" in values:
        return "internal"
    if "public" in values:
        return "public"
    return values[0] if values else None


def _is_risky_pair(pair: dict[str, Any]) -> bool:
    text = f"{pair.get('notes') or ''} {' '.join(str(reason) for reason in pair.get('exclusion_reasons') or [])}".lower()
    return any(pattern in text for pattern in RISK_PATTERNS)


def _path_name(file_item: dict[str, Any] | None) -> str:
    if not file_item:
        return ""
    return Path(str(file_item.get("absolute_path") or "")).name


def _files_by_id(files: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("file_id") or ""): dict(item) for item in files}


def _pair_summary(pairs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    active = [pair for pair in pairs if pair.get("accuracy_status") == ACTIVE_STATUS]
    excluded = [pair for pair in pairs if pair.get("accuracy_status") == EXCLUDED_STATUS]
    return {
        "pair_count": len(pairs),
        "active_pair_count": len(active),
        "excluded_pair_count": len(excluded),
        "pair_type_counts": dict(sorted(Counter(str(pair.get("pair_type") or "unknown") for pair in active).items())),
        "version_counts": dict(sorted(Counter(str(pair.get("dwg_version") or "unknown") for pair in active).items())),
    }


def _confidential_file_count(files: Sequence[dict[str, Any]]) -> int:
    return sum(1 for item in files if str(item.get("confidentiality") or "") == "customer_confidential")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = _resolve(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    resolved = _resolve(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(text, encoding="utf-8")


def _resolve(path: Path | None) -> Path:
    if path is None:
        raise ValueError("path is required")
    return path if path.is_absolute() else ROOT / path


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
