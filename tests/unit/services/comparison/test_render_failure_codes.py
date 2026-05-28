# -*- coding: utf-8 -*-
"""Unit tests for RenderFailureCode enum + info table (S1.1)."""

from __future__ import annotations

from src.services.comparison.render_failure_codes import (
    ALL_FAILURE_CODES,
    ERROR_CODES,
    FAILURE_CODE_INFO,
    HIDDEN_CODES,
    INFO_CODES,
    SEVERITIES,
    USER_ACTION_REQUIRED_CODES,
    WARN_CODES,
    describe,
    highest_severity,
    info_for,
    is_valid_code,
    severity_of,
    to_payload,
)


def test_eleven_codes_present() -> None:
    """The taxonomy must have exactly 11 codes (S1.1 + S1.3.1 contract).

    S1.3.1 added ``dwg_vector_normalise_failed`` to distinguish a
    failed-then-cached path (warn) from normal DWG cache reuse (info).
    """

    assert len(ALL_FAILURE_CODES) == 11
    assert "ok" in ALL_FAILURE_CODES
    assert set(ALL_FAILURE_CODES) == {
        "ok",
        "dwg_unsupported_version",
        "vector_draw_partial",
        "vector_draw_failed",
        "backend_fallback_qquickwidget",
        "backend_fallback_canvas_skeleton",
        "ai_heuristic_fallback",
        "dwg_using_cached_dxf",
        "dwg_vector_normalise_failed",
        "zone_crop_stale",
        "zone_crop_cancelled",
    }


def test_dwg_vector_normalise_failed_is_warn_with_user_action() -> None:
    """S1.3.1: the failed-then-cached DWG path must be visible as warn.

    Distinguishes from ``dwg_using_cached_dxf`` (info) which is a normal
    cache reuse. The warn variant tells the reviewer that the result
    they see is from a stale cache because the live normalisation
    failed — so they should treat differences with extra scrutiny.
    """

    info = FAILURE_CODE_INFO["dwg_vector_normalise_failed"]
    assert info.severity == "warn"
    assert info.requires_user_action is True
    assert "dwg_vector_normalise_failed" in USER_ACTION_REQUIRED_CODES
    assert "dwg_vector_normalise_failed" in WARN_CODES
    assert "dwg_vector_normalise_failed" not in INFO_CODES
    assert "DWG" in info.message_ko
    assert "캐시" in info.message_ko or "정규화" in info.message_ko


def test_dwg_cached_dxf_and_normalise_failed_are_distinct() -> None:
    """Sibling codes must have different severity tiers.

    Plan S1.3.1: ``dwg_using_cached_dxf`` = normal reuse (info),
    ``dwg_vector_normalise_failed`` = degraded reuse after failure (warn).
    """

    info_normal = FAILURE_CODE_INFO["dwg_using_cached_dxf"]
    info_failed = FAILURE_CODE_INFO["dwg_vector_normalise_failed"]
    assert info_normal.severity == "info"
    assert info_failed.severity == "warn"
    assert info_normal.requires_user_action is False
    assert info_failed.requires_user_action is True


def test_every_code_has_info_entry() -> None:
    for code in ALL_FAILURE_CODES:
        assert code in FAILURE_CODE_INFO, f"missing info: {code}"


def test_every_code_has_korean_message() -> None:
    """All codes must contain Korean characters in message_ko."""

    for code in ALL_FAILURE_CODES:
        msg = FAILURE_CODE_INFO[code].message_ko
        assert isinstance(msg, str) and len(msg) > 0, f"empty message: {code}"
        has_korean = any("가" <= ch <= "힣" for ch in msg)
        assert has_korean, f"{code} message has no Korean character: {msg!r}"


def test_severity_distribution_meets_minimum() -> None:
    """At least one of each severity must exist."""

    assert len(INFO_CODES) >= 1
    assert len(WARN_CODES) >= 1
    assert len(ERROR_CODES) >= 1


def test_severity_buckets_partition_all_codes() -> None:
    """Every code falls into exactly one severity bucket."""

    union = INFO_CODES | WARN_CODES | ERROR_CODES
    assert union == set(ALL_FAILURE_CODES)
    assert not (INFO_CODES & WARN_CODES)
    assert not (WARN_CODES & ERROR_CODES)
    assert not (INFO_CODES & ERROR_CODES)


def test_ok_is_only_hidden_code() -> None:
    """The FailureBadge should hide only 'ok'."""

    assert HIDDEN_CODES == {"ok"}


def test_ok_is_info_severity_and_no_action_required() -> None:
    info = FAILURE_CODE_INFO["ok"]
    assert info.severity == "info"
    assert info.requires_user_action is False


def test_vector_draw_failed_is_error() -> None:
    """The hardest failure must escalate to error severity."""

    assert "vector_draw_failed" in ERROR_CODES
    assert severity_of("vector_draw_failed") == "error"


def test_dwg_unsupported_version_requires_user_action() -> None:
    """User must be told that DWG was substituted with cached DXF."""

    assert "dwg_unsupported_version" in USER_ACTION_REQUIRED_CODES
    info = FAILURE_CODE_INFO["dwg_unsupported_version"]
    assert "DWG" in info.message_ko


def test_is_valid_code_recognises_known_strings() -> None:
    for code in ALL_FAILURE_CODES:
        assert is_valid_code(code)


def test_is_valid_code_rejects_unknown() -> None:
    assert not is_valid_code("future_failure")
    assert not is_valid_code("")
    assert not is_valid_code(None)
    assert not is_valid_code(42)


def test_info_for_falls_back_to_ok_safely() -> None:
    """Unknown code must not crash the UI — fall back to ok."""

    fallback = info_for("not_a_real_code")  # type: ignore[arg-type]
    assert fallback is FAILURE_CODE_INFO["ok"]


def test_describe_returns_korean_text() -> None:
    desc = describe("dwg_unsupported_version")
    assert isinstance(desc, str) and len(desc) > 0
    assert any("가" <= ch <= "힣" for ch in desc)


def test_severity_of_returns_valid_tier() -> None:
    for code in ALL_FAILURE_CODES:
        sev = severity_of(code)
        assert sev in SEVERITIES


def test_to_payload_round_trip() -> None:
    """to_payload output must contain all required keys."""

    payload = to_payload("vector_draw_failed")
    for key in (
        "code",
        "severity",
        "message_ko",
        "suggested_action_ko",
        "requires_user_action",
    ):
        assert key in payload
    assert payload["code"] == "vector_draw_failed"
    assert payload["severity"] == "error"
    assert payload["requires_user_action"] is True


def test_highest_severity_picks_error_over_warn() -> None:
    result = highest_severity(
        "ai_heuristic_fallback",
        "dwg_unsupported_version",
        "vector_draw_failed",
    )
    assert result == "error"


def test_highest_severity_picks_warn_when_no_error() -> None:
    result = highest_severity("ai_heuristic_fallback", "dwg_unsupported_version")
    assert result == "warn"


def test_highest_severity_defaults_to_info() -> None:
    assert highest_severity() == "info"
    assert highest_severity("ok") == "info"


def test_highest_severity_ignores_invalid_codes() -> None:
    result = highest_severity("not_a_code", "vector_draw_failed")  # type: ignore[arg-type]
    assert result == "error"


def test_user_action_required_subset_is_consistent() -> None:
    """USER_ACTION_REQUIRED_CODES must match per-code flag."""

    for code in ALL_FAILURE_CODES:
        info = FAILURE_CODE_INFO[code]
        if info.requires_user_action:
            assert code in USER_ACTION_REQUIRED_CODES
        else:
            assert code not in USER_ACTION_REQUIRED_CODES


def test_severities_tuple_has_exactly_three_tiers() -> None:
    assert set(SEVERITIES) == {"info", "warn", "error"}
    assert len(SEVERITIES) == 3


def test_payload_is_json_serialisable() -> None:
    """Round-trip through JSON to verify no surprises (e.g. enum types)."""

    import json

    for code in ALL_FAILURE_CODES:
        payload = to_payload(code)
        # Must not raise
        encoded = json.dumps(payload, ensure_ascii=False)
        decoded = json.loads(encoded)
        assert decoded["code"] == code
