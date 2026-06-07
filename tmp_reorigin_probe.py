# -*- coding: utf-8 -*-
"""Probe the re-origin removal on the real POT BEARING pair.

Goal (change-hiding lens): quantify removed-by-type, and for the vulnerable
generic-fallback / INSERT types, re-check each REMOVED pair with a FULL-geometry
predicate to see if any genuine change was hidden.
"""
import sys, math, time
import ezdxf

BASE = r"C:/Users/user/AppData/Local/DrawingCompareWorkbench/dxf_cache/oda_auto/P5_154kv_POT_BEARING__7ef72584__12d077ac47509991.dxf"
R1 = r"C:/Users/user/AppData/Local/DrawingCompareWorkbench/dxf_cache/oda_auto/P5_154kv_POT_BEARING__R1__8ccf6778__babb088bfc3f4f35.dxf"

from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor
from src.services.comparison.dxf_comparator import (
    DxfComparator, DxfChangeType, _REORIGIN_TRANSLATION_MM,
)

t0 = time.time()
print("loading A...", flush=True)
docA = ezdxf.readfile(BASE)
print("loading B...", flush=True)
docB = ezdxf.readfile(R1)
print(f"loaded in {time.time()-t0:.1f}s", flush=True)

ext = DxfEntityExtractor()
ea = ext.extract(docA)
eb = ext.extract(docB)
na = sum(len(v) for v in ea.values())
nb = sum(len(v) for v in eb.values())
print(f"A entities={na} B entities={nb}", flush=True)

# Monkeypatch to capture the deleted/added BEFORE removal and the removed pairs.
import src.services.comparison.dxf_comparator as M
orig_remove = DxfComparator._remove_reorigin_unchanged_pairs
captured = {}

def spy_remove(self, deleted, added, alignment):
    captured["deleted_in"] = list(deleted)
    captured["added_in"] = list(added)
    captured["alignment"] = alignment
    kept_d, kept_a, removed_ids, removed_n, refined = orig_remove(self, deleted, added, alignment)
    captured["kept_deleted"] = kept_d
    captured["kept_added"] = kept_a
    captured["removed_ids"] = removed_ids
    captured["removed_n"] = removed_n
    captured["refined"] = refined
    return kept_d, kept_a, removed_ids, removed_n, refined

DxfComparator._remove_reorigin_unchanged_pairs = spy_remove

cmp = DxfComparator()
t0 = time.time()
r = cmp.compare_with_modified_detection(ea, eb)
print(f"compare in {time.time()-t0:.1f}s", flush=True)

al = r.metadata.get("alignment")
print("alignment:", al, flush=True)
print("reorigin_unchanged_removed:", r.metadata.get("reorigin_unchanged_removed"), flush=True)
print("_REORIGIN_TRANSLATION_MM:", _REORIGIN_TRANSLATION_MM, flush=True)

by = {}
for c in r.changes:
    by.setdefault(c.change_type, 0)
    by[c.change_type] += 1
print("result change counts:", {k.name: v for k, v in by.items()}, flush=True)
print("total changes:", len(r.changes), flush=True)

# Reconstruct the removed pairs: removed_ids holds id() of both deleted & added.
if "removed_ids" in captured:
    removed_ids = captured["removed_ids"]
    deleted_in = captured["deleted_in"]
    added_in = captured["added_in"]
    refined = captured["refined"]
    removed_del = [d for d in deleted_in if id(d) in removed_ids]
    removed_add = [a for a in added_in if id(a) in removed_ids]
    print(f"\nremoved deleted={len(removed_del)} removed added={len(removed_add)}", flush=True)

    # Breakdown removed by type
    from collections import Counter
    cdel = Counter(d.entity_type for d in removed_del)
    print("removed DELETED by type:", dict(cdel), flush=True)
    cadd = Counter(a.entity_type for a in removed_add)
    print("removed ADDED by type:", dict(cadd), flush=True)

    # Save captured for the second-stage audit script.
    import pickle
    with open("tmp_reorigin_captured.pkl", "wb") as f:
        pickle.dump({
            "deleted_in": [(id(d), d.entity_type, d.layer, d.location, d.old_data) for d in deleted_in],
            "added_in": [(id(a), a.entity_type, a.layer, a.location, a.new_data) for a in added_in],
            "removed_ids": removed_ids,
            "refined": (refined.dx, refined.dy, refined.theta_rad) if refined is not None else None,
            "align": (captured["alignment"].dx, captured["alignment"].dy, captured["alignment"].theta_rad) if captured["alignment"] else None,
        }, f)
    print("saved tmp_reorigin_captured.pkl", flush=True)
