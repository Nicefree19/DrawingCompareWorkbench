# -*- coding: utf-8 -*-
"""Stage 2: audit the REMOVED re-origin pairs against FULL-geometry ground truth.

For the vulnerable types the buggy predicate ignores discriminators:
- INSERT: block_text_fingerprint + extrusion_key (live hash includes them; predicate does NOT)
- HATCH:  boundary vertex POSITIONS (predicate sees only count)
- SPLINE: control point POSITIONS (predicate sees only point_count)

We re-extract A and B with the LIVE normalizers (which DO capture these via hash),
register B->A by the refined transform, and for each removed pair recompute a
translation-invariant signature to detect whether a GENUINE change was hidden.

Strategy: re-pair removed deleted/added by (type, layer, registered-centroid),
then compare a RICH signature that the predicate omitted.
"""
import sys, math, pickle, hashlib
import ezdxf

BASE = r"C:/Users/user/AppData/Local/DrawingCompareWorkbench/dxf_cache/oda_auto/P5_154kv_POT_BEARING__7ef72584__12d077ac47509991.dxf"
R1 = r"C:/Users/user/AppData/Local/DrawingCompareWorkbench/dxf_cache/oda_auto/P5_154kv_POT_BEARING__R1__8ccf6778__babb088bfc3f4f35.dxf"

with open("tmp_reorigin_captured.pkl", "rb") as f:
    cap = pickle.load(f)

dx, dy, th = cap["refined"]
print(f"refined transform B->A: dx={dx:.4f} dy={dy:.4f} theta={th:.3e}", flush=True)

def T(x, y):
    # pure-ish: apply rotation+translation (theta ~ 0)
    c, s = math.cos(th), math.sin(th)
    return (x * c - y * s + dx, x * s + y * c + dy)

# removed deleted/added with their .data (exactly what predicate saw)
removed_ids = cap["removed_ids"]
removed_del = [t for t in cap["deleted_in"] if t[0] in removed_ids]  # (id,type,layer,loc,old_data)
removed_add = [t for t in cap["added_in"] if t[0] in removed_ids]    # (id,type,layer,loc,new_data)

# Re-pair removed_del <-> removed_add greedily by (type, layer, registered centroid within 3mm)
from collections import defaultdict
addbuck = defaultdict(list)
for (aid, et, lay, loc, nd) in removed_add:
    if loc is None:
        continue
    tx, ty = T(float(loc[0]), float(loc[1]))
    addbuck[(et, lay, round(tx/3), round(ty/3))].append((aid, et, lay, loc, nd, tx, ty))

pairs = []
usedA = set()
for (did, et, lay, loc, od) in removed_del:
    if loc is None:
        continue
    qx, qy = float(loc[0]), float(loc[1])
    best = None
    for cx in (round(qx/3)-1, round(qx/3), round(qx/3)+1):
        for cy in (round(qy/3)-1, round(qy/3), round(qy/3)+1):
            for cand in addbuck.get((et, lay, cx, cy), ()):
                aid = cand[0]
                if aid in usedA:
                    continue
                if abs(cand[5]-qx) <= 3.0 and abs(cand[6]-qy) <= 3.0:
                    best = cand
                    break
            if best: break
        if best: break
    if best:
        usedA.add(best[0])
        pairs.append(((did, et, lay, loc, od), best))

print(f"re-paired {len(pairs)} of {len(removed_del)} removed-deleted to removed-added", flush=True)

# --- INSERT audit: re-extract live block_text_fingerprint + extrusion_key from raw docs ---
# Build maps from (insert_point rounded) to fingerprint for INSERTs in each doc.
from src.services.comparison.entity_normalizers import InsertNormalizer

def insert_sig_map(doc):
    """Map: (block_name, round(ipx,1), round(ipy,1)) -> (block_text_fp, extrusion_key)."""
    norm = InsertNormalizer()
    norm.reset_per_extraction_state()
    out = {}
    msp = doc.modelspace()
    for e in msp:
        if e.dxftype() != "INSERT":
            continue
        try:
            ne = norm.normalize(e)
        except Exception:
            continue
        key = (ne.data["block_name"], ne.data["insert_point"])
        out[key] = (ne.data.get("block_text_fingerprint",""), ne.data.get("extrusion_key"))
    return out

print("re-extracting INSERT signatures from raw docs (this loads both docs again)...", flush=True)
docA = ezdxf.readfile(BASE)
docB = ezdxf.readfile(R1)
sigA = insert_sig_map(docA)
sigB = insert_sig_map(docB)
print(f"INSERT sig maps: A={len(sigA)} B={len(sigB)}", flush=True)

insert_hidden = []
hatch_hidden = []
spline_hidden = []
checked_insert = checked_hatch = checked_spline = 0

for (D, A) in pairs:
    did, et, lay, dloc, od = D
    aid, _et, _lay, aloc, nd, tx, ty = A
    if et == "INSERT":
        checked_insert += 1
        # A insert_point in od["insert_point"], B in nd["insert_point"]
        ka = (od.get("block_name"), tuple(od.get("insert_point")) if od.get("insert_point") else None)
        kb = (nd.get("block_name"), tuple(nd.get("insert_point")) if nd.get("insert_point") else None)
        fa = sigA.get(ka)
        fb = sigB.get(kb)
        if fa is not None and fb is not None:
            if fa[0] != fb[0]:  # block_text_fingerprint differs => block-internal text changed
                insert_hidden.append((od.get("block_name"), fa, fb, dloc, aloc))
            elif str(fa[1]) != str(fb[1]):  # extrusion differs
                insert_hidden.append((od.get("block_name")+"|EXTRUSION", fa, fb, dloc, aloc))
    elif et == "HATCH":
        checked_hatch += 1
        # Compare actual boundary geometry: vertex COUNT same (predicate ok) but POSITIONS?
        # The .data only had counts; we recompute from raw is expensive. Instead, use the
        # registered-centroid + the fact that predicate ignored positions: flag if the
        # boundary_vertex_count matches but we cannot confirm positions => structural risk.
        # (Quantify how many HATCH removed had identical scalars only.)
        pass
    elif et == "SPLINE":
        checked_spline += 1
        pass

print(f"\n=== INSERT audit: checked {checked_insert} removed INSERT pairs ===", flush=True)
print(f"INSERT pairs where block_text_fingerprint/extrusion DIFFERS (GENUINE change HIDDEN): {len(insert_hidden)}", flush=True)
for h in insert_hidden[:20]:
    print("  HIDDEN INSERT:", h[0], "fpA=", h[1], "fpB=", h[2], "locA=", h[3], "locB=", h[4], flush=True)
