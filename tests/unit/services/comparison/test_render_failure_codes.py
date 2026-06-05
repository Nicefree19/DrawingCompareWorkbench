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


def test_thirteen_codes_present() -> None:
    """The taxonomy must have exactly 13 codes (S1.1 + S1.3.1 + P0-1).

    S1.3.1 added ``dwg_vector_normalise_failed``; P0-1 added
    ``alignment_low_confidence`` / ``alignment_not_applied`` so a
    silently-degraded RANSAC alignment reaches the badge.
    """

    assert len(ALL_FAILURE_CODES) == 13
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
        "alignment_low_confidence",
        "alignment_not_applied",
    }


def test_alignment_codes_severity_and_action() -> None:
    """P0-1: low-confidence is a warn that needs user attention; the
    no-op skip is a benign info."""

    low = FAILURE_CODE_INFO["alignment_low_confidence"]
    assert low.severity == "warn"
    assert low.requires_user_action is True
    assert "alignment_low_confidence" in WARN_CODES
    assert highest_severity("ok", "alignment_low_confidence") == "warn"

    skip = FAILURE_CODE_INFO["alignment_not_applied"]
    assert skip.severity == "info"
    assert skip.requires_user_action is False
    assert "alignment_not_applied" in INFO_CODES


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


# ---------------------------------------------------------------------------
# S1.3.3 — DwgFailureCode bridge tests
# ---------------------------------------------------------------------------


def test_from_dwg_failure_code_maps_unsupported_version() -> None:
    """S1.3.3: DWG_UNSUPPORTED_VERSION maps to dwg_unsupported_version."""

    from src.services.comparison.render_failure_codes import from_dwg_failure_code

    assert from_dwg_failure_code("DWG_UNSUPPORTED_VERSION") == "dwg_unsupported_version"


def test_from_dwg_failure_code_falls_back_to_vector_draw_failed() -> None:
    """S1.3.3: any unmapped DWG code surfaces as vector_draw_failed.

    Better to show a generic error than to silently hide a failure.
    """

    from src.services.comparison.render_failure_codes import from_dwg_failure_code

    for unmapped in (
        "DWG_CORRUPTED",
        "DWG_ENCRYPTED",
        "DWG_ADAPTER_UNAVAILABLE",
        "DWG_ADAPTER_FAILED",
        "DWG_FORBIDDEN_LICENSE",
        "DWG_NO_READABLE_ENTITIES",
        "DWG_UNSUPPORTED_ENTITY",
        "DWG_IMPORT_TIMEOUT",
        "DWG_IMPORT_CANCELLED",
        "DWG_ENTITY_LIMIT_EXCEEDED",
        "",
        "future_unknown_code",
    ):
        assert from_dwg_failure_code(unmapped) == "vector_draw_failed"


def test_from_dwg_failure_code_uses_real_dwg_importer_constant() -> None:
    """S1.3.3: integration — verify the constant value stays in sync.

    Imports the real DwgFailureCode class so a future rename of the
    constant breaks this test instead of silently producing the
    fallback code.
    """

    from src.services.comparison.dwg_importer import DwgFailureCode
    from src.services.comparison.render_failure_codes import from_dwg_failure_code

    assert (
        from_dwg_failure_code(DwgFailureCode.UNSUPPORTED_VERSION)
        == "dwg_unsupported_version"
    )


# ---------------------------------------------------------------------------
# P0-1 — codes_from_comparison_result / aggregate_run_failure_codes
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field  # noqa: E402
from typing import Any, Dict, List  # noqa: E402

from src.services.comparison.render_failure_codes import (  # noqa: E402
    aggregate_run_failure_codes,
    codes_from_comparison_result,
)


def test_mapper_clean_run_returns_empty() -> None:
    """A genuinely clean run produces no codes → badge stays hidden,
    preserving the honest '변경구역 없음' surface."""

    assert codes_from_comparison_result(warnings=(), metadata={}) == ()
    assert codes_from_comparison_result(warnings=[], metadata=None) == ()


def test_mapper_insignificant_alignment_is_not_applied() -> None:
    """alignment recorded but no applied_global_shift_mm = no real shift."""

    meta = {"alignment": {"dx": 0.0, "dy": 0.0}}
    assert codes_from_comparison_result(metadata=meta) == ("alignment_not_applied",)


def test_mapper_significant_alignment_emits_nothing() -> None:
    """When the shift WAS applied, there is no degradation to surface."""

    meta = {"alignment": {"dx": 50.0, "dy": 0.0}, "applied_global_shift_mm": (50.0, 0.0)}
    assert codes_from_comparison_result(metadata=meta) == ()


def test_mapper_low_confidence_flag_wins_over_not_applied() -> None:
    """A low-confidence rejection is the worse signal and is mutually
    exclusive with the benign no-op skip."""

    meta = {"alignment_low_confidence": True}
    assert codes_from_comparison_result(metadata=meta) == ("alignment_low_confidence",)
    # even if a stale alignment dict is also present, low-confidence wins
    meta2 = {"alignment_low_confidence": True, "alignment": {"dx": 0.0}}
    assert codes_from_comparison_result(metadata=meta2) == ("alignment_low_confidence",)


def test_mapper_known_warning_substring_maps() -> None:
    """Known free-text warnings reach the badge; unknown ones are ignored
    (no invented codes)."""

    assert codes_from_comparison_result(
        warnings=["diff 결과 신뢰도 낮음"], metadata={}
    ) == ("alignment_low_confidence",)
    assert codes_from_comparison_result(
        warnings=["alignment quality LOW (inlier 0.30)"], metadata={}
    ) == ("alignment_low_confidence",)
    assert codes_from_comparison_result(
        warnings=["완전히 모르는 경고 문자열"], metadata={}
    ) == ()


def test_mapper_passes_through_zone_codes_and_dedupes() -> None:
    """Zone-level RenderFailureCodes pass through; duplicates collapse,
    'ok' and invalid codes are dropped, order is preserved."""

    out = codes_from_comparison_result(
        warnings=["신뢰도 낮음"],
        metadata={"alignment_low_confidence": True},
        zone_failure_codes=("ok", "vector_draw_failed", "vector_draw_failed", "bogus"),
    )
    assert out == ("alignment_low_confidence", "vector_draw_failed")


@dataclass
class _FakeResult:
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeItem:
    result: Any = None


def test_aggregate_run_flattens_and_dedupes_across_items() -> None:
    items = [
        _FakeItem(result=_FakeResult(metadata={"alignment_low_confidence": True})),
        _FakeItem(result=_FakeResult(metadata={"alignment": {"dx": 0.0}})),
        _FakeItem(result=_FakeResult(metadata={"alignment_low_confidence": True})),
        _FakeItem(result=None),  # must not raise
        _FakeItem(result=_FakeResult()),  # clean → nothing
    ]
    out = aggregate_run_failure_codes(items)
    assert out == ("alignment_low_confidence", "alignment_not_applied")


def test_aggregate_run_handles_empty_and_none() -> None:
    assert aggregate_run_failure_codes(None) == ()
    assert aggregate_run_failure_codes([]) == ()
