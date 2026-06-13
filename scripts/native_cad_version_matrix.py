"""Validate the native CAD version coverage matrix."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.comparison.dwg_importer import DwgVersionDetector  # noqa: E402


DEFAULT_MATRIX_PATH = Path("docs/collab/native_cad_version_matrix.json")
STATE_ORDER = (
    "blocked",
    "contracted",
    "importable",
    "comparable",
    "viewable",
    "release_candidate",
    "enabled",
)
BACKEND_POLICIES = {"default_cleanroom", "explicit_bridge_only", "fail_closed"}
REQUIRED_EVIDENCE_KEYS = {
    "bridge_contract_fixture",
    "canonical_import_fixture",
    "compare_fixture",
    "viewer_lod0_fixture",
    "real_sample_corpus",
    "real_bridge_contract",
    "canonical_import_real",
    "compare_real",
    "viewer_lod0_real",
    "failure_taxonomy",
    "cache_identity",
    "policy_gate",
    "fallback_tests",
    "cleanroom_import",
}
PROMOTION_EVIDENCE_KEYS = {
    "real_sample_corpus",
    "canonical_import_real",
    "compare_real",
    "viewer_lod0_real",
    "failure_taxonomy",
    "cache_identity",
    "policy_gate",
    "fallback_tests",
}


@dataclass(frozen=True)
class MatrixIssue:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def expected_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for code, (_family, release) in DwgVersionDetector.KNOWN_UNSUPPORTED_CODES.items():
        versions[code] = release
    for code, (_family, release) in DwgVersionDetector.SUPPORTED_CODES.items():
        versions[code] = release
    return dict(sorted(versions.items()))


def supported_codes() -> set[str]:
    return set(DwgVersionDetector.SUPPORTED_CODES)


def load_matrix(path: Path = DEFAULT_MATRIX_PATH, *, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    resolved = path if path.is_absolute() else repo_root / path
    return json.loads(resolved.read_text(encoding="utf-8"))


def validate_matrix(payload: Mapping[str, Any]) -> list[MatrixIssue]:
    issues: list[MatrixIssue] = []
    if payload.get("schema_version") != 1:
        issues.append(MatrixIssue("MATRIX_SCHEMA", "schema_version must be 1."))
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return [*issues, MatrixIssue("MATRIX_ROWS", "rows must be a list.")]

    expected = expected_versions()
    seen: dict[str, Mapping[str, Any]] = {}
    duplicates: set[str] = set()
    for raw_row in rows:
        if not isinstance(raw_row, Mapping):
            issues.append(MatrixIssue("ROW_TYPE", "each row must be an object."))
            continue
        code = str(raw_row.get("code") or "")
        if code in seen:
            duplicates.add(code)
        seen[code] = raw_row

    for code in sorted(duplicates):
        issues.append(MatrixIssue("ROW_DUPLICATE", f"{code} appears more than once."))
    missing = sorted(set(expected) - set(seen))
    extra = sorted(set(seen) - set(expected))
    for code in missing:
        issues.append(MatrixIssue("ROW_MISSING", f"{code} is missing from the matrix."))
    for code in extra:
        issues.append(MatrixIssue("ROW_UNEXPECTED", f"{code} is not a known target version."))

    for code, row in sorted(seen.items()):
        if code not in expected:
            continue
        issues.extend(_validate_row(code, row, expected[code]))
    return issues


def _validate_row(code: str, row: Mapping[str, Any], expected_release: str) -> list[MatrixIssue]:
    issues: list[MatrixIssue] = []
    state = str(row.get("state") or "")
    backend_policy = str(row.get("backend_policy") or "")
    default_enabled = row.get("default_enabled")
    evidence = row.get("evidence")

    if row.get("release_family") != expected_release:
        issues.append(MatrixIssue("ROW_RELEASE", f"{code} release_family must be {expected_release!r}."))
    if state not in STATE_ORDER:
        issues.append(MatrixIssue("ROW_STATE", f"{code} state {state!r} is invalid."))
    if backend_policy not in BACKEND_POLICIES:
        issues.append(MatrixIssue("ROW_BACKEND_POLICY", f"{code} backend_policy {backend_policy!r} is invalid."))
    if not isinstance(default_enabled, bool):
        issues.append(MatrixIssue("ROW_DEFAULT_ENABLED_TYPE", f"{code} default_enabled must be boolean."))
    elif default_enabled != (code in supported_codes()):
        issues.append(
            MatrixIssue(
                "ROW_DEFAULT_ENABLED",
                f"{code} default_enabled must match DwgVersionDetector.SUPPORTED_CODES.",
            )
        )
    if backend_policy == "default_cleanroom" and default_enabled is not True:
        issues.append(MatrixIssue("ROW_DEFAULT_POLICY", f"{code} default_cleanroom must be default_enabled."))
    if backend_policy != "default_cleanroom" and default_enabled is True:
        issues.append(MatrixIssue("ROW_DEFAULT_POLICY", f"{code} enabled defaults must use default_cleanroom."))
    if not isinstance(evidence, Mapping):
        issues.append(MatrixIssue("ROW_EVIDENCE", f"{code} evidence must be an object."))
        return issues

    evidence_keys = set(str(key) for key in evidence)
    missing_evidence = sorted(REQUIRED_EVIDENCE_KEYS - evidence_keys)
    for key in missing_evidence:
        issues.append(MatrixIssue("ROW_EVIDENCE_MISSING", f"{code} evidence.{key} is missing."))
    for key in sorted(REQUIRED_EVIDENCE_KEYS & evidence_keys):
        if not isinstance(evidence.get(key), bool):
            issues.append(MatrixIssue("ROW_EVIDENCE_TYPE", f"{code} evidence.{key} must be boolean."))

    state_index = STATE_ORDER.index(state) if state in STATE_ORDER else 0
    if state_index >= STATE_ORDER.index("contracted") and not (
        evidence.get("bridge_contract_fixture") or evidence.get("real_bridge_contract")
    ):
        issues.append(MatrixIssue("ROW_CONTRACT_EVIDENCE", f"{code} contracted rows need bridge evidence."))
    if state_index >= STATE_ORDER.index("importable") and not (
        evidence.get("canonical_import_real")
        or evidence.get("canonical_import_fixture")
        or evidence.get("cleanroom_import")
    ):
        issues.append(MatrixIssue("ROW_IMPORT_EVIDENCE", f"{code} importable rows need import evidence."))
    if state_index >= STATE_ORDER.index("comparable") and not (
        evidence.get("compare_real") or evidence.get("compare_fixture")
    ):
        issues.append(MatrixIssue("ROW_COMPARE_EVIDENCE", f"{code} comparable rows need compare evidence."))
    if state_index >= STATE_ORDER.index("viewable") and not (
        evidence.get("viewer_lod0_real") or evidence.get("viewer_lod0_fixture")
    ):
        issues.append(MatrixIssue("ROW_VIEW_EVIDENCE", f"{code} viewable rows need LOD0 evidence."))
    if state in {"release_candidate", "enabled"}:
        for key in sorted(PROMOTION_EVIDENCE_KEYS):
            if evidence.get(key) is not True:
                issues.append(MatrixIssue("ROW_PROMOTION_EVIDENCE", f"{code} promotion requires evidence.{key}."))
    return issues


def summarize_matrix(payload: Mapping[str, Any]) -> dict[str, Any]:
    rows = [row for row in payload.get("rows", []) if isinstance(row, Mapping)]
    counts: dict[str, int] = {state: 0 for state in STATE_ORDER}
    for row in rows:
        state = str(row.get("state") or "")
        if state in counts:
            counts[state] += 1
    return {
        "schema_version": payload.get("schema_version"),
        "row_count": len(rows),
        "state_counts": counts,
        "default_enabled_codes": sorted(
            str(row.get("code"))
            for row in rows
            if isinstance(row.get("default_enabled"), bool) and row.get("default_enabled")
        ),
    }


def command_validate(args: argparse.Namespace) -> int:
    matrix = load_matrix(Path(args.matrix))
    issues = validate_matrix(matrix)
    output = {
        "status": "PASS" if not issues else "FAIL",
        "summary": summarize_matrix(matrix),
        "issues": [issue.to_dict() for issue in issues],
    }
    if args.json:
        print(json.dumps(output, indent=2, ensure_ascii=False))
    elif issues:
        print("Native CAD version matrix failed:", file=sys.stderr)
        for issue in issues:
            print(f"  - {issue.code}: {issue.message}", file=sys.stderr)
    else:
        print("Native CAD version matrix passed.")
    return 0 if not issues else 1


def command_summary(args: argparse.Namespace) -> int:
    matrix = load_matrix(Path(args.matrix))
    print(json.dumps(summarize_matrix(matrix), indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--matrix", default=str(DEFAULT_MATRIX_PATH))
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=command_validate)

    summary = subparsers.add_parser("summary")
    summary.add_argument("--matrix", default=str(DEFAULT_MATRIX_PATH))
    summary.set_defaults(func=command_summary)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
