"""Aggregate and validate ADR-004 Phase 0-C baseline summaries.

This evidence script reads one or more
``validate_adr004_version_sample_pack.py`` JSON summaries, chooses the best
baseline record per DWG version, and renders a Phase 0-C metrics matrix. It does
not import drawings, compare geometry, or invoke any converter.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
TARGET_DWG_CODES = ("AC1018", "AC1021", "AC1024", "AC1027", "AC1032")
DEFAULT_SUMMARIES = (
    Path("out/adr004_version_samples_20260602_104044/validation_summary_v2.json"),
    Path("out/adr004_compact_compare_samples_20260602_131140/validation_summary.json"),
    Path("out/adr004_ac1032_registered_baseline_20260602_132007/validation_summary.json"),
)
DEFAULT_JSON_REPORT = Path("out/adr004_phase0c_baseline_validation.json")
DEFAULT_MD_REPORT = Path("docs/collab/ADR004_PHASE0C_BASELINE_METRICS.md")


def build_report(
    summary_paths: Sequence[Path],
    *,
    target_versions: Sequence[str] = TARGET_DWG_CODES,
    gap_evidence_paths: Sequence[Path] = (),
    root: Path = ROOT,
) -> dict[str, Any]:
    source_reports = [_load_source_report(path, root=root) for path in summary_paths]
    gap_evidence = [_load_gap_evidence(path, root=root) for path in gap_evidence_paths]
    records_by_version = _records_by_version(source_reports, target_versions=target_versions)
    selected = {
        version: _select_best_record(records_by_version.get(version, []), version=version)
        for version in target_versions
    }
    missing_compare_versions = [
        version
        for version, record in selected.items()
        if not record.get("compare_baseline_ready")
    ]
    compare_ready_versions = [
        version
        for version, record in selected.items()
        if record.get("compare_baseline_ready")
    ]
    header_error_count = sum(
        1
        for records in records_by_version.values()
        for record in records
        if not record.get("headers_ok")
    )
    source_error_count = sum(
        len(source.get("manifest_errors") or []) + len(source.get("validation_errors") or [])
        for source in source_reports
    )
    status = "failed" if source_error_count or header_error_count else ("ok" if not missing_compare_versions else "partial")
    return {
        "schema_version": "adr004-phase0c-baseline-validation/v1",
        "generated_at": datetime.now().isoformat(),
        "status": status,
        "target_versions": list(target_versions),
        "source_summaries": [
            {
                "path": source["path"],
                "status": source["payload"].get("status"),
                "sample_pack": source["payload"].get("sample_pack"),
                "version_count": (source["payload"].get("summary") or {}).get("version_count"),
            }
            for source in source_reports
        ],
        "summary": {
            "target_version_count": len(target_versions),
            "compare_baseline_count": len(compare_ready_versions),
            "compare_ready_versions": compare_ready_versions,
            "missing_compare_versions": missing_compare_versions,
            "header_error_count": header_error_count,
            "source_error_count": source_error_count,
        },
        "versions": selected,
        "gap_evidence": gap_evidence,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# ADR-004 Phase 0-C Baseline Metrics",
        "",
        f"Date: {str(report.get('generated_at') or '')[:10]}",
        "",
        "## Scope",
        "",
        "This matrix consolidates local ADR-004 converted-DXF validation summaries.",
        "It is baseline evidence only and does not claim native AC1018+ DWG support.",
        "",
        "## Status",
        "",
        f"- Overall: `{report.get('status')}`",
        f"- Compare-ready versions: `{', '.join(report.get('summary', {}).get('compare_ready_versions') or []) or '-'}`",
        f"- Compare gaps: `{', '.join(report.get('summary', {}).get('missing_compare_versions') or []) or '-'}`",
        f"- Header errors: `{report.get('summary', {}).get('header_error_count')}`",
        f"- Source validation errors: `{report.get('summary', {}).get('source_error_count')}`",
        "",
        "## Source Summaries",
        "",
        "| status | versions | summary | sample pack |",
        "| --- | ---: | --- | --- |",
    ]
    for source in report.get("source_summaries") or []:
        lines.append(
            "| {status} | {count} | `{path}` | `{pack}` |".format(
                status=_md_cell(str(source.get("status") or "")),
                count=source.get("version_count") or "",
                path=_md_cell(str(source.get("path") or "")),
                pack=_md_cell(str(source.get("sample_pack") or "")),
            )
        )

    lines.extend(
        [
            "",
            "## Baseline Matrix",
            "",
            "| version | Phase 0-C status | pair kind | import entities | warnings | compare | diff summary | elapsed | selected source |",
            "| --- | --- | --- | ---: | --- | --- | --- | ---: | --- |",
        ]
    )
    for version in report.get("target_versions") or []:
        record = (report.get("versions") or {}).get(version) or {}
        lines.append(
            "| {version} | {status} | {kind} | {entities} | {warnings} | {compare} | {diff} | {elapsed} | `{source}` |".format(
                version=version,
                status=_md_cell(str(record.get("phase0c_status") or "")),
                kind=_md_cell(str(record.get("pair_kind") or "")),
                entities=_entity_cell(record.get("import_entity_counts") or {}),
                warnings=_md_cell(", ".join(record.get("warning_codes") or [])),
                compare=_md_cell(str(record.get("compare_status") or "")),
                diff=_md_cell(_diff_summary(record.get("diff_summary"))),
                elapsed=_elapsed_cell(record.get("compare_elapsed_ms")),
                source=_md_cell(str(record.get("source_summary") or "")),
            )
        )

    if report.get("gap_evidence"):
        lines.extend(
            [
                "",
                "## Gap Evidence",
                "",
                "| evidence | version | samples | candidates | classification | reason |",
                "| --- | --- | ---: | ---: | --- | --- |",
            ]
        )
        for evidence in report.get("gap_evidence") or []:
            payload = evidence.get("payload") or {}
            version_counts = (payload.get("summary") or {}).get("version_counts") or {}
            candidate_counts = (payload.get("summary") or {}).get("candidate_counts") or {}
            classifications = payload.get("classifications") or {}
            for version in payload.get("target_versions") or []:
                classification = classifications.get(version) or {}
                lines.append(
                    "| `{path}` | {version} | {samples} | {candidates} | {status} | {reason} |".format(
                        path=_md_cell(str(evidence.get("path") or "")),
                        version=version,
                        samples=version_counts.get(version, 0),
                        candidates=candidate_counts.get(version, 0),
                        status=_md_cell(str(classification.get("status") or "")),
                        reason=_md_cell(str(classification.get("reason") or "")),
                    )
                )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `compare_baseline_ready` means a non-duplicated before/after pair compared successfully with status `ok` or `partial`.",
            "- `import_only_duplicate` means the version has import/header coverage but no real compare-recall baseline.",
            "- `compare_blocked` means a candidate exists but compare failed, timed out, or was skipped.",
            "",
        ]
    )
    return "\n".join(lines)


def _load_source_report(path: Path, *, root: Path) -> dict[str, Any]:
    resolved = _resolve(root, path)
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    return {"path": str(resolved), "payload": payload}


def _load_gap_evidence(path: Path, *, root: Path) -> dict[str, Any]:
    resolved = _resolve(root, path)
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    return {"path": str(resolved), "payload": payload}


def _records_by_version(
    source_reports: Sequence[dict[str, Any]],
    *,
    target_versions: Sequence[str],
) -> dict[str, list[dict[str, Any]]]:
    target_set = set(target_versions)
    records: dict[str, list[dict[str, Any]]] = {version: [] for version in target_versions}
    for source in source_reports:
        payload = source["payload"]
        for item in payload.get("versions") or []:
            version = str(item.get("version") or "")
            if version not in target_set:
                continue
            records.setdefault(version, []).append(_baseline_record(source, item))
    return records


def _baseline_record(source: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    pair_kind = str(item.get("pair_kind") or "")
    compare = item.get("compare") or {}
    imports = item.get("imports") or {}
    outputs = item.get("outputs") or {}
    import_statuses = {
        side: str(result.get("status") or "")
        for side, result in imports.items()
    }
    import_entities = {
        side: result.get("entity_count")
        for side, result in imports.items()
    }
    warning_codes = _unique(
        code
        for result in imports.values()
        for code in (result.get("warning_codes") or [])
    )
    warning_codes.extend(
        code
        for code in (compare.get("warning_codes") or [])
        if code not in warning_codes
    )
    headers_ok = _headers_ok(outputs)
    compare_status = str(compare.get("status") or "not_run")
    duplicated = "duplicated" in pair_kind or "single_file" in pair_kind
    imports_ready = bool(imports) and all(status in {"ok", "partial"} for status in import_statuses.values())
    compare_ready = (
        headers_ok
        and imports_ready
        and not duplicated
        and compare_status in {"ok", "partial"}
    )
    phase0c_status = _phase0c_status(
        compare_ready=compare_ready,
        duplicated=duplicated,
        imports_ready=imports_ready,
        compare_status=compare_status,
    )
    return {
        "version": item.get("version"),
        "phase0c_status": phase0c_status,
        "compare_baseline_ready": compare_ready,
        "headers_ok": headers_ok,
        "pair_kind": pair_kind,
        "dxf_output_version": item.get("dxf_output_version"),
        "source_summary": source["path"],
        "sample_pack": source["payload"].get("sample_pack"),
        "summary_status": source["payload"].get("status"),
        "import_statuses": import_statuses,
        "import_entity_counts": import_entities,
        "warning_codes": warning_codes,
        "dxf_total_bytes": _dxf_total_bytes(outputs),
        "compare_status": compare_status,
        "compare_elapsed_ms": compare.get("elapsed_ms"),
        "diff_summary": compare.get("summary"),
        "blocking_reason": _blocking_reason(
            compare_ready=compare_ready,
            duplicated=duplicated,
            imports_ready=imports_ready,
            compare_status=compare_status,
        ),
        "selection_score": _selection_score(compare_ready, duplicated, imports_ready, compare_status),
    }


def _select_best_record(records: Sequence[dict[str, Any]], *, version: str) -> dict[str, Any]:
    if not records:
        return {
            "version": version,
            "phase0c_status": "missing",
            "compare_baseline_ready": False,
            "headers_ok": False,
            "pair_kind": "",
            "source_summary": "",
            "import_statuses": {},
            "import_entity_counts": {},
            "warning_codes": [],
            "dxf_total_bytes": 0,
            "compare_status": "missing",
            "compare_elapsed_ms": None,
            "diff_summary": None,
            "blocking_reason": "missing_version_record",
            "selection_score": 0,
        }
    return max(records, key=lambda record: (int(record.get("selection_score") or 0), -int(record.get("dxf_total_bytes") or 0)))


def _phase0c_status(*, compare_ready: bool, duplicated: bool, imports_ready: bool, compare_status: str) -> str:
    if compare_ready:
        return "compare_baseline_ready"
    if duplicated and imports_ready:
        return "import_only_duplicate"
    if imports_ready and compare_status in {"timeout", "failed", "skipped"}:
        return "compare_blocked"
    if imports_ready:
        return "import_only"
    return "missing_or_failed"


def _blocking_reason(*, compare_ready: bool, duplicated: bool, imports_ready: bool, compare_status: str) -> str:
    if compare_ready:
        return ""
    if duplicated and imports_ready:
        return "real_before_after_revision_pair_missing"
    if not imports_ready:
        return "import_not_ready"
    if compare_status == "timeout":
        return "compare_timeout"
    if compare_status == "skipped":
        return "compare_skipped"
    if compare_status == "failed":
        return "compare_failed"
    return "compare_baseline_missing"


def _selection_score(compare_ready: bool, duplicated: bool, imports_ready: bool, compare_status: str) -> int:
    if compare_ready:
        return 100
    if duplicated and imports_ready:
        return 60
    if imports_ready and compare_status in {"timeout", "failed", "skipped"}:
        return 50
    if imports_ready:
        return 40
    return 0


def _headers_ok(outputs: dict[str, Any]) -> bool:
    seen = 0
    for side in ("before", "after"):
        for output in outputs.get(side) or []:
            seen += 1
            if not output.get("exists") or not output.get("header_matches_expected"):
                return False
    return seen >= 2


def _dxf_total_bytes(outputs: dict[str, Any]) -> int:
    total = 0
    for side in ("before", "after"):
        for output in outputs.get(side) or []:
            total += int(output.get("actual_size") or output.get("manifest_size") or 0)
    return total


def _unique(values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "")
        if text and text not in result:
            result.append(text)
    return result


def _entity_cell(values: dict[str, Any]) -> str:
    before = values.get("before")
    after = values.get("after")
    if before is None and after is None:
        return ""
    return f"{before or ''} / {after or ''}"


def _elapsed_cell(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value) / 1000.0:.2f}s"
    except (TypeError, ValueError):
        return str(value)


def _diff_summary(summary: Any) -> str:
    if not isinstance(summary, dict):
        return ""
    keys = ("added", "removed", "modified", "unchanged", "total_changes")
    return ", ".join(f"{key} {summary.get(key)}" for key in keys if key in summary)


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _existing_default_summaries(root: Path) -> list[Path]:
    return [path for path in DEFAULT_SUMMARIES if _resolve(root, path).exists()]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", action="append", type=Path, default=[])
    parser.add_argument("--gap-evidence", action="append", type=Path, default=[])
    parser.add_argument("--out", type=Path, default=DEFAULT_JSON_REPORT)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_MD_REPORT)
    args = parser.parse_args(argv)

    summaries = args.summary or _existing_default_summaries(ROOT)
    if not summaries:
        parser.error("at least one --summary path is required when default summaries are absent")
    report = build_report(summaries, gap_evidence_paths=args.gap_evidence)
    out = _resolve(ROOT, args.out)
    report_md = _resolve(ROOT, args.report_md)
    _write_json(out, report)
    _write_text(report_md, render_markdown(report))
    print(
        "adr004 phase0c baselines: "
        f"status={report['status']} "
        f"compare_ready={report['summary']['compare_ready_versions']} "
        f"gaps={report['summary']['missing_compare_versions']} "
        f"json={out} md={report_md}"
    )
    return 0 if report["status"] in {"ok", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
