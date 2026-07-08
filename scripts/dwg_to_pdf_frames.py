# -*- coding: utf-8 -*-
"""DWG → 도곽(프레임)별 PDF 변환 — 에이전틱 비전 워크플로 지원.

Why (2026-07-01): 대형 ODA-변환 DXF는 OBJECTS/FIELD 블로트(~2.4M객체)로 ezdxf
Frontend 렌더가 사실상 행업한다. 이 스크립트는 그 경로를 우회한다:

  DWG --ODA--> DXF --dxf_slim(OBJECTS 절단)--> readfile(블록정의용)
      --수동 렌더(virtual_entities로 블록 전개, Frontend 미사용)--> stray 필터
      --도곽 X-군집--> 도곽별 PDF

핵심:
  * ezdxf ``Frontend``/``RenderContext`` 를 쓰지 않는다(그게 FIELD에서 행업).
    대신 ``INSERT.virtual_entities()`` 로 블록을 지오메트리로 전개해 직접
    matplotlib 으로 그린다 — FIELD 블로트와 무관.
  * stray(폭주) 엔티티는 robust 좌표범위 밖 세그먼트를 버려 제거.

Usage:
    python scripts/dwg_to_pdf_frames.py --dwg <path.dwg> --out-dir OUT [--frames 5]
    python scripts/dwg_to_pdf_frames.py --dxf <converted.dxf> --out-dir OUT
"""

from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

import logging  # noqa: E402
logging.disable(logging.CRITICAL)  # ezdxf/기타 경고 폭주 억제(FIELD 등)

ODA_CANDIDATES = [
    r"C:\Program Files\ODA\ODAFileConverter 26.10.0\ODAFileConverter.exe",
    r"C:\Program Files\ODA\ODAFileConverter.exe",
]


def _find_oda() -> Optional[str]:
    import glob
    for pat in [r"C:\Program Files\ODA\*\ODAFileConverter.exe", *ODA_CANDIDATES]:
        for p in glob.glob(pat):
            if os.path.exists(p):
                return p
    return None


def dwg_to_dxf(dwg: Path, workdir: Path) -> Path:
    """ODA로 DWG→DXF(ACAD2018). 반환: 변환된 dxf 경로."""
    oda = _find_oda()
    if not oda:
        raise RuntimeError("ODA File Converter를 찾을 수 없습니다.")
    din = workdir / "dwg_in"; dout = workdir / "dxf_out"
    din.mkdir(parents=True, exist_ok=True); dout.mkdir(parents=True, exist_ok=True)
    import shutil
    tmp = din / (dwg.stem + ".dwg")
    shutil.copy2(dwg, tmp)
    subprocess.run([oda, str(din), str(dout), "ACAD2018", "DXF", "0", "0"],
                   capture_output=True, text=True, timeout=590)
    out = dout / (dwg.stem + ".dxf")
    if not out.exists():
        raise RuntimeError(f"ODA 변환 실패: {out}")
    return out


def slim_dxf(dxf: Path, workdir: Path) -> Path:
    """OBJECTS 섹션 절단(dxf_slim). 실패해도 원본 반환."""
    try:
        from src.services.comparison.dxf_slim import strip_objects_section
        dst = workdir / (dxf.stem + "_slim.dxf")
        strip_objects_section(dxf, dst)
        return dst
    except Exception:  # noqa: BLE001
        return dxf


def _iter_geometry(entity, depth=0):
    """엔티티에서 (segments, circles) 수집. INSERT는 virtual_entities로 전개."""
    segs: List = []; circles: List = []
    dt = entity.dxftype()
    try:
        if dt == "LINE":
            a = entity.dxf.start; b = entity.dxf.end
            segs.append(((a[0], a[1]), (b[0], b[1])))
        elif dt == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in entity.get_points("xy")]
            closed = entity.closed
            for i in range(len(pts) - 1):
                segs.append((pts[i], pts[i + 1]))
            if closed and len(pts) > 2:
                segs.append((pts[-1], pts[0]))
        elif dt == "POLYLINE":
            pts = [(v.dxf.location[0], v.dxf.location[1]) for v in entity.vertices]
            for i in range(len(pts) - 1):
                segs.append((pts[i], pts[i + 1]))
        elif dt == "CIRCLE":
            c = entity.dxf.center; circles.append((c[0], c[1], entity.dxf.radius))
        elif dt == "ARC":
            c = entity.dxf.center; r = entity.dxf.radius
            a0 = math.radians(entity.dxf.start_angle); a1 = math.radians(entity.dxf.end_angle)
            if a1 < a0:
                a1 += 2 * math.pi
            steps = max(6, int((a1 - a0) / 0.35))
            prev = None
            for i in range(steps + 1):
                t = a0 + (a1 - a0) * i / steps
                p = (c[0] + r * math.cos(t), c[1] + r * math.sin(t))
                if prev is not None:
                    segs.append((prev, p))
                prev = p
        elif dt == "INSERT" and depth < 3:
            for ve in entity.virtual_entities():
                s2, c2 = _iter_geometry(ve, depth + 1)
                segs += s2; circles += c2
    except Exception:  # noqa: BLE001
        pass
    return segs, circles


def collect(dxf: Path) -> Tuple[list, list, float]:
    import ezdxf
    t0 = time.time()
    doc = ezdxf.readfile(str(dxf))
    msp = doc.modelspace()
    segs: List = []; circles: List = []
    for e in msp:
        s, c = _iter_geometry(e)
        segs += s; circles += c
    return segs, circles, round(time.time() - t0, 1)


def _robust_bounds(segs, circles):
    import numpy as np
    xs = []; ys = []
    for (a, b) in segs:
        xs += [a[0], b[0]]; ys += [a[1], b[1]]
    for (cx, cy, r) in circles:
        xs += [cx]; ys += [cy]
    xs = np.array(xs); ys = np.array(ys)
    # robust: 1~99 백분위(폭주 stray 제거)
    return (np.percentile(xs, 0.5), np.percentile(xs, 99.5),
            np.percentile(ys, 0.5), np.percentile(ys, 99.5))


def _filter_strays(segs, bounds, pad_frac=0.08):
    x0, x1, y0, y1 = bounds
    px = (x1 - x0) * pad_frac; py = (y1 - y0) * pad_frac
    X0, X1, Y0, Y1 = x0 - px, x1 + px, y0 - py, y1 + py
    out = []
    for (a, b) in segs:
        if X0 <= a[0] <= X1 and Y0 <= a[1] <= Y1 and X0 <= b[0] <= X1 and Y0 <= b[1] <= Y1:
            out.append((a, b))
    return out


def render(segs, circles, bounds, out_path: Path, dpi=150):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.patches import Circle
    x0, x1, y0, y1 = bounds
    w = x1 - x0; h = y1 - y0
    aspect = h / w if w else 1
    fig = plt.figure(figsize=(20, max(4, min(28, 20 * aspect))))
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_facecolor("white")
    ax.add_collection(LineCollection(segs, colors="black", linewidths=0.35))
    for cx, cy, r in circles:
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            ax.add_patch(Circle((cx, cy), r, fill=False, ec="red", lw=0.6))
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("equal"); ax.axis("off")
    fig.savefig(str(out_path), dpi=dpi, facecolor="white"); plt.close(fig)
    return out_path


def split_frames(segs, bounds, n_frames):
    """콘텐츠를 X축으로 n_frames 도곽으로 분할(세그먼트 중점 X 기준)."""
    import numpy as np
    x0, x1, y0, y1 = bounds
    mids = np.array([((a[0] + b[0]) / 2) for (a, b) in segs])
    inb = (mids >= x0) & (mids <= x1)
    mids_in = mids[inb]
    if len(mids_in) == 0:
        return [bounds]
    edges = np.linspace(x0, x1, n_frames + 1)
    frames = []
    for i in range(n_frames):
        frames.append((edges[i], edges[i + 1], y0, y1))
    return frames


def render_per_block(dxf: Path, pattern: str, out_dir: Path, dpi: int) -> list:
    """블록 이름이 정규식과 일치하는 각 블록정의를 '개별' PDF로 렌더.

    대형 위치도가 배경/층 블록을 겹쳐 삽입해 전체 전개 시 층이 오버랩되는
    경우, 층별 블록(예: ``\\d+F보강``)을 하나씩 렌더하면 오버랩이 사라진다.
    (도곽별 파일 하나씩 = 이 경로.)
    """
    import re as _re
    import ezdxf
    doc = ezdxf.readfile(str(dxf))
    rx = _re.compile(pattern)
    outs = []
    for blk in doc.blocks:
        if blk.name.startswith("*") or not rx.search(blk.name):
            continue
        segs: list = []; circles: list = []
        for e in blk:
            s, c = _iter_geometry(e)
            segs += s; circles += c
        if not segs:
            continue
        bounds = _robust_bounds(segs, circles)
        segs = _filter_strays(segs, bounds)
        safe = _re.sub(r"[^0-9A-Za-z가-힣_-]", "_", blk.name)
        p = out_dir / f"{safe}.pdf"
        render(segs, circles, bounds, p, dpi)
        outs.append(p)
        print(f"    {blk.name} -> {p.name} (segs={len(segs)} circ={len(circles)})", flush=True)
    return outs


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dwg", type=Path)
    ap.add_argument("--dxf", type=Path, help="이미 변환된 DXF(변환 생략)")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--frames", type=int, default=0, help="도곽 수(0=전체 1장만)")
    ap.add_argument("--per-block", type=str, default="",
                    help="블록 이름 정규식과 일치하는 각 블록정의를 개별 PDF로(예: '\\d+F보강'). 도곽별 파일 하나씩.")
    ap.add_argument("--dpi", type=int, default=150)
    args = ap.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    work = args.out_dir / "_work"; work.mkdir(exist_ok=True)

    if args.dxf:
        dxf = args.dxf
    elif args.dwg:
        print("[1/4] DWG→DXF(ODA)…", flush=True)
        dxf = dwg_to_dxf(args.dwg, work)
    else:
        print("--dwg 또는 --dxf 필요", file=sys.stderr); return 2

    print("[2/4] slim(OBJECTS 절단)…", flush=True)
    slim = slim_dxf(dxf, work)

    if args.per_block:
        print(f"[per-block] '{args.per_block}' 일치 블록을 개별 PDF로…", flush=True)
        outs = render_per_block(slim, args.per_block, args.out_dir, args.dpi)
        print(f"생성 {len(outs)}개:", flush=True)
        for p in outs:
            print(f"    {p}  ({p.stat().st_size:,} bytes)", flush=True)
        return 0

    print("[3/4] 지오메트리 수집(블록 전개)…", flush=True)
    segs, circles, secs = collect(slim)
    print(f"    segs={len(segs)} circles={len(circles)} ({secs}s)", flush=True)
    bounds = _robust_bounds(segs, circles)
    segs = _filter_strays(segs, bounds)
    print(f"    stray 필터 후 segs={len(segs)}  content_bbox={tuple(round(v) for v in bounds)}", flush=True)

    print("[4/4] 렌더…", flush=True)
    outs = []
    full = args.out_dir / "full.pdf"
    render(segs, circles, bounds, full, args.dpi); outs.append(full)
    if args.frames and args.frames > 1:
        for i, fb in enumerate(split_frames(segs, bounds, args.frames), 1):
            p = args.out_dir / f"frame_{i}.pdf"
            render(segs, circles, fb, p, args.dpi); outs.append(p)
    print("생성:", flush=True)
    for p in outs:
        print(f"    {p}  ({p.stat().st_size:,} bytes)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
