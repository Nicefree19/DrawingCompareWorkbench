# -*- coding: utf-8 -*-
"""Adversarial perf lens: real POT BEARING pair, time compare_with_modified_detection."""
from __future__ import annotations
import time
import ezdxf

from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor
from src.services.comparison.dxf_comparator import DxfComparator, DxfChangeType

BASE = r"C:/Users/user/AppData/Local/DrawingCompareWorkbench/dxf_cache/oda_auto/P5_154kv_POT_BEARING__7ef72584__12d077ac47509991.dxf"
R1 = r"C:/Users/user/AppData/Local/DrawingCompareWorkbench/dxf_cache/oda_auto/P5_154kv_POT_BEARING__R1__8ccf6778__babb088bfc3f4f35.dxf"


def load(path):
    t0 = time.perf_counter()
    doc = ezdxf.readfile(path)
    ents = DxfEntityExtractor().extract(doc)
    n = sum(len(v) for v in ents.values())
    print(f"  loaded {path.split('/')[-1][:40]}: {n} entities in {time.perf_counter()-t0:.2f}s")
    return ents


def main():
    print("=== LOAD ===")
    A = load(BASE)
    B = load(R1)

    print("\n=== compare_with_modified_detection (HEAD a7b2c7d) ===")
    cmp = DxfComparator()
    t0 = time.perf_counter()
    r = cmp.compare_with_modified_detection(A, B)
    dt = time.perf_counter() - t0
    by = {}
    for c in r.changes:
        by.setdefault(c.change_type, []).append(c)
    print(f"  TIME = {dt:.2f}s")
    print(f"  total changes = {len(r.changes)}")
    print(f"  ADDED={len(by.get(DxfChangeType.ADDED,[]))} "
          f"DELETED={len(by.get(DxfChangeType.DELETED,[]))} "
          f"MODIFIED={len(by.get(DxfChangeType.MODIFIED,[]))}")
    print(f"  reorigin_unchanged_removed = {r.metadata.get('reorigin_unchanged_removed')}")
    print(f"  alignment_suppressed = {r.stats.get('alignment_suppressed')}")
    align = r.metadata.get("alignment")
    if align:
        print(f"  alignment: dx={align.get('dx'):.1f} dy={align.get('dy'):.1f} "
              f"theta_deg={align.get('theta_deg'):.6f} inlier={align.get('inlier_ratio')}")
    refined = r.metadata.get("alignment_refined")
    if refined:
        print(f"  alignment_refined: dx={refined.get('dx'):.4f} dy={refined.get('dy'):.4f}")
    # sanity: MODIFIED native coords check on a sample
    mods = by.get(DxfChangeType.MODIFIED, [])
    if mods:
        m = mods[0]
        print(f"  sample MODIFIED: type={m.entity_type} loc(B)={m.location} old_loc(A)={m.old_location} cat={m.change_category}")
    # Run twice to see variance
    t0 = time.perf_counter()
    r2 = cmp.compare_with_modified_detection(A, B)
    dt2 = time.perf_counter() - t0
    print(f"  TIME (2nd run) = {dt2:.2f}s   total={len(r2.changes)}")


if __name__ == "__main__":
    main()
