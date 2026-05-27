from __future__ import annotations

from src.services.comparison.structural_output_safety import (
    find_structural_output_safety_findings,
)
from src.services.comparison.structural_review_draft_composer import (
    compose_structural_review_draft,
)


def _safe_evidence_packet() -> dict:
    return {
        "schema_version": "structural-drawing-evidence/v0.1",
        "run_id": "sde-test1234",
        "status": "ok",
        "source": {"source_health": "parsed"},
        "summary": {
            "answer": "Found bounded evidence candidates for human review.",
            "judgment_level": "issue_suggestion_only",
            "requires_human_review": True,
        },
        "issue_suggestions": [
            {
                "suggestion_id": "is:0001",
                "kind": "reference_review",
                "title": "Review drawing reference candidate",
                "rationale": "Evidence anchors matched the request.",
                "evidence_ids": ["ev:0001"],
                "confidence": "medium",
                "next_action": "Manually verify the referenced anchor.",
                "human_review_required": True,
                "judgment_level": "issue_suggestion_only",
            }
        ],
        "evidence": [
            {
                "evidence_id": "ev:0001",
                "anchor_text": "GRID-A1",
                "layer": "S-GRID",
                "domain_tags": ["grid"],
            }
        ],
    }


def test_output_safety_accepts_bounded_evidence_and_review_draft() -> None:
    evidence_packet = _safe_evidence_packet()
    draft = compose_structural_review_draft(
        evidence_packet,
        language="en",
        draft_type="rfi_reply",
    )

    assert find_structural_output_safety_findings(evidence_packet) == []
    assert find_structural_output_safety_findings(draft) == []


def test_output_safety_detects_raw_payload_keys() -> None:
    payload = _safe_evidence_packet()
    payload["canonical_drawing"] = {"entities": [{"type": "LINE"}]}

    findings = find_structural_output_safety_findings(payload)

    assert any(finding["code"] == "raw_payload_key" for finding in findings)


def test_output_safety_detects_secret_like_markers() -> None:
    payload = _safe_evidence_packet()
    marker_name = "pass" + "word"
    payload["diagnostics"] = {"message": f"{marker_name}='not-a-real-secret-value'"}

    findings = find_structural_output_safety_findings(payload)

    assert any(finding["code"] == "secret_like_marker" for finding in findings)


def test_output_safety_detects_positive_action_language() -> None:
    payload = _safe_evidence_packet()
    payload["summary"]["answer"] = "The drawing is approved and can proceed."

    findings = find_structural_output_safety_findings(payload)

    assert any(finding["code"] == "positive_action_language" for finding in findings)


def test_output_safety_detects_failed_source_all_clear_wording() -> None:
    payload = _safe_evidence_packet()
    payload["source"]["source_health"] = "failed"
    payload["summary"]["answer"] = "No issues found."

    findings = find_structural_output_safety_findings(payload)

    assert any(finding["code"] == "source_health_misleading_answer" for finding in findings)
