# -*- coding: utf-8 -*-
"""Render changed regions to before/after images so an AI can *see* the diff.

Why this exists (2026-07-01): a precise entity diff (``compare_drawings_cli.py``)
tells you WHERE and WHAT changed, but the human-labour it does not yet remove is
the *visual* review — a person still opens both sheets and eyeballs each change.
This CLI closes that: the deterministic engine locates the changes, and each
change cluster is rendered as a tight before/after PNG crop. An orchestrating
agent then applies vision to those crops to describe/judge the change in human
terms — automating the tedious "look at both drawings side by side" step.

Pipeline:
    DwgDiffer().compare(a, b)          # accurate entity diff (canonical path)
        -> change world-coordinates
        -> greedy spatial clustering   # group nearby changes into regions
        -> DxfRenderer.render_with_transform(a/b)   # headless, prod color/font
        -> crop each region from both renders (shared world frame)
        -> before_r{i}.png / after_r{i}.png + overviews

Honesty / limits (surfaced in the payload ``caveats``):
  * Crops assume before/after share a coordinate frame — true for revisions of
    one sheet; re-origined drawings need alignment (NOT applied here).
  * Region crops are cut from the full-sheet render; on very large sheets a
    small region may be low-resolution. Tight per-region rendering is a planned
    refinement.

Usage:
    python scripts/render_change_regions_cli.py --a before.dxf --b after.dxf \
        --out-dir OUT [--max-regions 6] [--dpi 120]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

CAVEATS: List[str] = [
    "before/after 크롭은 두 도면이 좌표계를 공유한다고 가정한다(한 시트의 리비전엔 참). "
    "재원점된 도면은 정합 변환이 필요하며 여기서는 적용하지 않는다.",
    "구역 크롭은 전체 시트 렌더에서 잘라낸다 — 초대형 시트에서 작은 구역은 저해상일 수 있다 "
    "(구역별 tight 렌더는 후속 개선).",
]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--a", dest="source_a", required=True)
    p.add_argument("--b", dest="source_b", required=True)
    p.add_argument("--out-dir", dest="out_dir", default="", help="빈값이면 임시 디렉토리")
    p.add_argument("--max-regions", type=int, default=6)
    p.add_argument("--dpi", type=int, default=120)
    p.add_argument("--min-region-mm", type=float, default=150.0, help="단일 변경 구역 최소 반경(mm)")
    p.add_argument("--pad-frac", type=float, default=0.6, help="구역 bbox 여백 비율")
    p.add_argument("--overview-max-px", type=int, default=1600)
    return p.parse_args(argv)


def _parse_xy(text: Any) -> Optional[Tuple[float, float]]:
    if isinstance(text, (list, tuple)) and len(text) >= 2:
        try:
            return (float(text[0]), float(text[1]))
        except (TypeError, ValueError):
            return None
    if not isinstance(text, str):
        return None
    parts = text.replace("(", "").replace(")", "").split(",")
    if len(parts) < 2:
        return None
    try:
        return (float(parts[0].strip()), float(parts[1].strip()))
    except ValueError:
        return None


def _change_point(ch: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    pt = _parse_xy(ch.get("location"))
    if pt is not None:
        return pt
    meta = ch.get("metadata") if isinstance(ch.get("metadata"), dict) else {}
    bbox = meta.get("bbox") if isinstance(meta.get("bbox"), dict) else None
    if bbox:
        try:
            return ((float(bbox["min_x"]) + float(bbox["max_x"])) / 2.0,
                    (float(bbox["min_y"]) + float(bbox["max_y"])) / 2.0)
        except (KeyError, TypeError, ValueError):
            return None
    return None


def _cluster(points: List[Tuple[int, float, float]], threshold: float
             ) -> List[List[int]]:
    """Greedy spatial clustering by centroid distance. Returns lists of indices."""
    clusters: List[Dict[str, Any]] = []
    for idx, x, y in points:
        placed = False
        for c in clusters:
            if math.hypot(x - c["cx"], y - c["cy"]) <= threshold:
                c["idx"].append(idx)
                n = len(c["idx"])
                c["cx"] += (x - c["cx"]) / n
                c["cy"] += (y - c["cy"]) / n
                placed = True
                break
        if not placed:
            clusters.append({"cx": x, "cy": y, "idx": [idx]})
    return [c["idx"] for c in clusters]


def _region_bbox(pts: List[Tuple[float, float]], min_mm: float, pad_frac: float
                 ) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    margin_x = max((x1 - x0) * pad_frac, min_mm)
    margin_y = max((y1 - y0) * pad_frac, min_mm)
    return (x0 - margin_x, y0 - margin_y, x1 + margin_x, y1 + margin_y)


def _crop(img, tf: Dict[str, Any], bbox: Tuple[float, float, float, float]):
    """Crop the world bbox from a full render using its world->pixel transform."""
    x0, y0, x1, y1 = bbox
    h, w = img.shape[0], img.shape[1]
    sx, sy = tf["scale_x"], tf["scale_y"]
    ox, oy = tf.get("offset_x", 0.0), tf.get("offset_y", 0.0)
    mnx, mny = tf["min_x"], tf["min_y"]
    px0 = (x0 - mnx) * sx + ox
    px1 = (x1 - mnx) * sx + ox
    # world y is bottom-up; image rows are top-down -> flip
    row_top = h - ((y1 - mny) * sy + oy)
    row_bot = h - ((y0 - mny) * sy + oy)
    cx0 = max(0, min(w, int(round(min(px0, px1)))))
    cx1 = max(0, min(w, int(round(max(px0, px1)))))
    cy0 = max(0, min(h, int(round(min(row_top, row_bot)))))
    cy1 = max(0, min(h, int(round(max(row_top, row_bot)))))
    if cx1 - cx0 < 2 or cy1 - cy0 < 2:
        return None
    return img[cy0:cy1, cx0:cx1]


def _overlay(crop_a, crop_b):
    """Superimpose before/after as a change-highlight image.

    before-only ink → red, after-only ink → blue, common ink → dark grey, on
    white. A parallel shift (the case raw before/after crops hide) shows as a
    red ghost beside a blue ghost — instantly legible to human and AI vision.
    """
    import cv2
    import numpy as np

    if crop_a is None or crop_b is None:
        return None
    h = min(crop_a.shape[0], crop_b.shape[0])
    w = min(crop_a.shape[1], crop_b.shape[1])
    if h < 2 or w < 2:
        return None
    a = cv2.resize(crop_a, (w, h), interpolation=cv2.INTER_AREA)
    b = cv2.resize(crop_b, (w, h), interpolation=cv2.INTER_AREA)
    ga = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY)
    gb = cv2.cvtColor(b, cv2.COLOR_RGB2GRAY)
    ink_a = ga < 245
    ink_b = gb < 245
    out = np.full((h, w, 3), 255, dtype=np.uint8)
    out[ink_a & ink_b] = (70, 70, 70)      # unchanged ink
    out[ink_a & ~ink_b] = (220, 30, 30)    # removed (before only) — red
    out[ink_b & ~ink_a] = (30, 60, 220)    # added (after only) — blue
    return out


def _save_png(img_rgb, path: Path, max_px: Optional[int] = None) -> Optional[Path]:
    import cv2

    arr = img_rgb
    if max_px:
        h, w = arr.shape[0], arr.shape[1]
        edge = max(h, w)
        if edge > max_px:
            scale = max_px / float(edge)
            arr = cv2.resize(arr, (max(1, int(w * scale)), max(1, int(h * scale))),
                             interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(path), cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
    return path if path.exists() else None


def run(args: argparse.Namespace) -> Dict[str, Any]:
    from src.services.comparison.dwg_differ import DwgDiffer
    from src.services.comparison.dxf_renderer import DxfRenderer

    source_a, source_b = Path(args.source_a), Path(args.source_b)
    for label, path in (("a", source_a), ("b", source_b)):
        if not path.exists():
            return {"status": "error", "error_code": "FILE_NOT_FOUND",
                    "message": f"입력 파일 없음 ({label}): {path}"}

    out_dir = Path(args.out_dir) if args.out_dir else Path(tempfile.mkdtemp(prefix="change_regions_"))
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    with DwgDiffer() as differ:
        result = differ.compare(source_a, source_b)
    payload = result.to_dict()
    _meta = payload.get("metadata", {}) or {}
    if _meta.get("pipeline_status") == "failed" or _meta.get("error_code"):
        # 정직성: 엔진 fail-closed면 0-region 렌더를 성공으로 위장하지 않는다.
        return {"status": "error",
                "error_code": str(_meta.get("error_code") or "ENGINE_PIPELINE_FAILED"),
                "message": str(_meta.get("message") or "비교 파이프라인 실패(fail-closed)"),
                "warnings": payload.get("warnings", [])[:6]}
    changes = payload.get("changes", []) or []

    # 변경 좌표 수집 + 공간 클러스터링
    pts: List[Tuple[int, float, float]] = []
    for i, ch in enumerate(changes):
        p = _change_point(ch) if isinstance(ch, dict) else None
        if p is not None:
            pts.append((i, p[0], p[1]))

    renderer = DxfRenderer(dpi=int(args.dpi), backend="auto")
    try:
        img_a, tf_a = renderer.render_with_transform(source_a)
        img_b, tf_b = renderer.render_with_transform(source_b)
    except Exception as exc:  # noqa: BLE001 — MemoryError 포함(초대형 시트 풀렌더 OOM)
        return {"status": "error", "error_code": "RENDER_TOO_LARGE",
                "message": f"전체 시트 렌더 실패({exc.__class__.__name__}): 초대형 시트로 추정. "
                           "구역별 tight 렌더(Phase 2)가 필요하다.",
                "summary": payload.get("summary", {}), "changes_total": len(changes)}

    ov_before = _save_png(img_a, out_dir / "overview_before.png", args.overview_max_px)
    ov_after = _save_png(img_b, out_dir / "overview_after.png", args.overview_max_px)

    regions_out: List[Dict[str, Any]] = []
    if pts:
        span = math.hypot(tf_a["max_x"] - tf_a["min_x"], tf_a["max_y"] - tf_a["min_y"])
        threshold = max(span * 0.04, args.min_region_mm * 2)
        clusters = _cluster(pts, threshold)
        # 변경 많은 구역 우선, max_regions 상한
        clusters.sort(key=len, reverse=True)
        for ridx, idxs in enumerate(clusters[: max(1, args.max_regions)]):
            member_pts = [(pts_x, pts_y) for (i, pts_x, pts_y) in pts if i in set(idxs)]
            bbox = _region_bbox(member_pts, args.min_region_mm, args.pad_frac)
            types: Dict[str, int] = {}
            for i in idxs:
                ct = str(changes[i].get("change_type", "?"))
                types[ct] = types.get(ct, 0) + 1
            crop_a = _crop(img_a, tf_a, bbox)
            crop_b = _crop(img_b, tf_b, bbox)
            ov = _overlay(crop_a, crop_b)
            bpng = _save_png(crop_a, out_dir / f"before_r{ridx}.png") if crop_a is not None else None
            apng = _save_png(crop_b, out_dir / f"after_r{ridx}.png") if crop_b is not None else None
            opng = _save_png(ov, out_dir / f"overlay_r{ridx}.png") if ov is not None else None
            regions_out.append({
                "region_id": ridx,
                "bbox_world": [round(v, 1) for v in bbox],
                "change_count": len(idxs),
                "change_types": types,
                "overlay_png": str(opng) if opng else None,
                "before_png": str(bpng) if bpng else None,
                "after_png": str(apng) if apng else None,
            })

    return {
        "status": "ok",
        "engine": "DrawingCompareWorkbench DwgDiffer + DxfRenderer (headless)",
        "source_a": str(source_a),
        "source_b": str(source_b),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "output_dir": str(out_dir),
        "summary": payload.get("summary", {}),
        "changes_total": len(changes),
        "changes_located": len(pts),
        "region_count": len(regions_out),
        "regions": regions_out,
        "overview_before": str(ov_before) if ov_before else None,
        "overview_after": str(ov_after) if ov_after else None,
        "warnings": payload.get("warnings", []),
        "caveats": CAVEATS,
        "vision_hint": "각 region의 overlay_png(빨강=삭제/이동전, 파랑=추가/이동후, 회색=불변)를 "
                       "먼저 보고 무엇이 어떻게 바뀌었는지 판독하라. before_png/after_png는 원본 대조용. "
                       "구조적 유의성은 KDS/KCS(kcsc-mcp)로 교차검증하라.",
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        out = run(args)
    except Exception as exc:  # noqa: BLE001
        import traceback

        out = {"status": "error", "error_code": exc.__class__.__name__,
               "message": str(exc), "traceback": traceback.format_exc().splitlines()[-6:]}
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if out.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
