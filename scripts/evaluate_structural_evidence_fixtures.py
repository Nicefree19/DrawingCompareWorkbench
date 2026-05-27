"""Evaluate the structural evidence chain against sanitized fixture cases."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = Path("docs/structural_evidence_fixture_matrix_v0_1.json")
DEFAULT_ARTIFACT_DIR = Path("build/structural-fixture-evaluation")
DEFAULT_JSON_REPORT = Path("build/reports/structural-evidence-fixture-evaluation.json")
DEFAULT_MD_REPORT = Path("build/reports/structural-evidence-fixture-evaluation.md")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.cli.structural_evidence_bridge import main as structural_bridge_main  # noqa: E402
from src.services.comparison.structural_output_safety import (  # noqa: E402
    find_structural_output_safety_findings,
)


def load_matrix(path: Path = DEFAULT_MATRIX, *, root: Path = ROOT) -> dict[str, Any]:
    return json.loads(_resolve(root, path).read_text(encoding="utf-8"))


def validate_matrix(matrix: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if matrix.get("schema_version") != "structural-evidence-fixture-matrix/v0.1":
        errors.append("matrix.schema_version must be structural-evidence-fixture-matrix/v0.1")
    cases = matrix.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("matrix.cases must be a non-empty list")
        return errors
    case_ids = [str(case.get("case_id") or "") for case in cases if isinstance(case, dict)]
    if len(case_ids) != len(set(case_ids)):
        errors.append("case_id values must be unique")
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            errors.append(f"cases[{index}] must be an object")
            continue
        prefix = f"cases[{index}]"
        mode = case.get("mode")
        if mode not in {"analyze", "compare"}:
            errors.append(f"{prefix}.mode must be analyze or compare")
        if not case.get("case_id"):
            errors.append(f"{prefix}.case_id is required")
        if not isinstance(case.get("expected"), dict):
            errors.append(f"{prefix}.expected is required")
        if mode == "analyze" and not isinstance(case.get("input"), dict):
            errors.append(f"{prefix}.input is required for analyze mode")
        if mode == "compare":
            if not isinstance(case.get("before"), dict):
                errors.append(f"{prefix}.before is required for compare mode")
            if not isinstance(case.get("after"), dict):
                errors.append(f"{prefix}.after is required for compare mode")
    return errors


def build_report(
    matrix: dict[str, Any],
    *,
    root: Path = ROOT,
    artifact_dir: Path | None = None,
) -> dict[str, Any]:
    matrix_errors = validate_matrix(matrix)
    artifact_root = _resolve(root, artifact_dir or DEFAULT_ARTIFACT_DIR)
    case_results = [
        _evaluate_case(case, root=root, artifact_root=artifact_root)
        for case in matrix.get("cases") or []
        if isinstance(case, dict)
    ]
    passed_count = sum(1 for result in case_results if result.get("passed") is True)
    case_count = len(case_results)
    pass_rate = round(passed_count / case_count, 4) if case_count else 0.0
    return {
        "schema_version": "structural-evidence-fixture-evaluation/v0.1",
        "generated_at": datetime.now().isoformat(),
        "status": "ok" if not matrix_errors and passed_count == case_count else "failed",
        "matrix_errors": matrix_errors,
        "quality_contract": matrix.get("quality_contract") or {},
        "summary": {
            "case_count": case_count,
            "passed_count": passed_count,
            "failed_count": case_count - passed_count,
            "pass_rate": pass_rate,
            "artifact_root": str(artifact_root),
        },
        "cases": case_results,
        "false_positive_backlog": matrix.get("false_positive_backlog") or [],
        "false_negative_backlog": matrix.get("false_negative_backlog") or [],
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Structural Evidence Fixture Evaluation",
        "",
        f"Status: **{report.get('status')}**",
        f"Pass rate: `{summary.get('passed_count')}/{summary.get('case_count')}`",
        f"Artifact root: `{summary.get('artifact_root')}`",
        "",
        "## Cases",
        "",
        "| case | mode | passed | source health | evidence | errors |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for case in report.get("cases") or []:
        errors = "; ".join(case.get("errors") or [])
        lines.append(
            "| {case_id} | {mode} | {passed} | {source_health} | {evidence_count} | {errors} |".format(
                case_id=case.get("case_id"),
                mode=case.get("mode"),
                passed=case.get("passed"),
                source_health=case.get("source_health"),
                evidence_count=case.get("evidence_count"),
                errors=errors,
            )
        )
    lines.extend(["", "## False Positive Backlog", ""])
    for item in report.get("false_positive_backlog") or []:
        lines.append(f"- `{item.get('id')}` ({item.get('case_id')}): {item.get('risk')}")
    lines.extend(["", "## False Negative Backlog", ""])
    for item in report.get("false_negative_backlog") or []:
        lines.append(f"- `{item.get('id')}` ({item.get('case_id')}): {item.get('risk')}")
    lines.append("")
    return "\n".join(lines)


def write_reports(
    report: dict[str, Any],
    *,
    json_report: Path = DEFAULT_JSON_REPORT,
    markdown_report: Path = DEFAULT_MD_REPORT,
    root: Path = ROOT,
) -> None:
    json_path = _resolve(root, json_report)
    markdown_path = _resolve(root, markdown_report)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")


def _evaluate_case(case: dict[str, Any], *, root: Path, artifact_root: Path) -> dict[str, Any]:
    case_id = str(case.get("case_id") or "unnamed")
    mode = str(case.get("mode") or "")
    case_artifacts = artifact_root / case_id / "artifacts"
    try:
        argv = _bridge_argv(case, root=root, artifact_root=artifact_root, case_artifacts=case_artifacts)
        exit_code, payload, output_text = _run_bridge(argv)
        safety_findings = find_structural_output_safety_findings(payload)
        errors = _check_expected(
            case=case,
            payload=payload,
            exit_code=exit_code,
            output_text=output_text,
            safety_findings=safety_findings,
        )
        return _case_result(
            case_id=case_id,
            mode=mode,
            payload=payload,
            exit_code=exit_code,
            output_text=output_text,
            safety_findings=safety_findings,
            errors=errors,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "case_id": case_id,
            "mode": mode,
            "passed": False,
            "errors": [f"case raised {type(exc).__name__}: {exc}"],
            "exit_code": None,
            "source_health": None,
            "evidence_count": 0,
            "issue_kinds": [],
            "evidence_source_kinds": [],
            "safety_findings": [],
            "output_json_bytes": 0,
            "artifact_keys": [],
        }


def _bridge_argv(
    case: dict[str, Any],
    *,
    root: Path,
    artifact_root: Path,
    case_artifacts: Path,
) -> list[str]:
    mode = str(case["mode"])
    argv = ["--json", mode]
    if mode == "analyze":
        source_path = _prepare_input(case["input"], root=root, artifact_root=artifact_root, case_id=case["case_id"])
        argv.extend(["--path", str(source_path)])
    elif mode == "compare":
        before_path = _prepare_input(case["before"], root=root, artifact_root=artifact_root, case_id=case["case_id"])
        after_path = _prepare_input(case["after"], root=root, artifact_root=artifact_root, case_id=case["case_id"])
        argv.extend(["--before", str(before_path), "--after", str(after_path)])
    else:
        raise ValueError(f"unsupported fixture mode: {mode}")
    question = str(case.get("question") or "")
    if question:
        argv.extend(["--question", question])
    for item in case.get("checklist") or []:
        argv.extend(["--checklist", str(item)])
    argv.extend(["--artifact-dir", str(case_artifacts)])
    argv.extend(["--max-evidence", str(int(case.get("max_evidence") or 30))])
    return argv


def _prepare_input(
    spec: dict[str, Any],
    *,
    root: Path,
    artifact_root: Path,
    case_id: str,
) -> Path:
    kind = str(spec.get("kind") or "")
    if kind == "repo_file":
        return _resolve(root, Path(str(spec["path"])))
    input_dir = artifact_root / str(case_id) / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    if kind == "transient_missing":
        return input_dir / str(spec.get("file_name") or "missing.dxf")
    if kind == "transient_dwg_header":
        path = input_dir / str(spec.get("file_name") or "blocked.dwg")
        version = str(spec.get("version") or "AC1032").encode("ascii", errors="replace")[:6]
        path.write_bytes(version + (b"0" * 100))
        return path
    if kind == "generated_large_dxf":
        path = input_dir / str(spec.get("file_name") or "large_grid.dxf")
        _write_large_grid_dxf(path, grid_count=int(spec.get("grid_count") or 80))
        return path
    raise ValueError(f"unsupported fixture input kind: {kind}")


def _run_bridge(argv: Sequence[str]) -> tuple[int, dict[str, Any], str]:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        exit_code = structural_bridge_main(list(argv))
    output_text = stdout.getvalue()
    payload = json.loads(output_text)
    if not isinstance(payload, dict):
        raise ValueError("bridge output is not a JSON object")
    return exit_code, payload, output_text


def _check_expected(
    *,
    case: dict[str, Any],
    payload: dict[str, Any],
    exit_code: int,
    output_text: str,
    safety_findings: list[dict[str, str]],
) -> list[str]:
    expected = case.get("expected") or {}
    errors: list[str] = []
    if exit_code != expected.get("exit_code"):
        errors.append(f"exit_code expected {expected.get('exit_code')} got {exit_code}")
    if payload.get("schema_version") != "structural-drawing-evidence/v0.1":
        errors.append("schema_version mismatch")
    if payload.get("status") != expected.get("status"):
        errors.append(f"status expected {expected.get('status')} got {payload.get('status')}")
    source_health = (payload.get("source") or {}).get("source_health")
    if source_health != expected.get("source_health"):
        errors.append(f"source_health expected {expected.get('source_health')} got {source_health}")
    evidence = payload.get("evidence") or []
    if len(evidence) < int(expected.get("min_evidence", 0)):
        errors.append(f"evidence below minimum: {len(evidence)}")
    if len(evidence) > int(expected.get("max_evidence", 30)):
        errors.append(f"evidence above maximum: {len(evidence)}")
    if len(evidence) > 30:
        errors.append(f"evidence above global cap: {len(evidence)}")
    issue_kinds = [str(item.get("kind") or "") for item in payload.get("issue_suggestions") or []]
    if issue_kinds != list(expected.get("issue_kinds") or []):
        errors.append(f"issue_kinds expected {expected.get('issue_kinds') or []} got {issue_kinds}")
    evidence_source_kinds = sorted(
        {str(item.get("source_kind") or "drawing_anchor") for item in evidence}
    )
    expected_source_kinds = sorted(str(item) for item in expected.get("evidence_source_kinds") or [])
    for source_kind in expected_source_kinds:
        if source_kind not in evidence_source_kinds:
            errors.append(f"missing evidence source_kind {source_kind}")
    for text in expected.get("anchor_text_contains") or []:
        if not any(str(text) in str(item.get("anchor_text") or "") for item in evidence):
            errors.append(f"missing anchor text containing {text}")
    if expected.get("unsupported_counts_present") and not payload.get("unsupported_counts"):
        errors.append("unsupported_counts expected but empty")
    if payload.get("summary", {}).get("judgment_level") != "issue_suggestion_only":
        errors.append("summary.judgment_level must be issue_suggestion_only")
    if payload.get("summary", {}).get("requires_human_review") is not True:
        errors.append("summary.requires_human_review must be true")
    diagnostics_error_code = expected.get("diagnostics_error_code")
    if diagnostics_error_code and (payload.get("diagnostics") or {}).get("error_code") != diagnostics_error_code:
        errors.append(f"diagnostics.error_code expected {diagnostics_error_code}")
    for diagnostic_path in expected.get("diagnostics_paths") or []:
        actual = _path_get(payload, tuple(diagnostic_path.get("path") or ()))
        if actual != diagnostic_path.get("equals"):
            errors.append(f"{'.'.join(diagnostic_path.get('path') or [])} expected {diagnostic_path.get('equals')} got {actual}")
    artifact_paths = payload.get("artifact_paths") or {}
    for key in expected.get("artifact_keys") or []:
        path_text = artifact_paths.get(str(key))
        if not path_text or not Path(path_text).is_file():
            errors.append(f"artifact {key} missing")
    if safety_findings:
        errors.append("safety findings present: " + ", ".join(finding["code"] for finding in safety_findings))
    max_bytes = int(expected.get("max_compact_json_bytes") or 0)
    if max_bytes and len(output_text.encode("utf-8")) > max_bytes:
        errors.append(f"compact JSON exceeds byte budget {max_bytes}")
    return errors


def _case_result(
    *,
    case_id: str,
    mode: str,
    payload: dict[str, Any],
    exit_code: int,
    output_text: str,
    safety_findings: list[dict[str, str]],
    errors: Sequence[str],
) -> dict[str, Any]:
    evidence = payload.get("evidence") or []
    artifact_paths = payload.get("artifact_paths") or {}
    return {
        "case_id": case_id,
        "mode": mode,
        "passed": not errors,
        "errors": list(errors),
        "exit_code": exit_code,
        "status": payload.get("status"),
        "source_health": (payload.get("source") or {}).get("source_health"),
        "evidence_count": len(evidence),
        "issue_kinds": [item.get("kind") for item in payload.get("issue_suggestions") or []],
        "evidence_source_kinds": sorted(
            {str(item.get("source_kind") or "drawing_anchor") for item in evidence}
        ),
        "safety_findings": safety_findings,
        "output_json_bytes": len(output_text.encode("utf-8")),
        "artifact_keys": sorted(artifact_paths),
        "run_id": payload.get("run_id"),
    }


def _write_large_grid_dxf(path: Path, *, grid_count: int) -> None:
    body: list[str] = [
        "0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1032", "0", "ENDSEC",
        "0", "SECTION", "2", "TABLES",
        "0", "TABLE", "2", "LAYER",
        "0", "LAYER", "2", "S-GRID", "70", "0", "62", "7", "6", "Continuous",
        "0", "LAYER", "2", "S-COL", "70", "0", "62", "3", "6", "Continuous",
        "0", "ENDTAB", "0", "ENDSEC",
        "0", "SECTION", "2", "BLOCKS", "0", "ENDSEC",
        "0", "SECTION", "2", "ENTITIES",
    ]
    for index in range(max(1, grid_count)):
        x = index * 10
        label = f"GRID-A{index + 1}"
        body.extend([
            "0", "TEXT", "8", "S-GRID", "10", str(x), "20", "0", "40", "2.5", "1", label,
            "0", "LINE", "8", "S-GRID", "10", str(x), "20", "0", "11", str(x), "21", "100",
        ])
    for index in range(10):
        x = index * 25
        label = f"COLUMN C{index + 1}"
        body.extend([
            "0", "TEXT", "8", "S-COL", "10", str(x), "20", "25", "40", "2.5", "1", label,
        ])
    body.extend(["0", "ENDSEC", "0", "EOF"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(body) + "\n", encoding="utf-8")


def _path_get(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _resolve(root: Path, path: Path) -> Path:
    if path.is_absolute():
        return path
    return root / path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate structural evidence fixture matrix.")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--json-report", type=Path, default=DEFAULT_JSON_REPORT)
    parser.add_argument("--markdown-report", type=Path, default=DEFAULT_MD_REPORT)
    parser.add_argument("--json", action="store_true", help="Print the evaluation report as JSON.")
    parser.add_argument("--no-write", action="store_true", help="Do not write report artifacts.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    matrix = load_matrix(args.matrix)
    report = build_report(matrix, artifact_dir=args.artifact_dir)
    if not args.no_write:
        write_reports(
            report,
            json_report=args.json_report,
            markdown_report=args.markdown_report,
        )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        summary = report["summary"]
        print(
            "structural fixture evaluation: "
            f"status={report['status']} "
            f"passed={summary['passed_count']}/{summary['case_count']} "
            f"artifact_root={summary['artifact_root']}"
        )
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
