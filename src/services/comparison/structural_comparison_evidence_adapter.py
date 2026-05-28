"""Adapt drawing comparison diffs into structural evidence packets.

This module treats comparison results as evidence, not as issue conclusions.
It keeps the same bounded packet contract used by structural drawing analysis
so MCP clients can consume comparison differences through the same review and
draft flow.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from src.services.comparison.structural_evidence_analyzer import SCHEMA_VERSION
from src.services.comparison.structural_rule_catalog import classify_domain_patterns


MAX_COMPARISON_EVIDENCE = 30


def build_comparison_evidence_packet(
    comparison_result: Mapping[str, Any],
    *,
    question: str | None = None,
    checklist: Sequence[str] | None = None,
    max_evidence: int = MAX_COMPARISON_EVIDENCE,
    run_id: str | None = None,
    artifact_paths: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build a structural evidence packet from a drawing diff payload."""

    question_text = str(question or "").strip()
    checklist_items = [str(item).strip() for item in checklist or [] if str(item).strip()]
    max_evidence = min(max(0, int(max_evidence or 0)), MAX_COMPARISON_EVIDENCE)
    changed_records = _changed_records(comparison_result)
    evidence = [
        _evidence_from_change(index, change)
        for index, change in enumerate(changed_records[:max_evidence], start=1)
    ]
    source = _source_payload(comparison_result)
    source_health = str(source["source_health"])
    summary = _summary_payload(evidence, comparison_result, source_health)
    issue_suggestions = _issue_suggestions(evidence, comparison_result)

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id or make_comparison_run_id(comparison_result, question_text, checklist_items),
        "status": "ok" if source_health == "parsed" else "partial",
        "source": source,
        "question": {
            "text": question_text,
            "keywords": _extract_keywords(" ".join([question_text, *checklist_items])),
            "checklist": checklist_items,
        },
        "intent": {
            "name": "check_mismatch",
            "confidence": "medium",
            "method": "comparison-diff-adapter-v0.1",
        },
        "summary": summary,
        "issue_suggestions": issue_suggestions,
        "evidence": evidence,
        "diagnostics": {
            "comparison": {
                "schema_version": comparison_result.get("schema_version"),
                "summary": comparison_result.get("summary") or {},
                "warning_count": len(comparison_result.get("warnings") or []),
                "warnings": list(comparison_result.get("warnings") or [])[:20],
            },
            "adapter": {
                "name": "structural_comparison_evidence_adapter",
                "version": "0.1.0",
                "llm_used": False,
            },
        },
        "unsupported_counts": {},
        "artifact_paths": dict(artifact_paths or {}),
    }


def make_comparison_run_id(
    comparison_result: Mapping[str, Any],
    question: str,
    checklist: Sequence[str],
) -> str:
    source_a = comparison_result.get("source_a") or {}
    source_b = comparison_result.get("source_b") or {}
    summary = comparison_result.get("summary") or {}
    payload = "|".join(
        [
            str(source_a.get("path") or source_a.get("file_name") or ""),
            str(source_b.get("path") or source_b.get("file_name") or ""),
            question.strip(),
            "\n".join(checklist),
            repr(sorted(summary.items())),
        ]
    )
    return "sde-cmp-" + hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:16]


def compact_comparison_diff_payload(comparison_result: Mapping[str, Any]) -> dict[str, Any]:
    """Return a bounded comparison artifact without entity snapshots."""

    return {
        "schema_version": comparison_result.get("schema_version"),
        "source_a": comparison_result.get("source_a") or {},
        "source_b": comparison_result.get("source_b") or {},
        "summary": comparison_result.get("summary") or {},
        "changes": [
            _compact_change(change)
            for change in list(comparison_result.get("changes") or [])[:100]
        ],
        "warnings": list(comparison_result.get("warnings") or [])[:20],
        "metadata": comparison_result.get("metadata") or {},
    }


def _changed_records(comparison_result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        change
        for change in comparison_result.get("changes") or []
        if isinstance(change, Mapping) and str(change.get("change_type") or "") != "unchanged"
    ]


def _evidence_from_change(index: int, change: Mapping[str, Any]) -> dict[str, Any]:
    change_type = str(change.get("change_type") or "modified")
    entity_type = str(change.get("entity_type") or "unknown")
    layer = change.get("layer_name") or change.get("layer_id")
    field_changes = _field_changes(change)
    anchor_text = _anchor_text(change, field_changes)
    domain_tags, pattern_matches = classify_domain_patterns(
        entity_type=entity_type,
        text=anchor_text,
        layer_name=str(layer or ""),
    )
    bbox = change.get("bbox")
    center = _center_from_change(change)
    return {
        "evidence_id": f"ev:{index:04d}",
        "anchor_text": anchor_text,
        "anchor_type": "comparison_diff",
        "entity_id": str(change.get("new_entity_id") or change.get("old_entity_id") or change.get("change_id") or ""),
        "entity_type": entity_type,
        "layer": layer,
        "layout": None,
        "block": None,
        "bbox": bbox if isinstance(bbox, Mapping) else None,
        "center": center,
        "nearby_entities": [],
        "domain_tags": domain_tags,
        "pattern_matches": pattern_matches,
        "relevance_score": round(max(0.0, 1.0 - ((index - 1) * 0.01)), 3),
        "confidence": "medium",
        "source_status": "comparison_diff",
        "reason": (
            f"Drawing comparison reported a {change_type} {entity_type} record. "
            "Treat this as review evidence only; a changed record is not an issue conclusion."
        ),
        "source_kind": "comparison_diff",
        "diff_context": {
            "change_type": change_type,
            "old_entity_id": change.get("old_entity_id"),
            "new_entity_id": change.get("new_entity_id"),
            "old_bbox": change.get("old_bbox") if isinstance(change.get("old_bbox"), Mapping) else None,
            "new_bbox": change.get("new_bbox") if isinstance(change.get("new_bbox"), Mapping) else None,
            "field_changes": field_changes,
        },
    }


def _field_changes(change: Mapping[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    geometry_diff = change.get("geometry_diff") or {}
    if isinstance(geometry_diff, Mapping):
        for field in list(geometry_diff.get("fields") or [])[:8]:
            if not isinstance(field, Mapping):
                continue
            row = {
                "path": str(field.get("path") or ""),
                "old": field.get("old"),
                "new": field.get("new"),
            }
            if "delta" in field:
                row["delta"] = field.get("delta")
            if "tolerance" in field:
                row["tolerance"] = field.get("tolerance")
            fields.append(row)
    for field in list(change.get("attribute_diffs") or [])[: max(0, 8 - len(fields))]:
        if isinstance(field, Mapping):
            fields.append(
                {
                    "path": str(field.get("path") or ""),
                    "old": field.get("old"),
                    "new": field.get("new"),
                }
            )
    return fields[:8]


def _anchor_text(change: Mapping[str, Any], field_changes: Sequence[Mapping[str, Any]]) -> str:
    change_type = str(change.get("change_type") or "modified")
    entity_type = str(change.get("entity_type") or "unknown")
    layer = str(change.get("layer_name") or change.get("layer_id") or "unknown layer")
    for field in field_changes:
        if field.get("path") == "geometry.canonical_text":
            return f"{change_type} {entity_type} on {layer}: {field.get('old')} -> {field.get('new')}"
    return f"{change_type} {entity_type} on {layer}"


def _center_from_change(change: Mapping[str, Any]) -> dict[str, float] | None:
    location = change.get("location")
    if isinstance(location, Mapping) and "x" in location and "y" in location:
        return {"x": float(location["x"]), "y": float(location["y"])}
    bbox = change.get("bbox")
    if isinstance(bbox, Mapping):
        try:
            return {
                "x": (float(bbox["min_x"]) + float(bbox["max_x"])) / 2.0,
                "y": (float(bbox["min_y"]) + float(bbox["max_y"])) / 2.0,
            }
        except (KeyError, TypeError, ValueError):
            return None
    return None


def _source_payload(comparison_result: Mapping[str, Any]) -> dict[str, Any]:
    source_a = comparison_result.get("source_a") or {}
    source_b = comparison_result.get("source_b") or {}
    path_a = str(source_a.get("path") or source_a.get("file_name") or "")
    path_b = str(source_b.get("path") or source_b.get("file_name") or "")
    format_a = str(source_a.get("format") or Path(path_a).suffix.lstrip(".") or "unknown")
    format_b = str(source_b.get("format") or Path(path_b).suffix.lstrip(".") or "unknown")
    return {
        "path": f"{path_a} -> {path_b}",
        "format": f"{format_a}+{format_b}",
        "importer": "drawing_compare_engine",
        "import_status": "ok",
        "source_health": "parsed",
        "version": None,
        "entity_count": _safe_int(source_a.get("entity_count")) + _safe_int(source_b.get("entity_count")),
        "layer_count": _safe_int(source_a.get("layer_count")) + _safe_int(source_b.get("layer_count")),
        "bbox": None,
        "elapsed_ms": 0.0,
    }


def _summary_payload(
    evidence: Sequence[Mapping[str, Any]],
    comparison_result: Mapping[str, Any],
    source_health: str,
) -> dict[str, Any]:
    summary = comparison_result.get("summary") or {}
    total_changes = _safe_int(summary.get("total_changes"))
    if total_changes:
        answer = (
            f"Drawing comparison produced {len(evidence)} bounded difference evidence "
            "item(s) for human review."
        )
    else:
        answer = "Drawing comparison produced no bounded difference evidence items."
    return {
        "answer": answer,
        "confidence": "medium",
        "source_health": source_health,
        "judgment_level": "issue_suggestion_only",
        "requires_human_review": True,
        "evidence_count": len(evidence),
        "notes": [
            "A comparison difference is not a final approval or structural issue conclusion.",
            "No structural safety approval, drawing approval, or release decision is made.",
        ],
    }


def _issue_suggestions(
    evidence: Sequence[Mapping[str, Any]],
    comparison_result: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not evidence:
        return []
    summary = comparison_result.get("summary") or {}
    total_changes = _safe_int(summary.get("total_changes"))
    return [
        {
            "suggestion_id": "is:0001",
            "kind": "comparison_diff_review",
            "title": "Review drawing comparison differences before treating them as issues",
            "rationale": (
                f"Comparison reported {total_changes} added/removed/modified record(s). "
                "Differences require manual review before any conclusion."
            ),
            "evidence_ids": [str(item["evidence_id"]) for item in evidence[:8]],
            "confidence": "medium",
            "next_action": "Open the linked evidence and verify the changed anchors against the review request.",
            "human_review_required": True,
            "judgment_level": "issue_suggestion_only",
        }
    ]


def _compact_change(change: Mapping[str, Any]) -> dict[str, Any]:
    keep_keys = (
        "change_id",
        "change_type",
        "entity_type",
        "layer_id",
        "layer_name",
        "old_entity_id",
        "new_entity_id",
        "location",
        "bbox",
        "old_bbox",
        "new_bbox",
        "geometry_diff",
        "attribute_diffs",
        "visualization",
    )
    return {key: change.get(key) for key in keep_keys if key in change}


def _extract_keywords(text: str) -> list[str]:
    tokens = re.findall(r"[\w.+/-]+", text, flags=re.UNICODE)
    result: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        cleaned = token.strip(".,;:()[]{}<>\"'")
        if len(cleaned) < 2:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result[:24]


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "build_comparison_evidence_packet",
    "compact_comparison_diff_payload",
    "make_comparison_run_id",
]
