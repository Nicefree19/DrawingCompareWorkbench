from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from src.services.comparison.structural_review_draft_composer import (
    DRAFT_SCHEMA_VERSION,
    compose_structural_review_draft,
)


ROOT = Path(__file__).resolve().parents[4]
SCHEMA_PATH = ROOT / "docs" / "schemas" / "structural-review-draft-v0.1.schema.json"
FORBIDDEN_POSITIVE_ACTION_PHRASES = (
    "approved",
    "released",
    "send now",
    "submit now",
    "can proceed",
    "approval granted",
    "release is allowed",
)


def _validate_schema(packet: dict) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(packet)


def _assert_no_positive_action_language(draft: dict) -> None:
    text = json.dumps(draft, ensure_ascii=False).casefold()
    for phrase in FORBIDDEN_POSITIVE_ACTION_PHRASES:
        assert phrase not in text


def _evidence_packet() -> dict:
    return {
        "schema_version": "structural-drawing-evidence/v0.1",
        "run_id": "sde-test1234",
        "status": "ok",
        "source": {
            "source_health": "parsed",
        },
        "question": {
            "text": "Check missing section reference for GRID-A1 and C1",
        },
        "summary": {
            "answer": "Found 2 bounded evidence candidate(s) for the question.",
            "judgment_level": "issue_suggestion_only",
        },
        "issue_suggestions": [
            {
                "suggestion_id": "is:0001",
                "kind": "missing_reference_review",
                "title": "Review possible missing or incomplete drawing reference",
                "rationale": "Evidence anchors matched the request.",
                "evidence_ids": ["ev:0001", "ev:0002"],
                "confidence": "medium",
                "next_action": "Compare linked evidence anchors against the target checklist.",
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
            },
            {
                "evidence_id": "ev:0002",
                "anchor_text": "COLUMN C1",
                "layer": "S-COL",
                "domain_tags": ["member_tag"],
            },
        ],
    }


def test_compose_review_draft_uses_only_evidence_packet_contract() -> None:
    packet = _evidence_packet()

    draft = compose_structural_review_draft(packet, language="en")

    _validate_schema(draft)
    assert draft["schema_version"] == DRAFT_SCHEMA_VERSION
    assert draft["source_run_id"] == "sde-test1234"
    assert draft["status"] == "drafted"
    assert draft["language"] == "en"
    assert draft["draft_type"] == "review_note"
    assert draft["safety"] == {
        "llm_used": False,
        "auto_submit_allowed": False,
        "human_review_required": True,
        "judgment_level": "issue_suggestion_only",
        "source_limited": False,
    }
    assert draft["basis"]["evidence_ids"] == ["ev:0001", "ev:0002"]
    assert "GRID-A1" in draft["draft"]["body"]
    assert "COLUMN C1" in draft["draft"]["body"]
    assert "Review Note" in draft["draft"]["subject"]
    _assert_no_positive_action_language(draft)
    assert "canonical_drawing" not in json.dumps(draft, ensure_ascii=False)
    assert draft["artifact_paths"] == {}


def test_compose_review_draft_profiles_have_distinct_shapes_and_ids() -> None:
    packet = _evidence_packet()

    drafts = {
        draft_type: compose_structural_review_draft(
            packet,
            language="en",
            draft_type=draft_type,
        )
        for draft_type in ("review_note", "rfi_reply", "checklist_findings")
    }

    assert len({draft["draft_id"] for draft in drafts.values()}) == 3
    assert "Review Note" in drafts["review_note"]["draft"]["subject"]
    assert "RFI Reply Draft" in drafts["rfi_reply"]["draft"]["subject"]
    assert "Checklist Findings" in drafts["checklist_findings"]["draft"]["subject"]

    for draft_type, draft in drafts.items():
        _validate_schema(draft)
        assert draft["draft_type"] == draft_type
        assert draft["safety"]["auto_submit_allowed"] is False
        assert draft["safety"]["human_review_required"] is True
        assert draft["safety"]["judgment_level"] == "issue_suggestion_only"
        assert draft["draft"]["evidence_references"]
        _assert_no_positive_action_language(draft)


def test_compose_review_draft_blocks_invalid_draft_type() -> None:
    draft = compose_structural_review_draft(
        _evidence_packet(),
        language="en",
        draft_type="send_approval",
    )

    _validate_schema(draft)
    assert draft["status"] == "blocked"
    assert draft["draft_type"] == "review_note"
    assert "Unsupported draft type" in draft["draft"]["body"]
    assert draft["safety"]["auto_submit_allowed"] is False
    _assert_no_positive_action_language(draft)


def test_compose_review_draft_blocks_invalid_source_packet() -> None:
    draft = compose_structural_review_draft({"schema_version": "bad"}, language="en")

    _validate_schema(draft)
    assert draft["status"] == "blocked"
    assert draft["source_run_id"] == "unknown"
    assert draft["draft_type"] == "review_note"
    assert draft["safety"]["auto_submit_allowed"] is False
    assert draft["draft"]["evidence_references"] == []
