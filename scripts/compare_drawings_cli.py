# -*- coding: utf-8 -*-
"""Headless JSON diff entrypoint for the real Workbench comparison engine.

Why this exists (2026-07-01): the ``dwg-diff-mcp`` server historically wrapped
an OLD, separate engine whose ``compare_drawings`` only diffed *layer sets* and
*element counts* — none of the canonical entity-level pipeline (alignment,
add/delete/modified matching, change zones, golden-measured accuracy). This
script exposes the SHIPPED ``DwgDiffer`` (same canonical default path measured by
``scripts/measure_golden_accuracy_baseline.py``) as a Qt-free CLI that emits a
clean JSON diff on stdout, so the MCP can shell out to the accurate engine
instead of re-implementing a shallow one.

Design:
  * No PySide6 / GUI import — pure ``DwgDiffer().compare()`` → ``ComparisonResult``.
  * stdout carries ONLY the JSON payload (progress/logging go to stderr) so the
    caller can ``json.loads`` it directly.
  * Honest failure: a hard error returns ``{"status": "error", ...}`` and a
    non-zero exit code. It never silently degrades to a weaker diff.

Usage:
    python scripts/compare_drawings_cli.py --a before.dxf --b after.dxf \
        [--json-out out.json] [--max-changes 500] \
        [--include-layers A,B] [--exclude-layers Defpoints]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows consoles default to cp949; diff payloads carry Korean layer names and
# em-dashes, so force utf-8 on both streams (same guard as other repo scripts).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — non-reconfigurable stream (tests)
        pass

#: Known heuristic behaviours the caller (and any LLM narrating the diff) must
#: not mistake for ground truth. Surfaced in every payload for honesty.
ENGINE_NOTES: List[str] = [
    "A parallel-moved entity is often reported as a delete+add PAIR rather than "
    "a single 'modified' — treat co-located add/delete as a possible move.",
    "Change classification is heuristic-only (no embedding/LLM tier); layer and "
    "entity-type labels come from the drawing, not semantic inference.",
]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", dest="source_a", required=True, help="Before/base file (DXF or DWG)")
    parser.add_argument(
        "--b", dest="source_b", required=True, help="After/target file (DXF or DWG)"
    )
    parser.add_argument(
        "--json-out",
        dest="json_out",
        default="",
        help="Optional path to also write the JSON payload",
    )
    parser.add_argument(
        "--max-changes",
        type=int,
        default=500,
        help="Truncate the returned changes[] to this many for token budget (0 = no cap). "
        "Counts/summary always reflect the FULL diff.",
    )
    parser.add_argument(
        "--include-layers", default="", help="Comma-separated layers to include (default: all)"
    )
    parser.add_argument("--exclude-layers", default="", help="Comma-separated layers to exclude")
    return parser.parse_args(argv)


def _split_layers(value: str) -> Optional[List[str]]:
    items = [part.strip() for part in (value or "").split(",") if part.strip()]
    return items or None


def run_compare(args: argparse.Namespace) -> Dict[str, Any]:
    from src.services.comparison.dwg_differ import DwgDiffer

    source_a = Path(args.source_a)
    source_b = Path(args.source_b)
    for label, path in (("a", source_a), ("b", source_b)):
        if not path.exists():
            return {
                "status": "error",
                "error_code": "FILE_NOT_FOUND",
                "message": f"입력 파일 없음 ({label}): {path}",
            }

    started = time.perf_counter()
    with DwgDiffer() as differ:
        result = differ.compare(
            source_a,
            source_b,
            include_layers=_split_layers(args.include_layers),
            exclude_layers=_split_layers(args.exclude_layers),
        )
    elapsed = round(time.perf_counter() - started, 3)

    payload = result.to_dict()
    summary = payload.get("summary", {})
    all_changes = payload.get("changes", []) or []
    total = len(all_changes)

    cap = int(args.max_changes or 0)
    truncated = bool(cap and total > cap)
    changes = all_changes[:cap] if cap else all_changes

    meta = payload.get("metadata", {}) or {}
    # 정직성: 엔진이 fail-closed(토큰 한도 초과 등)면 0-changes를 성공으로 위장하지 않는다.
    pstatus = meta.get("pipeline_status")
    ecode = meta.get("error_code")
    if pstatus == "failed" or ecode:
        return {
            "status": "error",
            "error_code": str(ecode or "ENGINE_PIPELINE_FAILED"),
            "message": str(meta.get("message") or "비교 파이프라인 실패(fail-closed)"),
            "pipeline_status": pstatus,
            "warnings": payload.get("warnings", [])[:6],
            "summary": summary,
        }
    return {
        "status": "ok",
        "engine": "DrawingCompareWorkbench DwgDiffer (canonical default path)",
        "source_a": payload.get("source_a", str(source_a)),
        "source_b": payload.get("source_b", str(source_b)),
        "elapsed_seconds": elapsed,
        "pipeline_status": meta.get("pipeline_status"),
        "summary": summary,
        "changes": changes,
        "changes_returned": len(changes),
        "changes_total": total,
        "changes_truncated": truncated,
        "warnings": payload.get("warnings", []),
        "engine_notes": ENGINE_NOTES,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        payload = run_compare(args)
    except Exception as exc:  # noqa: BLE001 — top-level guard, reported honestly
        import traceback

        payload = {
            "status": "error",
            "error_code": exc.__class__.__name__,
            "message": str(exc),
            "traceback": traceback.format_exc().splitlines()[-6:],
        }

    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.json_out:
        try:
            Path(args.json_out).write_text(text, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] --json-out write failed: {exc}", file=sys.stderr)
    print(text)
    return 0 if payload.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
