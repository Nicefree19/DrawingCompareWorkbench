# -*- coding: utf-8 -*-
"""Phase J Step 4 (K1) — extract zones from review_state.json + change_zones.json
into a flat CSV ready for human labeling.

Discovers all `change_zones.json` files under the search root, reads
their accompanying `review_state.json` (when present, used to
prioritise already-reviewed zones), and emits one CSV row per zone
with all the evidence fields the AI classifier reads.

Output CSV columns:
  zone_id, pair_id, drawing_number, source_a_filename, source_b_filename,
  change_type, raw_count, layer (first), entity_type (first),
  review_status, expected_category (BLANK — user fills in)

Sampling policy (when --max-zones < total):
  1. Stratified by review_status: prefer confirmed > needs_review >
     ignored. Confirmed zones carry validated user judgment, so
     they're the most reliable training signal.
  2. Within each status bucket, stratified by drawing_number so the
     golden set spans many pairs (avoids over-sampling a single
     long drawing).
  3. After stratification, random.shuffle with a fixed seed so the
     CSV is reproducible.

Usage:
    python tools/extract_zones_for_labeling.py \\
        --root "D:\\path\\to\\drawing\\compare\\runs" \\
        --output tools/labeling/zones_to_label_v1.csv \\
        --max-zones 200 \\
        --seed 42

Then the user opens the CSV in Excel / a text editor and fills in
the ``expected_category`` column with one of:
  structural_member, dimension, text_label, grid, layout,
  detail_drawing, note, unknown

Then runs ``tools/build_golden_set_v2.py`` to convert to JSON.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CSV_COLUMNS: tuple[str, ...] = (
    "zone_id",
    "pair_id",
    "drawing_number",
    "source_a_filename",
    "source_b_filename",
    "change_type",
    "raw_count",
    "layer",
    "entity_type",
    "review_status",
    "expected_category",
)

# Status priority for stratified sampling. Higher index = lower
# priority. Confirmed zones are the most reliable training signal
# (user has explicitly accepted the change).
STATUS_PRIORITY: tuple[str, ...] = (
    "confirmed",
    "needs_review",
    "review_required",
    "ignored",
    "false_positive",
)

CHANGE_ZONES_FILENAME = "change_zones.json"
REVIEW_STATE_FILENAME = "review_state.json"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _setup_logging() -> None:
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, datefmt="%H:%M:%S")
    for n in ("matplotlib", "fontTools", "ezdxf", "PIL"):
        logging.getLogger(n).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _iter_change_zone_files(root: Path) -> Iterator[Path]:
    """Yield every change_zones.json under ``root`` (recursive)."""

    for path in root.rglob(CHANGE_ZONES_FILENAME):
        if path.is_file():
            yield path


def _read_json_safely(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _zones_from_change_file(path: Path) -> list[dict[str, Any]]:
    """Parse one change_zones.json. Returns a flat list of zone dicts.

    The file shape is the output of
    ``ChangeArtifactPackage.to_dict()`` — typically:

      {"schema_version": ..., "pairs": [
          {"pair_id": "...", "drawing_number": "...",
           "source_a": "...", "source_b": "...",
           "zones": [{"zone_id": "...", ...}, ...]},
          ...
      ]}

    We tolerate variations: top-level "items"/"pairs"/"results" + a
    flat "zones" list as fallback.
    """

    payload = _read_json_safely(path)
    if not isinstance(payload, dict):
        return []

    out: list[dict[str, Any]] = []
    pairs = (
        payload.get("pairs")
        or payload.get("items")
        or payload.get("results")
        or []
    )
    if not isinstance(pairs, list):
        return []

    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        pair_id = str(pair.get("pair_id") or pair.get("pair_uuid") or "")
        drawing_number = str(pair.get("drawing_number") or "")
        source_a = Path(str(pair.get("source_a") or "")).name
        source_b = Path(str(pair.get("source_b") or "")).name
        zones = pair.get("zones") or []
        if not isinstance(zones, list):
            continue
        for z in zones:
            if not isinstance(z, dict):
                continue
            out.append({
                **z,
                "_pair_id": pair_id,
                "_drawing_number": drawing_number,
                "_source_a": source_a,
                "_source_b": source_b,
            })

    # Fallback: flat "zones" array at root (older schema)
    if not out:
        flat = payload.get("zones")
        if isinstance(flat, list):
            for z in flat:
                if isinstance(z, dict):
                    out.append({
                        **z,
                        "_pair_id": str(z.get("pair_id") or ""),
                        "_drawing_number": str(z.get("drawing_number") or ""),
                        "_source_a": "",
                        "_source_b": "",
                    })
    return out


def _read_review_state(change_zones_path: Path) -> dict[str, str]:
    """Read review_state.json sitting next to change_zones.json.

    Returns ``{key: status}`` where key = "{pair_id}::{zone_id}".
    Missing file → empty dict (caller treats as "needs_review").
    """

    candidate = change_zones_path.parent / REVIEW_STATE_FILENAME
    payload = _read_json_safely(candidate)
    if not isinstance(payload, dict):
        return {}
    rows = payload.get("records") or []
    if not isinstance(rows, list):
        return {}
    out: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        pair_id = str(row.get("pair_id") or "")
        zone_id = str(row.get("zone_id") or "")
        status = str(row.get("status") or "needs_review")
        if pair_id and zone_id:
            out[f"{pair_id}::{zone_id}"] = status
    return out


# ---------------------------------------------------------------------------
# Row-building
# ---------------------------------------------------------------------------


def _build_csv_rows(
    zones: list[dict[str, Any]],
    review_status: dict[str, str],
) -> list[dict[str, str]]:
    """Convert raw zone dicts → CSV rows. Adds review_status from
    the parallel review_state.json + leaves expected_category blank."""

    rows: list[dict[str, str]] = []
    for z in zones:
        pair_id = str(z.get("_pair_id") or z.get("pair_id") or "")
        zone_id = str(z.get("zone_id") or "")
        if not (pair_id and zone_id):
            continue
        layers = z.get("layers") or []
        entity_types = z.get("entity_types") or []
        # First layer / entity_type is what the dispatcher's
        # _zone_evidence_text() reads in production. The CSV row
        # should match that, NOT the full tuple.
        layer = str(layers[0]) if layers else ""
        entity_type = str(entity_types[0]) if entity_types else ""
        change_type = str(z.get("change_type") or "")
        raw_count = z.get("raw_change_count")
        try:
            raw_count_int = int(raw_count) if raw_count is not None else 0
        except (TypeError, ValueError):
            raw_count_int = 0
        status = review_status.get(f"{pair_id}::{zone_id}", "needs_review")
        rows.append({
            "zone_id": zone_id,
            "pair_id": pair_id,
            "drawing_number": str(z.get("_drawing_number")
                                  or z.get("drawing_number") or ""),
            "source_a_filename": str(z.get("_source_a") or ""),
            "source_b_filename": str(z.get("_source_b") or ""),
            "change_type": change_type,
            "raw_count": str(raw_count_int),
            "layer": layer,
            "entity_type": entity_type,
            "review_status": status,
            "expected_category": "",  # user fills in
        })
    return rows


# ---------------------------------------------------------------------------
# Stratified sampling
# ---------------------------------------------------------------------------


def _stratified_sample(
    rows: list[dict[str, str]],
    max_zones: int,
    seed: int,
) -> list[dict[str, str]]:
    """Sample at most ``max_zones`` rows. Stratify by:
      1. review_status (priority order: confirmed first)
      2. drawing_number within each status bucket

    Deterministic given the seed."""

    if max_zones <= 0 or len(rows) <= max_zones:
        return list(rows)

    rng = random.Random(seed)
    by_status: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        by_status[r.get("review_status") or "needs_review"].append(r)

    # Walk status priority, drawing_number-stratified within each
    selected: list[dict[str, str]] = []
    remaining = max_zones
    for status in STATUS_PRIORITY:
        bucket = by_status.pop(status, [])
        if not bucket or remaining <= 0:
            continue
        # Drawing-number stratification within bucket
        by_drawing: dict[str, list[dict[str, str]]] = defaultdict(list)
        for r in bucket:
            by_drawing[r["drawing_number"]].append(r)
        # Round-robin pick
        drawings = sorted(by_drawing.keys())
        rng.shuffle(drawings)
        local: list[dict[str, str]] = []
        while drawings and len(local) < remaining:
            for dn in drawings[:]:
                if not by_drawing[dn]:
                    drawings.remove(dn)
                    continue
                # Pick a random row within this drawing
                idx = rng.randrange(len(by_drawing[dn]))
                local.append(by_drawing[dn].pop(idx))
                if len(local) >= remaining:
                    break
        selected.extend(local)
        remaining = max_zones - len(selected)

    # Drain remaining buckets (status not in priority list — defensive)
    if remaining > 0:
        leftover: list[dict[str, str]] = []
        for bucket in by_status.values():
            leftover.extend(bucket)
        rng.shuffle(leftover)
        selected.extend(leftover[:remaining])

    return selected[:max_zones]


# ---------------------------------------------------------------------------
# CSV write
# ---------------------------------------------------------------------------


def _write_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in CSV_COLUMNS})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract review zones from change_zones.json files "
                    "into a flat CSV ready for human labeling (Phase J K1).",
    )
    parser.add_argument(
        "--root", type=Path, required=True,
        help="Search root containing change_zones.json files (recursive).",
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path("tools/labeling/zones_to_label_v1.csv"),
        help="Output CSV path.",
    )
    parser.add_argument(
        "--max-zones", type=int, default=200,
        help="Maximum number of zones to sample (stratified). 0 = no cap.",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for reproducible sampling.",
    )
    parser.add_argument(
        "--include-statuses", type=str, default="confirmed,needs_review",
        help=("Comma-separated review statuses to include. "
              "Default keeps confirmed + needs_review. "
              "Pass 'all' to include every status."),
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    _setup_logging()
    args = parse_args(argv)
    log = logging.getLogger("extract_zones")

    if not args.root.exists() or not args.root.is_dir():
        log.error("--root %s does not exist or is not a directory",
                  args.root)
        return 1

    # ----- Discover + parse -----
    all_zones: list[dict[str, Any]] = []
    n_files = 0
    for change_file in _iter_change_zone_files(args.root):
        n_files += 1
        zones = _zones_from_change_file(change_file)
        if not zones:
            log.debug("Skipping empty/invalid: %s", change_file)
            continue
        review_status = _read_review_state(change_file)
        all_zones.extend(_build_csv_rows(zones, review_status))

    if not all_zones:
        log.error("No zones found under %s (checked %d change_zones.json files)",
                  args.root, n_files)
        return 2
    log.info("Discovered %d zones across %d change_zones.json files",
             len(all_zones), n_files)

    # ----- Filter by status -----
    statuses_filter = args.include_statuses.strip().lower()
    if statuses_filter != "all":
        wanted = {s.strip() for s in statuses_filter.split(",") if s.strip()}
        before = len(all_zones)
        all_zones = [r for r in all_zones if r["review_status"] in wanted]
        log.info("Filtered by status %s: %d → %d zones",
                 sorted(wanted), before, len(all_zones))

    # ----- Stratified sample -----
    if args.max_zones > 0 and len(all_zones) > args.max_zones:
        sampled = _stratified_sample(all_zones, args.max_zones, args.seed)
        log.info("Sampled %d zones from %d total (seed=%d)",
                 len(sampled), len(all_zones), args.seed)
    else:
        sampled = all_zones
        log.info("Including all %d zones (no cap or under cap)",
                 len(sampled))

    # ----- Distribution report -----
    by_status = defaultdict(int)
    by_drawing = defaultdict(int)
    by_change_type = defaultdict(int)
    for r in sampled:
        by_status[r["review_status"]] += 1
        by_drawing[r["drawing_number"] or "(no drawing)"] += 1
        by_change_type[r["change_type"] or "(no type)"] += 1
    log.info("Distribution by status: %s", dict(by_status))
    log.info("Distribution by change_type: %s", dict(by_change_type))
    log.info("Distinct drawings: %d", len(by_drawing))

    # ----- Write -----
    _write_csv(sampled, args.output)
    log.info("Wrote %d rows → %s", len(sampled), args.output)
    log.info(
        "Next step: open the CSV, fill in the 'expected_category' column "
        "with one of: structural_member, dimension, text_label, grid, "
        "layout, detail_drawing, note, unknown."
    )
    log.info(
        "Then run: python tools/build_golden_set_v2.py "
        "--input %s --output tools/golden_zones_v2.json", args.output,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
