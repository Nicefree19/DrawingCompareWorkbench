"""Manual override serialization for region-level matching."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


MANUAL_MATCH_STATUSES = {"manual_match", "manual_matched"}
UNMATCHED_BEFORE_STATUSES = {"unmatched_before", "manual_unmatched_before"}
UNMATCHED_AFTER_STATUSES = {"unmatched_after", "manual_unmatched_after"}


@dataclass(frozen=True)
class RegionMatchOverride:
    before_region_id: str = ""
    after_region_id: str = ""
    status: str = "manual_match"
    reason: str = "manual region match override"
    pair_id: str = ""

    def normalized_status(self) -> str:
        status = str(self.status or "").strip().lower()
        if status in MANUAL_MATCH_STATUSES:
            return "manual_match"
        if status in UNMATCHED_BEFORE_STATUSES:
            return "unmatched_before"
        if status in UNMATCHED_AFTER_STATUSES:
            return "unmatched_after"
        return status or "manual_match"

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "before_region_id": self.before_region_id,
            "after_region_id": self.after_region_id,
            "status": self.normalized_status(),
            "reason": self.reason,
        }
        if self.pair_id:
            payload["pair_id"] = self.pair_id
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RegionMatchOverride":
        return cls(
            pair_id=str(data.get("pair_id") or ""),
            before_region_id=str(data.get("before_region_id") or ""),
            after_region_id=str(data.get("after_region_id") or ""),
            status=str(data.get("status") or "manual_match"),
            reason=str(data.get("reason") or "manual region match override"),
        )


def write_region_match_overrides(
    overrides: Sequence[RegionMatchOverride],
    output_path: str | Path,
    *,
    pair_id: str = "",
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "pair_id": pair_id,
        "overrides": [override.to_dict() for override in overrides],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_region_match_overrides(
    input_path: str | Path,
    *,
    pair_id: str = "",
) -> tuple[RegionMatchOverride, ...]:
    path = Path(input_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"region match override file must be a JSON object: {path}")
    file_pair_id = str(payload.get("pair_id") or "")
    if pair_id and file_pair_id and file_pair_id != pair_id:
        return tuple()
    raw_overrides = payload.get("overrides") or []
    if not isinstance(raw_overrides, Sequence) or isinstance(raw_overrides, (str, bytes)):
        raise ValueError(f"region match overrides must be a list: {path}")
    overrides = []
    for raw in raw_overrides:
        if not isinstance(raw, Mapping):
            raise ValueError(f"region match override item must be an object: {path}")
        item_pair_id = str(raw.get("pair_id") or file_pair_id)
        if pair_id and item_pair_id and item_pair_id != pair_id:
            continue
        overrides.append(RegionMatchOverride.from_dict(raw))
    return tuple(overrides)


__all__ = [
    "RegionMatchOverride",
    "load_region_match_overrides",
    "write_region_match_overrides",
]
