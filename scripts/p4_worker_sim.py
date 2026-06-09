# -*- coding: utf-8 -*-
"""P4 spike #3 — drive the REAL persistent zone_render_process worker with two
source renders of the SAME pair (different zones) and observe whether render-2
reuses the warm DXF index (cache survives in the persistent subprocess) or
re-parses cold. Settles churn (H1) vs usage (H4) for the "DXF idx hit 0.0%".
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _extents(dxf_path: Path):
    import ezdxf
    from ezdxf import bbox as ezdxf_bbox

    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
    ext = ezdxf_bbox.extents(msp, fast=True)
    return (ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)


def _window(ext, frac_lo, frac_hi):
    x0, y0, x1, y1 = ext
    w = x1 - x0
    return {
        "xmin": x0 + w * frac_lo, "ymin": y0,
        "xmax": x0 + w * frac_hi, "ymax": y1,
    }


def _request(zone_id, window, before, after, cache_root, dxf_cache_dir):
    return {
        "request_id": zone_id,
        "pair_uuid": "p4sim",
        "zone_id": zone_id,
        "source_before": before,
        "source_after": after,
        "world_window": window,
        "cache_root": cache_root,
        "dxf_cache_dir": dxf_cache_dir,
        "render_environment_hash": "p4sim-env",
        "prefer_source_render": True,
    }


def main(before: str, after: str) -> int:
    import tempfile

    cache_root = tempfile.mkdtemp(prefix="p4sim_cache_")
    dxf_cache_dir = tempfile.mkdtemp(prefix="p4sim_dxf_")
    ext = _extents(Path(before))
    print(f"extents={tuple(round(v) for v in ext)}")
    w1 = _window(ext, 0.0, 0.45)
    w2 = _window(ext, 0.55, 1.0)

    proc = subprocess.Popen(
        [sys.executable, "-m", "src.services.comparison.zone_render_process"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(ROOT),
    )

    def _send(obj):
        proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        proc.stdin.flush()

    def _read_json_line(timeout_s=180.0):
        # responses are line-delimited json on stdout
        line = proc.stdout.readline()
        return json.loads(line.decode("utf-8", "replace")) if line else None

    # ready event
    ev = _read_json_line()
    print(f"ready_event={ev}")

    results = []
    for zid, win in (("A", w1), ("B", w2)):
        req = _request(zid, win, before, after, cache_root, dxf_cache_dir)
        t0 = time.perf_counter()
        _send(req)
        resp = _read_json_line()
        dt = time.perf_counter() - t0
        res = (resp or {}).get("result", {})
        dic = {k: v for k, v in res.items() if "dxf_index_cache" in k}
        print(f"zone={zid} wall_s={dt:.2f} ok={(resp or {}).get('ok')} "
              f"fidelity={res.get('visual_fidelity')} lifecycle={res.get('render_lifecycle')}")
        print(f"  hit={dic.get('dxf_index_cache_hit_count')} miss={dic.get('dxf_index_cache_miss_count')} "
              f"rate={dic.get('dxf_index_cache_hit_rate')} entries={dic.get('dxf_index_cache_entries')}")
        results.append((zid, dt, dic))

    _send({"command": "shutdown"})
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()

    if len(results) == 2:
        _, dt1, _ = results[0]
        _, dt2, d2 = results[1]
        hit2 = d2.get("dxf_index_cache_hit_count") or 0
        print(f"VERDICT cache_reused_across_zones={hit2 > 0} speedup={dt1/max(dt2,1e-6):.1f}x "
              f"(zone1={dt1:.1f}s zone2={dt2:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
