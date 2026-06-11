# -*- coding: utf-8 -*-
"""Quantify matcher misses: added↔deleted near-pairs that should be MODIFIED.

A failed entity match shows up in the output as an ``added`` record and a
``deleted`` record of the SAME layer/entity type at (nearly) the same spot.
Reviewers experience these as "미매칭 변경점" — one real edit reported as two
phantom changes. This script measures that signature directly from a run's
change artifacts, so matcher changes can be A/B'd on real pairs:

    near_pair_rate = paired(added,deleted within tol) / (added+deleted)

Inputs (auto-detected):
  * change-zone stream JSONL (compare_state/streams/pair_*.jsonl) — full record set
  * artifacts/change_zones.json — zone representatives (capped) fallback

Usage:
    python scripts/measure_match_nearpairs.py <stream.jsonl|change_zones.json> \
        [--tol 50] [--top 10]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

THRESHOLDS = (1.0, 10.0, 50.0, 200.0, 1000.0)


def _center(rec: dict) -> Optional[tuple[float, float]]:
    bbox = rec.get("bbox") or rec.get("old_bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        try:
            return ((float(bbox[0]) + float(bbox[2])) / 2.0,
                    (float(bbox[1]) + float(bbox[3])) / 2.0)
        except (TypeError, ValueError):
            pass
    loc = rec.get("location") or rec.get("old_location")
    if isinstance(loc, (list, tuple)) and len(loc) >= 2:
        try:
            return (float(loc[0]), float(loc[1]))
        except (TypeError, ValueError):
            pass
    return None


def _text(rec: dict) -> str:
    return str(rec.get("new_text") or rec.get("old_text") or "").strip()


def load_records(path: Path) -> list[dict]:
    """Load change records from a stream JSONL or change_zones.json."""

    if path.suffix.lower() == ".jsonl":
        records = []
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records
    data = json.loads(path.read_text(encoding="utf-8"))
    zones = data.get("zones") if isinstance(data, dict) else data
    records: list[dict] = []
    for zone in zones or []:
        for ch in (zone.get("representative_changes") or zone.get("changes") or []):
            if isinstance(ch, dict):
                records.append(ch)
    return records


def _grid_key(pt: tuple[float, float], cell: float) -> tuple[int, int]:
    return (int(math.floor(pt[0] / cell)), int(math.floor(pt[1] / cell)))


def greedy_near_pairs(
    added: list[tuple[tuple[float, float], dict]],
    deleted: list[tuple[tuple[float, float], dict]],
    tol: float,
) -> list[tuple[float, dict, dict]]:
    """Greedy nearest pairing within ``tol`` using a uniform grid (O(n))."""

    cell = max(tol, 1e-9)
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for idx, (pt, _rec) in enumerate(deleted):
        grid[_grid_key(pt, cell)].append(idx)
    used: set[int] = set()
    candidates: list[tuple[float, int, int]] = []
    for a_idx, (apt, _arec) in enumerate(added):
        gx, gy = _grid_key(apt, cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for d_idx in grid.get((gx + dx, gy + dy), ()):  # neighbours
                    dpt = deleted[d_idx][0]
                    dist = math.hypot(apt[0] - dpt[0], apt[1] - dpt[1])
                    if dist <= tol:
                        candidates.append((dist, a_idx, d_idx))
    candidates.sort(key=lambda c: c[0])
    used_a: set[int] = set()
    pairs: list[tuple[float, dict, dict]] = []
    for dist, a_idx, d_idx in candidates:
        if a_idx in used_a or d_idx in used:
            continue
        used_a.add(a_idx)
        used.add(d_idx)
        pairs.append((dist, added[a_idx][1], deleted[d_idx][1]))
    return pairs


def analyze(records: Iterable[dict], *, tol: float, top: int) -> dict[str, Any]:
    by_type: dict[str, int] = defaultdict(int)
    buckets: dict[tuple[str, str], dict[str, list]] = defaultdict(
        lambda: {"added": [], "deleted": []}
    )
    for rec in records:
        ctype = str(rec.get("change_type") or "").lower()
        by_type[ctype] += 1
        if ctype not in ("added", "deleted"):
            continue
        pt = _center(rec)
        if pt is None:
            continue
        key = (str(rec.get("layer") or ""), str(rec.get("entity_type") or "").lower())
        buckets[key][ctype].append((pt, rec))

    all_pairs: list[tuple[float, dict, dict]] = []
    max_tol = max(THRESHOLDS + (tol,))
    for key, sides in buckets.items():
        if sides["added"] and sides["deleted"]:
            all_pairs.extend(greedy_near_pairs(sides["added"], sides["deleted"], max_tol))
    all_pairs.sort(key=lambda p: p[0])

    added_n = by_type.get("added", 0)
    deleted_n = by_type.get("deleted", 0)
    denom = added_n + deleted_n
    per_threshold = {}
    for t in sorted(set(THRESHOLDS + (tol,))):
        n = sum(1 for d, _a, _b in all_pairs if d <= t)
        per_threshold[t] = {
            "pairs": n,
            "near_pair_rate": round(2 * n / denom, 4) if denom else None,
        }
    same_text = [
        (d, a, b) for d, a, b in all_pairs
        if d <= tol and _text(a) and _text(a) == _text(b)
    ]
    examples = []
    for d, a, b in all_pairs[:top]:
        examples.append({
            "distance": round(d, 2),
            "layer": a.get("layer"),
            "entity_type": a.get("entity_type"),
            "added_at": _center(a),
            "deleted_at": _center(b),
            "added_text": _text(a)[:40],
            "deleted_text": _text(b)[:40],
        })
    return {
        "counts_by_type": dict(by_type),
        "pairable_added": added_n,
        "pairable_deleted": deleted_n,
        "near_pairs_by_threshold": per_threshold,
        "same_text_pairs_within_tol": len(same_text),
        "examples_nearest": examples,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--tol", type=float, default=50.0)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    records = load_records(args.path)
    print(f"records={len(records)} from {args.path.name}")
    report = analyze(records, tol=args.tol, top=args.top)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
