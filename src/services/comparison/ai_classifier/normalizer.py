# -*- coding: utf-8 -*-
"""Phase H Stage-2 prep — domain text canonicaliser.

The single biggest accuracy lever for embedding-based classification
of Korean structural drawing changes is NOT the model — it's
collapsing domain-specific tokens (H-beam sections, square tubes,
dimensions, grid IDs, detail callouts) into canonical forms BEFORE
embedding.

Without this step:
  "보 단면 H400×200×8×13 변경"  and
  "보 단면 H450×200×9×14 변경"
embed to nearly-orthogonal vectors despite being the same change
category. The numeric variation dominates the cosine signal.

With canonicalisation:
  "보 단면 H_BEAM_400_200_8_13 변경"  and
  "보 단면 H_BEAM_450_200_9_14 변경"
the numeric tokens have stable structure → the surrounding "보 단면 변경"
context wins, producing the correct STRUCTURAL_MEMBER classification.

Contract pinned by ``test_normalizer.py``:
  * Same input → same canonical form (deterministic)
  * Same canonical form → same hash → cache hit
  * Unicode-normalised (NFC) so "보" composed and decomposed match
  * Whitespace collapsed
  * Order-preserving — does NOT reorder tokens

Pure-Python regex chain. No model dependency. No cold start. Safe
to call from hot paths.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Iterable

NORMALIZER_VERSION = "v1.1"


# ---------------------------------------------------------------------------
# Regex chain — order matters (most specific first)
# ---------------------------------------------------------------------------

# H-beam section: H400×200×8×13, H400x200x8x13, H 400-200-8-13
_H_BEAM_RE = re.compile(
    r"H\s*(\d+)\s*[×xX\-_]\s*(\d+)\s*[×xX\-_]\s*(\d+)\s*[×xX\-_]\s*(\d+)",
)

# Square tube: □400×400×16, ㅁ400x400x16, BOX 400×400×16
_SQR_TUBE_RE = re.compile(
    r"(?:[□ㅁ]|BOX|SQR|HSS)\s*(\d+)\s*[×xX\-_]\s*(\d+)(?:\s*[×xX\-_]\s*(\d+))?",
    re.I,
)

# Round pipe: Ø250, φ250×6, PIPE 250
_ROUND_PIPE_RE = re.compile(
    r"(?:[ØøΦφϕ]|PIPE|HSS\s*ROUND)\s*(\d+)(?:\s*[×xX\-_]\s*(\d+))?",
    re.I,
)

# Dimension with mm/cm/m suffix (or bare 4-5 digit number followed by
# context that suggests a dimension)
_DIM_MM_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(mm|MM)")
_DIM_CM_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(cm|CM)\b")
_DIM_M_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(m|M)\b(?!\w)")

# Grid IDs — 2nd-review fix (P1-3): the previous regex was too greedy
# and ate member IDs like B-1, C-1, G-1 (which are STRUCTURAL_MEMBER
# marks, not grid axes). Korean structural drawings use these
# extensively. New policy:
#   * "GRID A-1" / "GRID X3" → grid (explicit prefix wins)
#   * Y2' / X3' → grid (prime suffix is grid-only convention)
#   * "FAB-1", "ZONE-2", "AXIS-3" → grid (long alphabetic prefix)
#   * "B-1", "C-1", "G-1" → NOT grid (single-letter member ID;
#     keep as-is so the categoriser sees the member context)
_GRID_PRIME_RE = re.compile(
    r"\b([XYAB])\s*(\d+)\s*(['′ʹ])",  # Y2', X3' style
)
# Hyphen form: require (a) explicit GRID prefix, OR (b) ≥2-letter
# alphabetic prefix (FAB, ZONE, AXIS). Single letters get rejected.
_GRID_HYPHEN_EXPLICIT_RE = re.compile(
    r"\bGRID\s+([A-Z]+)\s*[-–]\s*(\d+[A-Z]?)\b",
    re.I,
)
_GRID_HYPHEN_LONG_PREFIX_RE = re.compile(
    r"\b(FAB|ZONE|AXIS|GRID)\s*[-–]\s*(\d+[A-Z]?)\b",
    re.I,
)
_GRID_PLAIN_RE = re.compile(
    # Match "GRID X3" but NOT "GRID GRID_A_1" — exclude underscored
    # canonical residue. (?![A-Z]) prevents catching "GRID_" prefix.
    r"\bGRID\s+(?!_)([A-Z][A-Z0-9]*)\b",
    re.I,
)

# Korean structural domain tokens (review feedback addition):
#   PL12 / t16 → plate thickness
#   D13 / HD13 → rebar diameter
#   B1F / 1FL → floor designation
#   EL+1.5 / EL-0.3 → elevation
_PLATE_THICKNESS_RE = re.compile(
    r"\b(?:PL|t)\s*(\d+(?:\.\d+)?)\b", re.I,
)
_REBAR_RE = re.compile(
    r"\b(?:D|HD|SD)\s*(\d+)\b", re.I,
)
_FLOOR_RE = re.compile(
    r"\b([B]?\d+)\s*(?:F(?:L)?|층)\b", re.I,
)
_ELEVATION_RE = re.compile(
    r"\bEL\s*([+\-±])\s*(\d+(?:\.\d+)?)",
    re.I,
)

# Detail / section callouts — split EN vs KO so the Korean
# "단면" doesn't false-match against legitimate structural beam
# descriptions ("단면 H400×200×8×13" must stay STRUCTURAL_MEMBER, not
# get rewritten to DETAIL_H_BEAM_*).
#
# English: DET-03, S-12, SEC A-A. Alternation order: LONGER keyword
# first so "DETAIL" wins over "DET". (?![A-Z]) prevents "DET" matching
# inside "DETAIL_03" on the second pass (idempotency requirement).
_DETAIL_RE = re.compile(
    r"\b(DETAIL|DET|SECTION|SEC)(?![A-Z])\s*[-_]?\s*"
    r"([A-Z0-9]+(?:\s*[-_]\s*[A-Z0-9]+)?)",
    re.I,
)
# Korean: 상세 / 단면 followed by a callout-shaped token.
# CRITICAL — negative lookahead rejects ALREADY-canonicalised
# structural tokens (H_BEAM_, SQR_TUBE_, DIM_, REBAR_, PLATE_,
# ROUND_PIPE_, FLOOR_, ELEV_PLUS_/MINUS_/PM_, GRID_, DETAIL_) so:
#   "단면 H400×200×8×13" — H_BEAM_RE rewrites to "단면 H_BEAM_400_…"
#                          → KO regex skips (lookahead catches H_BEAM_)
#                          → final stays as STRUCTURAL_MEMBER text
#   "단면 A-A 추가"      — A is NOT a canonical prefix
#                          → KO regex matches → "DETAIL_A_A 추가"
#   "단면도 표기"         — "도" is Korean (not [A-Z0-9])
#                          → KO regex doesn't match → stays untouched
# Without this lookahead, every "단면 H_BEAM_…" beam description was
# silently re-classified as a DETAIL callout, ruining Stage-2 cosine
# matching against the STRUCTURAL_MEMBER prototypes.
_DETAIL_KO_RE = re.compile(
    r"\b(상세|단면)\s*[-_]?\s*"
    r"(?!H_BEAM_|SQR_TUBE_|ROUND_PIPE_|DIM_|REBAR_|PLATE_|"
    r"FLOOR_|ELEV_PLUS_|ELEV_MINUS_|ELEV_PM_|GRID_|DETAIL_)"
    r"([A-Z0-9]+(?:\s*[-_]\s*[A-Z0-9]+)?)",
)


# ---------------------------------------------------------------------------
# Canonicalisation chain
# ---------------------------------------------------------------------------


def canonicalize_zone_text(raw: str) -> str:
    """Produce a deterministic canonical form of the zone evidence.

    Steps:
      1. Unicode NFC normalisation (composed forms)
      2. Collapse internal whitespace
      3. Strip accidentally-included BOM / control chars
      4. Apply domain regex chain (H-beam → H_BEAM_..., dims → DIM_...)
      5. Trim leading/trailing whitespace

    The returned string is short, stable, and embedding-friendly —
    the regex tokens become single contiguous identifiers that the
    tokenizer treats atomically instead of splitting into
    digit-by-digit subwords.

    Empty / None input → empty string (caller decides whether to
    skip embedding entirely).
    """

    if not raw:
        return ""
    # 2nd-review fix (P1-3): NFKC + NFC like the Stage-1 heuristic.
    # NFKC alone collapses fullwidth (Ｈ → H, Ｂ-１ → B-1) but can
    # split Hangul jamo; following with NFC re-composes Korean so
    # subsequent regexes that match composed "보"/"기둥" still fire.
    # Without NFKC the H_BEAM_RE / GRID regexes miss any input that
    # arrived from a CAD export with fullwidth or compatibility forms.
    text = unicodedata.normalize(
        "NFC",
        unicodedata.normalize("NFKC", str(raw)),
    )
    # Strip control / BOM chars (keep \t \n; rest go)
    text = "".join(
        ch for ch in text
        if ch in {"\t", "\n"} or unicodedata.category(ch)[0] != "C"
    )

    # Apply most-specific regex first
    text = _H_BEAM_RE.sub(
        lambda m: f"H_BEAM_{m.group(1)}_{m.group(2)}_{m.group(3)}_{m.group(4)}",
        text,
    )

    def _sqr_repl(m: re.Match[str]) -> str:
        if m.group(3):
            return f"SQR_TUBE_{m.group(1)}_{m.group(2)}_{m.group(3)}"
        return f"SQR_TUBE_{m.group(1)}_{m.group(2)}"
    text = _SQR_TUBE_RE.sub(_sqr_repl, text)

    def _pipe_repl(m: re.Match[str]) -> str:
        if m.group(2):
            return f"ROUND_PIPE_{m.group(1)}_{m.group(2)}"
        return f"ROUND_PIPE_{m.group(1)}"
    text = _ROUND_PIPE_RE.sub(_pipe_repl, text)

    # Dimension tokens — collapse "5500mm" / "5500 mm" / "5500.5mm"
    text = _DIM_MM_RE.sub(
        lambda m: f"DIM_{m.group(1).replace('.', '_').replace(',', '_')}_MM",
        text,
    )
    text = _DIM_CM_RE.sub(
        lambda m: f"DIM_{m.group(1).replace('.', '_').replace(',', '_')}_CM",
        text,
    )
    text = _DIM_M_RE.sub(
        lambda m: f"DIM_{m.group(1).replace('.', '_').replace(',', '_')}_M",
        text,
    )

    # Korean structural shorthand — PLATE/REBAR/FLOOR/ELEVATION must
    # run BEFORE _DETAIL_KO_RE so its negative lookahead has the
    # canonical PLATE_/REBAR_/FLOOR_/ELEV_ tokens to reject. Without
    # this ordering, "단면 D13 배근" runs DETAIL first → "DETAIL_D13"
    # → REBAR_RE never sees "D13" and the structural signal is lost.
    # 2nd-review fix (P1-3): canonicalise BEFORE grid (which is more
    # permissive). Without this, "PL12" was indistinguishable from a
    # grid letter+digit pair.
    text = _PLATE_THICKNESS_RE.sub(
        lambda m: f"PLATE_{m.group(1).replace('.', '_')}",
        text,
    )
    text = _REBAR_RE.sub(
        lambda m: f"REBAR_{m.group(1)}",
        text,
    )
    text = _FLOOR_RE.sub(
        lambda m: f"FLOOR_{m.group(1).upper()}",
        text,
    )
    text = _ELEVATION_RE.sub(
        lambda m: (
            f"ELEV_{'PLUS' if m.group(1) == '+' else 'MINUS' if m.group(1) == '-' else 'PM'}"
            f"_{m.group(2).replace('.', '_')}"
        ),
        text,
    )

    # Grid tokens — prime version first (Y2'), then explicit GRID
    # prefix, then long-prefix hyphen (FAB/ZONE/AXIS), then plain.
    # 2nd-review fix (P1-3): single-letter hyphen forms (B-1, C-1)
    # are NO LONGER auto-classified as grid — they're member IDs and
    # need to stay readable so the categoriser sees member context.
    # Grids run BEFORE detail so _DETAIL_KO_RE's lookahead can reject
    # "단면 GRID_A_1" too. Grid regexes are restrictive enough (require
    # explicit GRID/FAB/ZONE/AXIS prefix or X/Y/A/B + prime) that they
    # don't accidentally match DET-03 / SEC A-A.
    text = _GRID_PRIME_RE.sub(
        lambda m: f"GRID_{m.group(1)}_{m.group(2)}_PRIME",
        text,
    )
    text = _GRID_HYPHEN_EXPLICIT_RE.sub(
        lambda m: f"GRID_{m.group(1).upper()}_{m.group(2)}",
        text,
    )
    text = _GRID_HYPHEN_LONG_PREFIX_RE.sub(
        lambda m: f"GRID_{m.group(1).upper()}_{m.group(2)}",
        text,
    )
    text = _GRID_PLAIN_RE.sub(
        lambda m: f"GRID_{m.group(1).upper()}",
        text,
    )

    # Detail / section callouts LAST — by the time we get here every
    # canonical structural token (H_BEAM_, SQR_TUBE_, ROUND_PIPE_,
    # DIM_, REBAR_, PLATE_, FLOOR_, ELEV_*, GRID_) has been rewritten,
    # so _DETAIL_KO_RE's negative lookahead can reliably reject them.
    # English first (covers DET/SEC/SECTION/DETAIL keywords).
    text = _DETAIL_RE.sub(
        lambda m: f"DETAIL_{m.group(2).replace(' ', '').replace('-', '_').replace('_', '_')}",
        text,
    )
    # Korean (상세/단면) — runs AFTER all structural-token regexes so
    # the negative lookahead in _DETAIL_KO_RE can reject "단면 H_BEAM_…"
    # without false-rewriting it. See _DETAIL_KO_RE comment for the
    # full reasoning.
    text = _DETAIL_KO_RE.sub(
        lambda m: f"DETAIL_{m.group(2).replace(' ', '').replace('-', '_').replace('_', '_')}",
        text,
    )

    # Collapse whitespace (do this LAST so regex anchors above still match)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def canonical_hash(canonical: str) -> str:
    """SHA-256 of the canonical form, hex-encoded.

    Used as a cache key — same canonical text → same embedding →
    skip the model call.
    """

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonicalize_batch(texts: Iterable[str]) -> list[str]:
    """Vectorised wrapper for batch encoding callers."""

    return [canonicalize_zone_text(t) for t in texts]


__all__ = [
    "NORMALIZER_VERSION",
    "canonicalize_zone_text",
    "canonical_hash",
    "canonicalize_batch",
]
