"""Deterministic structural drawing evidence packet builder.

This module does not call an LLM. It converts an ImportPipelineResult into a
bounded evidence packet that an MCP client can reason over without receiving a
full CanonicalDrawing dump.
"""
from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.services.comparison.structural_rule_catalog import (
    STRUCTURAL_DOMAIN_RULESET_VERSION,
    classify_domain_patterns,
    is_structural_layer,
    looks_like_reference,
)


SCHEMA_VERSION = "structural-drawing-evidence/v0.1"
ANALYZER_VERSION = "0.1.0"
MAX_EVIDENCE_ITEMS = 30
MAX_NEARBY_ENTITIES = 8

_TEXT_ANCHOR_TYPES = {
    "text",
    "mtext",
    "dimension",
    "block_reference",
}
_STOPWORDS = {
    "a",
    "an",
    "and",
    "around",
    "at",
    "by",
    "check",
    "drawing",
    "find",
    "for",
    "in",
    "near",
    "of",
    "on",
    "please",
    "review",
    "show",
    "the",
    "to",
}


def analyze_structural_evidence(
    import_result: Any,
    *,
    question: str | None = None,
    checklist: Sequence[str] | None = None,
    max_evidence: int = MAX_EVIDENCE_ITEMS,
    run_id: str | None = None,
    artifact_paths: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Build a schema-compatible structural evidence packet.

    The packet is intentionally compact: it exposes anchor text, location,
    nearby entity summaries, diagnostics, and unsupported counts, but not the
    full CanonicalDrawing payload.
    """

    max_evidence = min(max(0, int(max_evidence or 0)), MAX_EVIDENCE_ITEMS)
    drawing = import_result.normalized_drawing or import_result.canonical_drawing or {}
    question_text = (question or "").strip()
    checklist_items = [str(item).strip() for item in checklist or [] if str(item).strip()]
    keywords = _extract_keywords(" ".join([question_text, *checklist_items]))
    intent_name, intent_confidence = _classify_intent(question_text, checklist_items)
    unsupported_counts = _unsupported_counts(import_result.import_report)
    source_health = _source_health(import_result)
    source = _source_payload(import_result, drawing, source_health)
    diagnostics = _diagnostics_payload(import_result)

    evidence = _build_evidence(
        drawing,
        keywords=keywords,
        source_status=source_health,
        max_evidence=max_evidence,
    )

    packet_status = _packet_status(import_result)
    summary = _summary_payload(
        status=packet_status,
        source_health=source_health,
        evidence=evidence,
        unsupported_counts=unsupported_counts,
        question_text=question_text,
    )
    issue_suggestions = _issue_suggestions_payload(
        intent_name=intent_name,
        evidence=evidence,
        unsupported_counts=unsupported_counts,
        source_health=source_health,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id or make_run_id(
            source.get("path") or "",
            question_text,
            checklist_items,
            {"max_evidence": max_evidence, "analyzer_version": ANALYZER_VERSION},
        ),
        "status": packet_status,
        "source": source,
        "question": {
            "text": question_text,
            "keywords": keywords,
            "checklist": checklist_items,
        },
        "intent": {
            "name": intent_name,
            "confidence": intent_confidence,
            "method": "deterministic-rule-v0.1",
        },
        "summary": summary,
        "issue_suggestions": issue_suggestions,
        "evidence": evidence,
        "diagnostics": diagnostics,
        "unsupported_counts": unsupported_counts,
        "artifact_paths": dict(artifact_paths or {}),
    }


def make_run_id(
    source_path: str,
    question: str,
    checklist: Sequence[str],
    options: Dict[str, Any],
) -> str:
    payload = "|".join(
        [
            str(Path(source_path)).lower(),
            question.strip(),
            "\n".join(checklist),
            repr(sorted(options.items())),
        ]
    )
    return "sde-" + hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:20]


def _extract_keywords(text: str) -> List[str]:
    if not text.strip():
        return []
    tokens = re.findall(r"[\w.+/-]+", text, flags=re.UNICODE)
    result: List[str] = []
    seen: set[str] = set()
    for token in tokens:
        cleaned = token.strip(".,;:()[]{}<>\"'")
        if len(cleaned) < 2:
            continue
        key = cleaned.casefold()
        if key in _STOPWORDS or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result[:24]


def _classify_intent(question: str, checklist: Sequence[str]) -> Tuple[str, str]:
    text = " ".join([question, *checklist]).casefold()
    if not text.strip():
        return "general_review", "medium"
    if any(word in text for word in ("missing", "omit", "absent", "not found")):
        return "check_missing", "medium"
    if any(word in text for word in ("mismatch", "different", "conflict", "inconsistent")):
        return "check_mismatch", "medium"
    if any(word in text for word in ("reference", "section", "detail", "ref")):
        return "check_reference", "medium"
    return "find", "medium"


def _source_health(import_result: Any) -> str:
    status = str(getattr(import_result, "status", "") or "").lower()
    error_code = str(getattr(import_result, "error_code", "") or "")
    if status == "ok":
        return "parsed"
    if status == "partial":
        return "partial"
    if "UNSUPPORTED" in error_code:
        return "unsupported"
    return "failed"


def _packet_status(import_result: Any) -> str:
    status = str(getattr(import_result, "status", "") or "").lower()
    if status in {"ok", "partial"}:
        return status
    return "failed"


def _source_payload(import_result: Any, drawing: Dict[str, Any], source_health: str) -> Dict[str, Any]:
    compact = import_result.to_dict()
    source = (drawing.get("drawing") or {}).get("source") or {}
    return {
        "path": str(getattr(import_result, "source_path", "") or source.get("path") or ""),
        "format": str(getattr(import_result, "source_format", "") or source.get("format") or ""),
        "importer": str(getattr(import_result, "importer", "") or ""),
        "import_status": str(getattr(import_result, "status", "") or ""),
        "source_health": source_health,
        "version": compact.get("version"),
        "entity_count": int(compact.get("entity_count") or 0),
        "layer_count": int(compact.get("layer_count") or 0),
        "bbox": compact.get("bbox"),
        "elapsed_ms": float(getattr(import_result, "elapsed_ms", 0.0) or 0.0),
    }


def _diagnostics_payload(import_result: Any) -> Dict[str, Any]:
    report = dict(getattr(import_result, "import_report", {}) or {})
    warnings = list(getattr(import_result, "warnings", []) or [])
    return {
        "error_code": getattr(import_result, "error_code", None),
        "message": getattr(import_result, "message", "") or "",
        "warning_count": len(warnings),
        "warnings": warnings[:20],
        "import_stats": report.get("stats") or {},
        "normalization_report": _compact_normalization_report(
            getattr(import_result, "normalization_report", None)
        ),
        "analyzer": {
            "name": "structural_evidence_analyzer",
            "version": ANALYZER_VERSION,
            "rule_catalog_version": STRUCTURAL_DOMAIN_RULESET_VERSION,
            "llm_used": False,
        },
    }


def _compact_normalization_report(report: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(report, dict):
        return None
    return {
        key: value
        for key, value in report.items()
        if key != "changes"
    }


def _unsupported_counts(report: Dict[str, Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in report.get("unsupported_entities") or []:
        raw_type = str(item.get("raw_type") or "UNKNOWN")
        counts[raw_type] = counts.get(raw_type, 0) + int(item.get("count") or 0)
    return counts


def _build_evidence(
    drawing: Dict[str, Any],
    *,
    keywords: Sequence[str],
    source_status: str,
    max_evidence: int,
) -> List[Dict[str, Any]]:
    if max_evidence <= 0:
        return []
    entities = list(drawing.get("entities") or [])
    layers = _layers_by_id(drawing)
    anchors = [entity for entity in entities if _anchor_text(entity)]
    drawing_bbox = drawing.get("extents") if isinstance(drawing.get("extents"), dict) else None
    scored = []
    for entity in anchors:
        text = _anchor_text(entity)
        score, reason, domain_tags, pattern_matches = _score_anchor(entity, text, keywords, layers)
        scored.append((score, reason, domain_tags, pattern_matches, entity))
    scored.sort(key=lambda item: (-item[0], _entity_id(item[4])))

    evidence: List[Dict[str, Any]] = []
    for idx, (score, reason, domain_tags, pattern_matches, entity) in enumerate(
        scored[:max_evidence],
        start=1,
    ):
        bbox = _bbox2(entity.get("bbox"))
        center = _center(bbox)
        nearby = _nearby_entities(
            entity,
            entities,
            layers,
            drawing_bbox=drawing_bbox,
        )
        evidence.append(
            {
                "evidence_id": f"ev:{idx:04d}",
                "anchor_text": _anchor_text(entity),
                "anchor_type": _anchor_type(entity),
                "entity_id": _entity_id(entity),
                "entity_type": str(entity.get("type") or ""),
                "layer": _layer_name(entity, layers),
                "layout": entity.get("layout_name") or (entity.get("source") or {}).get("layout_name"),
                "block": entity.get("block_id") or (entity.get("source") or {}).get("block_name"),
                "bbox": entity.get("bbox"),
                "center": center,
                "nearby_entities": nearby,
                "domain_tags": domain_tags,
                "pattern_matches": pattern_matches,
                "relevance_score": round(float(score), 3),
                "confidence": _confidence(score, source_status),
                "source_status": source_status,
                "reason": reason,
                "source_kind": "drawing_anchor",
            }
        )
    return evidence


def _score_anchor(
    entity: Dict[str, Any],
    text: str,
    keywords: Sequence[str],
    layers: Dict[str, Dict[str, Any]],
) -> Tuple[float, str, List[str], List[Dict[str, str]]]:
    score = 1.0
    reasons: List[str] = []
    text_key = text.casefold()
    matched = [keyword for keyword in keywords if keyword.casefold() in text_key]
    if matched:
        score += min(8.0, 3.0 * len(matched))
        reasons.append("Matched keyword(s): " + ", ".join(matched[:4]))
    else:
        reasons.append("Selected text/tag anchor for structural review")
    entity_type = str(entity.get("type") or "")
    if entity_type in {"dimension", "block_reference"}:
        score += 1.25
        reasons.append(f"Anchor type is {entity_type}")
    layer_name = _layer_name(entity, layers) or ""
    if is_structural_layer(layer_name):
        score += 0.75
        reasons.append("Layer name has structural hint")
    domain_tags, pattern_matches = classify_domain_patterns(
        entity_type=entity_type,
        text=text,
        layer_name=layer_name,
    )
    if domain_tags:
        score += 1.0
        reasons.append("Matched structural domain pattern(s): " + ", ".join(domain_tags[:4]))
    if any(match.get("confidence") == "high" for match in pattern_matches):
        score += 0.75
    if looks_like_reference(text):
        score += 0.75
        reasons.append("Anchor text looks like reference/member notation")
    return score, "; ".join(reasons), domain_tags, pattern_matches


def _anchor_text(entity: Dict[str, Any]) -> str:
    entity_type = str(entity.get("type") or "")
    geometry = entity.get("geometry") if isinstance(entity.get("geometry"), dict) else {}
    if entity_type not in _TEXT_ANCHOR_TYPES:
        return ""
    for key in (
        "canonical_text",
        "plain_text",
        "text",
        "text_override",
        "measurement_text",
        "block_name",
        "name",
    ):
        value = geometry.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    source = entity.get("source") if isinstance(entity.get("source"), dict) else {}
    block_name = source.get("block_name")
    if entity_type == "block_reference" and block_name:
        return str(block_name)
    return ""


def _anchor_type(entity: Dict[str, Any]) -> str:
    entity_type = str(entity.get("type") or "")
    if entity_type in {"text", "mtext"}:
        return "text"
    if entity_type == "dimension":
        return "dimension"
    if entity_type == "block_reference":
        return "block"
    return "entity"


def _nearby_entities(
    anchor: Dict[str, Any],
    entities: Sequence[Dict[str, Any]],
    layers: Dict[str, Dict[str, Any]],
    *,
    drawing_bbox: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    anchor_bbox = _bbox2(anchor.get("bbox"))
    anchor_center = _center(anchor_bbox)
    radius = _dynamic_radius(anchor_bbox, drawing_bbox)
    rows = []
    for entity in entities:
        if entity is anchor:
            continue
        bbox = _bbox2(entity.get("bbox"))
        distance = _distance(anchor_center, _center(bbox))
        if distance > radius:
            continue
        rows.append((distance, entity))
    rows.sort(key=lambda item: (item[0], _entity_id(item[1])))
    return [
        {
            "entity_id": _entity_id(entity),
            "entity_type": str(entity.get("type") or ""),
            "layer": _layer_name(entity, layers),
            "distance": round(float(distance), 3),
            "bbox": entity.get("bbox"),
            "text": _anchor_text(entity) or None,
        }
        for distance, entity in rows[:MAX_NEARBY_ENTITIES]
    ]


def _dynamic_radius(anchor_bbox: Dict[str, float], drawing_bbox: Optional[Dict[str, Any]]) -> float:
    width = max(0.0, anchor_bbox["max_x"] - anchor_bbox["min_x"])
    height = max(0.0, anchor_bbox["max_y"] - anchor_bbox["min_y"])
    anchor_diag = math.hypot(width, height)
    drawing_diag = 0.0
    if drawing_bbox:
        drawing = _bbox2(drawing_bbox)
        drawing_diag = math.hypot(
            drawing["max_x"] - drawing["min_x"],
            drawing["max_y"] - drawing["min_y"],
        )
    return max(10.0, anchor_diag * 6.0, drawing_diag * 0.08)


def _summary_payload(
    *,
    status: str,
    source_health: str,
    evidence: Sequence[Dict[str, Any]],
    unsupported_counts: Dict[str, int],
    question_text: str,
) -> Dict[str, Any]:
    evidence_count = len(evidence)
    if status == "failed":
        answer = "Drawing import failed; review diagnostics before using this result."
        confidence = "low"
    elif evidence_count == 0:
        answer = "No direct text/tag evidence anchors were found in the parsed drawing."
        confidence = "low"
    elif question_text:
        answer = f"Found {evidence_count} bounded evidence candidate(s) for the question."
        confidence = "medium" if source_health != "parsed" else "high"
    else:
        answer = f"Found {evidence_count} text/tag anchor(s) for structural review."
        confidence = "medium" if source_health != "parsed" else "high"
    notes = [
        "Judgment is limited to issue suggestion and evidence retrieval.",
        "No structural safety approval or drawing release decision is made.",
    ]
    if unsupported_counts:
        notes.append("Unsupported entity counts are present; review may be incomplete.")
    if source_health != "parsed":
        notes.append(f"Source health is {source_health}.")
    return {
        "answer": answer,
        "confidence": confidence,
        "source_health": source_health,
        "judgment_level": "issue_suggestion_only",
        "requires_human_review": True,
        "evidence_count": evidence_count,
        "notes": notes,
    }


def _issue_suggestions_payload(
    *,
    intent_name: str,
    evidence: Sequence[Dict[str, Any]],
    unsupported_counts: Dict[str, int],
    source_health: str,
) -> List[Dict[str, Any]]:
    suggestions: List[Dict[str, Any]] = []
    evidence_ids = [str(item.get("evidence_id") or "") for item in evidence if item.get("evidence_id")]
    if evidence_ids:
        kind, title, next_action = _primary_issue_suggestion(intent_name)
        suggestions.append(
            _issue_suggestion(
                index=len(suggestions) + 1,
                kind=kind,
                title=title,
                rationale=(
                    "Evidence anchors matched the request, but the result is only a review "
                    "candidate and does not prove drawing correctness."
                ),
                evidence_ids=evidence_ids[:8],
                confidence="medium" if source_health == "parsed" else "low",
                next_action=next_action,
            )
        )
    if unsupported_counts:
        unsupported_summary = ", ".join(
            f"{name}:{count}" for name, count in sorted(unsupported_counts.items())
        )
        suggestions.append(
            _issue_suggestion(
                index=len(suggestions) + 1,
                kind="unsupported_content_review",
                title="Review unsupported drawing content before relying on this packet",
                rationale=f"Unsupported entity counts were reported: {unsupported_summary}.",
                evidence_ids=[],
                confidence="low",
                next_action="Open diagnostics and decide whether manual CAD review is required.",
            )
        )
    if source_health != "parsed":
        suggestions.append(
            _issue_suggestion(
                index=len(suggestions) + 1,
                kind="source_health_review",
                title="Review source health before using evidence suggestions",
                rationale=f"Source health is {source_health}; parsed evidence may be incomplete.",
                evidence_ids=evidence_ids[:8],
                confidence="low",
                next_action="Resolve import/source diagnostics or confirm limitations manually.",
            )
        )
    return suggestions[:5]


def _primary_issue_suggestion(intent_name: str) -> Tuple[str, str, str]:
    if intent_name == "check_missing":
        return (
            "missing_reference_review",
            "Review possible missing or incomplete drawing reference",
            "Compare the linked evidence anchors against the target checklist or sheet context.",
        )
    if intent_name == "check_mismatch":
        return (
            "mismatch_review",
            "Review possible mismatch between drawing evidence anchors",
            "Compare nearby tags, dimensions, and references before deciding whether a mismatch exists.",
        )
    if intent_name == "check_reference":
        return (
            "reference_review",
            "Review section/detail/reference evidence",
            "Trace the referenced sheet/detail manually before using it as a basis for response.",
        )
    return (
        "evidence_review",
        "Review matched structural drawing evidence",
        "Inspect the linked evidence anchors and nearby context before forming a conclusion.",
    )


def _issue_suggestion(
    *,
    index: int,
    kind: str,
    title: str,
    rationale: str,
    evidence_ids: Sequence[str],
    confidence: str,
    next_action: str,
) -> Dict[str, Any]:
    return {
        "suggestion_id": f"is:{index:04d}",
        "kind": kind,
        "title": title,
        "rationale": rationale,
        "evidence_ids": list(evidence_ids),
        "confidence": confidence,
        "next_action": next_action,
        "human_review_required": True,
        "judgment_level": "issue_suggestion_only",
    }


def _layers_by_id(drawing: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(layer.get("id")): layer
        for layer in drawing.get("layers") or []
        if layer.get("id") is not None
    }


def _layer_name(entity: Dict[str, Any], layers: Dict[str, Dict[str, Any]]) -> Optional[str]:
    layer_id = entity.get("layer_id")
    if layer_id is not None:
        layer = layers.get(str(layer_id))
        if layer:
            return str(layer.get("name") or layer_id)
    source = entity.get("source") if isinstance(entity.get("source"), dict) else {}
    layer = source.get("layer")
    return str(layer) if layer is not None else None


def _entity_id(entity: Dict[str, Any]) -> str:
    return str(entity.get("id") or "")


def _bbox2(value: Any) -> Dict[str, float]:
    if not isinstance(value, dict):
        return {"min_x": 0.0, "min_y": 0.0, "max_x": 0.0, "max_y": 0.0}
    return {
        "min_x": float(value.get("min_x", 0.0) or 0.0),
        "min_y": float(value.get("min_y", 0.0) or 0.0),
        "max_x": float(value.get("max_x", 0.0) or 0.0),
        "max_y": float(value.get("max_y", 0.0) or 0.0),
    }


def _center(bbox: Dict[str, float]) -> Dict[str, float]:
    return {
        "x": (bbox["min_x"] + bbox["max_x"]) / 2.0,
        "y": (bbox["min_y"] + bbox["max_y"]) / 2.0,
    }


def _distance(a: Dict[str, float], b: Dict[str, float]) -> float:
    return math.hypot(float(a["x"]) - float(b["x"]), float(a["y"]) - float(b["y"]))


def _confidence(score: float, source_status: str) -> str:
    if source_status != "parsed":
        return "low" if score < 5.0 else "medium"
    if score >= 5.0:
        return "high"
    if score >= 2.0:
        return "medium"
    return "low"


__all__ = [
    "ANALYZER_VERSION",
    "SCHEMA_VERSION",
    "analyze_structural_evidence",
    "make_run_id",
]
