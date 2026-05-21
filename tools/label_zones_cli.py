# -*- coding: utf-8 -*-
"""Phase J Step 4 (K1) — interactive CLI labeler for the
zones_to_label CSV produced by extract_zones_for_labeling.py.

Walks each row in the CSV and asks the user for the expected
category. Writes the answer back to the SAME row in the SAME CSV
(in-place atomic write). Already-labelled rows are skipped on
subsequent runs so labeling can be paused / resumed across sessions.

Usage:
    python tools/label_zones_cli.py \\
        --csv tools/labeling/zones_to_label_v1.csv

Per-zone screen:

    [12 / 200 — drawing S20-0002 — pair p3]
    Layer: BEAM
    Entity: LWPOLYLINE
    Change: modified, count=3
    Status: needs_review

    [1] structural_member  [2] dimension      [3] text_label
    [4] grid               [5] layout         [6] detail_drawing
    [7] note               [8] unknown
    [s] skip   [b] back     [q] quit (saves progress)
    > _

Notes:
  * Atomic write (.tmp + replace) — Ctrl-C / power loss never
    corrupts the CSV.
  * Already-labelled rows shown briefly then skipped — re-running
    the tool resumes cleanly.
  * 'b' (back) reopens the previous row so you can correct typos.
  * Distribution histogram printed at exit so the user knows whether
    they hit the per-category targets (~25 each).
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CATEGORY_KEYS: tuple[tuple[str, str], ...] = (
    ("1", "structural_member"),
    ("2", "dimension"),
    ("3", "text_label"),
    ("4", "grid"),
    ("5", "layout"),
    ("6", "detail_drawing"),
    ("7", "note"),
    ("8", "unknown"),
)
KEY_TO_CATEGORY: dict[str, str] = dict(CATEGORY_KEYS)
VALID_CATEGORIES: frozenset[str] = frozenset(c for _, c in CATEGORY_KEYS)


# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------


def _load_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Load the CSV, returning (fieldnames, rows). UTF-8 with BOM
    tolerated since Excel saves with BOM."""

    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    if "expected_category" not in fieldnames:
        raise ValueError(
            f"CSV {path} has no 'expected_category' column — "
            f"is this the output of extract_zones_for_labeling.py?"
        )
    return fieldnames, rows


def _save_csv_atomic(
    path: Path, fieldnames: list[str], rows: list[dict[str, str]],
) -> None:
    """Atomic CSV write — tmp + replace. Mirrors the manifest /
    ai_config persistence pattern."""

    tmp = path.parent / f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        with tmp.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in fieldnames})
        tmp.replace(path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def _clear_screen() -> None:
    if sys.stdout.isatty():
        # Windows: cls; POSIX: clear (system call already inferred)
        try:
            os.system("cls" if os.name == "nt" else "clear")
        except Exception:
            pass


def _term_width() -> int:
    try:
        return shutil.get_terminal_size((80, 24)).columns
    except Exception:
        return 80


def _show_zone(
    row: dict[str, str], idx: int, total: int, total_labelled: int,
) -> None:
    width = _term_width()
    sep = "─" * min(width, 80)
    print(sep)
    print(f"[{idx + 1} / {total}]   labelled so far: {total_labelled}")
    print(f"  drawing : {row.get('drawing_number') or '(none)'}")
    print(f"  pair    : {row.get('pair_id', '')}")
    print(f"  zone_id : {row.get('zone_id', '')}")
    print(f"  files   : {row.get('source_a_filename', '?')}"
          f"  →  {row.get('source_b_filename', '?')}")
    print(f"  layer   : {row.get('layer') or '(none)'}")
    print(f"  entity  : {row.get('entity_type') or '(none)'}")
    print(f"  change  : {row.get('change_type', '?')}, count="
          f"{row.get('raw_count', '?')}")
    print(f"  status  : {row.get('review_status', '?')}")
    if row.get("expected_category"):
        print(f"  ✓ already labelled: {row['expected_category']}")
    print(sep)
    print("  [1] structural_member  [2] dimension      [3] text_label")
    print("  [4] grid               [5] layout         [6] detail_drawing")
    print("  [7] note               [8] unknown")
    print("  [s] skip   [b] back     [q] quit (saves progress)")


def _show_distribution(rows: list[dict[str, str]]) -> None:
    counts = Counter(r.get("expected_category", "") for r in rows)
    labelled = sum(v for k, v in counts.items() if k in VALID_CATEGORIES)
    skipped = counts.get("", 0)
    print()
    print("=== Labeling distribution ===")
    for _, cat in CATEGORY_KEYS:
        n = counts.get(cat, 0)
        bar = "█" * min(n, 40)
        print(f"  {cat:20s} {n:4d}  {bar}")
    print(f"  {'(unlabelled)':20s} {skipped:4d}")
    print(f"\n  Total labelled: {labelled} / {len(rows)}")
    print(f"  Recommended target: ≥ 160 (8 categories × ≥ 20 each)")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def _run_labeler(
    rows: list[dict[str, str]],
    fieldnames: list[str],
    csv_path: Path,
    skip_labelled: bool,
) -> None:
    total = len(rows)
    idx = 0
    while 0 <= idx < total:
        row = rows[idx]
        already = row.get("expected_category", "")
        if skip_labelled and already in VALID_CATEGORIES:
            idx += 1
            continue
        _clear_screen()
        labelled = sum(1 for r in rows
                       if r.get("expected_category") in VALID_CATEGORIES)
        _show_zone(row, idx, total, labelled)
        try:
            choice = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n(interrupted — progress already saved)")
            break
        if choice == "q":
            print("\n(saving + quitting)")
            break
        if choice == "s":
            idx += 1
            continue
        if choice == "b":
            idx = max(0, idx - 1)
            continue
        if choice in KEY_TO_CATEGORY:
            row["expected_category"] = KEY_TO_CATEGORY[choice]
            _save_csv_atomic(csv_path, fieldnames, rows)
            idx += 1
            continue
        # Allow direct typed category name
        if choice in VALID_CATEGORIES:
            row["expected_category"] = choice
            _save_csv_atomic(csv_path, fieldnames, rows)
            idx += 1
            continue
        print(f"  (unknown choice {choice!r} — try 1-8 / s / b / q)")
        time.sleep(1.0)

    _save_csv_atomic(csv_path, fieldnames, rows)
    _clear_screen()
    _show_distribution(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactive CLI labeler for K1 golden-set CSV.",
    )
    parser.add_argument(
        "--csv", type=Path, required=True,
        help="Path to the CSV produced by extract_zones_for_labeling.py.",
    )
    parser.add_argument(
        "--no-skip-labelled", action="store_true",
        help="Show even already-labelled rows (default: skip).",
    )
    parser.add_argument(
        "--show-distribution-only", action="store_true",
        help="Print the labeling distribution + exit (no interactive prompt).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    try:
        fieldnames, rows = _load_csv(args.csv)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.show_distribution_only:
        _show_distribution(rows)
        return 0

    _run_labeler(
        rows, fieldnames, args.csv,
        skip_labelled=not args.no_skip_labelled,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
