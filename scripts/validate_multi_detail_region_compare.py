"""Run and summarize the multi-detail region compare pilot.

The pilot is intentionally evidence-driven. It can execute compare runs from a
manifest, or collect metrics from existing run output directories. Acceptance
criteria that require human review evidence remain "not_evaluable" until the
manifest supplies those counts.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


SUMMARY_JSON_NAME = "multi_detail_region_pilot_summary.json"
REPORT_MD_NAME = "MULTI_DETAIL_REGION_COMPARE_PILOT_REPORT.md"


@dataclass(frozen=True)
class PilotCase:
    case_id: str
    output_dir: Path | None
    source_a: Path | None
    source_b: Path | None
    recursive: bool
    expected_region_count: int | None
    expected_match_count: int | None
    review_evidence: Mapping[str, Any]
    screenshots: tuple[Path, ...]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Pilot manifest JSON.")
    parser.add_argument("--output", required=True, type=Path, help="Output directory.")
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="Do not run comparisons; every case must provide output_dir.",
    )
    parser.add_argument(
        "--max-preview-pairs",
        type=int,
        default=0,
        help="Forwarded to FolderCompareRunRequest when running cases.",
    )
    args = parser.parse_args(argv)

    try:
        payload = run_pilot(
            args.input,
            args.output,
            collect_only=args.collect_only,
            max_preview_pairs=args.max_preview_pairs,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"pilot validation failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(_console_payload(payload), ensure_ascii=False, indent=2))
    return 0 if payload["overall_status"] in {"passed", "needs_review_evidence"} else 1


def run_pilot(
    manifest_path: Path,
    output_dir: Path,
    *,
    collect_only: bool = False,
    max_preview_pairs: int = 0,
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = _read_json(manifest_path)
    cases = _load_cases(manifest, manifest_path.parent)
    if not cases:
        raise ValueError("pilot manifest must include at least one run or pair")

    started_at = _now_iso()
    case_payloads: list[dict[str, Any]] = []
    for case in cases:
        run_output = case.output_dir
        if run_output is None:
            if collect_only:
                raise ValueError(f"case {case.case_id} has no output_dir in collect-only mode")
            run_output = _run_case(case, output_dir, max_preview_pairs=max_preview_pairs)
        case_payloads.append(_collect_case_metrics(case, run_output))

    totals = _aggregate_cases(case_payloads)
    acceptance = _build_acceptance(totals)
    overall_status = _overall_status(acceptance)
    payload = {
        "schema_version": 1,
        "mode": "multi_detail_region_compare_pilot",
        "started_at": started_at,
        "finished_at": _now_iso(),
        "manifest_path": str(manifest_path),
        "output_dir": str(output_dir),
        "case_count": len(case_payloads),
        "overall_status": overall_status,
        "cases": case_payloads,
        "totals": totals,
        "acceptance": acceptance,
    }

    summary_path = output_dir / SUMMARY_JSON_NAME
    report_path = output_dir / REPORT_MD_NAME
    _write_json(summary_path, payload)
    report_path.write_text(_render_report(payload), encoding="utf-8")
    payload["summary_json"] = str(summary_path)
    payload["report_md"] = str(report_path)
    _write_json(summary_path, payload)
    return payload


def _load_cases(manifest: Mapping[str, Any], base_dir: Path) -> list[PilotCase]:
    cases: list[PilotCase] = []
    for index, item in enumerate(_as_list(manifest.get("runs"))):
        cases.append(_case_from_mapping(item, base_dir, default_id=f"run-{index + 1}"))
    for index, item in enumerate(_as_list(manifest.get("pairs"))):
        cases.append(_case_from_mapping(item, base_dir, default_id=f"pair-{index + 1}"))
    if not cases and ("source_a" in manifest or "source_b" in manifest or "output_dir" in manifest):
        cases.append(_case_from_mapping(manifest, base_dir, default_id="pilot"))
    return cases


def _case_from_mapping(item: Any, base_dir: Path, *, default_id: str) -> PilotCase:
    if not isinstance(item, Mapping):
        raise ValueError(f"pilot case must be an object: {item!r}")
    case_id = str(item.get("case_id") or item.get("pair_id") or item.get("run_id") or default_id)
    review_evidence = item.get("review_evidence")
    if not isinstance(review_evidence, Mapping):
        review_evidence = {}
    screenshots = tuple(
        _resolve_path(path, base_dir)
        for path in _as_list(item.get("screenshots") or item.get("viewer_screenshots"))
    )
    return PilotCase(
        case_id=_safe_name(case_id),
        output_dir=_optional_path(item.get("output_dir"), base_dir),
        source_a=_optional_path(item.get("source_a") or item.get("before"), base_dir),
        source_b=_optional_path(item.get("source_b") or item.get("after"), base_dir),
        recursive=bool(item.get("recursive", False)),
        expected_region_count=_optional_int(item.get("expected_region_count")),
        expected_match_count=_optional_int(item.get("expected_match_count")),
        review_evidence=review_evidence,
        screenshots=screenshots,
    )


def _run_case(case: PilotCase, output_root: Path, *, max_preview_pairs: int) -> Path:
    if case.source_a is None or case.source_b is None:
        raise ValueError(f"case {case.case_id} must provide source_a/source_b or output_dir")
    source_a = case.source_a
    source_b = case.source_b
    staged_root = output_root / "_staged_inputs" / case.case_id
    if source_a.is_file() and source_b.is_file():
        source_a, source_b = _stage_file_pair(case, staged_root)
    if not source_a.exists() or not source_b.exists():
        raise ValueError(f"case {case.case_id} source path does not exist")

    from src.services.comparison.folder_compare_pipeline import (
        FolderComparePipeline,
        FolderCompareRunRequest,
    )

    run_output = output_root / "runs" / case.case_id
    request = FolderCompareRunRequest(
        source_a=source_a,
        source_b=source_b,
        output_dir=run_output,
        recursive=case.recursive,
        max_preview_pairs=max_preview_pairs,
        export_profile="internal",
    )
    with _region_compare_env():
        result = FolderComparePipeline(request).run()
    return Path(result.output_dir).resolve()


def _stage_file_pair(case: PilotCase, staged_root: Path) -> tuple[Path, Path]:
    assert case.source_a is not None
    assert case.source_b is not None
    before_dir = staged_root / "before"
    after_dir = staged_root / "after"
    before_dir.mkdir(parents=True, exist_ok=True)
    after_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(case.source_a, before_dir / case.source_a.name)
    shutil.copy2(case.source_b, after_dir / case.source_b.name)
    return before_dir, after_dir


@contextmanager
def _region_compare_env() -> Iterable[None]:
    keys = {
        "DRAWING_COMPARE_MULTI_FRAME": "auto",
        "DRAWING_COMPARE_AUTO_REGION_COMPARE": "1",
    }
    old = {key: os.environ.get(key) for key in keys}
    os.environ.update(keys)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _collect_case_metrics(case: PilotCase, run_output: Path) -> dict[str, Any]:
    run_output = run_output.resolve()
    artifact_dir = run_output / "artifacts"
    detection = _read_optional_json(artifact_dir / "region_detection_summary.json")
    matching = _read_optional_json(artifact_dir / "region_match_summary.json")
    localized = _read_optional_json(artifact_dir / "localized_compare_summary.json")
    primary = _read_optional_json(artifact_dir / "localized_change_zones_v2.json")
    status = _read_optional_json(artifact_dir / "region_aware_status.json")
    viewer = _read_optional_json(artifact_dir / "region_viewer" / "region_viewer_manifest.json")
    global_zones = _read_optional_json(artifact_dir / "change_zones.json")

    integrity_metrics = _artifact_integrity_metrics(run_output, artifact_dir)
    detection_metrics = _detection_metrics(detection)
    match_metrics = _match_metrics(matching)
    localized_metrics = _localized_metrics(localized, primary, status)
    viewer_metrics = _viewer_metrics(viewer)
    review_metrics = _review_metrics(case.review_evidence)
    screenshot_metrics = _screenshot_metrics(case.screenshots)

    expected_regions = case.expected_region_count
    expected_matches = case.expected_match_count
    return {
        "case_id": case.case_id,
        "output_dir": str(run_output),
        "artifact_dir": str(artifact_dir),
        "expected_region_count": expected_regions,
        "expected_match_count": expected_matches,
        "detected_region_rate": (
            detection_metrics["region_count"] / expected_regions
            if expected_regions
            else None
        ),
        "auto_match_rate": (
            match_metrics["auto_matched_count"] / expected_matches
            if expected_matches
            else None
        ),
        "approved_match_rate": (
            match_metrics["approved_match_count"] / expected_matches
            if expected_matches
            else None
        ),
        "global_change_zone_count": _zone_count(global_zones),
        "artifact_integrity": integrity_metrics,
        "detection": detection_metrics,
        "matching": match_metrics,
        "localized": localized_metrics,
        "viewer": viewer_metrics,
        "review_evidence": review_metrics,
        "screenshots": screenshot_metrics,
    }


def _artifact_integrity_metrics(run_output: Path, artifact_dir: Path) -> dict[str, Any]:
    required_artifacts = (
        artifact_dir / "region_detection_summary.json",
        artifact_dir / "region_match_summary.json",
        artifact_dir / "localized_compare_summary.json",
        artifact_dir / "region_aware_status.json",
    )
    run_manifest = run_output / "run_manifest.json"
    manifest_status = ""
    if run_manifest.exists():
        manifest_payload = _read_optional_json(run_manifest)
        manifest_status = str(manifest_payload.get("status") or "")
    missing = [str(path) for path in required_artifacts if not path.exists()]
    success_marker = run_output / "_SUCCESS"
    passed = (
        success_marker.exists()
        and run_manifest.exists()
        and manifest_status == "completed"
        and not missing
    )
    return {
        "passed": passed,
        "success_marker": success_marker.exists(),
        "run_manifest": run_manifest.exists(),
        "run_manifest_status": manifest_status,
        "missing_artifacts": missing,
    }


def _detection_metrics(payload: Mapping[str, Any]) -> dict[str, Any]:
    source_count = 0
    region_count = 0
    whole_modelspace_count = 0
    for result in _as_list(payload.get("results")):
        source_count += 1
        for region in _as_list(result.get("regions")):
            region_count += 1
            if str(region.get("detection_method") or "") == "whole_modelspace":
                whole_modelspace_count += 1
    region_count = int(payload.get("region_count") or region_count)
    source_count = int(payload.get("source_count") or source_count)
    whole_modelspace_count = int(payload.get("whole_modelspace_count") or whole_modelspace_count)
    return {
        "source_count": source_count,
        "region_count": region_count,
        "whole_modelspace_count": whole_modelspace_count,
        "whole_modelspace_rate": (
            whole_modelspace_count / source_count if source_count else None
        ),
    }


def _match_metrics(payload: Mapping[str, Any]) -> dict[str, Any]:
    auto_matched = 0
    manual_matched = 0
    review_required = 0
    unmatched_before = 0
    unmatched_after = 0
    for summary in _as_list(payload.get("summaries")):
        auto_matched += _int(summary.get("auto_matched_count"))
        manual_matched += _int(summary.get("manual_matched_count"))
        review_required += _int(summary.get("review_required_count"))
        unmatched_before += _int(summary.get("unmatched_before_count"))
        unmatched_after += _int(summary.get("unmatched_after_count"))
        if not any(key.endswith("_count") for key in summary):
            for match in _as_list(summary.get("matches")):
                status = str(match.get("status") or "")
                if status == "auto_matched":
                    auto_matched += 1
                elif status == "manual_matched":
                    manual_matched += 1
                elif status == "review_required":
                    review_required += 1
                elif status == "unmatched_before":
                    unmatched_before += 1
                elif status == "unmatched_after":
                    unmatched_after += 1
    return {
        "pair_count": int(payload.get("pair_count") or len(_as_list(payload.get("summaries")))),
        "auto_matched_count": auto_matched,
        "manual_matched_count": manual_matched,
        "approved_match_count": auto_matched + manual_matched,
        "review_required_count": review_required,
        "unmatched_before_count": unmatched_before,
        "unmatched_after_count": unmatched_after,
    }


def _localized_metrics(
    localized: Mapping[str, Any],
    primary: Mapping[str, Any],
    status: Mapping[str, Any],
) -> dict[str, Any]:
    sidecar_total = 0
    for summary in _as_list(localized.get("summaries")):
        sidecar_total += _int(summary.get("total_zones"))
    primary_count = _zone_count(primary)
    if primary_count == 0:
        primary_count = _int(status.get("region_local_primary_zone_count"))
    return {
        "sidecar_total_zones": sidecar_total,
        "primary_zone_count": primary_count,
        "primary_enabled": bool(status.get("region_local_primary_enabled") or primary.get("primary_enabled")),
        "primary_status": str(
            status.get("region_local_primary_status")
            or primary.get("status")
            or "unknown"
        ),
        "primary_passed": bool(
            (status.get("region_local_primary_enabled") or primary.get("primary_enabled"))
            and str(
                status.get("region_local_primary_status")
                or primary.get("status")
                or "unknown"
            )
            == "passed"
            and primary_count > 0
        ),
    }


def _viewer_metrics(payload: Mapping[str, Any]) -> dict[str, Any]:
    entries = _as_list(payload.get("entries"))
    rendered_sides = 0
    for entry in entries:
        for side in ("before", "after"):
            side_payload = entry.get(side)
            if isinstance(side_payload, Mapping) and side_payload.get("render_status") == "rendered":
                rendered_sides += 1
    return {
        "entry_count": int(payload.get("entry_count") or len(entries)),
        "rendered_side_count": rendered_sides,
    }


def _review_metrics(payload: Mapping[str, Any]) -> dict[str, Any]:
    reviewed = _optional_int(payload.get("reviewed_region_matches"))
    correct = _optional_int(payload.get("correct_region_matches"))
    global_fp = _optional_int(payload.get("global_false_positive_count"))
    local_fp = _optional_int(payload.get("region_local_false_positive_count"))
    return {
        "reviewed_region_matches": reviewed,
        "correct_region_matches": correct,
        "match_accuracy": correct / reviewed if reviewed else None,
        "global_false_positive_count": global_fp,
        "region_local_false_positive_count": local_fp,
        "false_positive_reduction": (
            (global_fp - local_fp) / global_fp
            if global_fp and local_fp is not None
            else None
        ),
    }


def _screenshot_metrics(paths: tuple[Path, ...]) -> dict[str, Any]:
    return {
        "provided_count": len(paths),
        "existing_count": sum(1 for path in paths if path.exists()),
        "paths": [str(path) for path in paths],
    }


def _aggregate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    source_count = sum(case["detection"]["source_count"] for case in cases)
    whole = sum(case["detection"]["whole_modelspace_count"] for case in cases)
    detected = sum(case["detection"]["region_count"] for case in cases)
    expected_regions = sum(case["expected_region_count"] or 0 for case in cases)
    expected_matches = sum(case["expected_match_count"] or 0 for case in cases)
    auto = sum(case["matching"]["auto_matched_count"] for case in cases)
    approved = sum(case["matching"]["approved_match_count"] for case in cases)
    reviewed = sum(
        case["review_evidence"]["reviewed_region_matches"] or 0 for case in cases
    )
    correct = sum(
        case["review_evidence"]["correct_region_matches"] or 0 for case in cases
    )
    global_fp = sum(
        case["review_evidence"]["global_false_positive_count"] or 0 for case in cases
    )
    local_fp = sum(
        case["review_evidence"]["region_local_false_positive_count"] or 0 for case in cases
    )
    screenshot_count = sum(case["screenshots"]["existing_count"] for case in cases)
    return {
        "source_count": source_count,
        "case_count": len(cases),
        "artifact_integrity_passed_count": sum(
            1 for case in cases if case["artifact_integrity"]["passed"]
        ),
        "artifact_integrity_rate": (
            sum(1 for case in cases if case["artifact_integrity"]["passed"]) / len(cases)
            if cases
            else None
        ),
        "detected_region_count": detected,
        "expected_region_count": expected_regions or None,
        "detected_region_rate": detected / expected_regions if expected_regions else None,
        "whole_modelspace_fallback_count": whole,
        "whole_modelspace_fallback_rate": whole / source_count if source_count else None,
        "auto_matched_count": auto,
        "approved_match_count": approved,
        "manual_matched_count": sum(case["matching"]["manual_matched_count"] for case in cases),
        "review_required_count": sum(case["matching"]["review_required_count"] for case in cases),
        "unmatched_before_count": sum(case["matching"]["unmatched_before_count"] for case in cases),
        "unmatched_after_count": sum(case["matching"]["unmatched_after_count"] for case in cases),
        "unresolved_region_match_count": sum(
            case["matching"]["review_required_count"]
            + case["matching"]["unmatched_before_count"]
            + case["matching"]["unmatched_after_count"]
            for case in cases
        ),
        "expected_match_count": expected_matches or None,
        "auto_match_rate": auto / expected_matches if expected_matches else None,
        "approved_match_rate": approved / expected_matches if expected_matches else None,
        "localized_change_count": sum(case["localized"]["primary_zone_count"] for case in cases),
        "region_local_primary_passed_count": sum(
            1 for case in cases if case["localized"]["primary_passed"]
        ),
        "region_local_primary_pass_rate": (
            sum(1 for case in cases if case["localized"]["primary_passed"]) / len(cases)
            if cases
            else None
        ),
        "global_change_zone_count": sum(case["global_change_zone_count"] for case in cases),
        "reviewed_region_matches": reviewed or None,
        "correct_region_matches": correct or None,
        "user_approved_match_accuracy": correct / reviewed if reviewed else None,
        "global_false_positive_count": global_fp or None,
        "region_local_false_positive_count": local_fp if global_fp else None,
        "false_positive_reduction": (
            (global_fp - local_fp) / global_fp if global_fp else None
        ),
        "viewer_region_entry_count": sum(case["viewer"]["entry_count"] for case in cases),
        "viewer_rendered_side_count": sum(case["viewer"]["rendered_side_count"] for case in cases),
        "screenshot_existing_count": screenshot_count,
    }


def _build_acceptance(totals: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "artifact_integrity": _criterion(
            totals.get("artifact_integrity_rate"),
            minimum=1.0,
            description="Every pilot case must have _SUCCESS, completed run_manifest.json, and required region artifacts.",
        ),
        "detected_region_rate": _criterion(
            totals.get("detected_region_rate"),
            minimum=0.80,
            description="Detect at least 80 percent of expected regions.",
        ),
        "whole_modelspace_fallback_rate": _criterion(
            totals.get("whole_modelspace_fallback_rate"),
            maximum=0.10,
            strict_maximum=True,
            description="Whole-modelspace fallback below 10 percent.",
        ),
        "approved_region_match_rate": _criterion(
            totals.get("approved_match_rate"),
            minimum=1.0,
            description="Every expected region match must be auto- or manually approved.",
        ),
        "unresolved_region_matches": _criterion(
            totals.get("unresolved_region_match_count"),
            maximum=0,
            description="No review_required or unmatched regions remain after pilot review.",
        ),
        "region_local_primary_compare": _criterion(
            totals.get("region_local_primary_pass_rate"),
            minimum=1.0,
            description="Every pilot case must produce passed region-local primary compare output.",
        ),
        "user_approved_match_accuracy": _criterion(
            totals.get("user_approved_match_accuracy"),
            minimum=0.95,
            description="User-approved match accuracy at least 95 percent.",
        ),
        "false_positive_reduction": _criterion(
            totals.get("false_positive_reduction"),
            minimum=0.50,
            description="Region-local compare reduces false positives by at least 50 percent.",
        ),
        "viewer_screenshot_count": _criterion(
            totals.get("screenshot_existing_count"),
            minimum=3,
            description="Capture before/after viewer screenshots for at least three cases.",
        ),
    }


def _criterion(
    value: Any,
    *,
    description: str,
    minimum: float | None = None,
    maximum: float | None = None,
    strict_maximum: bool = False,
) -> dict[str, Any]:
    if value is None:
        return {"status": "not_evaluable", "value": None, "description": description}
    numeric = float(value)
    passed = True
    if minimum is not None:
        passed = passed and numeric >= minimum
    if maximum is not None:
        passed = passed and (numeric < maximum if strict_maximum else numeric <= maximum)
    return {
        "status": "passed" if passed else "failed",
        "value": value,
        "description": description,
    }


def _overall_status(acceptance: Mapping[str, Mapping[str, Any]]) -> str:
    statuses = {str(item.get("status")) for item in acceptance.values()}
    if "failed" in statuses:
        return "failed"
    if "not_evaluable" in statuses:
        return "needs_review_evidence"
    return "passed"


def _render_report(payload: Mapping[str, Any]) -> str:
    totals = payload["totals"]
    acceptance = payload["acceptance"]
    lines = [
        "# Multi-Detail Region Compare Pilot Report",
        "",
        f"- Status: `{payload['overall_status']}`",
        f"- Cases: {payload['case_count']}",
        f"- Generated: {payload['finished_at']}",
        f"- Manifest: `{payload['manifest_path']}`",
        "",
        "## Metrics",
        "",
        f"- Detected regions: {totals['detected_region_count']} / {totals['expected_region_count'] or 'n/a'}",
        f"- Whole-modelspace fallback: {totals['whole_modelspace_fallback_count']} / {totals['source_count']}",
        f"- Auto matched regions: {totals['auto_matched_count']}",
        f"- Review required regions: {totals['review_required_count']}",
        f"- Unmatched regions: {totals['unmatched_before_count'] + totals['unmatched_after_count']}",
        f"- Localized change count: {totals['localized_change_count']}",
        f"- Global change zone count: {totals['global_change_zone_count']}",
        f"- Viewer region entries: {totals['viewer_region_entry_count']}",
        f"- Existing screenshot evidence: {totals['screenshot_existing_count']}",
        "",
        "## Acceptance",
        "",
        "| Criterion | Status | Value |",
        "| --- | --- | --- |",
    ]
    for name, item in acceptance.items():
        lines.append(f"| {name} | `{item['status']}` | {_format_value(item.get('value'))} |")
    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| Case | Regions | Whole fallback | Auto | Review | Unmatched | Local zones |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for case in payload["cases"]:
        unmatched = case["matching"]["unmatched_before_count"] + case["matching"]["unmatched_after_count"]
        lines.append(
            "| {case_id} | {regions} | {whole} | {auto} | {review} | {unmatched} | {local} |".format(
                case_id=case["case_id"],
                regions=case["detection"]["region_count"],
                whole=case["detection"]["whole_modelspace_count"],
                auto=case["matching"]["auto_matched_count"],
                review=case["matching"]["review_required_count"],
                unmatched=unmatched,
                local=case["localized"]["primary_zone_count"],
            )
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- `needs_review_evidence` means technical artifacts were collected, but human match-accuracy or false-positive labels were missing.",
            "- Do not enable R10 defaults until all acceptance rows are `passed` on a real 10-20 pair pilot set.",
            "",
        ]
    )
    return "\n".join(lines)


def _console_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "overall_status": payload.get("overall_status"),
        "case_count": payload.get("case_count"),
        "summary_json": payload.get("summary_json"),
        "report_md": payload.get("report_md"),
        "acceptance": payload.get("acceptance"),
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return _read_json(path)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_path(value: Any, base_dir: Path) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else (base_dir / path).resolve()


def _optional_path(value: Any, base_dir: Path) -> Path | None:
    if value in (None, ""):
        return None
    return _resolve_path(value, base_dir)


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int:
    return _optional_int(value) or 0


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _zone_count(payload: Mapping[str, Any]) -> int:
    for key in ("zone_count", "total_zones", "change_zone_count"):
        if key in payload:
            return _int(payload.get(key))
    for key in ("zones", "change_zones", "items"):
        items = payload.get(key)
        if isinstance(items, list):
            return len(items)
    return 0


def _safe_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)
    return safe.strip("._") or "case"


def _format_value(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
