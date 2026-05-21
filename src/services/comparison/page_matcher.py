# -*- coding: utf-8 -*-
"""5-signal weighted scorer + Hungarian assignment for PDF page matching.

Phase H1 partner of ``page_descriptor.py``. Given two lists of
:class:`PerPageDescriptor` (one per PDF), produce a list of
:class:`PageMatchCandidate` mapping ``page_a_index ↔ page_b_index``
with a confidence score and a status (AUTO_CONFIRMED / REVIEW_REQUIRED /
UNMATCHED_A / UNMATCHED_B).

The 5 signals + weights are:

    drawing_number  35 %   exact code match (e.g. S20-0002)
    title           25 %   SequenceMatcher.ratio() over title-block text
    visual          20 %   pHash Hamming distance (page thumbnail)
    text            15 %   page full-text hash similarity
    dimension        5 %   page (width, height) ratio match

Why these weights vs the existing file-level matcher:
- file-level uses 35 % filename — pages have no filename
- file-level uses  5 % folder path — same PDF
- those 40 % are redistributed to drawing_number (already heaviest) +
  title (new strongest non-code signal) + visual + dim
- visual gets a real share at the page level because rasterised page
  layouts are extremely discriminative even when title text is sparse

After scoring all (i, j) pairs we build the cost matrix
``cost[i, j] = 1 - score[i, j]`` and solve via
:func:`scipy.optimize.linear_sum_assignment`. Pairs whose final score
falls below ``review_threshold`` (default 0.60) are dropped — those
pages get UNMATCHED_A / UNMATCHED_B status downstream.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

from src.services.comparison.page_descriptor import PerPageDescriptor

logger = logging.getLogger(__name__)


class PageMatchStatus(str, Enum):
    """Match status — mirrors the file-level ``MatchStatus`` enum but
    page-scoped so callers don't conflate the two layers."""

    AUTO_CONFIRMED = "auto_confirmed"
    REVIEW_REQUIRED = "review_required"
    UNMATCHED_A = "unmatched_a"
    UNMATCHED_B = "unmatched_b"


@dataclass(frozen=True)
class PageMatchOptions:
    """Tunable knobs. Defaults mirror the file-level MatchingOptions
    (auto 0.85, review 0.60) per the user's H Phase decision."""

    auto_confirm_threshold: float = 0.85
    review_threshold: float = 0.60
    # When both descriptors carry a drawing_number AND they differ → cap
    # the score at this value, even if visual + title look similar.
    # Mirrors the file-level "drawing code mismatch → cap at 0.59"
    # rejection penalty (drawing_batch.py).
    drawing_number_mismatch_cap: float = 0.59
    # When drawing_number matches AND title also matches strongly →
    # boost the final score to at least this value (so a clear pair
    # never accidentally lands in REVIEW_REQUIRED).
    confidence_boost_floor: float = 0.92
    confidence_boost_title_threshold: float = 0.70


@dataclass
class PageMatchCandidate:
    """One resolved page pair (or unmatched page)."""

    page_a_index: int
    page_b_index: int
    score: float
    status: PageMatchStatus
    score_breakdown: Dict[str, float] = field(default_factory=dict)

    @property
    def is_matched(self) -> bool:
        return self.status in {
            PageMatchStatus.AUTO_CONFIRMED,
            PageMatchStatus.REVIEW_REQUIRED,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "page_a_index": self.page_a_index,
            "page_b_index": self.page_b_index,
            "score": round(self.score, 4),
            "status": self.status.value,
            "score_breakdown": {k: round(v, 4) for k, v in self.score_breakdown.items()},
        }


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------


def _hash_similarity(hash_a: str, hash_b: str) -> float:
    """Hamming-distance similarity between two hex hashes.

    Returns 1.0 for identical hashes, 0.0 for maximally different. Empty
    inputs → 0.5 (neutral / signal unavailable).
    """

    if not hash_a or not hash_b:
        return 0.5
    if hash_a == hash_b:
        return 1.0
    # Pad to equal length so XOR works deterministically
    max_len = max(len(hash_a), len(hash_b))
    a = hash_a.rjust(max_len, "0")
    b = hash_b.rjust(max_len, "0")
    try:
        diff = int(a, 16) ^ int(b, 16)
    except ValueError:
        return 0.0
    bit_count = bin(diff).count("1")
    total_bits = max_len * 4  # each hex digit = 4 bits
    return max(0.0, 1.0 - bit_count / total_bits)


def _drawing_number_score(a: PerPageDescriptor, b: PerPageDescriptor) -> float:
    """1.0 = same code. 0.0 = different codes (strong reject signal).
    0.5 = at least one missing (neutral)."""

    code_a = a.drawing_number.strip().upper()
    code_b = b.drawing_number.strip().upper()
    if not code_a or not code_b:
        return 0.5  # signal unavailable
    if code_a == code_b:
        return 1.0
    return 0.0


def _title_score(a: PerPageDescriptor, b: PerPageDescriptor) -> float:
    """SequenceMatcher ratio over normalised title-block text."""

    if not a.title_text_normalised or not b.title_text_normalised:
        return 0.5
    return SequenceMatcher(
        None, a.title_text_normalised, b.title_text_normalised,
    ).ratio()


def _visual_score(a: PerPageDescriptor, b: PerPageDescriptor) -> float:
    return _hash_similarity(a.visual_hash, b.visual_hash)


def _text_score(a: PerPageDescriptor, b: PerPageDescriptor) -> float:
    return _hash_similarity(a.full_text_hash, b.full_text_hash)


def _dimension_score(a: PerPageDescriptor, b: PerPageDescriptor) -> float:
    """1.0 for identical (width, height); falls off with area ratio.

    Returns 0.5 (neutral) when either page is missing dimensions.
    """

    aw, ah = a.page_size
    bw, bh = b.page_size
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return 0.5
    if (aw, ah) == (bw, bh):
        return 1.0
    # Areas are guaranteed > 0 by the dimension check above, so the
    # ratio is well-defined. min(ratio, 1/ratio) gives a 0-1 closeness
    # score (1.0 when areas equal, falls off with size mismatch).
    ratio = (aw * ah) / (bw * bh)
    return min(ratio, 1.0 / ratio)


def score_page_match(
    a: PerPageDescriptor,
    b: PerPageDescriptor,
    *,
    options: Optional[PageMatchOptions] = None,
) -> Tuple[float, Dict[str, float]]:
    """Combined 5-signal score with confidence boosts and rejection caps.

    Returns ``(score, breakdown)`` where ``score`` is in ``[0.0, 1.0]``
    and ``breakdown`` is the per-signal raw score (useful for diagnostics
    and the GUI's REVIEW_REQUIRED tooltip).
    """

    opts = options or PageMatchOptions()

    breakdown: Dict[str, float] = {
        "drawing_number": _drawing_number_score(a, b),
        "title": _title_score(a, b),
        "visual": _visual_score(a, b),
        "text": _text_score(a, b),
        "dimension": _dimension_score(a, b),
    }

    score = (
        0.35 * breakdown["drawing_number"]
        + 0.25 * breakdown["title"]
        + 0.20 * breakdown["visual"]
        + 0.15 * breakdown["text"]
        + 0.05 * breakdown["dimension"]
    )

    # Rejection cap — explicit drawing-number conflict
    if (
        a.drawing_number and b.drawing_number
        and a.drawing_number != b.drawing_number
    ):
        score = min(score, opts.drawing_number_mismatch_cap)

    # Confidence boost — same drawing_number + strong title match
    if (
        breakdown["drawing_number"] >= 1.0
        and breakdown["title"] >= opts.confidence_boost_title_threshold
    ):
        score = max(score, opts.confidence_boost_floor)

    return (max(0.0, min(1.0, score)), breakdown)


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


def _classify(score: float, options: PageMatchOptions) -> PageMatchStatus:
    """Map a score to a *matched* status (AUTO_CONFIRMED / REVIEW_REQUIRED).

    Caller MUST filter out scores below ``review_threshold`` before
    invoking this — those pages are unmatched and the side-specific
    UNMATCHED_A / UNMATCHED_B status must be assigned by the caller (we
    don't know the side here). An assertion enforces the invariant so a
    future caller doesn't silently mislabel low-score pairs as
    UNMATCHED_A regardless of side.
    """

    if score >= options.auto_confirm_threshold:
        return PageMatchStatus.AUTO_CONFIRMED
    if score >= options.review_threshold:
        return PageMatchStatus.REVIEW_REQUIRED
    raise ValueError(
        f"_classify called with score {score:.3f} below review_threshold "
        f"{options.review_threshold} — caller must filter low scores first"
    )


def match_pdf_pages(
    desc_a: Sequence[PerPageDescriptor],
    desc_b: Sequence[PerPageDescriptor],
    *,
    options: Optional[PageMatchOptions] = None,
) -> List[PageMatchCandidate]:
    """Solve the page-to-page assignment between two PDF descriptor lists.

    Algorithm:
      1. If either side is empty, return only UNMATCHED candidates for
         the non-empty side.
      2. Compute the score matrix [N_a × N_b] via :func:`score_page_match`.
      3. Build a cost matrix ``cost = 1 - score`` and solve via
         :func:`scipy.optimize.linear_sum_assignment` (Hungarian).
         Falls back to a greedy nearest-neighbour matcher when scipy is
         missing.
      4. For each scipy-returned (row, col) pair, classify by score
         threshold. Pairs below ``review_threshold`` are dropped.
      5. Pages without a confirmed/review pair become UNMATCHED_A or
         UNMATCHED_B depending on which side they came from.

    Returns a list mixing matched + unmatched candidates. Caller can
    filter by ``candidate.is_matched`` if only successful pairs are
    needed.
    """

    opts = options or PageMatchOptions()
    n_a = len(desc_a)
    n_b = len(desc_b)

    # Edge cases — empty side
    if n_a == 0 and n_b == 0:
        return []
    if n_a == 0:
        return [
            PageMatchCandidate(
                page_a_index=-1,
                page_b_index=i,
                score=0.0,
                status=PageMatchStatus.UNMATCHED_B,
            )
            for i in range(n_b)
        ]
    if n_b == 0:
        return [
            PageMatchCandidate(
                page_a_index=i,
                page_b_index=-1,
                score=0.0,
                status=PageMatchStatus.UNMATCHED_A,
            )
            for i in range(n_a)
        ]

    # 1. Score matrix
    score_matrix: List[List[float]] = []
    breakdown_matrix: List[List[Dict[str, float]]] = []
    for a in desc_a:
        row_scores: List[float] = []
        row_breakdowns: List[Dict[str, float]] = []
        for b in desc_b:
            s, br = score_page_match(a, b, options=opts)
            row_scores.append(s)
            row_breakdowns.append(br)
        score_matrix.append(row_scores)
        breakdown_matrix.append(row_breakdowns)

    # 2. Solve assignment
    pairs = _assign_pages(score_matrix)

    # 3. Build candidates + classify
    matched_a: set[int] = set()
    matched_b: set[int] = set()
    candidates: List[PageMatchCandidate] = []
    for i, j in pairs:
        score = score_matrix[i][j]
        if score < opts.review_threshold:
            # Below review threshold → leave both pages unmatched
            continue
        status = _classify(score, opts)
        candidates.append(PageMatchCandidate(
            page_a_index=i,
            page_b_index=j,
            score=score,
            status=status,
            score_breakdown=dict(breakdown_matrix[i][j]),
        ))
        matched_a.add(i)
        matched_b.add(j)

    # 4. Unmatched pages
    for i in range(n_a):
        if i not in matched_a:
            candidates.append(PageMatchCandidate(
                page_a_index=i,
                page_b_index=-1,
                score=0.0,
                status=PageMatchStatus.UNMATCHED_A,
            ))
    for j in range(n_b):
        if j not in matched_b:
            candidates.append(PageMatchCandidate(
                page_a_index=-1,
                page_b_index=j,
                score=0.0,
                status=PageMatchStatus.UNMATCHED_B,
            ))

    logger.info(
        "match_pdf_pages: %d×%d pages → %d auto, %d review, %d unmatched-A, %d unmatched-B",
        n_a, n_b,
        sum(1 for c in candidates if c.status == PageMatchStatus.AUTO_CONFIRMED),
        sum(1 for c in candidates if c.status == PageMatchStatus.REVIEW_REQUIRED),
        sum(1 for c in candidates if c.status == PageMatchStatus.UNMATCHED_A),
        sum(1 for c in candidates if c.status == PageMatchStatus.UNMATCHED_B),
    )
    return candidates


def _assign_pages(score_matrix: List[List[float]]) -> List[Tuple[int, int]]:
    """Solve the assignment via scipy when available, greedy otherwise.

    Returns list of ``(i, j)`` pairs from the assignment. Always returns
    exactly ``min(N_a, N_b)`` pairs — the caller filters by score
    threshold.
    """

    n_a = len(score_matrix)
    n_b = len(score_matrix[0]) if n_a else 0
    if n_a == 0 or n_b == 0:
        return []

    # Try scipy first — proper Hungarian, optimal assignment
    try:
        from scipy.optimize import linear_sum_assignment  # type: ignore
        import numpy as np
        cost = np.array([
            [1.0 - s for s in row] for row in score_matrix
        ])
        row_ind, col_ind = linear_sum_assignment(cost)
        return list(zip(row_ind.tolist(), col_ind.tolist()))
    except ImportError:
        logger.info("scipy unavailable, falling back to greedy assignment")

    # Greedy fallback — sort all pairs by score descending, take in order
    flat: List[Tuple[float, int, int]] = []
    for i in range(n_a):
        for j in range(n_b):
            flat.append((score_matrix[i][j], i, j))
    flat.sort(reverse=True)

    used_a: set[int] = set()
    used_b: set[int] = set()
    pairs: List[Tuple[int, int]] = []
    for _score, i, j in flat:
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        pairs.append((i, j))
        if len(pairs) >= min(n_a, n_b):
            break
    return pairs


__all__ = [
    "PageMatchStatus",
    "PageMatchOptions",
    "PageMatchCandidate",
    "score_page_match",
    "match_pdf_pages",
]
