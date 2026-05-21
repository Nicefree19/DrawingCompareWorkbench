# -*- coding: utf-8 -*-
"""Heuristic categorisation of change zones.

Given an overlay/change-zone record, this module returns a Korean category
label + severity hint + plain-language description so the reviewer can prioritise
the kinds of changes that matter most. The classifier is **rule-based** so it
runs offline with no API cost; the public ``classify_zone()`` signature is
deliberately compatible with a future LLM-backed enrichment path (Phase E2.x)
that would call out for additional context.

Categories (ordered by typical structural-engineering review priority):
1. **구조 부재 변경** — beam/column/brace/wall layer match (highest priority)
2. **그리드 변경** — grid/axis lines (very high — geometric reference shift)
3. **치수/주석 변경** — dimension/text only (medium — reference data)
4. **상세/마킹 변경** — detail/section markers (medium)
5. **레이어/표기 변경** — layer rename, line type only (low)
6. **기타 변경** — fallback

The classifier inspects:
- ``change_type`` (added/deleted/modified/moved)
- ``layer`` (or ``top_layers``) name patterns
- ``entity_types`` (LINE, TEXT, MTEXT, ARC, ...)
- ``raw_change_count`` (large counts → boost severity)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

# Default Korean labels — keep stable so tests can assert exact text.
CATEGORY_STRUCTURAL_MEMBER = "구조 부재 변경"
CATEGORY_GRID = "그리드 변경"
CATEGORY_DIMENSION = "치수/주석 변경"
CATEGORY_DETAIL = "상세/마킹 변경"
CATEGORY_LAYER = "레이어/표기 변경"
CATEGORY_OTHER = "기타 변경"

# Layer name patterns mapped to category. Tested against case-insensitive
# substring/regex matches on the overlay's "layer" or first "top_layers" entry.
# Order matters — first match wins, so structural patterns precede dimensions.
_LAYER_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"BEAM|COL(?:UMN)?|BRACE|TRUSS|GIRDER|기둥|보|가새|트러스", re.IGNORECASE), CATEGORY_STRUCTURAL_MEMBER),
    (re.compile(r"WALL|SLAB|PLATE|FOOTING|FOUNDATION|벽|슬래브|기초", re.IGNORECASE), CATEGORY_STRUCTURAL_MEMBER),
    (re.compile(r"AXIS|GRID|GR_|축선|그리드|XGRID|YGRID", re.IGNORECASE), CATEGORY_GRID),
    (re.compile(r"DIM|DIMS|TXT|TEXT|MTEXT|치수|주석", re.IGNORECASE), CATEGORY_DIMENSION),
    (re.compile(r"DETL|DETAIL|MKX|MARK|PCN|SEC|상세|마킹|단면", re.IGNORECASE), CATEGORY_DETAIL),
    (re.compile(r"LAYER|HATCH|LINETYPE|레이어|해치", re.IGNORECASE), CATEGORY_LAYER),
)

# Entity-type fallback when no layer pattern hits — TEXT-only changes default to
# 치수/주석 (often dimension labels), pure LINE/ARC defaults to 기타.
_ENTITY_FALLBACK: dict[str, str] = {
    "TEXT": CATEGORY_DIMENSION,
    "MTEXT": CATEGORY_DIMENSION,
    "DIMENSION": CATEGORY_DIMENSION,
    "INSERT": CATEGORY_DETAIL,
}


@dataclass
class ZoneCategoryResult:
    """Outcome of classifying a single change zone."""

    category: str
    confidence: float  # 0..1 — heuristic match strength
    severity_boost: int  # added to existing severity score for prioritisation
    rationale_ko: str  # one-line explanation shown in the detail panel
    # Phase O4 — noise score (0..1, 1=확실 노이즈). 0 = 신경 쓸 가치 있는
    # 변경. zone promote 차단의 1차 신호. classify_zone() 단독으로는
    # 항상 0 — 컨텍스트를 가진 caller(change_zones._compute_zone_noise_score)
    # 가 별도로 계산해 zone.metadata 에 기록한다.
    noise_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "confidence": self.confidence,
            "severity_boost": self.severity_boost,
            "rationale_ko": self.rationale_ko,
            "noise_score": self.noise_score,
        }


def classify_zone(zone: dict[str, Any]) -> ZoneCategoryResult:
    """Categorise one zone (overlay or top_issue dict) heuristically."""

    if not isinstance(zone, dict):
        return ZoneCategoryResult(
            category=CATEGORY_OTHER,
            confidence=0.0,
            severity_boost=0,
            rationale_ko="입력 데이터가 비어있어 기본 분류로 처리",
        )

    layer_text = _extract_layer_text(zone)
    entities = _extract_entity_types(zone)
    change_type = str(zone.get("change_type") or "").lower()
    raw_count = _safe_int(zone.get("raw_change_count"))

    # 1) Layer name pattern wins
    for pattern, category in _LAYER_PATTERNS:
        match = pattern.search(layer_text)
        if match:
            confidence = 0.85 if category in {CATEGORY_STRUCTURAL_MEMBER, CATEGORY_GRID} else 0.7
            severity_boost = _severity_boost_for(category, change_type, raw_count)
            rationale = _rationale_for_layer_match(category, match.group(0), change_type, raw_count)
            return ZoneCategoryResult(category, confidence, severity_boost, rationale)

    # 2) Entity-type fallback
    if entities:
        top_entity = entities[0]
        if top_entity in _ENTITY_FALLBACK:
            category = _ENTITY_FALLBACK[top_entity]
            confidence = 0.5
            severity_boost = _severity_boost_for(category, change_type, raw_count)
            rationale = (
                f"엔티티 타입 ‘{top_entity}’ 기반 분류 — 레이어 단서 없음. "
                f"{_change_type_phrase(change_type)} {raw_count}건"
            )
            return ZoneCategoryResult(category, confidence, severity_boost, rationale)

    # 3) Pure default
    severity_boost = max(0, raw_count // 50)  # tiny boost for huge change counts
    return ZoneCategoryResult(
        category=CATEGORY_OTHER,
        confidence=0.2,
        severity_boost=severity_boost,
        rationale_ko=f"분류 단서 부족 — {_change_type_phrase(change_type)} {raw_count}건",
    )


def classify_zones(zones: Iterable[dict[str, Any]]) -> list[ZoneCategoryResult]:
    """Convenience batch wrapper."""

    return [classify_zone(z) for z in zones]


def category_summary(results: Iterable[ZoneCategoryResult]) -> dict[str, int]:
    """Aggregate counts per category for the dashboard label."""

    counts: dict[str, int] = {}
    for r in results:
        counts[r.category] = counts.get(r.category, 0) + 1
    return counts


# --- helpers ---------------------------------------------------------------


def _extract_layer_text(zone: dict[str, Any]) -> str:
    """Best-effort layer name string from heterogeneous zone records."""

    pieces: list[str] = []
    for key in ("layer", "top_layers", "layers", "major_layers"):
        value = zone.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            pieces.append(value)
        elif isinstance(value, (list, tuple)):
            pieces.extend(str(v) for v in value if v)
    return " | ".join(pieces)


def _extract_entity_types(zone: dict[str, Any]) -> list[str]:
    """Return the entity types in priority order."""

    raw = zone.get("entity_types") or zone.get("top_entity_types") or zone.get("entity_type")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [token.strip().upper() for token in re.split(r"[|,]", raw) if token.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(v).strip().upper() for v in raw if v]
    return []


def _safe_int(value: Any) -> int:
    try:
        return int(float(value)) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _change_type_phrase(change_type: str) -> str:
    return {
        "added": "추가",
        "deleted": "삭제",
        "modified": "수정",
        "moved": "이동",
        "mixed": "혼합 변경",
    }.get(change_type, "변경")


def _severity_boost_for(category: str, change_type: str, raw_count: int) -> int:
    base = {
        CATEGORY_STRUCTURAL_MEMBER: 10,
        CATEGORY_GRID: 8,
        CATEGORY_DETAIL: 4,
        CATEGORY_DIMENSION: 2,
        CATEGORY_LAYER: 1,
        CATEGORY_OTHER: 0,
    }.get(category, 0)
    if change_type in {"added", "deleted"}:
        base += 1  # binary changes draw more attention than tweaks
    if raw_count >= 500:
        base += 3
    elif raw_count >= 100:
        base += 1
    return base


def _rationale_for_layer_match(
    category: str,
    matched_token: str,
    change_type: str,
    raw_count: int,
) -> str:
    head = {
        CATEGORY_STRUCTURAL_MEMBER: "구조 부재 layer",
        CATEGORY_GRID: "그리드/축선 layer",
        CATEGORY_DIMENSION: "치수/주석 layer",
        CATEGORY_DETAIL: "상세/마킹 layer",
        CATEGORY_LAYER: "표기 layer",
        CATEGORY_OTHER: "layer",
    }.get(category, "layer")
    qualifier = ""
    if raw_count >= 500:
        qualifier = " · 대량 변경"
    elif raw_count >= 100:
        qualifier = " · 다수 변경"
    return f"{head} 매칭 ‘{matched_token}’ — {_change_type_phrase(change_type)} {raw_count}건{qualifier}"
