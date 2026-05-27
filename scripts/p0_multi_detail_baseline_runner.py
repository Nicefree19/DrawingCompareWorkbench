# -*- coding: utf-8 -*-
"""Run the P0-B multi-detail baseline evidence workflow.

This wrapper keeps the performance-degradation roadmap's multi-detail baseline
command reproducible. It enables region compare mode, runs the realset
validator with the evidence-producing options, then re-runs the P0 baseline
inventory gate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.comparison.perf_events import summarize_perf_events, write_perf_events_summary

SCHEMA_VERSION = "p0-multi-detail-baseline-run/v1"
REGION_ENV = {
    "DRAWING_COMPARE_MULTI_FRAME": "auto",
    "DRAWING_COMPARE_AUTO_REGION_COMPARE": "1",
}
P0_CONTRACT_FAILURE_RETURN_CODE = 3

REQUIRED_P0_FILES = {
    "success_marker": "_SUCCESS",
    "run_manifest": "run_manifest.json",
    "validation_summary": "validation_summary.json",
    "region_detection_summary": "artifacts/region_detection_summary.json",
    "region_match_summary": "artifacts/region_match_summary.json",
    "localized_compare_summary": "artifacts/localized_compare_summary.json",
    "localized_region_compare_results": "artifacts/localized_region_compare_results.json",
    "multi_frame_validation": "artifacts/multi_frame_validation.json",
    "region_aware_status": "artifacts/region_aware_status.json",
    "selected_zone_evidence": "viewer/selected_zone_evidence.json",
    "viewer_perf_jsonl": "viewer/viewer_perf.jsonl",
    "perf_events_summary": "perf_events_summary.json",
    "nonblank_pixel_probe": "nonblank_pixel_probe.json",
}
REGION_VIEWER_MANIFEST_CANDIDATES = (
    "artifacts/region_viewer/region_viewer_manifest.json",
    "viewer/region_viewer_manifest.json",
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", "--a", dest="before", required=True, type=Path)
    parser.add_argument("--after", "--b", dest="after", required=True, type=Path)
    parser.add_argument("--out", type=Path, help="Output run directory. Defaults under build/multi-detail-baseline.")
    parser.add_argument("--case-id", help="Stable output folder name when --out is omitted.")
    parser.add_argument("--recursive", action="store_true", help="Scan input folders recursively.")
    parser.add_argument("--max-workers", type=int, help="Forwarded to validate_drawing_compare_realset.py.")
    parser.add_argument("--dxf-cache-dir", type=Path)
    parser.add_argument("--compare-state-dir", type=Path)
    parser.add_argument("--viewer-render-policy", choices=("lazy", "top-issues", "all"), default="top-issues")
    parser.add_argument("--viewer-render-timeout-seconds", type=int, default=0)
    parser.add_argument("--selected-zone-evidence-per-pair", type=int, default=1)
    parser.add_argument("--max-preview-pairs", type=int, default=0)
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--inventory-json", type=Path, default=ROOT / ".benchmarks" / "performance_baseline_inventory.json")
    parser.add_argument("--inventory-md", type=Path, default=ROOT / "docs" / "collab" / "DRAWING_COMPARE_P0_BASELINE_REPORT.md")
    parser.add_argument("--inventory-root", type=Path, action="append", default=[])
    parser.add_argument("--inventory-max-runs", type=int, default=200)
    parser.add_argument("--allow-incomplete-inventory", action="store_true")
    parser.add_argument("--skip-inventory", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Write the run plan without launching subprocesses.")
    parser.add_argument(
        "--extra-realset-arg",
        action="append",
        default=[],
        help="Append one raw argument token to validate_drawing_compare_realset.py. Repeat as needed.",
    )
    parser.add_argument(
        "--extra-inventory-arg",
        action="append",
        default=[],
        help="Append one raw argument token to inventory_performance_baselines.py. Repeat as needed.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    before = args.before.resolve()
    after = args.after.resolve()
    output_dir = _output_dir_for_args(args, before, after)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not before.exists():
        _write_summary(output_dir, _summary_payload(args, before, after, output_dir, status="failed", error="before path does not exist"))
        print(f"before path does not exist: {before}", file=sys.stderr)
        return 2
    if not after.exists():
        _write_summary(output_dir, _summary_payload(args, before, after, output_dir, status="failed", error="after path does not exist"))
        print(f"after path does not exist: {after}", file=sys.stderr)
        return 2

    validation_cmd = build_validation_command(args, before, after, output_dir)
    inventory_cmd = build_inventory_command(args)
    payload = _summary_payload(
        args,
        before,
        after,
        output_dir,
        status="dry_run" if args.dry_run else "running",
        validation_cmd=validation_cmd,
        inventory_cmd=inventory_cmd,
    )
    _write_summary(output_dir, payload)
    if args.dry_run:
        print(json.dumps(_console_payload(payload), ensure_ascii=False, indent=2))
        return 0

    env = _region_env(os.environ)
    validation = subprocess.run(validation_cmd, cwd=ROOT, env=env)
    payload["validation_returncode"] = int(validation.returncode)
    if validation.returncode != 0:
        payload["status"] = "failed"
        payload["error"] = "validation command failed"
        _write_summary(output_dir, payload)
        return int(validation.returncode)

    postprocess = postprocess_evidence(output_dir)
    payload["postprocess"] = postprocess
    p0_contract = evaluate_p0_evidence_contract(output_dir)
    payload["p0_contract"] = p0_contract
    if not p0_contract.get("passed"):
        payload["status"] = "failed"
        payload["error"] = "P0 multi-detail evidence contract failed"
        _write_summary(output_dir, payload)
        print(json.dumps(_console_payload(payload), ensure_ascii=False, indent=2), file=sys.stderr)
        return P0_CONTRACT_FAILURE_RETURN_CODE

    if not args.skip_inventory:
        inventory = subprocess.run(inventory_cmd, cwd=ROOT, env=env)
        payload["inventory_returncode"] = int(inventory.returncode)
        if inventory.returncode != 0:
            payload["status"] = "failed"
            payload["error"] = "inventory command failed"
            _write_summary(output_dir, payload)
            return int(inventory.returncode)

    payload["status"] = "passed"
    _write_summary(output_dir, payload)
    print(json.dumps(_console_payload(payload), ensure_ascii=False, indent=2))
    return 0


def build_validation_command(args: argparse.Namespace, before: Path, after: Path, output_dir: Path) -> list[str]:
    command = [
        str(args.python_executable),
        str(ROOT / "scripts" / "validate_drawing_compare_realset.py"),
        "--a",
        str(before),
        "--b",
        str(after),
        "--out",
        str(output_dir),
        "--measure-runtime-budget",
        "--change-zone-report",
        "--review-dashboard",
        "--executive-review",
        "--export-viewer-package",
        "--viewer-render-policy",
        str(args.viewer_render_policy),
        "--viewer-perf-log",
        "--render-selected-zone-evidence",
        "--selected-zone-evidence-per-pair",
        str(max(1, int(args.selected_zone_evidence_per_pair or 1))),
        "--max-preview-pairs",
        str(max(0, int(args.max_preview_pairs or 0))),
        "--export-profile",
        "internal",
    ]
    if args.recursive:
        command.append("--recursive")
    if args.max_workers is not None:
        command.extend(["--max-workers", str(args.max_workers)])
    if args.dxf_cache_dir is not None:
        command.extend(["--dxf-cache-dir", str(args.dxf_cache_dir.resolve())])
    if args.compare_state_dir is not None:
        command.extend(["--compare-state-dir", str(args.compare_state_dir.resolve())])
    if int(args.viewer_render_timeout_seconds or 0) > 0:
        command.extend(["--viewer-render-timeout-seconds", str(int(args.viewer_render_timeout_seconds))])
    command.extend(str(token) for token in args.extra_realset_arg)
    return command


def build_inventory_command(args: argparse.Namespace) -> list[str]:
    command = [
        str(args.python_executable),
        str(ROOT / "scripts" / "inventory_performance_baselines.py"),
        "--max-runs",
        str(max(1, int(args.inventory_max_runs or 1))),
        "--output-json",
        str(args.inventory_json),
        "--output-md",
        str(args.inventory_md),
    ]
    for root in args.inventory_root:
        command.extend(["--root", str(root.resolve())])
    if not args.allow_incomplete_inventory:
        command.append("--fail-on-incomplete")
    command.extend(str(token) for token in args.extra_inventory_arg)
    return command


def postprocess_evidence(output_dir: Path) -> dict[str, Any]:
    output_dir = Path(output_dir)
    perf_summary = summarize_perf_events(output_dir)
    write_perf_events_summary(output_dir, perf_summary)
    nonblank_probe = write_nonblank_pixel_probe(output_dir)
    return {
        "schema_version": 1,
        "status": "passed" if nonblank_probe.get("passed") else "needs_visual_evidence",
        "perf_events_summary_json": str(output_dir / "perf_events_summary.json"),
        "perf_events_summary_status": perf_summary.get("status"),
        "nonblank_pixel_probe_json": str(output_dir / "nonblank_pixel_probe.json"),
        "nonblank_pixel_probe_status": nonblank_probe.get("status"),
    }


def evaluate_p0_evidence_contract(output_dir: Path) -> dict[str, Any]:
    """Validate the P0-C multi-detail baseline evidence contract.

    The runner is intentionally stricter than the inventory scanner: this
    command is specifically for proving a real multi-detail baseline, so a
    single-region malformed-CAD smoke run must fail here instead of being
    promoted by accident.
    """

    output_dir = Path(output_dir)
    missing: list[str] = []
    failures: list[str] = []
    evidence: dict[str, Any] = {}
    for key, relative in REQUIRED_P0_FILES.items():
        path = output_dir / relative
        exists = path.exists()
        evidence[key] = {"path": relative, "exists": exists}
        if not exists:
            missing.append(relative)

    region_viewer = _first_existing_path(
        output_dir,
        REGION_VIEWER_MANIFEST_CANDIDATES,
    )
    if region_viewer is None:
        missing.append("artifacts/region_viewer/region_viewer_manifest.json")
        evidence["region_viewer_manifest"] = {
            "exists": False,
            "candidates": list(REGION_VIEWER_MANIFEST_CANDIDATES),
        }
    else:
        evidence["region_viewer_manifest"] = {
            "exists": True,
            "path": _relative_posix(region_viewer, output_dir),
        }

    detection = _read_json_if_exists(output_dir / REQUIRED_P0_FILES["region_detection_summary"])
    matching = _read_json_if_exists(output_dir / REQUIRED_P0_FILES["region_match_summary"])
    localized = _read_json_if_exists(output_dir / REQUIRED_P0_FILES["localized_compare_summary"])
    localized_results = _read_json_if_exists(output_dir / REQUIRED_P0_FILES["localized_region_compare_results"])
    primary = _read_json_if_exists(output_dir / "artifacts" / "localized_change_zones_v2.json")
    region_viewer_payload = _read_json_if_exists(region_viewer) if region_viewer is not None else None
    selected_zone = _read_json_if_exists(output_dir / REQUIRED_P0_FILES["selected_zone_evidence"])
    nonblank = _read_json_if_exists(output_dir / REQUIRED_P0_FILES["nonblank_pixel_probe"])
    perf_summary = _read_json_if_exists(output_dir / REQUIRED_P0_FILES["perf_events_summary"])

    side_counts = _region_counts_by_side(detection)
    total_regions = sum(side_counts.values())
    evidence["region_counts_by_side"] = side_counts
    evidence["detected_region_count"] = total_regions
    if side_counts.get("before", 0) < 2 or side_counts.get("after", 0) < 2:
        failures.append("real multi-detail evidence requires at least 2 detected regions per side")
    if _detection_failed_count(detection) > 0:
        failures.append("region detection contains failed source results")

    approved_matches = _approved_region_match_count(matching)
    review_required_matches = _review_required_match_count(matching)
    evidence["approved_match_count"] = approved_matches
    evidence["review_required_match_count"] = review_required_matches
    if approved_matches <= 0 and review_required_matches <= 0:
        failures.append("region matching must contain approved matches or an explicit review gate")

    localized_assigned = _localized_assigned_count(localized)
    primary_zone_count = len(_as_list((primary or {}).get("zones"))) if isinstance(primary, dict) else 0
    compared_region_count = _int_value((localized_results or {}).get("compared_region_count")) if isinstance(localized_results, dict) else 0
    unsupported_pair_count = _int_value((localized_results or {}).get("unsupported_pair_count")) if isinstance(localized_results, dict) else 0
    evidence["localized_assigned_zone_count"] = localized_assigned
    evidence["region_local_primary_zone_count"] = primary_zone_count
    evidence["compared_region_count"] = compared_region_count
    evidence["unsupported_pair_count"] = unsupported_pair_count
    if unsupported_pair_count > 0:
        failures.append("localized_region_compare_results has unsupported pairs")
    if approved_matches > 0 and max(localized_assigned, primary_zone_count, compared_region_count) <= 0:
        failures.append("approved region matches did not produce localized compare evidence")

    if isinstance(region_viewer_payload, dict):
        entry_count = _int_value(region_viewer_payload.get("entry_count")) or len(_as_list(region_viewer_payload.get("entries")))
        render_failed_count = _region_viewer_render_failed_count(region_viewer_payload)
        evidence["region_viewer_entry_count"] = entry_count
        evidence["region_viewer_render_failed_count"] = render_failed_count
        if approved_matches > 0 and entry_count <= 0:
            failures.append("region viewer manifest has no entries for approved region matches")
        if render_failed_count > 0:
            failures.append("region viewer manifest contains render failures")

    if not _selected_zone_passed(selected_zone):
        failures.append("selected_zone_evidence.json did not pass")
    if not _nonblank_passed(nonblank):
        failures.append("nonblank_pixel_probe.json did not pass")
    if _int_value((perf_summary or {}).get("event_count")) <= 0:
        failures.append("perf_events_summary.json has no events")
    viewer_perf_jsonl = output_dir / REQUIRED_P0_FILES["viewer_perf_jsonl"]
    if viewer_perf_jsonl.exists() and _file_size(viewer_perf_jsonl) <= 0:
        failures.append("viewer/viewer_perf.jsonl is empty")

    return {
        "schema_version": 1,
        "passed": not missing and not failures,
        "missing": missing,
        "failures": failures,
        "evidence": evidence,
    }


def write_nonblank_pixel_probe(output_dir: Path) -> dict[str, Any]:
    output_dir = Path(output_dir)
    image_paths = _candidate_visual_images(output_dir)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": "missing",
        "passed": False,
        "checked": 0,
        "images": [],
    }
    if not image_paths:
        _write_json(output_dir / "nonblank_pixel_probe.json", payload)
        return payload
    try:
        from PIL import Image, ImageStat  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - optional local dependency
        payload["status"] = "unavailable"
        payload["reason"] = f"pillow_unavailable:{exc.__class__.__name__}"
        _write_json(output_dir / "nonblank_pixel_probe.json", payload)
        return payload

    checked: list[dict[str, Any]] = []
    for path in image_paths[:30]:
        try:
            with Image.open(path) as image:
                converted = image.convert("RGB")
                stat = ImageStat.Stat(converted)
                extrema = converted.getextrema()
        except Exception as exc:
            checked.append(
                {
                    "path": str(path.relative_to(output_dir)).replace("\\", "/"),
                    "status": "unreadable",
                    "reason": exc.__class__.__name__,
                }
            )
            continue
        channel_ranges = [int(high) - int(low) for low, high in extrema]
        mean = sum(float(value) for value in stat.mean) / max(1, len(stat.mean))
        nonblank = any(value > 3 for value in channel_ranges) or mean < 250.0
        entry = {
            "path": str(path.relative_to(output_dir)).replace("\\", "/"),
            "status": "nonblank" if nonblank else "blank_like",
            "width": int(converted.width),
            "height": int(converted.height),
            "channel_ranges": channel_ranges,
            "mean": round(mean, 3),
        }
        checked.append(entry)
        if nonblank:
            payload.update({"status": "passed", "passed": True, "checked": len(checked), "images": checked})
            _write_json(output_dir / "nonblank_pixel_probe.json", payload)
            return payload
    payload.update({"status": "failed", "passed": False, "checked": len(checked), "images": checked})
    _write_json(output_dir / "nonblank_pixel_probe.json", payload)
    return payload


def _candidate_visual_images(output_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for subdir in (
        "screenshots",
        "viewer/images",
        "viewer/zone_crops",
        "viewer/focus_tiles",
        "viewer/tiles",
    ):
        root = output_dir / subdir
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


def _first_existing_path(root: Path, candidates: Sequence[str]) -> Path | None:
    for relative in candidates:
        path = root / relative
        if path.exists():
            return path
    return None


def _read_json_if_exists(path: Path | None) -> dict[str, Any] | None:
    if path is None or not Path(path).exists():
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _region_counts_by_side(payload: Mapping[str, Any] | None) -> dict[str, int]:
    counts = {"before": 0, "after": 0}
    if not isinstance(payload, Mapping):
        return counts
    for result in _as_list(payload.get("results")):
        if not isinstance(result, Mapping):
            continue
        side = _normalize_side(result.get("side"))
        if not side:
            side = _normalize_side(result.get("label"))
        regions = _as_list(result.get("regions"))
        count = _int_value(result.get("region_count")) or len(regions)
        if side in counts:
            counts[side] += count
    if not any(counts.values()):
        regions = _as_list(payload.get("regions"))
        for region in regions:
            if isinstance(region, Mapping):
                side = _normalize_side(region.get("side"))
                if side in counts:
                    counts[side] += 1
    return counts


def _normalize_side(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"before", "a", "old", "left"}:
        return "before"
    if text in {"after", "b", "new", "right"}:
        return "after"
    return ""


def _detection_failed_count(payload: Mapping[str, Any] | None) -> int:
    if not isinstance(payload, Mapping):
        return 0
    return sum(
        1
        for result in _as_list(payload.get("results"))
        if isinstance(result, Mapping) and str(result.get("status") or "").lower() == "failed"
    )


def _approved_region_match_count(payload: Mapping[str, Any] | None) -> int:
    if not isinstance(payload, Mapping):
        return 0
    total = 0
    for summary in _as_list(payload.get("summaries")):
        if not isinstance(summary, Mapping):
            continue
        counted = _int_value(summary.get("auto_matched_count")) + _int_value(
            summary.get("manual_matched_count")
        )
        if counted:
            total += counted
            continue
        total += sum(
            1
            for match in _as_list(summary.get("matches"))
            if isinstance(match, Mapping)
            and str(match.get("status") or "") in {"auto_matched", "manual_matched"}
        )
    return total


def _review_required_match_count(payload: Mapping[str, Any] | None) -> int:
    if not isinstance(payload, Mapping):
        return 0
    total = 0
    for summary in _as_list(payload.get("summaries")):
        if not isinstance(summary, Mapping):
            continue
        counted = _int_value(summary.get("review_required_count"))
        if counted:
            total += counted
            continue
        total += sum(
            1
            for match in _as_list(summary.get("matches"))
            if isinstance(match, Mapping)
            and str(match.get("status") or "") == "review_required"
        )
    return total


def _localized_assigned_count(payload: Mapping[str, Any] | None) -> int:
    if not isinstance(payload, Mapping):
        return 0
    total = _int_value(payload.get("assigned_zones"))
    for summary in _as_list(payload.get("summaries")):
        if isinstance(summary, Mapping):
            total += _int_value(summary.get("assigned_zones"))
            total += len(_as_list(summary.get("localized_zones")))
    return total


def _region_viewer_render_failed_count(payload: Mapping[str, Any]) -> int:
    count = 0
    for entry in _as_list(payload.get("entries")):
        if not isinstance(entry, Mapping):
            continue
        for side in ("before", "after"):
            side_payload = entry.get(side)
            if isinstance(side_payload, Mapping) and str(side_payload.get("render_status") or "").lower() == "failed":
                count += 1
    return count


def _selected_zone_passed(payload: Mapping[str, Any] | None) -> bool:
    if not isinstance(payload, Mapping):
        return False
    return str(payload.get("status") or "").lower() == "passed" and _int_value(payload.get("failure_count")) <= 0


def _nonblank_passed(payload: Mapping[str, Any] | None) -> bool:
    if not isinstance(payload, Mapping):
        return False
    return payload.get("passed") is True or str(payload.get("status") or "").lower() == "passed"


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int_value(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _relative_posix(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _output_dir_for_args(args: argparse.Namespace, before: Path, after: Path) -> Path:
    if args.out is not None:
        return args.out.resolve()
    case_id = _safe_name(args.case_id or f"{before.stem}_vs_{after.stem}")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return (ROOT / "build" / "multi-detail-baseline" / f"{case_id}_{timestamp}").resolve()


def _safe_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value).strip())
    return text.strip("-._") or "multi-detail"


def _region_env(base: Mapping[str, str]) -> dict[str, str]:
    env = dict(base)
    env.update(REGION_ENV)
    return env


def _summary_payload(
    args: argparse.Namespace,
    before: Path,
    after: Path,
    output_dir: Path,
    *,
    status: str,
    validation_cmd: Sequence[str] | None = None,
    inventory_cmd: Sequence[str] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "before": str(before),
        "after": str(after),
        "output_dir": str(output_dir),
        "env_overrides": dict(REGION_ENV),
        "inventory_json": str(args.inventory_json),
        "inventory_md": str(args.inventory_md),
        "validation_command": list(validation_cmd or []),
        "inventory_command": list(inventory_cmd or []),
    }
    if error:
        payload["error"] = error
    return payload


def _write_summary(output_dir: Path, payload: dict[str, Any]) -> Path:
    path = output_dir / "p0_multi_detail_baseline_run.json"
    _write_json(path, payload)
    return path


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _console_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": payload.get("status"),
        "output_dir": payload.get("output_dir"),
        "summary_json": str(Path(str(payload.get("output_dir"))) / "p0_multi_detail_baseline_run.json"),
        "inventory_json": payload.get("inventory_json"),
        "inventory_md": payload.get("inventory_md"),
        "validation_returncode": payload.get("validation_returncode"),
        "inventory_returncode": payload.get("inventory_returncode"),
        "error": payload.get("error"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
