# -*- coding: utf-8 -*-
"""Inventory P0 performance-degradation baseline evidence.

This is pre-optimization tooling for the performance-degradation roadmap. It
does not certify a release. It scans existing run output folders, summarizes
which evidence exists, and reports whether the P0 baseline set is ready for
review before further performance changes are made.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


SCHEMA_VERSION = "performance-baseline-inventory/v1"

IGNORED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "_internal",
    "node_modules",
    "site-packages",
}

RUN_MARKER_FILES = {
    "_SUCCESS",
    "_FAILED",
    "run_manifest.json",
    "validation_summary.json",
    "perf_events_summary.json",
    "workbench_acceptance_summary.json",
    "nonblank_pixel_probe.json",
}

VIEWER_MARKER_FILES = {
    "viewer_perf.json",
    "viewer_perf.jsonl",
    "selected_zone_evidence.json",
}

BASELINE_KINDS = {
    "pdf": "PDF baseline",
    "cad": "CAD baseline",
    "large_cad": "Large CAD baseline",
    "multi_detail": "Multi-detail baseline",
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        action="append",
        default=[],
        help="Folder to scan. Repeatable. Defaults to tmp/build/logs/.benchmarks when present.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path(".benchmarks") / "performance_baseline_inventory.json",
        help="JSON inventory output path.",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=Path(".benchmarks") / "performance_baseline_inventory.md",
        help="Markdown inventory report output path.",
    )
    parser.add_argument(
        "--include-ignored-dirs",
        action="store_true",
        help="Do not skip generated/cache directories while scanning.",
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=200,
        help="Maximum candidate run folders to summarize after sorting by mtime.",
    )
    parser.add_argument(
        "--fail-on-incomplete",
        action="store_true",
        help="Exit with code 1 unless the P0 baseline set is ready for review.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    roots = [path.resolve() for path in args.root] or default_scan_roots(Path.cwd())
    inventory = build_inventory(
        roots,
        include_ignored_dirs=args.include_ignored_dirs,
        max_runs=max(1, int(args.max_runs)),
    )
    write_inventory_outputs(inventory, args.output_json, args.output_md)
    print(json.dumps(inventory, ensure_ascii=False, indent=2))
    if args.fail_on_incomplete and inventory["overall_status"] != "ready_for_p0_review":
        return 1
    return 0


def default_scan_roots(cwd: Path) -> list[Path]:
    candidates = [
        cwd / "tmp",
        cwd / "build",
        cwd / "release",
        cwd / "logs",
        cwd / ".benchmarks",
        cwd / "out",
    ]
    roots = [path.resolve() for path in candidates if path.exists()]
    return roots or [cwd.resolve()]


def build_inventory(
    roots: Sequence[Path],
    *,
    include_ignored_dirs: bool = False,
    max_runs: int = 200,
) -> dict[str, Any]:
    resolved_roots = [Path(root).resolve() for root in roots]
    run_dirs = discover_run_dirs(
        resolved_roots,
        include_ignored_dirs=include_ignored_dirs,
    )
    summaries = [
        summarize_run_dir(run_dir, resolved_roots)
        for run_dir in sorted(run_dirs, key=_latest_mtime, reverse=True)[:max_runs]
    ]
    coverage = _baseline_coverage(summaries)
    acceptance = _acceptance_checks(coverage)
    overall_status = (
        "ready_for_p0_review"
        if all(check["status"] == "passed" for check in acceptance)
        else "needs_more_evidence"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_status": overall_status,
        "scan_roots": [str(root) for root in resolved_roots],
        "candidate_run_count": len(summaries),
        "coverage": coverage,
        "acceptance": acceptance,
        "runs": summaries,
        "next_actions": _next_actions(coverage, acceptance),
    }


def discover_run_dirs(
    roots: Sequence[Path],
    *,
    include_ignored_dirs: bool = False,
) -> list[Path]:
    candidates: set[Path] = set()
    for root in roots:
        root = Path(root)
        if root.is_file():
            candidates.add(root.parent.resolve())
            continue
        if not root.exists():
            continue
        for current, dirs, files in os.walk(root):
            current_path = Path(current)
            if not include_ignored_dirs:
                dirs[:] = [name for name in dirs if name not in IGNORED_DIRS]
            file_set = set(files)
            if file_set & RUN_MARKER_FILES:
                candidates.add(current_path.resolve())
            viewer_dir = current_path / "viewer"
            if viewer_dir.is_dir():
                try:
                    viewer_files = {child.name for child in viewer_dir.iterdir() if child.is_file()}
                except OSError:
                    viewer_files = set()
                if viewer_files & VIEWER_MARKER_FILES:
                    candidates.add(current_path.resolve())
    return sorted(candidates)


def summarize_run_dir(run_dir: Path, roots: Sequence[Path]) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    run_manifest = _load_json(run_dir / "run_manifest.json")
    validation = _load_json(run_dir / "validation_summary.json")
    viewer_perf_json = _resolve_evidence_path(run_dir, validation, "viewer_perf_json") or (
        run_dir / "viewer" / "viewer_perf.json"
    )
    viewer_perf_jsonl = run_dir / "viewer" / "viewer_perf.jsonl"
    selected_zone = _resolve_evidence_path(run_dir, validation, "selected_zone_evidence_json") or (
        run_dir / "viewer" / "selected_zone_evidence.json"
    )
    perf_summary_path = _resolve_evidence_path(run_dir, validation, "perf_events_summary_json") or (
        run_dir / "perf_events_summary.json"
    )
    nonblank_path = run_dir / "nonblank_pixel_probe.json"
    nonblank_file = _load_json(nonblank_path)
    workbench_acceptance_path = run_dir / "workbench_acceptance_summary.json"
    screenshots = _find_screenshots(run_dir)
    nonblank_probe = _probe_nonblank_images(run_dir)

    evidence = {
        "success_sentinel": (run_dir / "_SUCCESS").exists(),
        "failed_sentinel": (run_dir / "_FAILED").exists(),
        "run_manifest": (run_dir / "run_manifest.json").exists(),
        "validation_summary": (run_dir / "validation_summary.json").exists(),
        "perf_events_summary": perf_summary_path.exists(),
        "runtime_budget": bool(_nested(validation, "runtime_budget") or _nested(run_manifest, "runtime_budget")),
        "legacy_performance_metrics": _has_legacy_performance_metrics(validation),
        "viewer_perf_json": viewer_perf_json.exists(),
        "viewer_perf_jsonl": viewer_perf_jsonl.exists(),
        "selected_zone_evidence": selected_zone.exists(),
        "workbench_acceptance_summary": workbench_acceptance_path.exists(),
        "nonblank_pixel_probe": _nonblank_file_passed(nonblank_file),
        "nonblank_image_probe": bool(nonblank_probe.get("passed")),
        "screenshots": bool(screenshots),
    }
    tags = _classify_run(run_dir, validation, run_manifest)
    multi_detail = _multi_detail_diagnostics(run_dir) if "multi_detail" in tags else {}
    missing = _missing_required_evidence(evidence)
    missing.extend(_missing_multi_detail_evidence(tags, multi_detail))
    instrumentation_gaps = _instrumentation_gaps(evidence)
    metrics = _extract_metrics(
        validation=validation,
        run_manifest=run_manifest,
        viewer_perf=_load_json(viewer_perf_json),
        selected_zone=_load_json(selected_zone),
        perf_summary=_load_json(perf_summary_path),
    )
    return {
        "path": _path_reference(run_dir, roots),
        "mtime": datetime.fromtimestamp(_latest_mtime(run_dir), tz=timezone.utc).isoformat(),
        "run_id": str(
            _nested(run_manifest, "run_id")
            or _nested(validation, "run_manifest", "run_id")
            or _nested(validation, "run_id")
            or ""
        ),
        "status": str(_nested(run_manifest, "status") or _nested(validation, "status") or ""),
        "tags": tags,
        "evidence": evidence,
        "missing_required_evidence": missing,
        "p1_instrumentation_gaps": instrumentation_gaps,
        "p0_evidence_status": "complete" if not missing else "partial",
        "metrics": metrics,
        "multi_detail": multi_detail,
        "nonblank_pixel_probe": nonblank_file or {},
        "nonblank_image_probe": nonblank_probe,
        "screenshots": [_path_reference(path, roots) for path in screenshots[:10]],
    }


def write_inventory_outputs(inventory: dict[str, Any], json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(inventory), encoding="utf-8")


def render_markdown(inventory: dict[str, Any]) -> str:
    lines = [
        "# Performance Baseline Inventory",
        "",
        f"Generated: {inventory.get('generated_at', '')}",
        f"Status: {inventory.get('overall_status', '')}",
        f"Candidate runs: {inventory.get('candidate_run_count', 0)}",
        "",
        "## Baseline Coverage",
        "",
        "| Baseline | Status | Candidate | Missing evidence |",
        "| --- | --- | --- | --- |",
    ]
    coverage = inventory.get("coverage") or {}
    for key, label in BASELINE_KINDS.items():
        item = coverage.get(key) or {}
        candidate = item.get("candidate") or {}
        candidate_path = _display_path(candidate.get("path"))
        missing = ", ".join(item.get("missing_required_evidence") or [])
        lines.append(f"| {label} | {item.get('status', 'missing')} | {candidate_path or '-'} | {missing or '-'} |")
    lines.extend(["", "## Candidate Metrics", ""])
    lines.append("| Baseline | Candidate | total_s | peak_mb | zone_count | selected_cold_p95_ms | warnings |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: |")
    for key, label in BASELINE_KINDS.items():
        for run in _top_candidates(coverage.get(key), limit=3):
            metrics = run.get("metrics") or {}
            diagnostics = run.get("multi_detail") or {}
            warning_count = len(diagnostics.get("warnings") or [])
            lines.append(
                "| "
                + " | ".join(
                    [
                        label,
                        _display_path(run.get("path")) or "-",
                        str(metrics.get("total_s", 0)),
                        str(metrics.get("peak_mb", 0)),
                        str(metrics.get("zone_count", 0)),
                        str(metrics.get("selected_zone_cold_p95_ms", 0)),
                        str(warning_count),
                    ]
                )
                + " |"
            )
    lines.extend(["", "## Acceptance", ""])
    for check in inventory.get("acceptance") or []:
        lines.append(f"- {check.get('status')}: {check.get('name')} - {check.get('detail')}")
    multi_candidate = ((coverage.get("multi_detail") or {}).get("candidate") or {})
    multi_detail = multi_candidate.get("multi_detail") or {}
    if multi_detail:
        lines.extend(["", "## Multi-Detail Diagnostics", ""])
        lines.append(f"- detected_region_count: {multi_detail.get('detected_region_count', 0)}")
        lines.append(f"- detection_failed_count: {multi_detail.get('detection_failed_count', 0)}")
        lines.append(f"- approved_match_count: {multi_detail.get('approved_match_count', 0)}")
        lines.append(f"- localized_assigned_zone_count: {multi_detail.get('localized_assigned_zone_count', 0)}")
        lines.append(f"- render_failed_count: {multi_detail.get('render_failed_count', 0)}")
        warnings = multi_detail.get("warnings") or []
        if warnings:
            lines.append(f"- first_warning: {warnings[0]}")
    lines.extend(["", "## Next Actions", ""])
    for action in inventory.get("next_actions") or []:
        lines.append(f"- {action}")
    lines.append("")
    return "\n".join(lines)


def _top_candidates(item: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(item, dict):
        return []
    candidates = list(item.get("candidates") or [])
    if not candidates:
        candidate = item.get("candidate")
        candidates = [candidate] if isinstance(candidate, dict) else []
    return candidates[:limit]


def _baseline_coverage(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    coverage: dict[str, Any] = {}
    for key in BASELINE_KINDS:
        candidates = [run for run in runs if key in set(run.get("tags") or [])]
        if key == "large_cad":
            best = sorted(candidates, key=_candidate_sort_key, reverse=True)[0] if candidates else None
        else:
            best = sorted(candidates, key=_evidence_score, reverse=True)[0] if candidates else None
        if best is None:
            coverage[key] = {
                "status": "missing",
                "candidate": None,
                "missing_required_evidence": ["candidate run"],
            }
            continue
        missing = list(best.get("missing_required_evidence") or [])
        coverage[key] = {
            "status": "passed" if not missing else "partial",
            "candidate": best,
            "candidates": sorted(candidates, key=_candidate_sort_key, reverse=True)[:5],
            "candidate_count": len(candidates),
            "missing_required_evidence": missing,
        }
    return coverage


def _acceptance_checks(coverage: dict[str, Any]) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    for key, label in BASELINE_KINDS.items():
        item = coverage.get(key) or {}
        status = "passed" if item.get("status") == "passed" else "failed"
        detail = "complete baseline evidence present"
        if item.get("status") == "missing":
            detail = "no candidate run found"
        elif item.get("status") == "partial":
            detail = "missing: " + ", ".join(item.get("missing_required_evidence") or [])
        checks.append({"name": label, "status": status, "detail": detail})
    return checks


def _next_actions(coverage: dict[str, Any], acceptance: Sequence[dict[str, str]]) -> list[str]:
    if all(check["status"] == "passed" for check in acceptance):
        return [
            "Freeze this inventory JSON/Markdown as the P0 baseline.",
            "Proceed to P1 mandatory instrumentation gate.",
        ]
    actions: list[str] = []
    for key, label in BASELINE_KINDS.items():
        item = coverage.get(key) or {}
        if item.get("status") == "missing":
            actions.append(f"Add at least one {label} run with validation outputs.")
        elif item.get("status") == "partial":
            missing = ", ".join(item.get("missing_required_evidence") or [])
            actions.append(f"Complete {label} evidence: {missing}.")
    actions.append("Re-run this script after adding missing evidence.")
    return actions


def _classify_run(run_dir: Path, validation: dict[str, Any] | None, run_manifest: dict[str, Any] | None) -> list[str]:
    tags: set[str] = set()
    extensions = _source_extensions(validation, run_manifest)
    kind_counts = _kind_counts(validation)
    if "pdf" in kind_counts or ".pdf" in extensions:
        tags.add("pdf")
    if "cad" in kind_counts or extensions & {".dwg", ".dxf"}:
        tags.add("cad")
    if "cad" in tags and _is_large_run(validation, run_manifest):
        tags.add("large_cad")
    if _is_multi_detail_run(run_dir, validation, run_manifest):
        tags.add("multi_detail")
    return sorted(tags)


def _source_extensions(validation: dict[str, Any] | None, run_manifest: dict[str, Any] | None) -> set[str]:
    values: list[str] = []
    for payload in (validation, run_manifest):
        if not isinstance(payload, dict):
            continue
        values.extend(
            str(item or "")
            for item in (
                _nested(payload, "input", "a"),
                _nested(payload, "input", "b"),
                _nested(payload, "inputs", "source_a"),
                _nested(payload, "inputs", "source_b"),
            )
        )
        artifacts = _nested(payload, "change_artifacts", "artifacts")
        if isinstance(artifacts, list):
            for artifact in artifacts:
                if isinstance(artifact, dict):
                    values.extend([str(artifact.get("source_a") or ""), str(artifact.get("source_b") or "")])
    return {Path(value).suffix.lower() for value in values if Path(value).suffix}


def _kind_counts(validation: dict[str, Any] | None) -> set[str]:
    out: set[str] = set()
    files = _nested(validation, "files")
    if not isinstance(files, dict):
        return out
    for key in ("a_kind_counts", "b_kind_counts"):
        counts = files.get(key)
        if isinstance(counts, dict):
            out.update(str(name) for name, count in counts.items() if count)
    return out


def _is_large_run(validation: dict[str, Any] | None, run_manifest: dict[str, Any] | None) -> bool:
    input_bytes = (
        _int_value(_nested(validation, "files", "a_size_bytes"))
        + _int_value(_nested(validation, "files", "b_size_bytes"))
    )
    if input_bytes >= 10 * 1024 * 1024:
        return True
    if _float_value(_nested(validation, "timings", "total_s")) >= 60.0:
        return True
    if _int_value(_nested(validation, "stability", "large_mode_pairs")) > 0:
        return True
    if _float_value(_nested(validation, "memory", "peak_mb")) >= 1024.0:
        return True
    if _float_value(_nested(validation, "runtime_budget", "peak_rss_mb")) >= 1024.0:
        return True
    selected = _load_selected_zone_payload_from_validation(validation)
    selected_renders = selected.get("renders") if isinstance(selected, dict) else None
    if isinstance(selected_renders, list):
        cold_samples = [
            _float_value(item.get("render_ms"))
            for item in selected_renders
            if isinstance(item, dict)
            and item.get("cache_hit") is not True
            and item.get("phase") in {"cold", None, ""}
        ]
        if _percentile(cold_samples, 95.0) >= 10_000.0:
            return True
    if _int_value(_nested(run_manifest, "counts", "raw_change_count")) >= 100_000:
        return True
    return False


def _is_multi_detail_run(run_dir: Path, validation: dict[str, Any] | None, run_manifest: dict[str, Any] | None) -> bool:
    artifact_dir = run_dir / "artifacts"
    if any(
        (artifact_dir / name).exists()
        for name in (
            "region_detection_summary.json",
            "region_match_summary.json",
            "localized_compare_summary.json",
            "localized_change_zones_v2.json",
            "region_aware_status.json",
        )
    ):
        return True
    text = json.dumps(validation or {}, ensure_ascii=False) + json.dumps(run_manifest or {}, ensure_ascii=False)
    return any(token in text for token in ("region_local", "multi_detail", "region_match_summary"))


def _multi_detail_diagnostics(run_dir: Path) -> dict[str, Any]:
    artifact_dir = run_dir / "artifacts"
    detection = _load_json(artifact_dir / "region_detection_summary.json")
    matching = _load_json(artifact_dir / "region_match_summary.json")
    localized = _load_json(artifact_dir / "localized_compare_summary.json")
    primary = _load_json(artifact_dir / "localized_change_zones_v2.json")
    status = _load_json(artifact_dir / "region_aware_status.json")
    viewer = _load_json(run_dir / "viewer" / "viewer_manifest.json") or _load_json(
        artifact_dir / "region_viewer" / "region_viewer_manifest.json"
    )

    detection_results = _as_list(_nested(detection, "results"))
    detected_region_count = _int_value(_nested(detection, "region_count"))
    if detected_region_count <= 0:
        detected_region_count = sum(
            _int_value(item.get("region_count")) or len(_as_list(item.get("regions")))
            for item in detection_results
            if isinstance(item, dict)
        )
    detection_failed_count = sum(
        1 for item in detection_results if isinstance(item, dict) and str(item.get("status") or "") == "failed"
    )

    match_summaries = _as_list(_nested(matching, "summaries"))
    auto_match_count = sum(_int_value(item.get("auto_matched_count")) for item in match_summaries if isinstance(item, dict))
    manual_match_count = sum(
        _int_value(item.get("manual_matched_count")) for item in match_summaries if isinstance(item, dict)
    )
    approved_match_count = auto_match_count + manual_match_count
    review_required_match_count = sum(
        _int_value(item.get("review_required_count")) for item in match_summaries if isinstance(item, dict)
    )

    localized_summaries = _as_list(_nested(localized, "summaries"))
    localized_total_zones = sum(
        _int_value(item.get("total_zones")) or len(_as_list(item.get("localized_zones")))
        for item in localized_summaries
        if isinstance(item, dict)
    )
    localized_assigned_zones = sum(
        _int_value(item.get("assigned_zones")) for item in localized_summaries if isinstance(item, dict)
    )
    localized_assigned_zones = max(
        localized_assigned_zones,
        _int_value(_nested(status, "localized_assigned_zones")),
        _int_value(_nested(status, "region_local_primary_zone_count")),
        len(_as_list(_nested(primary, "zones"))) if isinstance(primary, dict) else 0,
    )

    viewer_pairs = _as_list(_nested(viewer, "pairs"))
    viewer_entries = _as_list(_nested(viewer, "entries"))
    render_failed_count = sum(
        1
        for item in viewer_pairs
        if isinstance(item, dict) and str(item.get("render_status") or "") == "render_failed"
    )
    for entry in viewer_entries:
        if not isinstance(entry, dict):
            continue
        before = entry.get("before") if isinstance(entry.get("before"), dict) else {}
        after = entry.get("after") if isinstance(entry.get("after"), dict) else {}
        if str(before.get("render_status") or "") == "render_failed" or str(after.get("render_status") or "") == "render_failed":
            render_failed_count += 1
    rendered_pair_count = _int_value(_nested(viewer, "rendered_pair_count")) + sum(
        1
        for item in viewer_pairs
        if isinstance(item, dict) and str(item.get("render_status") or "") in {"rendered", "ready"}
    )

    warnings: list[str] = []
    for payload in (detection, matching, localized, viewer):
        warnings.extend(_collect_warning_strings(payload))

    return {
        "has_region_detection_summary": isinstance(detection, dict),
        "has_region_match_summary": isinstance(matching, dict),
        "has_localized_compare_summary": isinstance(localized, dict),
        "has_region_local_primary": isinstance(primary, dict),
        "has_region_aware_status": isinstance(status, dict),
        "has_viewer_manifest": isinstance(viewer, dict),
        "detected_region_count": detected_region_count,
        "detection_failed_count": detection_failed_count,
        "approved_match_count": approved_match_count,
        "review_required_match_count": review_required_match_count,
        "localized_total_zone_count": localized_total_zones,
        "localized_assigned_zone_count": localized_assigned_zones,
        "rendered_pair_count": rendered_pair_count,
        "render_failed_count": render_failed_count,
        "warnings": warnings[:10],
    }


def _missing_multi_detail_evidence(tags: Sequence[str], diagnostics: dict[str, Any]) -> list[str]:
    if "multi_detail" not in set(tags):
        return []
    missing: list[str] = []
    if not diagnostics.get("has_region_detection_summary"):
        missing.append("region_detection_summary.json")
    if _int_value(diagnostics.get("detected_region_count")) <= 0:
        missing.append("detected detail regions")
    if _int_value(diagnostics.get("detection_failed_count")) > 0:
        missing.append("successful region detection")
    if not diagnostics.get("has_region_match_summary"):
        missing.append("region_match_summary.json")
    elif _int_value(diagnostics.get("detected_region_count")) > 0 and _int_value(diagnostics.get("approved_match_count")) <= 0:
        missing.append("approved region matches")
    if not diagnostics.get("has_localized_compare_summary") and not diagnostics.get("has_region_local_primary"):
        missing.append("localized region compare summary")
    elif _int_value(diagnostics.get("detected_region_count")) > 0 and _int_value(
        diagnostics.get("localized_assigned_zone_count")
    ) <= 0:
        missing.append("localized region assignments")
    if _int_value(diagnostics.get("render_failed_count")) > 0:
        missing.append("region viewer rendered background")
    return missing


def _missing_required_evidence(evidence: dict[str, bool]) -> list[str]:
    missing: list[str] = []
    if not evidence.get("run_manifest"):
        missing.append("run_manifest.json")
    if not evidence.get("validation_summary"):
        missing.append("validation_summary.json")
    if not (
        evidence.get("perf_events_summary")
        or evidence.get("runtime_budget")
        or evidence.get("legacy_performance_metrics")
    ):
        missing.append("baseline performance metrics")
    if not (evidence.get("viewer_perf_json") or evidence.get("viewer_perf_jsonl")):
        missing.append("viewer_perf.json/jsonl")
    if not evidence.get("selected_zone_evidence"):
        missing.append("selected_zone_evidence.json")
    if not (
        evidence.get("screenshots")
        or evidence.get("nonblank_pixel_probe")
        or evidence.get("nonblank_image_probe")
    ):
        missing.append("screenshots or nonblank pixel evidence")
    return missing


def _instrumentation_gaps(evidence: dict[str, bool]) -> list[str]:
    gaps: list[str] = []
    if not evidence.get("perf_events_summary"):
        gaps.append("perf_events_summary.json")
    if not evidence.get("runtime_budget"):
        gaps.append("runtime_budget")
    if not evidence.get("viewer_perf_jsonl"):
        gaps.append("viewer_perf.jsonl")
    if not evidence.get("nonblank_pixel_probe") and evidence.get("nonblank_image_probe"):
        gaps.append("persisted nonblank_pixel_probe.json")
    return gaps


def _nonblank_file_passed(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("passed") is True:
        return True
    return str(payload.get("status") or "").lower() in {"passed", "nonblank", "ready"}


def _has_legacy_performance_metrics(validation: dict[str, Any] | None) -> bool:
    if not isinstance(validation, dict):
        return False
    has_timing = _float_value(_nested(validation, "timings", "total_s")) > 0
    has_memory = (
        _float_value(_nested(validation, "memory", "peak_mb")) > 0
        or _float_value(_nested(validation, "runtime_budget", "peak_rss_mb")) > 0
        or _float_value(_nested(validation, "runtime_budget", "peak_working_set_mb")) > 0
    )
    return has_timing and has_memory


def _extract_metrics(
    *,
    validation: dict[str, Any] | None,
    run_manifest: dict[str, Any] | None,
    viewer_perf: dict[str, Any] | None,
    selected_zone: dict[str, Any] | None,
    perf_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    first_ready = _nested(validation, "first_interactive_ready") or {}
    selected_stats = _nested(selected_zone, "actual_crop_stats") or _nested(
        validation,
        "selected_zone_evidence",
        "actual_crop_stats",
    )
    selected_renders = _nested(selected_zone, "renders") or _nested(
        validation,
        "selected_zone_evidence",
        "renders",
    )
    if not isinstance(selected_renders, list):
        selected_renders = []
    cold_samples = [
        _float_value(item.get("render_ms"))
        for item in selected_renders
        if isinstance(item, dict)
        and item.get("phase") in {"cold", None, ""}
        and item.get("cache_hit") is not True
    ]
    hit_samples = [
        _float_value(item.get("render_ms"))
        for item in selected_renders
        if isinstance(item, dict)
        and (item.get("cache_hit") is True or item.get("phase") == "cache_hit_probe")
    ]
    peak_mb = max(
        _float_value(_nested(validation, "memory", "peak_mb")),
        _float_value(_nested(validation, "runtime_budget", "peak_rss_mb")),
        _float_value(_nested(validation, "runtime_budget", "peak_working_set_mb")),
        _float_value(_nested(perf_summary, "peak_rss_mb")),
        _float_value(_nested(perf_summary, "peak_working_set_mb")),
    )
    return {
        "total_s": _float_value(_nested(validation, "timings", "total_s")),
        "peak_mb": peak_mb,
        "completed_pairs": _int_value(
            _nested(validation, "comparison", "completed_pairs")
            or _nested(run_manifest, "counts", "completed_pairs")
        ),
        "failed_pairs": _int_value(
            _nested(validation, "comparison", "failed_pairs")
            or _nested(run_manifest, "counts", "failed_pairs")
        ),
        "raw_change_count": _int_value(
            _nested(validation, "comparison", "total_changes")
            or _nested(run_manifest, "counts", "raw_change_count")
        ),
        "zone_count": _int_value(
            _nested(validation, "change_artifacts", "zone_count")
            or _nested(run_manifest, "counts", "zone_count")
        ),
        "first_interactive_status": str(first_ready.get("status") or ""),
        "first_top_issue_ready_s": _float_value(first_ready.get("first_top_issue_ready_s")),
        "viewer_event_count": _int_value(_nested(viewer_perf, "event_count")),
        "selected_zone_status": str(_nested(selected_zone, "status") or ""),
        "selected_zone_event_count": _int_value(_nested(selected_zone, "event_count")),
        "selected_zone_cold_p95_ms": _percentile(cold_samples, 95.0),
        "selected_zone_hit_p95_ms": _percentile(hit_samples, 95.0),
        "actual_crop_rate": _float_value(_nested(selected_stats, "actual_crop_available_rate")),
        "perf_event_count": _int_value(_nested(perf_summary, "event_count")),
    }


def _find_screenshots(run_dir: Path) -> list[Path]:
    screenshot_dirs = [
        run_dir / "screenshots",
        run_dir / "viewer" / "screenshots",
        run_dir / "workbench_screenshots",
    ]
    paths: list[Path] = []
    for directory in screenshot_dirs:
        if not directory.exists():
            continue
        try:
            paths.extend(
                path
                for path in directory.iterdir()
                if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}
            )
        except OSError:
            continue
    return sorted(paths)


def _probe_nonblank_images(run_dir: Path) -> dict[str, Any]:
    image_paths = _candidate_visual_images(run_dir)
    if not image_paths:
        return {"status": "missing", "passed": False, "checked": 0, "images": []}
    try:
        from PIL import Image, ImageStat  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - depends on local optional dependency
        return {
            "status": "unavailable",
            "passed": False,
            "checked": 0,
            "images": [],
            "reason": f"pillow_unavailable:{exc.__class__.__name__}",
        }

    checked: list[dict[str, Any]] = []
    for path in image_paths[:20]:
        try:
            with Image.open(path) as image:
                converted = image.convert("RGB")
                stat = ImageStat.Stat(converted)
                extrema = converted.getextrema()
        except Exception as exc:
            checked.append(
                {
                    "path": str(path.relative_to(run_dir)).replace("\\", "/"),
                    "status": "unreadable",
                    "reason": exc.__class__.__name__,
                }
            )
            continue
        channel_ranges = [int(high) - int(low) for low, high in extrema]
        mean = sum(float(value) for value in stat.mean) / max(1, len(stat.mean))
        nonblank = any(value > 3 for value in channel_ranges) or mean < 250.0
        entry = {
            "path": str(path.relative_to(run_dir)).replace("\\", "/"),
            "status": "nonblank" if nonblank else "blank_like",
            "width": int(converted.width),
            "height": int(converted.height),
            "channel_ranges": channel_ranges,
            "mean": round(mean, 3),
        }
        checked.append(entry)
        if nonblank:
            return {"status": "passed", "passed": True, "checked": len(checked), "images": checked}
    return {"status": "failed", "passed": False, "checked": len(checked), "images": checked}


def _candidate_visual_images(run_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for subdir in (
        "screenshots",
        "viewer/images",
        "viewer/zone_crops",
        "viewer/focus_tiles",
        "viewer/tiles",
    ):
        root = run_dir / subdir
        if not root.exists():
            continue
        try:
            iterator = root.rglob("*") if root.is_dir() else iter([root])
            candidates.extend(
                path
                for path in iterator
                if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}
            )
        except OSError:
            continue
    return sorted(candidates)


def _resolve_evidence_path(run_dir: Path, validation: dict[str, Any] | None, key: str) -> Path | None:
    value = _nested(validation, "outputs", key)
    if not value:
        return None
    path = Path(str(value))
    if not path.is_absolute():
        path = run_dir / path
    return path


def _load_selected_zone_payload_from_validation(validation: dict[str, Any] | None) -> dict[str, Any]:
    payload = _nested(validation, "selected_zone_evidence")
    return payload if isinstance(payload, dict) else {}


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _collect_warning_strings(value: Any) -> list[str]:
    warnings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "warnings":
                warnings.extend(str(item) for item in _as_list(child) if str(item))
            else:
                warnings.extend(_collect_warning_strings(child))
    elif isinstance(value, list):
        for child in value:
            warnings.extend(_collect_warning_strings(child))
    return warnings


def _nested(payload: Any, *keys: str) -> Any:
    current = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _path_reference(path: Path | str | None, roots: Sequence[Path]) -> dict[str, str]:
    if not path:
        return {}
    path_obj = Path(path).resolve()
    for index, root in enumerate(roots):
        try:
            relative = path_obj.relative_to(Path(root).resolve())
        except ValueError:
            continue
        return {"root": f"root_{index}", "relative_path": str(relative).replace("\\", "/")}
    return {"root": "absolute", "relative_path": str(path_obj)}


def _display_path(ref: Any) -> str:
    if not isinstance(ref, dict):
        return ""
    root = str(ref.get("root") or "")
    rel = str(ref.get("relative_path") or "")
    if not rel:
        return ""
    return f"{root}:{rel}" if root else rel


def _latest_mtime(path: Path) -> float:
    try:
        latest = path.stat().st_mtime
    except OSError:
        return 0.0
    for child_name in (
        "validation_summary.json",
        "run_manifest.json",
        "perf_events_summary.json",
        "viewer/viewer_perf.json",
        "viewer/viewer_perf.jsonl",
        "viewer/selected_zone_evidence.json",
    ):
        child = path / child_name
        try:
            latest = max(latest, child.stat().st_mtime)
        except OSError:
            continue
    return latest


def _evidence_score(run: dict[str, Any]) -> tuple[int, int, str]:
    evidence = run.get("evidence") or {}
    present = sum(1 for value in evidence.values() if bool(value))
    missing = len(run.get("missing_required_evidence") or [])
    path = _display_path(run.get("path"))
    return (present, -missing, path)


def _candidate_sort_key(run: dict[str, Any]) -> tuple[int, float, float, str]:
    evidence = run.get("evidence") or {}
    missing = len(run.get("missing_required_evidence") or [])
    metrics = run.get("metrics") or {}
    return (
        -missing,
        _float_value(metrics.get("total_s")),
        _float_value(metrics.get("selected_zone_cold_p95_ms")),
        _display_path(run.get("path")),
    )


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _percentile(samples: Sequence[float], pct: float) -> float:
    values = sorted(float(value) for value in samples if value >= 0)
    if not values:
        return 0.0
    if len(values) == 1:
        return round(values[0], 3)
    rank = (len(values) - 1) * (pct / 100.0)
    low = int(rank)
    high = min(low + 1, len(values) - 1)
    fraction = rank - low
    return round(values[low] + (values[high] - values[low]) * fraction, 3)


if __name__ == "__main__":
    raise SystemExit(main())
