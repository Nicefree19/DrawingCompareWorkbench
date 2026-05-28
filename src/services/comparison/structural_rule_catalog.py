"""Deterministic structural drawing rule catalog.

The catalog is intentionally small and inspectable. It classifies text anchors
using only local text, entity type, and layer context; it does not make safety
or compliance decisions.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple


STRUCTURAL_DOMAIN_RULESET_VERSION = "0.2.0"

_STRUCTURAL_LAYER_HINTS = (
    "BEAM",
    "COLUMN",
    "COL",
    "BRACE",
    "GIRDER",
    "TRUSS",
    "WALL",
    "SLAB",
    "FOOTING",
    "FOUNDATION",
    "PILE",
    "FRAME",
    "GRID",
    "AXIS",
    "SECTION",
    "DETAIL",
    "SEC",
    "DIM",
    "BM_",
    "CL_",
    "GR_",
    "WL_",
    "FT_",
    "구조",
    "기둥",
    "보",
    "벽",
    "슬래브",
    "기초",
    "철근",
    "배근",
    "단면",
    "상세",
    "그리드",
    "축선",
)
_GRID_LAYER_HINTS = (
    "GRID",
    "GRD",
    "AXIS",
    "그리드",
    "축선",
)
_GRID_RE = re.compile(
    r"\bGRID[-_ ]?[A-Z0-9]+(?:[-_ ][A-Z0-9]+)?\b",
    re.IGNORECASE,
)
_GRID_LABEL_RE = re.compile(
    r"(?:[A-Z]{1,3}[-_ ]?)?\d+[A-Z]?|[A-Z]{1,3}[-_ ]?\d+[A-Z]?",
    re.IGNORECASE,
)
_MEMBER_TAG_RE = re.compile(
    r"\b(?:COLUMN|COL|BEAM|GIRDER|BRACE|SLAB|WALL|FOOTING|PILE)\s+[A-Z]{0,3}\d+[A-Z]?\b"
    r"|\b(?:C|B|G|BM|CL|BR|W|F)[-_ ]?\d+[A-Z]?\b"
    r"|\bH[-_ ]?\d+(?:X|x)\d+\b"
    r"|(?:기둥|보|벽체|벽|슬래브|바닥|기초|파일|말뚝|브레이스)\s*[A-Z]{0,3}[-_ ]?\d+[A-Z]?",
    re.IGNORECASE,
)
_SECTION_REF_RE = re.compile(
    r"\b(?:SEE\s+)?S[-_ ]?\d{2,4}(?:[/.-][A-Z0-9]+)?\b"
    r"|\b(?:SECTION|DETAIL|REF(?:ERENCE)?)\b"
    r"|(?:상세|단면|참조)\s*S[-_ ]?\d{2,4}(?:[/.-][A-Z0-9]+)?"
    r"|(?:상세|단면|참조)",
    re.IGNORECASE,
)
_DIMENSION_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:MM|M)?\b", re.IGNORECASE)
_NOTE_RE = re.compile(r"\b(?:NOTE|GENERAL\s+NOTE|REMARK)\b|(?:주기|일반사항|비고)")


def classify_domain_patterns(
    *,
    entity_type: str,
    text: str,
    layer_name: str,
) -> Tuple[List[str], List[Dict[str, str]]]:
    """Return bounded structural domain tags and matched pattern values."""

    tags: List[str] = []
    matches: List[Dict[str, str]] = []

    def add(kind: str, value: str, confidence: str) -> None:
        cleaned = str(value).strip()
        if not cleaned:
            return
        if kind not in tags:
            tags.append(kind)
        row = {"kind": kind, "value": cleaned, "confidence": confidence}
        if row not in matches:
            matches.append(row)

    for match in _GRID_RE.finditer(text):
        add("grid", match.group(0), "high")
    if is_grid_layer(layer_name) and _GRID_LABEL_RE.fullmatch(text.strip()):
        add("grid", text, "medium")

    for match in _MEMBER_TAG_RE.finditer(text):
        add("member_tag", match.group(0), "high")
    for match in _SECTION_REF_RE.finditer(text):
        add("section_reference", match.group(0), "high")
    for match in _NOTE_RE.finditer(text):
        add("note", match.group(0), "medium")

    if str(entity_type or "") == "dimension":
        add("dimension", text, "high")
    elif looks_like_dimension_text(text, layer_name):
        for match in _DIMENSION_RE.finditer(text):
            add("dimension", match.group(0), "medium")

    if is_structural_layer(layer_name):
        add("structural_layer", layer_name, "medium")

    return tags, matches


def is_structural_layer(layer_name: str) -> bool:
    layer_key = str(layer_name or "").upper()
    return any(hint.upper() in layer_key for hint in _STRUCTURAL_LAYER_HINTS)


def is_grid_layer(layer_name: str) -> bool:
    layer_key = str(layer_name or "").upper()
    return any(hint.upper() in layer_key for hint in _GRID_LAYER_HINTS)


def looks_like_dimension_text(text: str, layer_name: str) -> bool:
    if _DIMENSION_RE.fullmatch(str(text or "").strip()):
        return True
    return "DIM" in str(layer_name or "").upper()


def looks_like_reference(text: str) -> bool:
    text_value = str(text or "")
    return bool(
        re.search(r"\b[A-Z]{1,4}[-_]?\d+[A-Z]?\b|\bS[-_]?\d{2,4}\b", text_value.upper())
        or _SECTION_REF_RE.search(text_value)
    )


__all__ = [
    "STRUCTURAL_DOMAIN_RULESET_VERSION",
    "classify_domain_patterns",
    "is_grid_layer",
    "is_structural_layer",
    "looks_like_dimension_text",
    "looks_like_reference",
]
