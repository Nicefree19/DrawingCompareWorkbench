# -*- coding: utf-8 -*-
"""Subprocess entrypoint for one-off zone SVG vector renders.

Mirrors the ``viewer_render_worker.py`` pattern: take CLI args, do the
render, write a result JSON the parent process reads back. Keeps the
zone vector pipeline isolated so a malformed DXF entity that crashes
ezdxf inside ``Frontend.draw_layout`` cannot take down the GUI.

Invocation (from QProcess in the workbench):

    python -m src.services.comparison.zone_vector_worker \
        --dxf-path "C:/path/to/file.dxf" \
        --zone-bbox 100,200,400,500 \
        --output-svg "C:/cache/zone.svg" \
        --result-json "C:/cache/zone.result.json" \
        [--padding-ratio 0.1] [--max-entities 1500]

In a PyInstaller build the same worker is dispatched through
``DrawingCompareWorkbench.exe --drawing-compare-zone-vector-worker`` so the
child process does not re-open the GUI.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Sequence

logger = logging.getLogger(__name__)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Atomic JSON write with surrogate-safe encoding (mirrors the policy
    landed in commit ``ded522ab`` so this worker doesn't reintroduce the
    ``surrogates not allowed`` bug on Korean filenames)."""

    from .safe_unicode import safe_unicode

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(safe_unicode(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def _parse_zone_bbox(text: str) -> tuple[float, float, float, float]:
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            f"--zone-bbox expects 4 comma-separated floats, got {text!r}"
        )
    try:
        x0, y0, x1, y1 = (float(p) for p in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--zone-bbox values must be numeric: {exc}")
    return (x0, y0, x1, y1)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render one zone of a DXF as SVG (Phase B1 vector zoom)."
    )
    parser.add_argument("--dxf-path", required=True, help="Source DXF path")
    parser.add_argument(
        "--zone-bbox",
        required=True,
        type=_parse_zone_bbox,
        help="World-coord zone bbox 'x0,y0,x1,y1'",
    )
    parser.add_argument("--output-svg", required=True, help="Destination SVG path")
    parser.add_argument(
        "--result-json", required=True, help="JSON path for the parent to read back"
    )
    parser.add_argument(
        "--padding-ratio",
        type=float,
        default=0.1,
        help="Expand bbox by this fraction on each side (default 0.1)",
    )
    parser.add_argument(
        "--max-entities",
        type=int,
        default=1500,
        help="Cap accepted entities; over this returns truncated=True",
    )
    args = parser.parse_args(argv)

    # Force stdout/stderr to UTF-8 so Korean log messages don't crash on
    # Windows cp949 consoles. Same pattern as the workbench scripts.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    from .zone_vector_renderer import render_zone_svg

    try:
        result = render_zone_svg(
            dxf_path=Path(args.dxf_path),
            zone_world_bbox=args.zone_bbox,
            output_svg=Path(args.output_svg),
            padding_ratio=args.padding_ratio,
            max_entities=args.max_entities,
        )
        payload = result.to_dict()
        payload["status"] = "ok" if result.svg_path else "skipped"
    except Exception as exc:  # surface any uncaught error to the parent
        logger.exception("zone_vector_worker failed")
        payload = {
            "svg_path": "",
            "entity_count": 0,
            "elapsed_ms": 0.0,
            "world_bbox": list(args.zone_bbox),
            "truncated": False,
            "skipped_reason": f"{exc.__class__.__name__}: {exc}",
            "status": "error",
        }

    _write_json_atomic(Path(args.result_json), payload)
    return 0 if payload.get("status") in {"ok", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
