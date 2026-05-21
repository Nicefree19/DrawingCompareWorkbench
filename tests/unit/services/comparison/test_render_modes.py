# -*- coding: utf-8 -*-
"""Unit tests for the 7-state RenderMode enum + transition table (Phase G1)."""

from __future__ import annotations

import pytest

from src.services.comparison.render_modes import (
    ALL_RENDER_MODES,
    ALLOWED_TRANSITIONS,
    AUTHORITATIVE_MODES,
    RENDER_MODE_STYLES,
    TERMINAL_MODES,
    best_authoritative,
    describe,
    is_valid_mode,
    style_for,
    transition,
)


def test_all_seven_modes_present() -> None:
    assert set(ALL_RENDER_MODES) == {
        "relative_only", "skeleton_preview", "vector_focus",
        "raster_refined", "render_pending", "render_timeout", "render_failed",
    }


def test_every_mode_has_style_entry() -> None:
    for mode in ALL_RENDER_MODES:
        assert mode in RENDER_MODE_STYLES, f"missing style: {mode}"


def test_every_mode_has_transition_entry() -> None:
    for mode in ALL_RENDER_MODES:
        assert mode in ALLOWED_TRANSITIONS, f"missing transitions: {mode}"


def test_only_relative_only_shows_watermark() -> None:
    for mode, style in RENDER_MODE_STYLES.items():
        if mode == "relative_only":
            assert style.show_watermark is True
        else:
            assert style.show_watermark is False, f"{mode} unexpectedly shows watermark"


def test_only_authoritative_modes_enable_measurement() -> None:
    for mode, style in RENDER_MODE_STYLES.items():
        if mode in AUTHORITATIVE_MODES:
            assert style.enable_measurement is True
        else:
            assert style.enable_measurement is False, f"{mode} unexpectedly enables measurement"


def test_only_render_pending_is_transient() -> None:
    for mode, style in RENDER_MODE_STYLES.items():
        if mode == "render_pending":
            assert style.is_transient is True
            assert mode not in TERMINAL_MODES
        else:
            assert style.is_transient is False
            assert mode in TERMINAL_MODES


def test_is_valid_mode_recognises_known_strings() -> None:
    for mode in ALL_RENDER_MODES:
        assert is_valid_mode(mode)


def test_is_valid_mode_rejects_unknown() -> None:
    assert not is_valid_mode("ultra_high_def")
    assert not is_valid_mode("")
    assert not is_valid_mode(None)
    assert not is_valid_mode(42)


def test_style_for_falls_back_to_relative_only_safely() -> None:
    fallback = style_for("not_a_real_mode")  # type: ignore[arg-type]
    assert fallback is RENDER_MODE_STYLES["relative_only"]
    assert fallback.show_watermark is True  # safest UX


def test_transition_allowed_returns_target() -> None:
    # skeleton -> vector_focus is allowed per the table
    assert transition("skeleton_preview", "vector_focus") == "vector_focus"


def test_transition_disallowed_keeps_current() -> None:
    # render_failed -> raster_refined isn't allowed (must go through pending or skeleton)
    result = transition("render_failed", "raster_refined")
    assert result == "render_failed"


def test_transition_to_same_mode_idempotent() -> None:
    for mode in ALL_RENDER_MODES:
        assert transition(mode, mode) == mode


def test_transition_invalid_target_keeps_current() -> None:
    assert transition("vector_focus", "imaginary_state") == "vector_focus"  # type: ignore[arg-type]


def test_transition_from_invalid_accepts_valid_target() -> None:
    """First transition from None/garbage should accept any valid target."""

    assert transition("garbage", "skeleton_preview") == "skeleton_preview"  # type: ignore[arg-type]


def test_describe_returns_korean_text() -> None:
    desc = describe("vector_focus")
    assert isinstance(desc, str) and len(desc) > 0
    # Korean character present
    assert any("가" <= ch <= "힣" for ch in desc)


def test_best_authoritative_picks_highest_fidelity() -> None:
    assert best_authoritative("skeleton_preview", "vector_focus", "raster_refined") == "raster_refined"
    assert best_authoritative("skeleton_preview", "vector_focus") == "vector_focus"
    assert best_authoritative("skeleton_preview") == "skeleton_preview"


def test_best_authoritative_skips_non_authoritative() -> None:
    """render_pending/timeout/failed should never beat a real frame."""

    assert best_authoritative("render_failed", "skeleton_preview") == "skeleton_preview"
    assert best_authoritative("render_pending", "render_timeout") == "relative_only"
    assert best_authoritative(None, None) == "relative_only"


def test_style_to_dict_has_all_required_keys() -> None:
    d = RENDER_MODE_STYLES["vector_focus"].to_dict()
    for key in (
        "label_ko", "badge_color", "badge_text_color",
        "show_watermark", "enable_measurement", "is_transient",
        "description_ko",
    ):
        assert key in d
