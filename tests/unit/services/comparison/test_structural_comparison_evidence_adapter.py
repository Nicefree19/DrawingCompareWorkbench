from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from src.services.comparison.structural_comparison_evidence_adapter import (
    build_comparison_evidence_packet,
)
from src.services.comparison.structural_output_safety import (
    find_structural_output_safety_findings,
)
from src.services.comparison.structural_review_draft_composer import (
    compose_structural_review_draft,
)


ROOT = Path(__file__).resolve().parents[4]
SCHEMA_PATH = ROOT / "docs" / "schemas" / "structural-drawing-evidence-v0.1.schema.json"


def _validate_schema(packet: dict) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(packet)


def _comparison_payload() -> dict:
    return {
        "schema_version": "drawing-diff/v1",
        "source_a": {"path": "before.dxf", "format": "dxf", "entity_count": 2},
        "source_b": {"path": "after.dxf", "format": "dxf", "entity_count": 2},
        "summary": {
            "added": 0,
            "removed": 0,
            "modified": 1,
            "unchanged": 1,
            "total_changes": 1,
            "total_records": 2,
        },
        "changes": [
            {
                "change_id": "chg:0001",
                "change_type": "modified",
                "entity_type": "text",
                "layer_name": "S-BEAM",
                "old_entity_id": "text:old",
                "new_entity_id": "text:new",
                "location": {"x": 10.0, "y": 20.0},
                "bbox": {"min_x": 10.0, "min_y": 20.0, "max_x": 35.0, "max_y": 23.0},
                "old_bbox": {"min_x": 10.0, "min_y": 20.0, "max_x": 30.0, "max_y": 23.0},
                "new_bbox": {"min_x": 10.0, "min_y": 20.0, "max_x": 35.0, "max_y": 23.0},
                "geometry_diff": {
                    "entity_type": "text",
                    "changed": True,
                    "categories": ["text"],
                    "fields": [
                        {
                            "path": "geometry.canonical_text",
                            "old": "B1",
                            "new": "B2",
                        }
                    ],
                    "metrics": {},
                },
                "attribute_diffs": [],
            }
        ],
        "warnings": [],
        "metadata": {"deterministic": True},
    }


def test_comparison_diff_becomes_schema_valid_bounded_evidence_packet() -> None:
    packet = build_comparison_evidence_packet(
        _comparison_payload(),
        question="Review beam tag changes",
        checklist=["Confirm changed member tags manually"],
        run_id="sde-comparetest",
    )

    _validate_schema(packet)
    assert find_structural_output_safety_findings(packet) == []
    assert packet["source"]["source_health"] == "parsed"
    assert packet["summary"]["judgment_level"] == "issue_suggestion_only"
    assert packet["issue_suggestions"][0]["kind"] == "comparison_diff_review"
    assert packet["issue_suggestions"][0]["human_review_required"] is True
    assert packet["evidence"][0]["source_kind"] == "comparison_diff"
    assert packet["evidence"][0]["diff_context"]["change_type"] == "modified"
    assert packet["evidence"][0]["diff_context"]["field_changes"][0] == {
        "path": "geometry.canonical_text",
        "old": "B1",
        "new": "B2",
    }
    assert "changed" in packet["evidence"][0]["reason"].casefold()
    assert "incorrect" not in packet["evidence"][0]["reason"].casefold()


def test_comparison_diff_packet_can_drive_existing_draft_composer() -> None:
    packet = build_comparison_evidence_packet(_comparison_payload(), run_id="sde-comparetest")

    draft = compose_structural_review_draft(
        packet,
        language="en",
        draft_type="checklist_findings",
    )

    assert draft["status"] == "drafted"
    assert draft["safety"]["auto_submit_allowed"] is False
    assert draft["basis"]["evidence_ids"] == ["ev:0001"]
    assert draft["draft"]["evidence_references"][0]["evidence_id"] == "ev:0001"


def test_comparison_diff_packet_caps_evidence_at_30() -> None:
    payload = _comparison_payload()
    payload["changes"] = [
        {**payload["changes"][0], "change_id": f"chg:{index:04d}"}
        for index in range(40)
    ]

    packet = build_comparison_evidence_packet(payload, max_evidence=30)

    _validate_schema(packet)
    assert len(packet["evidence"]) == 30
    assert packet["summary"]["evidence_count"] == 30
    assert len(packet["issue_suggestions"][0]["evidence_ids"]) == 8


def test_comparison_without_changes_does_not_claim_final_approval() -> None:
    payload = _comparison_payload()
    payload["summary"]["modified"] = 0
    payload["summary"]["total_changes"] = 0
    payload["changes"] = []

    packet = build_comparison_evidence_packet(payload)

    _validate_schema(packet)
    assert packet["evidence"] == []
    assert packet["issue_suggestions"] == []
    assert "not a final approval" in packet["summary"]["notes"][0].casefold()
    assert find_structural_output_safety_findings(packet) == []
