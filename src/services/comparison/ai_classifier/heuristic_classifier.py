# -*- coding: utf-8 -*-
"""Phase H Stage 1 — heuristic-only zone classifier.

Zero-dependency starter implementation. Uses keyword matching against
each zone's:
  * ``layer`` name (e.g. "BEAM", "COLUMN", "GRID", "DIM", "TEXT")
  * ``entity_type`` (e.g. "TEXT", "MTEXT", "DIMENSION", "LWPOLYLINE")
  * ``change_type`` (added / deleted / modified / moved)
  * extracted text snippet (when overlay carries one)

Produces a ``ChangeClassification`` per zone in <1ms each. The
embedding + LLM tiers (next iterations) plug into the same dispatch
contract — they REPLACE the heuristic call when available, never run
alongside.

This module's correctness depends on a) the keyword patterns matching
real customer drawings — verified against the user's S20 (평택 복합)
DWG dataset and the 17-change PDF revision pair.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from typing import Any, Optional

from .schema import (
    ChangeCategory,
    Severity,
    ChangeClassification,
    CATEGORY_LABELS_KO,
    DEFAULT_SEVERITY_BY_CATEGORY,
    DEFAULT_ACTION_BY_SEVERITY,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Layer-keyword → category mapping
# ---------------------------------------------------------------------------
# Patterns are MATCHED IN ORDER — first match wins. Each entry is a
# (compiled regex, category) pair. Patterns are case-insensitive and
# match against the layer name. The most specific patterns come first
# so e.g. "BEAM_TEXT" classifies as TEXT_LABEL, not STRUCTURAL_MEMBER.

_LAYER_PATTERNS: list[tuple[re.Pattern[str], ChangeCategory]] = [
    # Text/label layers (high priority — beats member layer suffix)
    (re.compile(r"(?:^|_)(TEXT|LABEL|TXT|MTEXT|NOTE|REMARK|주기|텍스트)(?:$|_)", re.I),
     ChangeCategory.TEXT_LABEL),
    # Dimension layers
    (re.compile(r"(?:^|_)(DIM|DIMENSION|치수)(?:$|_)", re.I),
     ChangeCategory.DIMENSION),
    # Grid layers
    (re.compile(r"(?:^|_)(GRID|AXIS|그리드|축선)(?:$|_)", re.I),
     ChangeCategory.GRID),
    # Detail / section layers
    (re.compile(r"(?:^|_)(DETAIL|SECTION|상세|단면)(?:$|_)", re.I),
     ChangeCategory.DETAIL_DRAWING),
    # Structural member layers — broadest match comes last
    (re.compile(
        r"(?:^|_)(BEAM|COLUMN|SLAB|WALL|FOOTING|FOUNDATION|"
        r"GIRDER|JOIST|PILE|보|기둥|슬래브|벽체|기초)(?:$|_)",
        re.I,
    ), ChangeCategory.STRUCTURAL_MEMBER),
]


# Entity-type → category fallback when layer doesn't match.
_ENTITY_TYPE_MAP: dict[str, ChangeCategory] = {
    "TEXT": ChangeCategory.TEXT_LABEL,
    "MTEXT": ChangeCategory.TEXT_LABEL,
    "PDF_TEXT": ChangeCategory.TEXT_LABEL,
    "DIMENSION": ChangeCategory.DIMENSION,
    "ALIGNED_DIMENSION": ChangeCategory.DIMENSION,
    "ROTATED_DIMENSION": ChangeCategory.DIMENSION,
    "LINE": ChangeCategory.STRUCTURAL_MEMBER,
    "LWPOLYLINE": ChangeCategory.STRUCTURAL_MEMBER,
    "POLYLINE": ChangeCategory.STRUCTURAL_MEMBER,
    "CIRCLE": ChangeCategory.STRUCTURAL_MEMBER,  # often columns
    "ARC": ChangeCategory.STRUCTURAL_MEMBER,
    "INSERT": ChangeCategory.STRUCTURAL_MEMBER,  # block reference
    "HATCH": ChangeCategory.DETAIL_DRAWING,
    "LEADER": ChangeCategory.NOTE,
    "MULTILEADER": ChangeCategory.NOTE,
}


# Text-content keywords — boost the category when the zone has an OCR
# snippet that mentions a structural concept.
_TEXT_KEYWORDS: list[tuple[re.Pattern[str], ChangeCategory]] = [
    (re.compile(r"H\d|□\d|HSS|H[-_ ]?BEAM|보|GIRDER|JOIST"), ChangeCategory.STRUCTURAL_MEMBER),
    (re.compile(r"기둥|COLUMN|COL\d|HSS|PIPE"), ChangeCategory.STRUCTURAL_MEMBER),
    (re.compile(r"^\s*[XY]\d+\s*$|GRID|AXIS"), ChangeCategory.GRID),
    (re.compile(r"\b\d{2,5}\s*mm\b|\bDIM\b|치수"), ChangeCategory.DIMENSION),
    (re.compile(r"NOTE|주기|REMARK|일반사항"), ChangeCategory.NOTE),
]


def _normalize_korean(text: str) -> str:
    """Phase I review fix #4 + 3rd-review fix P2: delegate to the
    Stage-2 canonical text helper.

    Originally this was a Stage-1-local NFKC+NFC + control-char strip
    so the heuristic classifier could match fullwidth Latin / NFD
    Hangul / zero-width-joiner inputs from messy CAD exports.

    3rd-review feedback flagged that having TWO separate normalisation
    chains (Stage-1 ``_normalize_korean()`` and Stage-2 ``normalizer.
    canonicalize_zone_text()``) creates long-term drift risk: a fix
    or new domain token added to one wouldn't auto-propagate to the
    other → "분류는 A로 보는데 임베딩 검색 키는 B로 본다".

    Resolution: Stage-1 now calls Stage-2's canonical pipeline. The
    Stage-2 helper does the same NFKC + NFC + control strip the
    Stage-1 version did, PLUS the domain-token canonicalisation
    (H_BEAM_..., GRID_..., DIM_..., etc.). The extra tokenisation is
    a no-op for layer/text matching because the regex lookups don't
    care about those substrings — they just need fullwidth and NFD
    collapsed.

    Single source of truth → no drift. Tests on both layers continue
    to pass because the canonical form is a strict superset of what
    Stage-1 needed.
    """

    if not text:
        return ""
    from .normalizer import canonicalize_zone_text
    return canonicalize_zone_text(text)


def _category_from_layer(layer: str) -> Optional[ChangeCategory]:
    if not layer:
        return None
    layer = _normalize_korean(layer)
    for pattern, cat in _LAYER_PATTERNS:
        if pattern.search(layer):
            return cat
    return None


def _category_from_entity_type(entity_type: str) -> Optional[ChangeCategory]:
    if not entity_type:
        return None
    return _ENTITY_TYPE_MAP.get(entity_type.upper())


def _category_from_text(snippet: str) -> Optional[ChangeCategory]:
    if not snippet:
        return None
    snippet = _normalize_korean(snippet)
    for pattern, cat in _TEXT_KEYWORDS:
        if pattern.search(snippet):
            return cat
    return None


def _summary_korean(
    category: ChangeCategory, change_type: str, raw_count: int,
    layer: str, snippet: str,
) -> str:
    """Build a one-line Korean summary used in the workbench right-panel.

    Example outputs:
      "BEAM 레이어 추가 5건"
      "치수 텍스트 수정 1건 (\"5000mm → 5500mm\")"
      "GRID X3 위치 변경"
    """

    cat_label = CATEGORY_LABELS_KO[category]
    change_word = {
        "added": "추가", "deleted": "삭제",
        "modified": "수정", "moved": "이동",
    }.get(str(change_type or "").lower(), "변경")

    parts = [cat_label]
    if layer:
        parts.append(f"({layer} 레이어)")
    parts.append(f"{change_word}")
    if raw_count and raw_count > 0:
        parts.append(f"{raw_count}건")
    if snippet:
        snippet_short = snippet.strip().replace("\n", " ")[:30]
        if snippet_short:
            parts.append(f'"{snippet_short}"')
    return " ".join(parts)


def classify_zone_heuristic(zone: dict) -> ChangeClassification:
    """Classify a single zone using only keyword + entity-type rules.

    Args:
        zone: Overlay-shaped dict (workbench's ``_active_overlays_by_zone``
            value). Reads ``zone_id``, ``layer``, ``entity_type``,
            ``change_type``, ``raw_change_count``, optional
            ``text_snippet``.

    Returns:
        A populated ``ChangeClassification``. Even completely empty
        input produces a result with category=UNKNOWN, severity=NORMAL,
        and confidence=0.3 — never None.
    """

    t0 = time.perf_counter()
    zone_id = str(zone.get("zone_id") or "")
    layer = str(zone.get("layer") or "")
    entity_type = str(zone.get("entity_type") or "")
    change_type = str(zone.get("change_type") or "")
    raw_count = int(zone.get("raw_change_count") or 0)
    snippet = str(zone.get("text_snippet") or "")

    # Resolution chain — most specific signal wins
    category: Optional[ChangeCategory] = None
    confidence = 0.3  # baseline for uninformed
    why = []

    # 1. Layer name
    cat_from_layer = _category_from_layer(layer)
    if cat_from_layer:
        category = cat_from_layer
        confidence = 0.85
        why.append(f"layer:{layer}")

    # 2. Text snippet — can override layer when text strongly suggests
    # a different category (e.g. dimension text on a BEAM layer)
    cat_from_text = _category_from_text(snippet)
    if cat_from_text and cat_from_text != ChangeCategory.UNKNOWN:
        if category is None or category == ChangeCategory.UNKNOWN:
            category = cat_from_text
            confidence = max(confidence, 0.75)
        # Text snippet can boost confidence even when layer matched
        elif category == cat_from_text:
            confidence = min(0.95, confidence + 0.10)
        why.append(f"text:{snippet[:20]}")

    # 3. Entity type fallback
    if category is None:
        cat_from_entity = _category_from_entity_type(entity_type)
        if cat_from_entity:
            category = cat_from_entity
            confidence = 0.55
            why.append(f"entity:{entity_type}")

    if category is None:
        category = ChangeCategory.UNKNOWN
        confidence = 0.3
        why.append("default")

    severity = DEFAULT_SEVERITY_BY_CATEGORY[category]
    # PDF_PAGE_x layer (visual diff) → minor severity
    if layer.startswith("PDF_PAGE_"):
        severity = Severity.MINOR
    action = DEFAULT_ACTION_BY_SEVERITY[severity]

    summary = _summary_korean(category, change_type, raw_count, layer, snippet)

    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    return ChangeClassification(
        zone_id=zone_id,
        category=category,
        severity=severity,
        confidence=confidence,
        suggested_action=action,
        summary_ko=summary,
        kds_references=[],  # populated by LLM tier
        classifier_used="heuristic",
        elapsed_ms=elapsed_ms,
        raw_evidence={"why": why, "layer": layer, "entity_type": entity_type},
    )


__all__ = ["classify_zone_heuristic"]
