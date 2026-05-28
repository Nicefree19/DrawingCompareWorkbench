"""Configurable CAD region detection profile.

Profiles keep office- or project-specific drawing conventions out of the
sheet detector's core geometry logic.  The defaults intentionally mirror the
legacy hard-coded behavior so existing callers get the same broad decisions
unless they opt into a custom profile.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .drawing_id_pattern import DRAWING_NUMBER_PATTERN_STR
from .title_block_layer_patterns import is_title_block_layer


def default_profile_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "region_profiles" / "default.yaml"


def _as_tuple(value: Any, fallback: Sequence[str]) -> tuple[str, ...]:
    if value is None:
        return tuple(fallback)
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value if str(item).strip())
    return tuple(fallback)


def _matches_pattern(value: str, patterns: Sequence[str]) -> bool:
    candidate = value.upper()
    for pattern in patterns:
        normalized = str(pattern).strip().upper()
        if not normalized:
            continue
        if fnmatch.fnmatch(candidate, normalized):
            return True
        if "*" not in normalized and "?" not in normalized and normalized in candidate:
            return True
    return False


def _contains_token(value: str, tokens: Sequence[str]) -> bool:
    candidate = value.upper()
    return any(str(token).strip().upper() in candidate for token in tokens if str(token).strip())


@dataclass(frozen=True)
class RegionProfile:
    """Detection tuning knobs for multi-detail CAD sheets."""

    name: str = "default"
    frame_layer_patterns: tuple[str, ...] = ("*FRAME*", "*BORDER*", "*SHEET*")
    title_layer_patterns: tuple[str, ...] = ("*TITLE*", "*TITLEBLOCK*")
    table_layer_patterns: tuple[str, ...] = (
        "*TABLE*",
        "*SCHEDULE*",
        "*BOM*",
        "*BLOCK INFO*",
        "*BLOCKINFO*",
    )
    table_reject_keywords: tuple[str, ...] = (
        "BLOCK INFO",
        "BLOCKINFO",
        "TITLE",
        "TITLEBLOCK",
        "REV",
        "REVISION",
        "TABLE",
        "SCHEDULE",
        "BOM",
        "MATERIAL",
        "DWG",
        "SHEET",
        "SCALE",
        "DATE",
        "DRAWN",
        "CHECK",
        "APPROVED",
    )
    structural_layer_tokens: tuple[str, ...] = ("BEAM", "COL", "SLAB", "WALL", "GRID", "REBAR")
    nonstructural_layer_tokens: tuple[str, ...] = (
        "TITLE",
        "TABLE",
        "REV",
        "BOM",
        "BLOCK INFO",
    )
    drawing_number_patterns: tuple[str, ...] = (
        DRAWING_NUMBER_PATTERN_STR,
        r"\b[A-Z]{1,4}[-_]?\d{1,4}(?:[-_][A-Z0-9]{1,6})?\b",
        r"\b\d{1,4}[-_][A-Z]{1,4}[-_]?\d{1,4}\b",
    )
    title_area_policy: str = "bottom_or_right_title_band"

    @classmethod
    @lru_cache(maxsize=1)
    def default(cls) -> "RegionProfile":
        path = default_profile_path()
        if path.exists():
            return cls.from_yaml(path)
        return cls()

    @classmethod
    def load(cls, value: "RegionProfile | str | Path | None" = None) -> "RegionProfile":
        if value is None:
            return cls.default()
        if isinstance(value, cls):
            return value
        return cls.from_yaml(Path(value))

    @classmethod
    def from_yaml(cls, path: str | Path) -> "RegionProfile":
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - declared dependency.
            raise RuntimeError("PyYAML is required to load region profiles") from exc

        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(data, Mapping):
            raise ValueError(f"region profile must be a mapping: {path}")
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RegionProfile":
        base = cls()
        return cls(
            name=str(data.get("name", base.name) or base.name),
            frame_layer_patterns=_as_tuple(
                data.get("frame_layer_patterns"),
                base.frame_layer_patterns,
            ),
            title_layer_patterns=_as_tuple(
                data.get("title_layer_patterns"),
                base.title_layer_patterns,
            ),
            table_layer_patterns=_as_tuple(
                data.get("table_layer_patterns"),
                base.table_layer_patterns,
            ),
            table_reject_keywords=_as_tuple(
                data.get("table_reject_keywords"),
                base.table_reject_keywords,
            ),
            structural_layer_tokens=_as_tuple(
                data.get("structural_layer_tokens"),
                base.structural_layer_tokens,
            ),
            nonstructural_layer_tokens=_as_tuple(
                data.get("nonstructural_layer_tokens"),
                base.nonstructural_layer_tokens,
            ),
            drawing_number_patterns=_as_tuple(
                data.get("drawing_number_patterns"),
                base.drawing_number_patterns,
            ),
            title_area_policy=str(
                data.get("title_area_policy", base.title_area_policy)
                or base.title_area_policy
            ),
        )

    def matches_frame_layer(self, layer: str) -> bool:
        return _matches_pattern(layer, self.frame_layer_patterns)

    def matches_title_layer(self, layer: str) -> bool:
        return is_title_block_layer(layer) or _matches_pattern(layer, self.title_layer_patterns)

    def matches_table_layer(self, layer: str) -> bool:
        return _matches_pattern(layer, self.table_layer_patterns)

    def contains_structural_token(self, layer: str) -> bool:
        return _contains_token(layer, self.structural_layer_tokens)

    def contains_nonstructural_token(self, layer: str) -> bool:
        return _contains_token(layer, self.nonstructural_layer_tokens)
