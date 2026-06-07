# -*- coding: utf-8 -*-
"""Dump the text content of the 'Sheet2' block definition in A and B to prove the
hidden INSERT change is a genuine, meaningful block-internal text change."""
import ezdxf

BASE = r"C:/Users/user/AppData/Local/DrawingCompareWorkbench/dxf_cache/oda_auto/P5_154kv_POT_BEARING__7ef72584__12d077ac47509991.dxf"
R1 = r"C:/Users/user/AppData/Local/DrawingCompareWorkbench/dxf_cache/oda_auto/P5_154kv_POT_BEARING__R1__8ccf6778__babb088bfc3f4f35.dxf"

def block_texts(path, name):
    doc = ezdxf.readfile(path)
    blk = doc.blocks.get(name)
    if blk is None:
        return None
    items = []
    for e in blk:
        et = e.dxftype()
        try:
            if et == "TEXT":
                items.append(("TEXT", (e.dxf.text or "").strip()))
            elif et == "MTEXT":
                items.append(("MTEXT", (e.plain_text() or "").strip()))
            elif et == "ATTDEF":
                items.append(("ATTDEF", f"{(e.dxf.tag or '').strip()}={(e.dxf.text or '').strip()}"))
        except Exception:
            continue
    return items

ta = block_texts(BASE, "Sheet2")
tb = block_texts(R1, "Sheet2")
print(f"A 'Sheet2' has {len(ta) if ta else 0} text items")
print(f"B 'Sheet2' has {len(tb) if tb else 0} text items")

sa = set(t for t in (ta or []))
sb = set(t for t in (tb or []))
only_a = sa - sb
only_b = sb - sa
print(f"\n# text items ONLY in A (removed/changed-from): {len(only_a)}")
for t in sorted(only_a)[:40]:
    print("   A-only:", t)
print(f"\n# text items ONLY in B (added/changed-to): {len(only_b)}")
for t in sorted(only_b)[:40]:
    print("   B-only:", t)
