# -*- coding: utf-8 -*-
"""Human-first review dashboard outputs for drawing comparison artifacts."""

from __future__ import annotations

import csv
import fnmatch
import html
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence, Union

REVIEW_DASHBOARD_SCHEMA_VERSION = 1
REVIEW_QUEUE_TOP_PER_DRAWING = 5

STRUCTURAL_CORE_CATEGORIES = ("rebar", "member", "dimension", "grid", "mixed")

REBAR_PATTERN = re.compile(
    r"(?:S?H?D|HD|SD|D)\s*\d{1,3}\s*@\s*\d{2,4}|[ΦØ]\s*\d{1,3}\s*@\s*\d{2,4}",
    re.IGNORECASE,
)
MEMBER_PATTERN = re.compile(
    r"\b(?:BEAM|COLUMN|GIRDER|BRACE|SLAB|WALL)\b|H[-\s]?\d{2,4}\s*[xX]\s*\d{2,4}|기둥|보|슬래브|벽체|부재",
    re.IGNORECASE,
)
GRID_PATTERN = re.compile(r"\b(?:GRID|AXIS)\b|그리드|축선|기준선", re.IGNORECASE)
DIMENSION_PATTERN = re.compile(
    r"\b\d{2,5}(?:\.\d+)?\s*(?:mm|m)?\b|\bDIM(?:ENSION)?\b|치수",
    re.IGNORECASE,
)

DEFAULT_REPETITIVE_LAYER_PATTERNS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("축선/그리드", ("*AXIS*", "*GRID*"), "축선/그리드 변경은 여러 도면에 반복될 수 있어 대표 도면부터 확인합니다."),
    ("치수", ("*DIMS*", "*DIM*"), "치수 레이어 변경은 상세 위치보다 반복 패턴 여부를 먼저 확인합니다."),
    ("상세/참조", ("*DETL*", "*PCN*"), "상세/참조 레이어 변경은 동일 상세가 여러 도면에 반복되는지 확인합니다."),
    ("마킹/텍스트", ("*MKX*", "*TXT*"), "마킹/텍스트 변경은 표기 규칙 변경인지 실제 부재 변경인지 구분합니다."),
)


@dataclass
class ReviewDashboardOptions:
    """Options for review-first dashboard generation."""

    top_review_issues: int = 100
    # Phase I1 — was 20, but the GUI list cap (also 30) was hiding zones
    # that the viewer was rendering, so the user couldn't find marks they
    # could see on screen. Bumped to 100 to match top_review_issues; the
    # workbench list is now uncapped so users see everything per-drawing
    # and rely on the AI category filter / batch action for triage.
    top_issues_per_drawing: int = 100
    fold_repetitive_layers: bool = True


@dataclass
class ReviewDashboardPackage:
    """Generated review dashboard artifacts."""

    output_dir: str
    generated_at: str
    total_issue_count: int
    review_issue_count: int
    folded_pattern_count: int
    drawing_count: int
    output_paths: dict[str, str] = field(default_factory=dict)
    totals: dict[str, Any] = field(default_factory=dict)
    drawings: list[dict[str, Any]] = field(default_factory=list)
    top_project_issues: list[dict[str, Any]] = field(default_factory=list)
    layer_patterns: list[dict[str, Any]] = field(default_factory=list)
    review_queue: dict[str, Any] = field(default_factory=dict)
    preview_status_counts: dict[str, int] = field(default_factory=dict)
    action_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": REVIEW_DASHBOARD_SCHEMA_VERSION,
            "output_dir": self.output_dir,
            "generated_at": self.generated_at,
            "total_issue_count": self.total_issue_count,
            "review_issue_count": self.review_issue_count,
            "folded_pattern_count": self.folded_pattern_count,
            "drawing_count": self.drawing_count,
            "output_paths": self.output_paths,
            "totals": self.totals,
            "drawings": self.drawings,
            "top_drawings": self.drawings,
            "review_queue": self.review_queue,
            "top_issues": self.top_project_issues,
            "top_project_issues": self.top_project_issues,
            "pattern_groups": self.layer_patterns,
            "layer_patterns": self.layer_patterns,
            "preview_status_counts": self.preview_status_counts,
            "action_counts": self.action_counts,
            "warnings": self.warnings,
        }


def export_review_dashboard(
    artifact_dir: Union[str, Path],
    *,
    preview_manifest_path: Optional[Union[str, Path]] = None,
    top_review_issues: int = 100,
    top_issues_per_drawing: int = 20,
    fold_repetitive_layers: bool = True,
) -> ReviewDashboardPackage:
    """Create review-first dashboard JSON/CSV outputs from exported artifacts."""

    artifact_dir = Path(artifact_dir).resolve()
    manifest_path = artifact_dir / "artifact_manifest.json"
    zones_csv_path = artifact_dir / "change_zones.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(f"artifact manifest not found: {manifest_path}")
    if not zones_csv_path.exists():
        raise FileNotFoundError(f"change zone CSV not found: {zones_csv_path}")

    manifest = _read_json(manifest_path)
    preview_manifest = _load_preview_manifest(manifest, preview_manifest_path)
    preview_by_pair = _preview_lookup(preview_manifest)
    artifact_by_pair = _artifact_lookup(manifest)
    options = ReviewDashboardOptions(
        top_review_issues=max(int(top_review_issues or 100), 1),
        top_issues_per_drawing=max(int(top_issues_per_drawing or 20), 1),
        fold_repetitive_layers=bool(fold_repetitive_layers),
    )

    zone_rows = _read_csv(zones_csv_path)
    issues = [
        _review_issue_from_zone(
            row,
            artifact_by_pair=artifact_by_pair,
            preview_by_pair=preview_by_pair,
            fold_repetitive_layers=options.fold_repetitive_layers,
        )
        for row in zone_rows
    ]
    issues.sort(key=_issue_sort_key)
    for index, issue in enumerate(issues, start=1):
        issue["priority_rank"] = index
    _assign_priority_rank_in_drawing(issues)

    visible_issues = [issue for issue in issues if not issue.get("folded_pattern")]
    representative_folded = _representative_folded_issues(issues)
    top_pool = sorted(visible_issues + representative_folded, key=_issue_sort_key)
    if not top_pool:
        top_pool = issues
    top_project_issues = top_pool[: options.top_review_issues]

    drawings = _summarize_dashboard_drawings(
        issues,
        top_pool,
        top_per_drawing=options.top_issues_per_drawing,
        artifact_by_pair=artifact_by_pair,
        preview_by_pair=preview_by_pair,
    )
    layer_patterns = _summarize_layer_patterns(issues)
    preview_status_counts = _preview_status_counts(drawings)
    action_counts = _action_counts(issues)

    paths = {
        "review_dashboard_json": str(artifact_dir / "review_dashboard.json"),
        "review_priority_csv": str(artifact_dir / "review_priority.csv"),
        "layer_pattern_summary_csv": str(artifact_dir / "layer_pattern_summary.csv"),
    }
    totals = {
        "pair_count": _int_cell(manifest.get("pair_count")),
        "raw_change_count": _int_cell(manifest.get("raw_change_count")),
        "zone_count": _int_cell(manifest.get("zone_count"), len(zone_rows)),
        "zone_coverage_complete": bool(manifest.get("zone_coverage_complete", True)),
        "cloud_region_count": _int_cell(manifest.get("cloud_region_count")),
        "cloud_omitted_zone_count": _int_cell(manifest.get("cloud_omitted_zone_count")),
        "total_issue_count": len(issues),
        "review_issue_count": len(top_project_issues),
        "visible_issue_count": len(top_pool),
        "folded_issue_count": sum(1 for issue in issues if issue.get("folded_pattern")),
        "representative_folded_issue_count": len(representative_folded),
        "folded_pattern_count": len(layer_patterns),
        "preview_count": _int_cell((preview_manifest or {}).get("preview_count")),
        "preview_manifest_json": _preview_manifest_path(manifest, preview_manifest_path),
        "preview_status_counts": preview_status_counts,
        "action_counts": action_counts,
        "structural_core_counts": _structural_core_counts(issues),
    }
    review_queue = _build_review_queue(
        manifest=manifest,
        totals=totals,
        issues=issues,
        drawings=drawings,
        top_project_issues=top_project_issues,
        layer_patterns=layer_patterns,
    )
    package = ReviewDashboardPackage(
        output_dir=str(artifact_dir),
        generated_at=datetime.now().isoformat(),
        total_issue_count=len(issues),
        review_issue_count=len(top_project_issues),
        folded_pattern_count=len(layer_patterns),
        drawing_count=len(drawings),
        output_paths=paths,
        totals=totals,
        drawings=drawings,
        top_project_issues=top_project_issues,
        layer_patterns=layer_patterns,
        review_queue=review_queue,
        preview_status_counts=preview_status_counts,
        action_counts=action_counts,
    )

    _write_review_priority_csv(Path(paths["review_priority_csv"]), issues)
    _write_layer_pattern_summary_csv(Path(paths["layer_pattern_summary_csv"]), layer_patterns)
    _write_json(Path(paths["review_dashboard_json"]), package.to_dict())
    _update_manifest_with_dashboard(manifest_path, package)
    return package


def _review_issue_from_zone(
    row: dict[str, Any],
    *,
    artifact_by_pair: dict[str, dict[str, Any]],
    preview_by_pair: dict[str, dict[str, Any]],
    fold_repetitive_layers: bool,
) -> dict[str, Any]:
    pair_id = str(row.get("pair_id") or "")
    pair_uuid = str(row.get("pair_uuid") or pair_id)
    zone_id = str(row.get("zone_id") or "")
    layers = _split_layers(row.get("layers"))
    pattern_group, pattern_reason = _repetitive_pattern_for_layers(layers)
    folded = bool(fold_repetitive_layers and pattern_group)
    preview = preview_by_pair.get(pair_id, {})
    overlay = (preview.get("overlays") or {}).get(zone_id, {})
    artifact = artifact_by_pair.get(pair_id, {})
    added = _int_cell(row.get("added") or row.get("added_count"))
    deleted = _int_cell(row.get("deleted") or row.get("deleted_count"))
    modified = _int_cell(row.get("modified") or row.get("modified_count"))
    entity_types = _split_layers(row.get("entity_types"))
    source_a = str(row.get("source_a") or artifact.get("source_a") or "")
    source_b = str(row.get("source_b") or artifact.get("source_b") or "")
    bbox = _bbox_from_row(row)
    old_bbox = _bbox_from_row(row, prefix="old_bbox")
    raw = _int_cell(row.get("raw_change_count"))
    category = _structural_category(row, layers=layers, entity_types=entity_types)
    source_format = _source_format(row, source_a=source_a, source_b=source_b, entity_types=entity_types)
    detection_source = _detection_source(row, source_format=source_format, entity_types=entity_types)
    bbox_status = _bbox_status(row, bbox=bbox, source_format=source_format, overlay=overlay)
    summary_ko = _change_summary_ko(row, category=category)
    reason_ko = _reason_ko(
        row,
        category=category,
        detection_source=detection_source,
        bbox_status=bbox_status,
    )
    score = _priority_score(
        severity=str(row.get("severity") or ""),
        raw=raw,
        added=added,
        deleted=deleted,
        modified=modified,
        bbox=bbox,
        folded=folded,
        layers=layers,
        category=category,
        content_change=_has_content_change(row, category=category),
        bbox_status=bbox_status,
    )
    fallback_reason = _priority_reason(
        row,
        raw=raw,
        modified=modified,
        folded=folded,
        pattern_group=pattern_group,
    )
    if category == "other" and not reason_ko:
        reason_ko = fallback_reason
    drawing = str(row.get("drawing_number") or artifact.get("drawing_number") or pair_id)
    return {
        "pair_id": pair_id,
        "pair_uuid": pair_uuid,
        "display_label": str(row.get("display_label") or row.get("drawing_number") or pair_id),
        "zone_id": zone_id,
        "drawing_number": drawing,
        "change_type": str(row.get("change_type") or "mixed"),
        "change_type_ko": _change_type_ko(row.get("change_type")),
        "severity": str(row.get("severity") or ""),
        "severity_ko": _severity_ko(row.get("severity")),
        "status": str(row.get("status") or ""),
        "raw_change_count": raw,
        "added_count": added,
        "deleted_count": deleted,
        "modified_count": modified,
        "layers": layers,
        "major_layers": " | ".join(layers[:4]),
        "entity_types": entity_types,
        "bbox": list(bbox) if bbox else [],
        "old_bbox": list(old_bbox) if old_bbox else [],
        "bbox_text": _bbox_text(bbox),
        "bbox_area": _bbox_area(bbox),
        "priority_score": round(score, 3),
        "priority_reason_ko": reason_ko,
        "reason_ko": reason_ko,
        "change_summary_ko": summary_ko,
        "category": category,
        "source_format": source_format,
        "detection_source": detection_source,
        "bbox_status": bbox_status,
        "review_status": _review_status(row.get("status")),
        "folded_pattern": folded,
        "pattern_group": pattern_group,
        "pattern_reason_ko": pattern_reason,
        "after_marked_dxf": str(artifact.get("after_marked_dxf") or ""),
        "before_marked_dxf": str(artifact.get("before_marked_dxf") or ""),
        "before_image": str(preview.get("before_image") or ""),
        "after_image": str(preview.get("after_image") or ""),
        "preview_available": bool(preview.get("before_image") and preview.get("after_image")),
        "preview_status": _preview_status(preview),
        "preview_warnings": preview.get("warnings") or [],
        "before_bbox_px": overlay.get("before_bbox_px") or [],
        "after_bbox_px": overlay.get("after_bbox_px") or [],
        "source_a": source_a,
        "source_b": source_b,
    }


def _priority_score(
    *,
    severity: str,
    raw: int,
    added: int,
    deleted: int,
    modified: int,
    bbox: Optional[tuple[float, float, float, float]],
    folded: bool,
    layers: Sequence[str],
    category: str = "other",
    content_change: bool = False,
    bbox_status: str = "relative_only",
) -> float:
    severity_score = {"critical": 420.0, "high": 320.0, "medium": 180.0, "low": 80.0}.get(
        severity.lower(),
        120.0,
    )
    raw_score = min(float(raw), 1000.0) * 0.22
    area_score = min(math.sqrt(max(_bbox_area(bbox), 0.0)), 2500.0) * 0.04
    change_mix_score = 40.0 if sum(1 for value in (added, deleted, modified) if value > 0) >= 2 else 0.0
    modified_score = min(float(modified), 100.0) * 0.6
    category_boost = {
        "rebar": 720.0,
        "member": 680.0,
        "mixed": 620.0,
        "dimension": 560.0,
        "grid": 520.0,
        "other": 0.0,
    }.get(category, 0.0)
    structural_boost = 70.0 if layers and not _repetitive_pattern_for_layers(layers)[0] else 0.0
    content_boost = 220.0 if content_change else 0.0
    bbox_boost = 35.0 if bbox_status == "exact" else 0.0
    score = (
        severity_score
        + raw_score
        + area_score
        + change_mix_score
        + modified_score
        + structural_boost
        + category_boost
        + content_boost
        + bbox_boost
    )
    if folded:
        score *= 0.75 if category in STRUCTURAL_CORE_CATEGORIES else 0.45
    return score


def _priority_reason(
    row: dict[str, Any],
    *,
    raw: int,
    modified: int,
    folded: bool,
    pattern_group: str,
) -> str:
    if folded:
        return f"{pattern_group} 반복 패턴으로 접힘. 대표 도면과 패턴 요약을 먼저 확인하세요."
    if str(row.get("severity") or "").lower() in {"critical", "high"}:
        return "심각도가 높아 우선 검토 대상입니다."
    if modified > 0:
        return "수정 변경이 포함되어 형상/표기 차이를 확인해야 합니다."
    if raw >= 50:
        return "한 구역에 많은 변경이 집중되어 우선 확인이 필요합니다."
    return "일반 변경구역입니다."


def _assign_priority_rank_in_drawing(issues: Sequence[dict[str, Any]]) -> None:
    by_pair: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for issue in issues:
        by_pair[str(issue.get("pair_id") or issue.get("pair_uuid") or "")].append(issue)
    for pair_issues in by_pair.values():
        for index, issue in enumerate(sorted(pair_issues, key=_issue_sort_key), start=1):
            issue["priority_rank_in_drawing"] = index


def _structural_core_counts(issues: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for issue in issues:
        category = str(issue.get("category") or "other")
        if _is_structural_core_category(category):
            counts[category] += 1
    for category in STRUCTURAL_CORE_CATEGORIES:
        counts.setdefault(category, 0)
    counts["total"] = sum(counts[category] for category in STRUCTURAL_CORE_CATEGORIES)
    return dict(counts)


def _summarize_dashboard_drawings(
    issues: Sequence[dict[str, Any]],
    visible_issues: Sequence[dict[str, Any]],
    *,
    top_per_drawing: int,
    artifact_by_pair: dict[str, dict[str, Any]],
    preview_by_pair: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    by_pair: dict[str, list[dict[str, Any]]] = defaultdict(list)
    visible_by_pair: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for issue in issues:
        by_pair[str(issue.get("pair_id") or "")].append(issue)
    for issue in visible_issues:
        visible_by_pair[str(issue.get("pair_id") or "")].append(issue)

    drawings: list[dict[str, Any]] = []
    for pair_id, pair_issues in by_pair.items():
        artifact = artifact_by_pair.get(pair_id, {})
        preview = preview_by_pair.get(pair_id, {})
        visible = sorted(visible_by_pair.get(pair_id, []), key=_issue_sort_key)
        top_issues = visible[:top_per_drawing] if visible else sorted(pair_issues, key=_issue_sort_key)[:top_per_drawing]
        structural_issues = [
            issue
            for issue in sorted(pair_issues, key=_issue_sort_key)
            if _is_structural_core_category(issue.get("category"))
        ]
        review_queue_items = [_queue_item_from_issue(issue) for issue in structural_issues[:REVIEW_QUEUE_TOP_PER_DRAWING]]
        folded_count = sum(1 for issue in pair_issues if issue.get("folded_pattern"))
        raw_total = sum(_int_cell(issue.get("raw_change_count")) for issue in pair_issues)
        zone_count = len(pair_issues)
        priority_seed = structural_issues[:REVIEW_QUEUE_TOP_PER_DRAWING] or top_issues[:5]
        priority = sum(float(issue.get("priority_score") or 0.0) for issue in priority_seed)
        priority += raw_total * 0.01 + zone_count * 0.05
        drawing = str(
            artifact.get("drawing_number")
            or (pair_issues[0].get("drawing_number") if pair_issues else "")
            or pair_id
        )
        layer_counter = Counter(layer for issue in pair_issues for layer in issue.get("layers", []))
        drawings.append(
            {
                "pair_id": pair_id,
                "drawing_number": drawing,
                "priority_score": round(priority, 3),
                "raw_change_count": raw_total,
                "zone_count": zone_count,
                "review_issue_count": len(top_issues),
                "structural_core_issue_count": len(structural_issues),
                "visible_issue_count": len(visible),
                "folded_issue_count": folded_count,
                "high_issue_count": sum(1 for issue in pair_issues if str(issue.get("severity")).lower() == "high"),
                "major_layers": " | ".join(layer for layer, _ in layer_counter.most_common(4)),
                "preview_available": bool(preview.get("before_image") and preview.get("after_image")),
                "preview_status": _preview_status(preview),
                "preview_warnings": preview.get("warnings") or [],
                "before_image": str(preview.get("before_image") or ""),
                "after_image": str(preview.get("after_image") or ""),
                "after_marked_dxf": str(artifact.get("after_marked_dxf") or ""),
                "cloud_region_count": _int_cell(artifact.get("cloud_region_count")),
                "cloud_omitted_zone_count": _int_cell(artifact.get("cloud_omitted_zone_count")),
                "top_issues": top_issues,
                "review_queue_items": review_queue_items,
            }
        )
    return sorted(drawings, key=lambda item: (-float(item.get("priority_score") or 0.0), str(item.get("drawing_number") or "")))


def _summarize_layer_patterns(issues: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    drawings_by_pattern: dict[str, set[str]] = defaultdict(set)
    layers_by_pattern: dict[str, Counter[str]] = defaultdict(Counter)
    reps_by_pattern: dict[str, list[str]] = defaultdict(list)
    for issue in issues:
        pattern = str(issue.get("pattern_group") or "")
        if not pattern:
            continue
        entry = grouped.setdefault(
            pattern,
            {
                "pattern": pattern,
                "raw_change_count": 0,
                "zone_count": 0,
                "affected_drawing_count": 0,
                "top_drawings": "",
                "top_layers": "",
                "representative_zones": "",
                "interpretation_ko": issue.get("pattern_reason_ko") or "",
            },
        )
        entry["raw_change_count"] += _int_cell(issue.get("raw_change_count"))
        entry["zone_count"] += 1
        drawing = str(issue.get("drawing_number") or "")
        if drawing:
            drawings_by_pattern[pattern].add(drawing)
        for layer in issue.get("layers", []):
            layers_by_pattern[pattern][layer] += 1
        if len(reps_by_pattern[pattern]) < 5:
            reps_by_pattern[pattern].append(f"{drawing}:{issue.get('zone_id')}")

    for pattern, entry in grouped.items():
        entry["affected_drawing_count"] = len(drawings_by_pattern[pattern])
        entry["top_drawings"] = " | ".join(sorted(drawings_by_pattern[pattern])[:8])
        entry["top_layers"] = " | ".join(layer for layer, _ in layers_by_pattern[pattern].most_common(5))
        entry["representative_zones"] = " | ".join(reps_by_pattern[pattern])
    return sorted(grouped.values(), key=lambda item: (-_int_cell(item.get("raw_change_count")), str(item.get("pattern"))))


def _representative_folded_issues(issues: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    representatives: dict[tuple[str, str], dict[str, Any]] = {}
    for issue in issues:
        if not issue.get("folded_pattern"):
            continue
        key = (str(issue.get("pair_id") or ""), str(issue.get("pattern_group") or ""))
        current = representatives.get(key)
        if current is None or _issue_sort_key(issue) < _issue_sort_key(current):
            representative = dict(issue)
            representative["folded_representative"] = True
            representatives[key] = representative
    return list(representatives.values())


def _preview_status_counts(drawings: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for drawing in drawings:
        status = str(drawing.get("preview_status") or "render_pending")
        counts[status] += 1
    for key in ("real_preview", "relative_only", "render_pending", "render_failed"):
        counts.setdefault(key, 0)
    return dict(counts)


def _action_counts(issues: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for issue in issues:
        status = _review_status(issue.get("review_status") or issue.get("status"))
        counts[status] += 1
    for key in ("needs_review", "confirmed", "hold", "false_positive"):
        counts.setdefault(key, 0)
    return dict(counts)


def _build_review_queue(
    *,
    manifest: dict[str, Any],
    totals: dict[str, Any],
    issues: Sequence[dict[str, Any]],
    drawings: Sequence[dict[str, Any]],
    top_project_issues: Sequence[dict[str, Any]],
    layer_patterns: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    unmatched_a = _int_cell(manifest.get("unmatched_a_count"))
    unmatched_b = _int_cell(manifest.get("unmatched_b_count"))
    blocked = _int_cell(manifest.get("blocked_pair_count") or manifest.get("blocked_pairs"))
    failed = _int_cell(manifest.get("failed_pair_count") or manifest.get("compare_failed"))
    completed = _int_cell(manifest.get("pair_count") or totals.get("pair_count"))
    queue_items = _review_queue_items(issues)
    structural_core_count = _int_cell((totals.get("structural_core_counts") or {}).get("total"))
    return {
        "schema_version": 1,
        "mode": "structural_core",
        "top_per_drawing": REVIEW_QUEUE_TOP_PER_DRAWING,
        "auto_completed_count": completed,
        "priority_issue_count": len(top_project_issues),
        "structural_core_issue_count": structural_core_count,
        "queue_item_count": len(queue_items),
        "pattern_group_count": len(layer_patterns),
        "unmatched_count": unmatched_a + unmatched_b,
        "blocked_count": blocked,
        "failed_count": failed,
        "items": queue_items,
        "top_structural_items": queue_items,
        "top_by_drawing": _review_queue_by_drawing(drawings),
        "top_items": list(top_project_issues),
        "pattern_groups": list(layer_patterns),
    }


def _review_queue_items(issues: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for issue in sorted(issues, key=_issue_sort_key):
        if not _is_structural_core_category(issue.get("category")):
            continue
        grouped[str(issue.get("pair_uuid") or issue.get("pair_id") or "")].append(issue)

    items: list[dict[str, Any]] = []
    for pair_issues in grouped.values():
        items.extend(_queue_item_from_issue(issue) for issue in pair_issues[:REVIEW_QUEUE_TOP_PER_DRAWING])
    items.sort(key=lambda item: (-float(item.get("priority_score") or 0.0), str(item.get("drawing_label") or ""), str(item.get("zone_id") or "")))
    return items


def _review_queue_by_drawing(drawings: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for drawing in drawings:
        items = list(drawing.get("review_queue_items") or [])
        if not items:
            continue
        rows.append(
            {
                "pair_id": drawing.get("pair_id"),
                "pair_uuid": items[0].get("pair_uuid") if items else drawing.get("pair_id"),
                "drawing_label": drawing.get("drawing_number"),
                "structural_core_issue_count": drawing.get("structural_core_issue_count", len(items)),
                "items": items,
            }
        )
    return rows


def _queue_item_from_issue(issue: dict[str, Any]) -> dict[str, Any]:
    pair_uuid = str(issue.get("pair_uuid") or issue.get("pair_id") or "")
    zone_id = str(issue.get("zone_id") or "")
    return {
        "queue_key": f"{pair_uuid}:{zone_id}",
        "pair_uuid": pair_uuid,
        "pair_id": issue.get("pair_id") or "",
        "zone_id": zone_id,
        "drawing_label": issue.get("display_label") or issue.get("drawing_number") or issue.get("pair_id") or "",
        "drawing_number": issue.get("drawing_number") or "",
        "category": issue.get("category") or "other",
        "priority_rank_in_drawing": _int_cell(issue.get("priority_rank_in_drawing"), 0),
        "priority_score": issue.get("priority_score") or 0.0,
        "reason_ko": issue.get("reason_ko") or issue.get("priority_reason_ko") or "",
        "change_summary_ko": issue.get("change_summary_ko") or "",
        "source_format": issue.get("source_format") or "cad",
        "detection_source": issue.get("detection_source") or "cad_entity",
        "bbox_status": issue.get("bbox_status") or "relative_only",
        "review_status": issue.get("review_status") or _review_status(issue.get("status")),
        "folded_pattern": bool(issue.get("folded_pattern")),
        "change_type_ko": issue.get("change_type_ko") or "",
        "severity_ko": issue.get("severity_ko") or "",
        "raw_change_count": issue.get("raw_change_count") or 0,
        "added_count": issue.get("added_count") or 0,
        "deleted_count": issue.get("deleted_count") or 0,
        "modified_count": issue.get("modified_count") or 0,
        "major_layers": issue.get("major_layers") or "",
        "entity_types": issue.get("entity_types") or [],
        "bbox": issue.get("bbox") or [],
        "old_bbox": issue.get("old_bbox") or [],
        "bbox_text": issue.get("bbox_text") or "",
        "preview_status": issue.get("preview_status") or "",
        "before_bbox_px": issue.get("before_bbox_px") or [],
        "after_bbox_px": issue.get("after_bbox_px") or [],
        "after_marked_dxf": issue.get("after_marked_dxf") or "",
    }


def _write_review_priority_csv(path: Path, issues: Sequence[dict[str, Any]]) -> None:
    columns = [
        "priority_rank",
        "priority_score",
        "folded_pattern",
        "folded_representative",
        "pattern_group",
        "drawing_number",
        "pair_id",
        "pair_uuid",
        "display_label",
        "zone_id",
        "category",
        "priority_rank_in_drawing",
        "source_format",
        "detection_source",
        "bbox_status",
        "review_status",
        "change_summary_ko",
        "reason_ko",
        "change_type_ko",
        "severity_ko",
        "raw_change_count",
        "added_count",
        "deleted_count",
        "modified_count",
        "major_layers",
        "bbox_text",
        "priority_reason_ko",
        "preview_available",
        "before_image",
        "after_image",
        "after_bbox_px",
        "after_marked_dxf",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for issue in issues:
            row = {column: issue.get(column, "") for column in columns}
            row["after_bbox_px"] = json.dumps(issue.get("after_bbox_px") or [], ensure_ascii=False)
            writer.writerow(row)


def _write_layer_pattern_summary_csv(path: Path, patterns: Sequence[dict[str, Any]]) -> None:
    columns = [
        "pattern",
        "raw_change_count",
        "zone_count",
        "affected_drawing_count",
        "top_drawings",
        "top_layers",
        "representative_zones",
        "interpretation_ko",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in patterns:
            writer.writerow({column: row.get(column, "") for column in columns})


def _update_manifest_with_dashboard(manifest_path: Path, package: ReviewDashboardPackage) -> None:
    manifest = _read_json(manifest_path)
    outputs = manifest.setdefault("output_paths", {})
    outputs.update(package.output_paths)
    manifest["review_dashboard_json"] = package.output_paths.get("review_dashboard_json", "")
    manifest["review_priority_csv"] = package.output_paths.get("review_priority_csv", "")
    manifest["layer_pattern_summary_csv"] = package.output_paths.get("layer_pattern_summary_csv", "")
    manifest["review_issue_count"] = package.review_issue_count
    manifest["total_review_issue_count"] = package.total_issue_count
    manifest["folded_pattern_count"] = package.folded_pattern_count
    manifest["review_queue"] = package.review_queue
    manifest["preview_status_counts"] = package.preview_status_counts
    manifest["action_counts"] = package.action_counts
    manifest["review_dashboard"] = package.to_dict()
    _write_json(manifest_path, manifest)


def _artifact_lookup(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = manifest.get("artifacts")
    if not isinstance(rows, list):
        rows = manifest.get("items", [])
    return {
        str(item.get("pair_id") or ""): dict(item)
        for item in rows
        if isinstance(item, dict) and item.get("pair_id")
    }


def _preview_lookup(preview_manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not preview_manifest:
        return {}
    lookup: dict[str, dict[str, Any]] = {}
    for artifact in preview_manifest.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        pair_id = str(artifact.get("pair_id") or "")
        if not pair_id:
            continue
        overlays = {}
        for overlay in artifact.get("zone_overlays", []) or []:
            if isinstance(overlay, dict) and overlay.get("zone_id"):
                overlays[str(overlay["zone_id"])] = overlay
        item = dict(artifact)
        item["overlays"] = overlays
        lookup[pair_id] = item
    return lookup


def _preview_status(preview: dict[str, Any]) -> str:
    if not preview:
        return "render_pending"
    warnings = " ".join(str(item).lower() for item in preview.get("warnings") or [])
    if preview.get("before_image") and preview.get("after_image"):
        return "real_preview"
    if "skipped" in warnings:
        return "render_pending"
    if warnings:
        return "render_failed"
    return "relative_only"


def _load_preview_manifest(
    manifest: dict[str, Any],
    preview_manifest_path: Optional[Union[str, Path]],
) -> dict[str, Any] | None:
    path_text = _preview_manifest_path(manifest, preview_manifest_path)
    if not path_text:
        return None
    path = Path(path_text)
    if not path.exists():
        return None
    try:
        return _read_json(path)
    except Exception:
        return None


def _preview_manifest_path(
    manifest: dict[str, Any],
    preview_manifest_path: Optional[Union[str, Path]],
) -> str:
    if preview_manifest_path:
        return str(Path(preview_manifest_path).resolve())
    output_paths = manifest.get("output_paths") if isinstance(manifest, dict) else {}
    return str(
        (output_paths or {}).get("preview_manifest_json")
        or manifest.get("preview_manifest")
        or ""
    )


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _split_layers(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        parts = [str(item) for item in value]
    else:
        parts = str(value).replace(",", "|").split("|")
    return [part.strip() for part in parts if part and part.strip()]


def _text_blob(row: dict[str, Any], *, layers: Sequence[str], entity_types: Sequence[str]) -> str:
    fields = [
        "reasons",
        "reason",
        "change_detail",
        "change_details",
        "old_text",
        "new_text",
        "old_content",
        "new_content",
        "before_text",
        "after_text",
        "text",
        "content",
        "description",
        "change_type",
    ]
    chunks = [str(row.get(field) or "") for field in fields]
    chunks.extend(str(item) for item in layers)
    chunks.extend(str(item) for item in entity_types)
    return " ".join(chunks)


def _old_new_text(row: dict[str, Any]) -> tuple[str, str]:
    old_text = str(
        row.get("old_text")
        or row.get("old_content")
        or row.get("before_text")
        or row.get("from_text")
        or ""
    ).strip()
    new_text = str(
        row.get("new_text")
        or row.get("new_content")
        or row.get("after_text")
        or row.get("to_text")
        or ""
    ).strip()
    if old_text and new_text:
        return old_text, new_text

    blob = str(row.get("reasons") or row.get("change_detail") or row.get("change_details") or "")
    for sep in ("->", "=>", "→", " to "):
        if sep in blob:
            left, right = blob.split(sep, 1)
            return left.strip(" :;[]()"), right.strip(" :;[]()")
    return old_text, new_text


def _structural_category(
    row: dict[str, Any],
    *,
    layers: Sequence[str],
    entity_types: Sequence[str],
) -> str:
    blob = _text_blob(row, layers=layers, entity_types=entity_types)
    matches: set[str] = set()
    rebar_match = bool(REBAR_PATTERN.search(blob))
    if rebar_match:
        matches.add("rebar")
    if MEMBER_PATTERN.search(blob):
        matches.add("member")
    if GRID_PATTERN.search(blob):
        matches.add("grid")
    if not rebar_match and _is_dimension_like(row, blob=blob, layers=layers, entity_types=entity_types):
        matches.add("dimension")
    if len(matches) > 1:
        return "mixed"
    return next(iter(matches), "other")


def _is_dimension_like(
    row: dict[str, Any],
    *,
    blob: str,
    layers: Sequence[str],
    entity_types: Sequence[str],
) -> bool:
    layer_blob = " ".join(layers).upper()
    entity_blob = " ".join(entity_types).upper()
    if "DIMENSION" in entity_blob or "DIM" in layer_blob:
        return True
    if str(row.get("change_type") or "").lower() in {"dimension", "dimension_modified"}:
        return True
    return bool(DIMENSION_PATTERN.search(blob)) and _has_content_change(row, category="dimension")


def _source_format(
    row: dict[str, Any],
    *,
    source_a: str,
    source_b: str,
    entity_types: Sequence[str],
) -> str:
    explicit = str(row.get("source_format") or "").lower()
    if explicit in {"cad", "pdf"}:
        return explicit
    values = [source_a, source_b, str(row.get("coordinate_source") or ""), " ".join(entity_types)]
    joined = " ".join(values).lower()
    return "pdf" if ".pdf" in joined or "pdf_" in joined or "image_pixels" in joined else "cad"


def _detection_source(row: dict[str, Any], *, source_format: str, entity_types: Sequence[str]) -> str:
    explicit = str(row.get("detection_source") or "").lower()
    if explicit in {"cad_entity", "pdf_text", "pdf_ocr", "pdf_visual", "hybrid"}:
        return explicit
    if source_format != "pdf":
        return "cad_entity"
    blob = " ".join([str(row.get("change_type") or ""), " ".join(entity_types)]).lower()
    sources: set[str] = set()
    if "ocr" in blob or row.get("ocr_confidence"):
        sources.add("pdf_ocr")
    if "text" in blob:
        sources.add("pdf_text")
    if "visual" in blob or "region" in blob or "page" in blob:
        sources.add("pdf_visual")
    if len(sources) > 1:
        return "hybrid"
    return next(iter(sources), "pdf_visual")


def _bbox_status(
    row: dict[str, Any],
    *,
    bbox: Optional[tuple[float, float, float, float]],
    source_format: str,
    overlay: dict[str, Any],
) -> str:
    explicit = str(row.get("bbox_status") or "").lower()
    if explicit in {"exact", "page_fallback", "relative_only"}:
        return explicit
    if bbox or overlay.get("before_bbox_px") or overlay.get("after_bbox_px"):
        return "exact"
    if source_format == "pdf":
        return "page_fallback"
    return "relative_only"


def _review_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    if status in {"confirmed", "false_positive", "hold", "needs_review"}:
        return status
    if status in {"ignored", "rejected"}:
        return "hold"
    if status in {"pending", "review_required", "unreviewed", ""}:
        return "needs_review"
    return "needs_review"


def _has_content_change(row: dict[str, Any], *, category: str) -> bool:
    old_text, new_text = _old_new_text(row)
    if old_text and new_text and old_text != new_text:
        return True
    blob = str(row.get("reasons") or row.get("change_detail") or row.get("change_details") or "")
    if any(sep in blob for sep in ("->", "=>", "→")):
        return True
    if category in {"rebar", "dimension", "member", "grid"} and _int_cell(row.get("modified") or row.get("modified_count")) > 0:
        return True
    return False


def _change_summary_ko(row: dict[str, Any], *, category: str) -> str:
    old_text, new_text = _old_new_text(row)
    labels = {
        "rebar": "배근 간격 변경",
        "dimension": "치수 변경",
        "member": "구조 부재 표기 변경",
        "grid": "그리드/축선 변경",
        "mixed": "구조 핵심 복합 변경",
        "other": "도면 변경 후보",
    }
    label = labels.get(category, "도면 변경 후보")
    if old_text and new_text and old_text != new_text:
        return f"{label}: {old_text} → {new_text}"
    raw = _int_cell(row.get("raw_change_count"))
    if category in STRUCTURAL_CORE_CATEGORIES:
        return f"{label}: 구조 검토가 필요한 변경 후보입니다."
    return f"{label}: 변경 {raw}건"


def _reason_ko(
    row: dict[str, Any],
    *,
    category: str,
    detection_source: str,
    bbox_status: str,
) -> str:
    if category == "rebar":
        base = "철근 직경/간격 패턴이 변경되어 구조 핵심 검토 대상으로 올렸습니다."
    elif category == "dimension":
        base = "치수 또는 숫자 표기가 변경되어 구조 검토가 필요합니다."
    elif category == "member":
        base = "부재명 또는 단면 표기가 변경되어 구조 검토가 필요합니다."
    elif category == "grid":
        base = "그리드/축선 관련 표기가 변경되어 기준 위치 검토가 필요합니다."
    elif category == "mixed":
        base = "여러 구조 핵심 신호가 같은 구역에 함께 나타났습니다."
    else:
        base = ""
    if not base:
        return ""
    source_note = {
        "cad_entity": "CAD 엔티티 비교 결과입니다.",
        "pdf_text": "PDF 텍스트 추출 결과입니다.",
        "pdf_ocr": "PDF OCR 추출 결과입니다.",
        "pdf_visual": "PDF 시각 변화 후보입니다.",
        "hybrid": "PDF 텍스트/OCR/시각 신호가 함께 감지되었습니다.",
    }.get(detection_source, "")
    bbox_note = " 정확 위치를 사용할 수 있습니다." if bbox_status == "exact" else " 정확 위치가 제한되어 우선 검토 큐에 표시합니다."
    return " ".join(part for part in (base, source_note + bbox_note) if part).strip()


def _is_structural_core_category(category: Any) -> bool:
    return str(category or "") in STRUCTURAL_CORE_CATEGORIES


def _repetitive_pattern_for_layers(layers: Sequence[str]) -> tuple[str, str]:
    if not layers:
        return "", ""
    matched: list[tuple[str, str]] = []
    unmatched = []
    for layer in layers:
        name = layer.upper()
        layer_matched = False
        for label, patterns, reason in DEFAULT_REPETITIVE_LAYER_PATTERNS:
            if any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
                matched.append((label, reason))
                layer_matched = True
                break
        if not layer_matched:
            unmatched.append(layer)
    if matched and not unmatched:
        counter = Counter(label for label, _reason in matched)
        label = counter.most_common(1)[0][0]
        reason = next(reason for item_label, reason in matched if item_label == label)
        return label, reason
    return "", ""


def _bbox_from_row(
    row: dict[str, Any],
    *,
    prefix: str = "bbox",
) -> Optional[tuple[float, float, float, float]]:
    try:
        return (
            float(row.get(f"{prefix}_min_x")),
            float(row.get(f"{prefix}_min_y")),
            float(row.get(f"{prefix}_max_x")),
            float(row.get(f"{prefix}_max_y")),
        )
    except (TypeError, ValueError):
        return None


def _bbox_area(bbox: Optional[tuple[float, float, float, float]]) -> float:
    if not bbox:
        return 0.0
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _bbox_text(bbox: Optional[tuple[float, float, float, float]]) -> str:
    if not bbox:
        return ""
    return f"{bbox[0]:.1f}, {bbox[1]:.1f} - {bbox[2]:.1f}, {bbox[3]:.1f}"


def _int_cell(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return default


def _change_type_ko(value: Any) -> str:
    text = str(value or "").lower()
    return {
        "added": "+ 추가",
        "deleted": "- 삭제",
        "modified": "~ 수정",
        "moved": "이동",
        "mixed": "혼합",
    }.get(text, text or "혼합")


def _severity_ko(value: Any) -> str:
    text = str(value or "").lower()
    return {
        "critical": "긴급",
        "high": "높음",
        "medium": "보통",
        "low": "낮음",
    }.get(text, text or "보통")


def _issue_sort_key(issue: dict[str, Any]) -> tuple[float, int, str, str]:
    return (
        -float(issue.get("priority_score") or 0.0),
        -_int_cell(issue.get("raw_change_count")),
        str(issue.get("drawing_number") or ""),
        str(issue.get("zone_id") or ""),
    )
