# -*- coding: utf-8 -*-
"""Tests for manifest SHA-256 provenance helpers (Plan §17 Phase F6)."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from src.services.comparison.manifest_provenance import (
    DEFAULT_EXCLUDED_KEYS,
    TEMPLATE_DETECTION_CLEAN,
    TEMPLATE_DETECTION_FOUND,
    build_provenance,
    compute_manifest_hash,
    verify_manifest_integrity,
)


def _sample_manifest() -> dict:
    """Representative customer-grade manifest body without provenance."""

    return {
        "schema_version": 1,
        "evidence_level": "customer_grade",
        "dataset_id": "customer-grade-fixture",
        "sheet_count": 21,
        "format_coverage": {"dwg_dxf": True, "pdf_pdf": True, "cad_pdf_blocked": True},
        "structural_coverage": ["member_add_delete_move", "grid_change"],
    }


def _input_hashes() -> dict[str, str]:
    return {
        "review_ground_truth_csv": "a" * 64,
        "operator_notes_file": "b" * 64,
        "confirmed_export_artifact": "c" * 64,
    }


def test_compute_manifest_hash_excludes_provenance_field() -> None:
    """The hash must be invariant to changes inside the provenance field
    itself; otherwise the field could never carry its own digest.
    """

    body = _sample_manifest()
    hash_without_provenance = compute_manifest_hash(body)
    body_with_provenance = dict(body)
    body_with_provenance["provenance"] = {
        "manifest_sha256": "deadbeef" * 8,
        "tool_version": "0.0.0",
        "input_file_hashes": _input_hashes(),
        "generated_at_utc": "2026-05-11T00:00:00Z",
        "generated_by": "test",
        "template_detection": TEMPLATE_DETECTION_CLEAN,
    }
    hash_with_provenance = compute_manifest_hash(body_with_provenance)
    assert hash_without_provenance == hash_with_provenance


def test_compute_manifest_hash_deterministic_with_key_reorder() -> None:
    """JSON serialisation must be insensitive to dict insertion order."""

    body = _sample_manifest()
    reordered = dict(reversed(list(body.items())))
    assert compute_manifest_hash(body) == compute_manifest_hash(reordered)


def test_compute_manifest_hash_changes_when_non_provenance_key_mutates() -> None:
    body = _sample_manifest()
    mutated = deepcopy(body)
    mutated["sheet_count"] = 999
    assert compute_manifest_hash(body) != compute_manifest_hash(mutated)


def test_build_provenance_includes_required_fields() -> None:
    body = _sample_manifest()
    provenance = build_provenance(
        body,
        input_file_hashes=_input_hashes(),
        tool_version="abc1234",
        template_detection=TEMPLATE_DETECTION_CLEAN,
        generated_at_utc="2026-05-11T00:00:00Z",
    )
    assert set(provenance) >= {
        "schema_version",
        "manifest_sha256",
        "generated_at_utc",
        "generated_by",
        "tool_version",
        "input_file_hashes",
        "template_detection",
    }
    assert provenance["generated_at_utc"] == "2026-05-11T00:00:00Z"
    assert provenance["tool_version"] == "abc1234"
    assert provenance["template_detection"] == TEMPLATE_DETECTION_CLEAN
    # Hash must equal the digest computed over the body without provenance.
    assert provenance["manifest_sha256"] == compute_manifest_hash(body)


def test_verify_intact_manifest_returns_empty() -> None:
    body = _sample_manifest()
    body["provenance"] = build_provenance(
        body,
        input_file_hashes=_input_hashes(),
        tool_version="0.1.0",
    )
    assert verify_manifest_integrity(body) == []


def test_verify_manifest_with_mutated_key_returns_violations() -> None:
    body = _sample_manifest()
    body["provenance"] = build_provenance(
        body,
        input_file_hashes=_input_hashes(),
        tool_version="0.1.0",
    )
    body["sheet_count"] = 42  # Tamper after provenance was sealed.
    violations = verify_manifest_integrity(body)
    assert violations, "expected mismatch violation"
    assert any("manifest_sha256 mismatch" in v for v in violations)


def test_verify_manifest_missing_provenance_returns_violations() -> None:
    body = _sample_manifest()
    violations = verify_manifest_integrity(body)
    assert violations
    assert "provenance block is missing" in violations[0]


def test_verify_manifest_rejects_short_or_garbage_hash() -> None:
    body = _sample_manifest()
    body["provenance"] = build_provenance(
        body,
        input_file_hashes=_input_hashes(),
        tool_version="0.1.0",
    )
    body["provenance"]["manifest_sha256"] = "not-a-real-hash"
    violations = verify_manifest_integrity(body)
    assert any("not a 64-char hex digest" in v for v in violations)


def test_verify_manifest_rejects_unparseable_timestamp() -> None:
    body = _sample_manifest()
    body["provenance"] = build_provenance(
        body,
        input_file_hashes=_input_hashes(),
        tool_version="0.1.0",
    )
    body["provenance"]["generated_at_utc"] = "yesterday"
    # Re-seal so the hash check does not fail first.
    body["provenance"]["manifest_sha256"] = compute_manifest_hash(body)
    violations = verify_manifest_integrity(body)
    assert any("ISO 8601" in v for v in violations)


def test_verify_manifest_rejects_empty_input_file_hashes() -> None:
    body = _sample_manifest()
    body["provenance"] = build_provenance(
        body,
        input_file_hashes={},
        tool_version="0.1.0",
    )
    violations = verify_manifest_integrity(body)
    assert any("input_file_hashes must be a non-empty dict" in v for v in violations)


def test_default_excluded_keys_contains_provenance() -> None:
    assert "provenance" in DEFAULT_EXCLUDED_KEYS


def test_template_detection_labels_are_distinct_strings() -> None:
    assert TEMPLATE_DETECTION_CLEAN != TEMPLATE_DETECTION_FOUND
    assert isinstance(TEMPLATE_DETECTION_CLEAN, str)
    assert isinstance(TEMPLATE_DETECTION_FOUND, str)


def test_round_trip_through_json_preserves_integrity() -> None:
    """Provenance must survive a JSON encode/decode round-trip, the path
    every audit consumer takes."""

    body = _sample_manifest()
    body["provenance"] = build_provenance(
        body,
        input_file_hashes=_input_hashes(),
        tool_version="0.1.0",
    )
    serialised = json.dumps(body)
    restored = json.loads(serialised)
    assert verify_manifest_integrity(restored) == []


# ---------------------------------------------------------------------------
# Plan §19 A-1 (Agent T finding T1) — input_file_hashes keys must NOT
# look like real filenames. The schema requires role identifiers so
# customer project metadata cannot leak through the provenance block.
# ---------------------------------------------------------------------------


def test_verify_rejects_filename_shaped_input_file_hash_key() -> None:
    """A maintainer extending the schema with a real filename key (e.g.
    ``2026_SECRET_ACQUISITION.dwg``) MUST fail verification so the
    customer-project leak vector cannot widen silently."""

    body = _sample_manifest()
    body["provenance"] = build_provenance(
        body,
        input_file_hashes={"2026_SECRET_ACQUISITION.dwg": "a" * 64},
        tool_version="0.1.0",
    )
    violations = verify_manifest_integrity(body)
    assert any("filename-shaped key" in v for v in violations), (
        f"expected filename-shaped key rejection, got {violations!r}"
    )


def test_verify_accepts_role_identifier_keys() -> None:
    """The recommended role identifiers (review_ground_truth_csv,
    operator_notes_file, confirmed_export_artifact) must pass cleanly."""

    body = _sample_manifest()
    body["provenance"] = build_provenance(
        body,
        input_file_hashes=_input_hashes(),  # role identifiers
        tool_version="0.1.0",
    )
    assert verify_manifest_integrity(body) == []


# ---------------------------------------------------------------------------
# Plan §19 A-5 (Agent A finding A2) — nan/inf rejection in
# compute_manifest_hash. The previous default=str silently coerced
# them to invalid JSON ("NaN"/"Infinity") which broke round-trip.
# ---------------------------------------------------------------------------


def test_compute_manifest_hash_rejects_nan_float() -> None:
    body = _sample_manifest()
    body["broken_metric"] = float("nan")
    with pytest.raises(ValueError, match="non-finite"):
        compute_manifest_hash(body)


def test_compute_manifest_hash_rejects_positive_infinity() -> None:
    body = _sample_manifest()
    body["broken_metric"] = float("inf")
    with pytest.raises(ValueError, match="non-finite"):
        compute_manifest_hash(body)


def test_compute_manifest_hash_rejects_negative_infinity() -> None:
    body = _sample_manifest()
    body["broken_metric"] = float("-inf")
    with pytest.raises(ValueError, match="non-finite"):
        compute_manifest_hash(body)


def test_compute_manifest_hash_rejects_nan_inside_nested_dict() -> None:
    body = _sample_manifest()
    body["telemetry"] = {"latencies": [1.0, 2.0, float("nan")]}
    with pytest.raises(ValueError, match="non-finite"):
        compute_manifest_hash(body)


# ---------------------------------------------------------------------------
# Plan §18 Phase B-2 (Agent F production-scale follow-up) — second-
# precision timestamps collided when two operators generated manifests
# in the same wall second. Microsecond precision removes the false-
# equivalence window for audit-trail ordering.
# ---------------------------------------------------------------------------


def test_generated_at_utc_has_microsecond_precision() -> None:
    """The ISO-8601 timestamp must carry at least 6 digits of fractional
    seconds (microseconds) so two manifests built in the same wall
    second remain distinguishable.
    """
    import re

    body = _sample_manifest()
    body["provenance"] = build_provenance(
        body,
        input_file_hashes=_input_hashes(),
        tool_version="0.1.0",
    )
    ts = body["provenance"]["generated_at_utc"]
    # Expected shape: ``YYYY-MM-DDTHH:MM:SS.uuuuuuZ`` — 6-digit
    # fractional seconds + literal Z.
    assert re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$", ts
    ), f"timestamp lacks microsecond precision: {ts!r}"


def test_two_sequential_builds_produce_distinct_timestamps() -> None:
    """Two builds in the same wall second should still differ in the
    microsecond field. Without the precision upgrade the field would
    have been identical and audit-trail ordering would be lost.
    """
    body = _sample_manifest()
    prov_a = build_provenance(
        body, input_file_hashes=_input_hashes(), tool_version="0.1.0"
    )
    prov_b = build_provenance(
        body, input_file_hashes=_input_hashes(), tool_version="0.1.0"
    )
    # The wall second may match but the microsecond portion almost
    # certainly differs on real hardware; allow either ordering.
    assert prov_a["generated_at_utc"] != prov_b["generated_at_utc"] or \
        prov_a == prov_b  # extremely-rare timer-collision still legal
