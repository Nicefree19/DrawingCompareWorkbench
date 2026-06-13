from __future__ import annotations

import copy

from scripts import native_cad_version_matrix as matrix


def test_default_version_matrix_validates() -> None:
    payload = matrix.load_matrix()

    issues = matrix.validate_matrix(payload)
    summary = matrix.summarize_matrix(payload)

    assert issues == []
    assert summary["row_count"] == len(matrix.expected_versions())
    assert summary["default_enabled_codes"] == ["AC1015"]
    assert summary["state_counts"]["contracted"] >= 1


def test_matrix_rejects_missing_known_version() -> None:
    payload = matrix.load_matrix()
    payload = copy.deepcopy(payload)
    payload["rows"] = [row for row in payload["rows"] if row["code"] != "AC1032"]

    codes = {issue.code for issue in matrix.validate_matrix(payload)}

    assert "ROW_MISSING" in codes


def test_matrix_rejects_default_enablement_that_broadens_detector_policy() -> None:
    payload = matrix.load_matrix()
    payload = copy.deepcopy(payload)
    for row in payload["rows"]:
        if row["code"] == "AC1032":
            row["default_enabled"] = True
            row["backend_policy"] = "default_cleanroom"

    messages = [issue.message for issue in matrix.validate_matrix(payload)]

    assert any("SUPPORTED_CODES" in message for message in messages)


def test_matrix_rejects_promotion_without_real_evidence() -> None:
    payload = matrix.load_matrix()
    payload = copy.deepcopy(payload)
    for row in payload["rows"]:
        if row["code"] == "AC1027":
            row["state"] = "release_candidate"

    codes = {issue.code for issue in matrix.validate_matrix(payload)}

    assert "ROW_PROMOTION_EVIDENCE" in codes
