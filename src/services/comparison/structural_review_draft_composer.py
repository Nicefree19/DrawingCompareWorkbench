"""Deterministic review-draft composer for structural evidence packets.

This module does not call an LLM and does not approve, submit, or release
anything. It converts a bounded evidence packet into a human-review draft that
can be edited by a user.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Sequence


DRAFT_SCHEMA_VERSION = "structural-review-draft/v0.1"
SOURCE_SCHEMA_VERSION = "structural-drawing-evidence/v0.1"
DEFAULT_DRAFT_TYPE = "review_note"
DRAFT_TYPES = ("review_note", "rfi_reply", "checklist_findings")


def compose_structural_review_draft(
    evidence_packet: Dict[str, Any],
    *,
    language: str = "ko",
    draft_type: str = DEFAULT_DRAFT_TYPE,
    artifact_paths: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    """Build a bounded review draft from a structural evidence packet."""

    language = _normalize_language(language)
    normalized_draft_type = _normalize_draft_type(draft_type)
    if normalized_draft_type is None:
        return _blocked_draft(
            language=language,
            draft_type=DEFAULT_DRAFT_TYPE,
            reason=f"Unsupported draft type: {draft_type}",
        )
    if evidence_packet.get("schema_version") != SOURCE_SCHEMA_VERSION:
        return _blocked_draft(
            language=language,
            draft_type=normalized_draft_type,
            reason="Unsupported evidence packet schema.",
        )

    run_id = str(evidence_packet.get("run_id") or "unknown")
    question = str((evidence_packet.get("question") or {}).get("text") or "")
    source_health = str((evidence_packet.get("source") or {}).get("source_health") or "unknown")
    suggestions = list(evidence_packet.get("issue_suggestions") or [])
    evidence = list(evidence_packet.get("evidence") or [])
    evidence_refs = _evidence_references(evidence)
    suggestion_lines = _suggestion_lines(suggestions)
    limitations = _limitations(source_health)
    subject = _subject(normalized_draft_type, language)
    body = _body(
        normalized_draft_type,
        language=language,
        question=question,
        suggestion_lines=suggestion_lines,
        evidence_refs=evidence_refs,
        limitations=limitations,
    )
    checklist = _review_checklist(normalized_draft_type, language)

    return {
        "schema_version": DRAFT_SCHEMA_VERSION,
        "draft_id": make_draft_id(
            run_id,
            language,
            normalized_draft_type,
            suggestion_lines,
            evidence_refs,
        ),
        "source_run_id": run_id,
        "source_packet_schema": SOURCE_SCHEMA_VERSION,
        "status": "drafted",
        "language": language,
        "draft_type": normalized_draft_type,
        "safety": _safety(source_health),
        "basis": {
            "question": question,
            "source_health": source_health,
            "issue_suggestion_count": len(suggestions),
            "evidence_count": len(evidence_refs),
            "evidence_ids": [item["evidence_id"] for item in evidence_refs],
        },
        "draft": {
            "subject": subject,
            "body": body,
            "review_checklist": checklist,
            "evidence_references": evidence_refs,
            "limitations": limitations,
        },
        "artifact_paths": dict(artifact_paths or {}),
    }


def make_draft_id(
    source_run_id: str,
    language: str,
    draft_type: str,
    suggestion_lines: Sequence[str],
    evidence_refs: Sequence[Dict[str, Any]],
) -> str:
    payload = "|".join(
        [
            source_run_id,
            language,
            draft_type,
            "\n".join(suggestion_lines),
            "\n".join(f"{item['evidence_id']}:{item['anchor_text']}" for item in evidence_refs),
        ]
    )
    return "srd-" + hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:20]


def _blocked_draft(*, language: str, draft_type: str, reason: str) -> Dict[str, Any]:
    draft_type = _normalize_draft_type(draft_type) or DEFAULT_DRAFT_TYPE
    subject = _subject(draft_type, language) + " blocked"
    body = f"Draft generation was blocked: {reason}"
    if language == "ko":
        body = f"초안 생성이 차단되었습니다: {reason}"
    return {
        "schema_version": DRAFT_SCHEMA_VERSION,
        "draft_id": make_draft_id("unknown", language, draft_type, [reason], []),
        "source_run_id": "unknown",
        "source_packet_schema": SOURCE_SCHEMA_VERSION,
        "status": "blocked",
        "language": language,
        "draft_type": draft_type,
        "safety": _safety("failed"),
        "basis": {
            "question": "",
            "source_health": "failed",
            "issue_suggestion_count": 0,
            "evidence_count": 0,
            "evidence_ids": [],
        },
        "draft": {
            "subject": subject,
            "body": body,
            "review_checklist": ["Review source packet and regenerate evidence."],
            "evidence_references": [],
            "limitations": [reason],
        },
        "artifact_paths": {},
    }


def _normalize_language(language: str) -> str:
    return language if language in {"ko", "en"} else "ko"


def _normalize_draft_type(draft_type: str) -> str | None:
    value = str(draft_type or DEFAULT_DRAFT_TYPE)
    if value in DRAFT_TYPES:
        return value
    return None


def _safety(source_health: str) -> Dict[str, Any]:
    return {
        "llm_used": False,
        "auto_submit_allowed": False,
        "human_review_required": True,
        "judgment_level": "issue_suggestion_only",
        "source_limited": source_health != "parsed",
    }


def _subject(draft_type: str, language: str) -> str:
    if language == "ko":
        return {
            "review_note": "구조 도면 근거 검토 메모",
            "rfi_reply": "구조 도면 RFI 회신 초안",
            "checklist_findings": "구조 도면 체크리스트 검토 결과",
        }[draft_type]
    return {
        "review_note": "Structural Drawing Review Note",
        "rfi_reply": "Structural Drawing RFI Reply Draft",
        "checklist_findings": "Structural Drawing Checklist Findings",
    }[draft_type]


def _body(
    draft_type: str,
    *,
    language: str,
    question: str,
    suggestion_lines: Sequence[str],
    evidence_refs: Sequence[Dict[str, Any]],
    limitations: Sequence[str],
) -> str:
    if language == "ko":
        return _korean_body(
            draft_type,
            question=question,
            suggestion_lines=suggestion_lines,
            evidence_refs=evidence_refs,
            limitations=limitations,
        )
    return _english_body(
        draft_type,
        question=question,
        suggestion_lines=suggestion_lines,
        evidence_refs=evidence_refs,
        limitations=limitations,
    )


def _review_checklist(draft_type: str, language: str) -> List[str]:
    if language == "ko":
        base = [
            "도면에서 각 evidence anchor를 직접 확인한다.",
            "주변 태그, 치수, 단면/상세 참조가 제안 이슈를 뒷받침하는지 확인한다.",
            "이 초안은 검토용이며 자동 제출, 승인, 배포 결정을 하지 않는다.",
        ]
        if draft_type == "rfi_reply":
            return [
                "질의 또는 회신 대상 범위를 현장 문서와 대조한다.",
                *base,
            ]
        if draft_type == "checklist_findings":
            return [
                "체크리스트 항목별 evidence id를 확인한다.",
                *base,
            ]
        return base
    base = [
        "Confirm each referenced evidence anchor in the drawing.",
        "Check whether nearby tags, dimensions, and section/detail references support the issue.",
        "Edit this draft before external use; it does not authorize any approval or release decision.",
    ]
    if draft_type == "rfi_reply":
        return [
            "Confirm the question or reply scope against the project record.",
            *base,
        ]
    if draft_type == "checklist_findings":
        return [
            "Confirm each checklist item against the listed evidence ids.",
            *base,
        ]
    return base


def _evidence_references(evidence: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    for item in evidence[:30]:
        evidence_id = str(item.get("evidence_id") or "")
        if not evidence_id:
            continue
        refs.append(
            {
                "evidence_id": evidence_id,
                "anchor_text": str(item.get("anchor_text") or ""),
                "layer": item.get("layer"),
                "domain_tags": [str(tag) for tag in item.get("domain_tags") or []],
            }
        )
    return refs


def _suggestion_lines(suggestions: Sequence[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    for suggestion in suggestions[:5]:
        title = str(suggestion.get("title") or suggestion.get("kind") or "Review evidence")
        rationale = str(suggestion.get("rationale") or "").strip()
        next_action = str(suggestion.get("next_action") or "").strip()
        line = title
        if rationale:
            line += f" - {rationale}"
        if next_action:
            line += f" Next: {next_action}"
        lines.append(line)
    return lines


def _limitations(source_health: str) -> List[str]:
    limitations = [
        "This draft is based only on bounded evidence packet fields.",
        "No structural safety approval, drawing approval, or release decision is made.",
    ]
    if source_health != "parsed":
        limitations.append(f"Source health is {source_health}; evidence may be incomplete.")
    return limitations


def _korean_body(
    draft_type: str,
    *,
    question: str,
    suggestion_lines: Sequence[str],
    evidence_refs: Sequence[Dict[str, Any]],
    limitations: Sequence[str],
) -> str:
    lines = [
        f"문서 유형: {_subject(draft_type, 'ko')}",
        "이 문서는 구조 도면 evidence packet에 포함된 제한된 근거만 사용한 검토용 초안입니다.",
        "최종 승인, 구조 안전 판정, SHOP/fabrication release 결정이 아닙니다.",
    ]
    if draft_type == "rfi_reply":
        lines.append("RFI 회신 또는 질의응답에 사용할 수 있도록 확인 필요 사항을 정리합니다.")
    elif draft_type == "checklist_findings":
        lines.append("체크리스트 검토 결과 형식으로 근거와 확인 사항을 정리합니다.")
    if question:
        lines.append(f"검토 요청: {question}")
    _append_common_sections(lines, suggestion_lines, evidence_refs, limitations)
    return "\n".join(lines)


def _english_body(
    draft_type: str,
    *,
    question: str,
    suggestion_lines: Sequence[str],
    evidence_refs: Sequence[Dict[str, Any]],
    limitations: Sequence[str],
) -> str:
    lines = [
        f"Draft type: {_subject(draft_type, 'en')}",
        "This is a human-review draft based only on the structural drawing evidence packet.",
        "It is not a final approval, safety judgment, or SHOP/fabrication release decision.",
    ]
    if draft_type == "rfi_reply":
        lines.append("Use this as an RFI reply draft after manually checking each referenced anchor.")
    elif draft_type == "checklist_findings":
        lines.append("Use this as checklist findings after manually checking each referenced anchor.")
    if question:
        lines.append(f"Review request: {question}")
    _append_common_sections(lines, suggestion_lines, evidence_refs, limitations)
    return "\n".join(lines)


def _append_common_sections(
    lines: List[str],
    suggestion_lines: Sequence[str],
    evidence_refs: Sequence[Dict[str, Any]],
    limitations: Sequence[str],
) -> None:
    lines.append("")
    lines.append("Review suggestions:")
    lines.extend(f"- {line}" for line in suggestion_lines or ["Manually review the evidence anchors."])
    lines.append("")
    lines.append("Evidence references:")
    for ref in evidence_refs[:8]:
        tags = ", ".join(ref["domain_tags"]) or "untagged"
        lines.append(
            f"- {ref['evidence_id']}: {ref['anchor_text']} "
            f"(layer={ref['layer']}, tags={tags})"
        )
    lines.append("")
    lines.append("Limitations:")
    lines.extend(f"- {item}" for item in limitations)


__all__ = [
    "DRAFT_SCHEMA_VERSION",
    "SOURCE_SCHEMA_VERSION",
    "DRAFT_TYPES",
    "compose_structural_review_draft",
    "make_draft_id",
]
