# -*- coding: utf-8 -*-
"""Tests for the Phase H Stage-2 prep — domain text canonicaliser.

Pins the regex chain so future "improvements" can't silently break
the prototype-cache hash key (which would invalidate every
pre-computed embedding).

Pure-Python — no model dependency.
"""

from __future__ import annotations

import unicodedata

import pytest


# ---------------------------------------------------------------------------
# Determinism + idempotency
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty_string() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    assert canonicalize_zone_text("") == ""
    assert canonicalize_zone_text(None) == ""  # type: ignore[arg-type]


def test_canonicalize_is_idempotent() -> None:
    """Running the canonicaliser twice must produce the same output."""

    from src.services.comparison.ai_classifier import canonicalize_zone_text
    raw = "보 H400×200×8×13 GRID A-1 5500mm DET-03"
    once = canonicalize_zone_text(raw)
    twice = canonicalize_zone_text(once)
    assert once == twice


def test_same_input_same_hash() -> None:
    from src.services.comparison.ai_classifier import (
        canonicalize_zone_text, canonical_hash,
    )
    a = canonicalize_zone_text("기둥 □400×400 변경")
    b = canonicalize_zone_text("기둥 □400×400 변경")
    assert canonical_hash(a) == canonical_hash(b)


# ---------------------------------------------------------------------------
# H-beam normalisation
# ---------------------------------------------------------------------------


def test_h_beam_unicode_multiplication_sign() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out = canonicalize_zone_text("H400×200×8×13")
    assert "H_BEAM_400_200_8_13" in out


def test_h_beam_x_letter_separator() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out = canonicalize_zone_text("H400x200x8x13")
    assert "H_BEAM_400_200_8_13" in out


def test_h_beam_with_spaces() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out = canonicalize_zone_text("H 400 × 200 × 8 × 13")
    assert "H_BEAM_400_200_8_13" in out


def test_two_h_beams_in_same_text() -> None:
    """Comparing-revision text often has both before+after section."""

    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out = canonicalize_zone_text("H400×200×8×13 → H450×200×9×14")
    assert "H_BEAM_400_200_8_13" in out
    assert "H_BEAM_450_200_9_14" in out


# ---------------------------------------------------------------------------
# Square tube + round pipe
# ---------------------------------------------------------------------------


def test_square_tube_box_symbol() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out = canonicalize_zone_text("□400×400×16")
    assert "SQR_TUBE_400_400_16" in out


def test_square_tube_box_keyword() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out = canonicalize_zone_text("BOX 300×300")
    assert "SQR_TUBE_300_300" in out


def test_round_pipe_phi() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out = canonicalize_zone_text("Φ250×6")
    assert "ROUND_PIPE_250_6" in out


# ---------------------------------------------------------------------------
# Dimension tokens
# ---------------------------------------------------------------------------


def test_dimension_mm() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out = canonicalize_zone_text("5500mm")
    assert "DIM_5500_MM" in out


def test_dimension_with_space_and_decimal() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out = canonicalize_zone_text("5500.5 mm")
    assert "DIM_5500_5_MM" in out


def test_dimension_cm_and_m() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    assert "DIM_25_CM" in canonicalize_zone_text("25cm")
    assert "DIM_3_M" in canonicalize_zone_text("3m")


def test_two_dimensions_same_text() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out = canonicalize_zone_text("5500mm → 5800mm")
    assert "DIM_5500_MM" in out
    assert "DIM_5800_MM" in out


# ---------------------------------------------------------------------------
# Grid IDs
# ---------------------------------------------------------------------------


def test_grid_hyphen_form() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out = canonicalize_zone_text("GRID A-1 변경")
    assert "GRID_A_1" in out


def test_grid_prime_form() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out = canonicalize_zone_text("Y2'")
    assert "GRID_Y_2_PRIME" in out


def test_grid_fab_keyword() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out = canonicalize_zone_text("FAB-1 추가")
    assert "GRID_FAB_1" in out


# ---------------------------------------------------------------------------
# Detail / section callouts
# ---------------------------------------------------------------------------


def test_detail_callout_basic() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out = canonicalize_zone_text("DET-03 참조")
    assert "DETAIL_03" in out


def test_section_callout_two_letter() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out = canonicalize_zone_text("SEC A-A")
    assert "DETAIL_A_A" in out


def test_korean_section_callout_with_alphanumeric() -> None:
    """단면 followed by a non-structural callout token still rewrites
    to DETAIL_ — preserves the legitimate Korean detail-callout flow.
    """

    from src.services.comparison.ai_classifier import canonicalize_zone_text
    assert "DETAIL_A_A" in canonicalize_zone_text("단면 A-A 추가")
    assert "DETAIL_03" in canonicalize_zone_text("상세 03 보강")


def test_korean_단면_followed_by_beam_token_is_NOT_detail() -> None:
    """W2 regression — "보 단면 H400×200×8×13 변경" must NOT get
    rewritten to "보 DETAIL_H_BEAM_…" by the detail callout regex.

    This was a real bug: the previous _DETAIL_RE alternated 상세/단면
    with the English keywords, so any "단면 H_BEAM_…" sequence got
    consumed as a detail callout — destroying STRUCTURAL_MEMBER text
    and collapsing Stage-2 cosine matching to garbage. The new
    _DETAIL_KO_RE has a negative lookahead that rejects already-
    canonicalised structural tokens.
    """

    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out = canonicalize_zone_text("보 단면 H400×200×8×13 변경")
    # H_BEAM token preserved — STRUCTURAL_MEMBER signal stays intact
    assert "H_BEAM_400_200_8_13" in out
    # No DETAIL token leaked in front of the beam code
    assert "DETAIL_H_BEAM" not in out
    # Original Korean keywords still present
    assert "보" in out
    assert "단면" in out


def test_korean_단면도_with_korean_suffix_is_unchanged() -> None:
    """단면도 (with the Korean 도 suffix) is a noun, not a callout
    introducer. The KO regex requires an [A-Z0-9]+ token, so it
    correctly leaves "단면도 표기" untouched.
    """

    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out = canonicalize_zone_text("단면도 표기")
    assert "DETAIL" not in out
    assert "단면도" in out


def test_korean_detail_callout_negative_lookahead_covers_all_canonical_tokens() -> None:
    """단면 followed by any of the canonical structural prefixes
    (H_BEAM_, SQR_TUBE_, ROUND_PIPE_, DIM_, REBAR_, PLATE_, FLOOR_,
    ELEV_*, GRID_, DETAIL_) must NOT trigger a DETAIL rewrite."""

    from src.services.comparison.ai_classifier import canonicalize_zone_text
    cases = [
        # (raw_text, must-survive token)
        ("단면 H400×200×8×13 변경", "H_BEAM_400_200_8_13"),
        ("단면 □400×400×16 추가", "SQR_TUBE_400_400_16"),
        ("단면 D13 배근", "REBAR_13"),
        ("단면 PL12 보강", "PLATE_12"),
        ("단면 5500mm 추가", "DIM_5500_MM"),
        ("단면 GRID A-1 위치", "GRID_A_1"),
    ]
    for raw, must_have in cases:
        out = canonicalize_zone_text(raw)
        assert must_have in out, f"{raw!r} → {out!r} (lost {must_have})"
        # And no DETAIL_ prefix snuck onto the structural token
        assert f"DETAIL_{must_have}" not in out, \
            f"{raw!r} → {out!r} (false-DETAIL on {must_have})"


# ---------------------------------------------------------------------------
# Unicode normalisation
# ---------------------------------------------------------------------------


def test_nfc_normalisation_korean() -> None:
    """Korean composed and decomposed forms must produce the same hash."""

    from src.services.comparison.ai_classifier import canonicalize_zone_text
    composed = "보"  # U+BCF4
    decomposed = unicodedata.normalize("NFD", composed)
    out_composed = canonicalize_zone_text(f"{composed} 변경")
    out_decomposed = canonicalize_zone_text(f"{decomposed} 변경")
    assert out_composed == out_decomposed


def test_strips_control_characters() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    raw = "보\x00 변경\x07"
    out = canonicalize_zone_text(raw)
    assert "\x00" not in out
    assert "\x07" not in out
    assert "보" in out and "변경" in out


def test_collapses_whitespace() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out = canonicalize_zone_text("보   변경    H400×200×8×13")
    # Multiple spaces collapsed to single, no leading/trailing
    assert "  " not in out
    assert not out.startswith(" ")
    assert not out.endswith(" ")


# ---------------------------------------------------------------------------
# Realistic full-zone evidence strings
# ---------------------------------------------------------------------------


def test_realistic_beam_change_text() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    raw = "보 G3 단면 H400×200×8×13 → H450×200×9×14, GRID A-1, 5500mm 스팬"
    out = canonicalize_zone_text(raw)
    assert "H_BEAM_400_200_8_13" in out
    assert "H_BEAM_450_200_9_14" in out
    assert "GRID_A_1" in out
    assert "DIM_5500_MM" in out
    assert "보" in out  # Korean preserved
    assert "G3" in out  # member ID preserved (no rule for it yet)


def test_realistic_dimension_change_text() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    raw = "치수 8000mm → 8500mm 변경"
    out = canonicalize_zone_text(raw)
    assert "DIM_8000_MM" in out
    assert "DIM_8500_MM" in out


# ---------------------------------------------------------------------------
# Batch helper
# ---------------------------------------------------------------------------


def test_batch_canonicalize_preserves_order() -> None:
    from src.services.comparison.ai_classifier import canonicalize_batch
    out = canonicalize_batch(["H400×200×8×13", "GRID A-1", "5500mm"])
    assert len(out) == 3
    assert "H_BEAM_400_200_8_13" in out[0]
    assert "GRID_A_1" in out[1]
    assert "DIM_5500_MM" in out[2]


def test_batch_canonicalize_empty_input() -> None:
    from src.services.comparison.ai_classifier import canonicalize_batch
    assert canonicalize_batch([]) == []


# ---------------------------------------------------------------------------
# Hash properties
# ---------------------------------------------------------------------------


def test_hash_is_64_hex_chars() -> None:
    from src.services.comparison.ai_classifier import canonical_hash
    h = canonical_hash("foo")
    assert len(h) == 64
    int(h, 16)  # parses as hex


def test_hash_distinguishes_different_inputs() -> None:
    from src.services.comparison.ai_classifier import canonical_hash
    h1 = canonical_hash("foo")
    h2 = canonical_hash("bar")
    assert h1 != h2


# ---------------------------------------------------------------------------
# 2nd-review fix (P1-3) — NFKC + member-id negative + new domain tokens
# ---------------------------------------------------------------------------


def test_fullwidth_h_beam_canonicalised_via_nfkc() -> None:
    """Fullwidth Ｈ400×200×8×13 must canonicalise like halfwidth."""

    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out_fw = canonicalize_zone_text("Ｈ400×200×8×13")
    out_hw = canonicalize_zone_text("H400×200×8×13")
    assert out_fw == out_hw
    assert "H_BEAM_400_200_8_13" in out_fw


def test_fullwidth_digits_in_grid_canonicalised() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out_fw = canonicalize_zone_text("GRID Ａ-１")
    assert "GRID_A_1" in out_fw


def test_member_id_b1_not_classified_as_grid() -> None:
    """B-1 / C-1 / G-1 are MEMBER IDs in Korean structural drawings,
    not grid axes. The previous regex over-matched them as grid."""

    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out = canonicalize_zone_text("B-1 보 변경")
    # Must NOT contain GRID_B_1
    assert "GRID_B_1" not in out
    # B-1 should remain readable so the categoriser sees member context
    assert "B-1" in out or "B_1" in out


def test_grid_with_explicit_prefix_still_works() -> None:
    """The explicit "GRID A-1" form must continue to canonicalise."""

    from src.services.comparison.ai_classifier import canonicalize_zone_text
    out = canonicalize_zone_text("GRID A-1 변경")
    assert "GRID_A_1" in out


def test_long_prefix_grid_form_still_works() -> None:
    """FAB-1, ZONE-2, AXIS-3 patterns stay grid (≥2-letter prefix)."""

    from src.services.comparison.ai_classifier import canonicalize_zone_text
    assert "GRID_FAB_1" in canonicalize_zone_text("FAB-1")
    assert "GRID_ZONE_2" in canonicalize_zone_text("ZONE-2")
    assert "GRID_AXIS_3" in canonicalize_zone_text("AXIS-3")


def test_plate_thickness_token() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    assert "PLATE_12" in canonicalize_zone_text("PL12")
    assert "PLATE_16" in canonicalize_zone_text("t16")
    assert "PLATE_25_4" in canonicalize_zone_text("PL25.4")


def test_rebar_token() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    assert "REBAR_13" in canonicalize_zone_text("D13")
    assert "REBAR_25" in canonicalize_zone_text("HD25")


def test_floor_token() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    assert "FLOOR_B1" in canonicalize_zone_text("B1F")
    assert "FLOOR_3" in canonicalize_zone_text("3FL")


def test_elevation_token() -> None:
    from src.services.comparison.ai_classifier import canonicalize_zone_text
    assert "ELEV_PLUS_1_5" in canonicalize_zone_text("EL+1.5")
    assert "ELEV_MINUS_0_3" in canonicalize_zone_text("EL-0.3")
