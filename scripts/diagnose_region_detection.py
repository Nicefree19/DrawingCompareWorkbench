"""Summarize multi-detail region detection artifacts for one compare run.

The script is intentionally read-only. It is used as a lightweight regression
and support tool when a CAD run silently falls back to whole-modelspace
comparison instead of comparing matched detail regions.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose region detection, matching, localization, and viewer bbox artifacts.",
    )
    parser.add_argument("run_dir", type=Path, help="DrawingCompareWorkbench run directory.")
    parser.add_argument(
        "--viewer-bbox-mismatch-threshold",
        type=float,
        default=10.0,
        help="Area-ratio threshold for flagging before/after viewer bbox mismatch.",
    )
    args = parser.parse_args(argv)

    run_dir = args.run_dir.resolve()
    if not run_dir.is_dir():
        print(f"run directory does not exist: {run_dir}", file=sys.stderr)
        return 2
    if args.viewer_bbox_mismatch_threshold <= 0:
        print("--viewer-bbox-mismatch-threshold must be greater than 0", file=sys.stderr)
        return 2

    try:
        payload = diagnose_run(
            run_dir,
            viewer_bbox_mismatch_threshold=args.viewer_bbox_mismatch_threshold,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def diagnose_run(
    run_dir: Path,
    *,
    viewer_bbox_mismatch_threshold: float = 10.0,
) -> dict[str, Any]:
    artifacts_dir = run_dir / "artifacts"
    viewer_dir = run_dir / "viewer"
    region_detection = _load_optional_json(
        artifacts_dir / "region_detection_summary.json"
    )
    region_match = _load_optional_json(artifacts_dir / "region_match_summary.json")
    localized = _load_optional_json(artifacts_dir / "localized_compare_summary.json")
    region_status = _load_optional_json(artifacts_dir / "region_aware_status.json")
    viewer_manifest = _load_optional_json(viewer_dir / "viewer_manifest.json")

    detection_summary = _summarize_region_detection(region_detection)
    matching_summary = _summarize_region_matching(region_match)
    localized_summary = _summarize_localized_compare(localized)
    status_summary = _summarize_region_status(region_status)
    viewer_summary = _summarize_viewer_manifest(
        viewer_manifest,
        mismatch_threshold=viewer_bbox_mismatch_threshold,
    )

    risk_flags = []
    if detection_summary["single_region_source_count"] >= 2:
        risk_flags.append("single_region_per_source")
    if detection_summary["whole_modelspace_count"]:
        risk_flags.append("whole_modelspace_fallback")
    if matching_summary["approved_match_count"] == 0 and detection_summary["region_count"] > 0:
        risk_flags.append("no_approved_region_matches")
    if matching_summary["auto_matched_count"] == 0 and detection_summary["region_count"] > 1:
        risk_flags.append("no_auto_region_matches")
    if (
        matching_summary["unmatched_before_count"]
        or matching_summary["unmatched_after_count"]
    ):
        risk_flags.append("unmatched_regions")
    if viewer_summary["bbox_mismatch_pairs"]:
        risk_flags.append("viewer_bbox_mismatch")
    if localized_summary["unassigned_zone_count"]:
        risk_flags.append("unassigned_localized_zones")
    if localized_summary["review_required_pair_count"]:
        risk_flags.append("localized_review_gate")
    if status_summary.get("automatic_localized_compare_requested") is False:
        risk_flags.append("region_local_not_requested")
    if status_summary.get("automatic_localized_compare_enabled") is False:
        risk_flags.append("region_local_not_enabled")

    return {
        "run_dir": str(run_dir),
        "artifacts": {
            "region_detection_summary": _artifact_status(
                artifacts_dir / "region_detection_summary.json"
            ),
            "region_match_summary": _artifact_status(
                artifacts_dir / "region_match_summary.json"
            ),
            "localized_compare_summary": _artifact_status(
                artifacts_dir / "localized_compare_summary.json"
            ),
            "viewer_manifest": _artifact_status(viewer_dir / "viewer_manifest.json"),
        },
        "region_detection": detection_summary,
        "region_matching": matching_summary,
        "localized_compare": localized_summary,
        "region_aware_status": status_summary,
        "viewer": viewer_summary,
        "risk_flags": risk_flags,
    }


def _load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"failed to read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _artifact_status(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "present": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }


def _summarize_region_detection(payload: dict[str, Any]) -> dict[str, Any]:
    methods: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    whole_regions: list[dict[str, Any]] = []
    source_summaries: list[dict[str, Any]] = []
    region_details: list[dict[str, Any]] = []
    single_region_source_count = 0

    for result in _list(payload.get("results")):
        side = str(result.get("side") or "")
        status = str(result.get("status") or "unknown")
        status_counts[status] += 1
        source_regions = _list(result.get("regions"))
        if len(source_regions) == 1:
            single_region_source_count += 1
        source_summaries.append(
            {
                "side": side,
                "status": status,
                "region_count": len(source_regions),
                "methods": dict(
                    Counter(
                        str(region.get("detection_method") or "unknown")
                        for region in source_regions
                    )
                ),
            }
        )
        for region in source_regions:
            method = str(region.get("detection_method") or "unknown")
            bbox = _bbox(region.get("bbox"))
            methods[method] += 1
            region_details.append(
                {
                    "side": side,
                    "region_id": str(region.get("region_id") or ""),
                    "detection_method": method,
                    "bbox": bbox,
                    "bbox_area": _bbox_area(bbox),
                    "entity_count": _int(region.get("entity_count")),
                    "confidence": _float(region.get("confidence")),
                    "drawing_number": str(region.get("drawing_number") or ""),
                    "has_title_text": bool(str(region.get("title_text") or "").strip()),
                    "title_text_sample": str(region.get("title_text") or "")[:120],
                }
            )
            if method == "whole_modelspace":
                whole_regions.append(
                    {
                        "side": side,
                        "region_id": str(region.get("region_id") or ""),
                        "bbox": bbox,
                        "confidence": _float(region.get("confidence")),
                    }
                )

    return {
        "source_count": int(payload.get("source_count") or len(source_summaries)),
        "region_count": int(payload.get("region_count") or sum(methods.values())),
        "methods": dict(sorted(methods.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "single_region_source_count": single_region_source_count,
        "whole_modelspace_count": len(whole_regions),
        "whole_modelspace_regions": whole_regions,
        "sources": source_summaries,
        "regions": region_details,
    }


def _summarize_region_matching(payload: dict[str, Any]) -> dict[str, Any]:
    counters: Counter[str] = Counter()
    warning_count = 0
    match_details: list[dict[str, Any]] = []
    for summary in _list(payload.get("summaries")):
        counters["auto_matched_count"] += _int(summary.get("auto_matched_count"))
        counters["manual_matched_count"] += _int(summary.get("manual_matched_count"))
        counters["review_required_count"] += _int(summary.get("review_required_count"))
        counters["unmatched_before_count"] += _int(summary.get("unmatched_before_count"))
        counters["unmatched_after_count"] += _int(summary.get("unmatched_after_count"))
        warning_count += len(_list(summary.get("warnings")))
        pair_id = str(summary.get("pair_id") or "")
        for match in _list(summary.get("matches")):
            status = str(match.get("status") or "")
            if not any(
                key in summary
                for key in (
                    "auto_matched_count",
                    "manual_matched_count",
                    "review_required_count",
                    "unmatched_before_count",
                    "unmatched_after_count",
                )
            ):
                if status == "auto_matched":
                    counters["auto_matched_count"] += 1
                elif status == "manual_matched":
                    counters["manual_matched_count"] += 1
                elif status == "review_required":
                    counters["review_required_count"] += 1
                elif status == "unmatched_before":
                    counters["unmatched_before_count"] += 1
                elif status == "unmatched_after":
                    counters["unmatched_after_count"] += 1
            match_details.append(
                {
                    "pair_id": pair_id,
                    "match_id": str(match.get("match_id") or ""),
                    "status": status,
                    "score": _float(match.get("score")),
                    "before_region_id": str(match.get("before_region_id") or ""),
                    "after_region_id": str(match.get("after_region_id") or ""),
                    "component_scores": _dict_of_float(match.get("component_scores")),
                    "reasons": [str(item) for item in _list(match.get("reasons"))],
                }
            )
    approved_count = counters["auto_matched_count"] + counters["manual_matched_count"]
    return {
        "pair_count": int(payload.get("pair_count") or len(_list(payload.get("summaries")))),
        "auto_matched_count": int(counters["auto_matched_count"]),
        "manual_matched_count": int(counters["manual_matched_count"]),
        "approved_match_count": int(approved_count),
        "review_required_count": int(counters["review_required_count"]),
        "unmatched_before_count": int(counters["unmatched_before_count"]),
        "unmatched_after_count": int(counters["unmatched_after_count"]),
        "warning_count": warning_count,
        "max_score": max((item["score"] for item in match_details), default=0.0),
        "matches": match_details,
    }


def _summarize_localized_compare(payload: dict[str, Any]) -> dict[str, Any]:
    total_zones = 0
    assigned_zones = 0
    unassigned = 0
    cross_region = 0
    review_required = 0
    gate_status_counts: Counter[str] = Counter()
    review_required_pair_count = 0
    for summary in _list(payload.get("summaries")):
        total_zones += _int(summary.get("total_zones"))
        assigned_zones += _int(summary.get("assigned_zones"))
        unassigned += _int(summary.get("unassigned_zone_count"))
        cross_region += _int(summary.get("cross_region_zone_count"))
        review_required += _int(summary.get("review_required_zone_count"))
        gate_status = str(summary.get("gate_status") or "unknown")
        gate_status_counts[gate_status] += 1
        if gate_status == "review_required":
            review_required_pair_count += 1
    return {
        "pair_count": int(payload.get("pair_count") or len(_list(payload.get("summaries")))),
        "total_zones": total_zones,
        "assigned_zones": assigned_zones,
        "unassigned_zone_count": unassigned,
        "cross_region_zone_count": cross_region,
        "review_required_zone_count": review_required,
        "review_required_pair_count": review_required_pair_count,
        "assignment_rate": assigned_zones / total_zones if total_zones else 0.0,
        "gate_status_counts": dict(sorted(gate_status_counts.items())),
    }


def _summarize_region_status(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {
            "present": False,
            "automatic_localized_compare_requested": None,
            "automatic_localized_compare_enabled": None,
        }
    return {
        "present": True,
        "feature_mode": str(payload.get("feature_mode") or ""),
        "fallback_reason": str(payload.get("fallback_reason") or ""),
        "automatic_localized_compare_requested": _bool_or_none(
            payload.get("automatic_localized_compare_requested")
        ),
        "automatic_localized_compare_enabled": _bool_or_none(
            payload.get("automatic_localized_compare_enabled")
        ),
        "automatic_localized_compare_status": str(
            payload.get("automatic_localized_compare_status") or ""
        ),
        "automatic_localized_compare_request_source": str(
            payload.get("automatic_localized_compare_request_source") or ""
        ),
        "region_default_enablement_status": str(
            payload.get("region_default_enablement_status") or ""
        ),
        "localized_gate_status": str(payload.get("localized_gate_status") or ""),
        "unassigned_zone_count": _int(payload.get("unassigned_zone_count")),
        "review_required_zone_count": _int(payload.get("review_required_zone_count")),
        "gate_reasons": [str(item) for item in _list(payload.get("gate_reasons"))],
        "default_enablement": payload.get("default_enablement")
        if isinstance(payload.get("default_enablement"), dict)
        else {},
    }


def _summarize_viewer_manifest(
    payload: dict[str, Any],
    *,
    mismatch_threshold: float,
) -> dict[str, Any]:
    mismatch_pairs = 0
    max_ratio = 0.0
    pair_details: list[dict[str, Any]] = []
    for pair in _list(payload.get("pairs")):
        before_area = _transform_area(pair.get("before_transform"))
        after_area = _transform_area(pair.get("after_transform"))
        ratio = _area_ratio(before_area, after_area)
        max_ratio = max(max_ratio, ratio)
        mismatch = ratio >= mismatch_threshold
        if mismatch:
            mismatch_pairs += 1
        pair_details.append(
            {
                "pair_id": str(pair.get("pair_id") or ""),
                "before_area": before_area,
                "after_area": after_area,
                "bbox_area_ratio": ratio,
                "bbox_mismatch": mismatch,
            }
        )
    return {
        "pair_count": int(payload.get("pair_count") or len(pair_details)),
        "bbox_mismatch_threshold": mismatch_threshold,
        "bbox_mismatch_pairs": mismatch_pairs,
        "max_bbox_area_ratio": max_ratio,
        "pairs": pair_details,
    }


def _transform_area(transform: Any) -> float:
    if not isinstance(transform, dict):
        return 0.0
    bbox = _bbox(
        [
            transform.get("min_x"),
            transform.get("min_y"),
            transform.get("max_x"),
            transform.get("max_y"),
        ]
    )
    if bbox is None:
        return 0.0
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _area_ratio(area_a: float, area_b: float) -> float:
    if area_a <= 0.0 or area_b <= 0.0:
        return 0.0
    return max(area_a, area_b) / min(area_a, area_b)


def _bbox_area(bbox: list[float] | None) -> float:
    if bbox is None:
        return 0.0
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) and not isinstance(value, tuple):
        return None
    if len(value) != 4:
        return None
    return [_float(item) for item in value]


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _dict_of_float(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _float(item) for key, item in value.items()}


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


if __name__ == "__main__":
    raise SystemExit(main())
