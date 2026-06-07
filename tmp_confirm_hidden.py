# -*- coding: utf-8 -*-
"""Confirm the 4 Sheet2 INSERTs are ABSENT from the final re-origin result, and
that HATCH/SPLINE removed pairs can have differing full geometry (positional
discriminator the predicate ignores). Also: do a DIRECT full-geometry recheck of
ALL removed HATCH/SPLINE pairs by recomputing translation-invariant vertex sets.
"""
import math, pickle
import ezdxf
from collections import defaultdict

BASE = r"C:/Users/user/AppData/Local/DrawingCompareWorkbench/dxf_cache/oda_auto/P5_154kv_POT_BEARING__7ef72584__12d077ac47509991.dxf"
R1 = r"C:/Users/user/AppData/Local/DrawingCompareWorkbench/dxf_cache/oda_auto/P5_154kv_POT_BEARING__R1__8ccf6778__babb088bfc3f4f35.dxf"

from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor
from src.services.comparison.dxf_comparator import DxfComparator, DxfChangeType

docA = ezdxf.readfile(BASE); docB = ezdxf.readfile(R1)
ext = DxfEntityExtractor(); ea = ext.extract(docA); eb = ext.extract(docB)

# Run WITH the fix (default) and capture final changes.
r_fix = DxfComparator().compare_with_modified_detection(ea, eb)

# Count INSERTs in the Sheet2 block referencing region in the final result.
# The hidden ones had locB y ~ -109316.6 (B-space). Check whether any INSERT change
# in the final result sits at those B locations.
hidden_locsB = [(494031.3,-109316.6),(506670.6,-109316.6),(481392.0,-109316.6),(519309.9,-109316.6)]
def near_any(loc, targets, tol=2.0):
    if loc is None: return False
    return any(abs(loc[0]-t[0])<=tol and abs(loc[1]-t[1])<=tol for t in targets)

ins_changes_at_hidden = [c for c in r_fix.changes
                         if c.entity_type=="INSERT" and (near_any(c.location, hidden_locsB)
                         or near_any(c.old_location, [(365683.7,206633.8),(378323.0,206633.8),(353044.5,206633.8),(390962.3,206633.8)]))]
print(f"[WITH FIX] INSERT changes at the 4 hidden Sheet2 locations: {len(ins_changes_at_hidden)} (expect 0 => hidden)", flush=True)

# Now DISABLE re-origin by monkeypatching the threshold high, to get the counterfactual.
import src.services.comparison.dxf_comparator as M
old = M._REORIGIN_TRANSLATION_MM
M._REORIGIN_TRANSLATION_MM = 1e12  # disable re-origin path
ext2 = DxfEntityExtractor(); ea2 = ext2.extract(ezdxf.readfile(BASE)); eb2 = ext2.extract(ezdxf.readfile(R1))
r_legacy = DxfComparator().compare_with_modified_detection(ea2, eb2)
M._REORIGIN_TRANSLATION_MM = old

ins_legacy_at_hidden = [c for c in r_legacy.changes
                        if c.entity_type=="INSERT" and (near_any(c.location, hidden_locsB)
                        or near_any(c.old_location, [(365683.7,206633.8),(378323.0,206633.8),(353044.5,206633.8),(390962.3,206633.8)]))]
print(f"[LEGACY no-reorigin] INSERT changes at those locations: {len(ins_legacy_at_hidden)} (expect >0 => would be surfaced)", flush=True)
for c in ins_legacy_at_hidden[:8]:
    print("   legacy surfaced:", c.change_type.name, c.entity_type, "loc=", c.location, "old=", c.old_location, flush=True)

# --- HATCH/SPLINE positional re-check ---
with open("tmp_reorigin_captured.pkl","rb") as f:
    cap = pickle.load(f)
dx,dy,th = cap["refined"]
def T(x,y):
    c,s=math.cos(th),math.sin(th); return (x*c-y*s+dx, x*s+y*c+dy)
removed_ids = cap["removed_ids"]
rdel = [t for t in cap["deleted_in"] if t[0] in removed_ids]
radd = [t for t in cap["added_in"] if t[0] in removed_ids]

# Build raw geometry maps keyed by (block-ish centroid) for HATCH & SPLINE from raw docs.
def hatch_verts(doc):
    """list of (centroid, sorted full boundary vertex tuple) for each HATCH."""
    out=[]
    for e in doc.modelspace():
        if e.dxftype()!="HATCH": continue
        vs=[]
        try:
            for p in e.paths:
                if hasattr(p,"vertices"):
                    for v in p.vertices:
                        vs.append((round(v[0],2),round(v[1],2)))
                elif hasattr(p,"edges"):
                    for ed in p.edges:
                        if hasattr(ed,"start"):
                            vs.append((round(ed.start[0],2),round(ed.start[1],2)))
        except Exception:
            pass
        if vs:
            xs=[v[0] for v in vs]; ys=[v[1] for v in vs]
            ctr=((min(xs)+max(xs))/2,(min(ys)+max(ys))/2)
            out.append((ctr, tuple(sorted(vs))))
    return out

print("\nre-extracting HATCH full boundary geometry from raw docs...", flush=True)
HA = hatch_verts(docA); HB = hatch_verts(docB)
# index B hatches by registered centroid cell
hb_idx = defaultdict(list)
for ctr,vs in HB:
    tx,ty=T(ctr[0],ctr[1]); hb_idx[(round(tx/3),round(ty/3))].append((tx,ty,vs))
# For each removed HATCH (use A-space centroid from rdel), find matching B hatch, compare
# translation-invariant vertex shape (subtract centroid).
def shape(vs):
    if not vs: return ()
    cx=sum(v[0] for v in vs)/len(vs); cy=sum(v[1] for v in vs)/len(vs)
    return tuple(sorted((round(v[0]-cx,1),round(v[1]-cy,1)) for v in vs))
ha_by_ctr={ctr:vs for ctr,vs in HA}
hatch_checked=hatch_diffshape=0
for (did,et,lay,loc,od) in rdel:
    if et!="HATCH" or loc is None: continue
    # find A hatch whose centroid ~ loc
    a_vs=None
    for ctr,vs in HA:
        if abs(ctr[0]-loc[0])<=3 and abs(ctr[1]-loc[1])<=3:
            a_vs=vs; break
    if a_vs is None: continue
    qx,qy=loc
    bmatch=None
    for cx in (round(qx/3)-1,round(qx/3),round(qx/3)+1):
        for cy in (round(qy/3)-1,round(qy/3),round(qy/3)+1):
            for (tx,ty,vs) in hb_idx.get((cx,cy),()):
                if abs(tx-qx)<=3 and abs(ty-qy)<=3:
                    bmatch=vs; break
            if bmatch: break
        if bmatch: break
    if bmatch is None: continue
    hatch_checked+=1
    if shape(a_vs)!=shape(bmatch):
        hatch_diffshape+=1
print(f"HATCH removed pairs re-checked by FULL boundary shape: {hatch_checked}; DIFFERENT shape (potential hidden reshape): {hatch_diffshape}", flush=True)
