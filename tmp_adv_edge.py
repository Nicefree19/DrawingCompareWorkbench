# -*- coding: utf-8 -*-
"""Adversarial edge-and-perf lens for a7b2c7d re-origin fix."""
from __future__ import annotations
import math
import time
import traceback

from src.services.comparison.dxf_comparator import DxfComparator, DxfChangeType
from src.services.comparison.dxf_entity_extractor import NormalizedEntity
from src.services.comparison.global_alignment import RigidTransform

OK = "OK"


def _line(x1, y1, x2, y2, layer="0"):
    start, end = (x1, y1), (x2, y2)
    return NormalizedEntity(
        hash=f"LINE:{tuple(sorted([start, end]))}",
        entity_type="LINE", layer=layer,
        data={"start": start, "end": end},
        location=((x1 + x2) / 2, (y1 + y2) / 2),
    )


def _text(x, y, content, layer="TEXT"):
    return NormalizedEntity(
        hash=f"TEXT:{(x, y)}:{content}",
        entity_type="TEXT", layer=layer,
        data={"position": (x, y), "content": content},
        location=(x, y),
    )


def _by_type(entities):
    d = {}
    for e in entities:
        d.setdefault(e.entity_type, []).append(e)
    return d


def _rot(x, y, theta, dx, dy):
    c, s = math.cos(theta), math.sin(theta)
    return (c * x - s * y + dx, s * x + c * y + dy)


def _grid_lines(n=30, layer="GRID"):
    out = []
    for i in range(n):
        x = 1000.0 + i * 137.0
        y = 2000.0 + (i % 7) * 211.0
        out.append(_line(x, y, x + 100.0, y, layer=layer))
    return out


def _rot_line(ln, theta, dx, dy):
    s = ln.data["start"]
    e = ln.data["end"]
    ns = _rot(s[0], s[1], theta, dx, dy)
    ne = _rot(e[0], e[1], theta, dx, dy)
    return _line(ns[0], ns[1], ne[0], ne[1], ln.layer)


# ---------------------------------------------------------------------------
# (a) ROTATED RE-ORIGIN: theta = 90 degrees + large translation
# ---------------------------------------------------------------------------
def test_rotated_reorigin_90deg():
    print("\n=== (a) ROTATED RE-ORIGIN 90deg ===")
    theta = math.radians(90.0)
    DX, DY = 150000.0, -90000.0
    base = _grid_lines(30)
    a_text = _text(1500.0, 2500.0, "OLD")
    entities_a = _by_type(base + [a_text])
    # B = A rotated 90deg + translated, TEXT content changed
    b_lines = [_rot_line(ln, theta, DX, DY) for ln in base]
    bx, by = _rot(1500.0, 2500.0, theta, DX, DY)
    b_text = _text(bx, by, "NEW")
    entities_b = _by_type(b_lines + [b_text])

    r = DxfComparator().compare_with_modified_detection(entities_a, entities_b)
    align = r.metadata.get("alignment")
    print(f"  alignment metadata: {align}")
    print(f"  reorigin_unchanged_removed = {r.metadata.get('reorigin_unchanged_removed')}")
    print(f"  total changes = {len(r.changes)}")
    by_t = {}
    for c in r.changes:
        by_t.setdefault(c.change_type, []).append(c)
    mods = by_t.get(DxfChangeType.MODIFIED, [])
    print(f"  MODIFIED={len(mods)} ADDED={len(by_t.get(DxfChangeType.ADDED,[]))} "
          f"DELETED={len(by_t.get(DxfChangeType.DELETED,[]))}")
    text_mods = [c for c in mods if c.entity_type == "TEXT"]
    line_mods = [c for c in mods if c.entity_type == "LINE"]
    print(f"  TEXT modified={len(text_mods)} (expect 1 genuine content change)")
    print(f"  LINE modified={len(line_mods)} (the 30 identical-after-rotation lines)")
    if align is not None:
        tdeg = align.get("theta_deg")
        print(f"  estimator theta_deg={tdeg}")
    # Did the rotation get recognized at all?
    return r


# ---------------------------------------------------------------------------
# (a2) ROTATED RE-ORIGIN: small rotation theta = 5 deg + large translation
# ---------------------------------------------------------------------------
def test_rotated_reorigin_5deg():
    print("\n=== (a2) ROTATED RE-ORIGIN 5deg ===")
    theta = math.radians(5.0)
    DX, DY = 150000.0, -90000.0
    base = _grid_lines(40)
    entities_a = _by_type(base)
    b_lines = [_rot_line(ln, theta, DX, DY) for ln in base]
    entities_b = _by_type(b_lines)
    r = DxfComparator().compare_with_modified_detection(entities_a, entities_b)
    align = r.metadata.get("alignment")
    print(f"  alignment: theta_deg={align.get('theta_deg') if align else None} "
          f"trans_mag={align.get('dx') if align else None},{align.get('dy') if align else None}")
    print(f"  reorigin_unchanged_removed = {r.metadata.get('reorigin_unchanged_removed')} (expect 40 if recognized)")
    print(f"  total changes = {len(r.changes)} (expect ~0 if all recognized as unchanged)")
    return r


# ---------------------------------------------------------------------------
# (a3) Direct unit test of _registered_geometry_unchanged with rotation
# ---------------------------------------------------------------------------
def test_registered_geometry_unchanged_rotation():
    print("\n=== (a3) _registered_geometry_unchanged with 90deg rotation (LINE/ARC/CIRCLE) ===")
    theta = math.radians(90.0)
    DX, DY = 150000.0, -90000.0
    align = RigidTransform(dx=DX, dy=DY, theta_rad=theta, inlier_ratio=1.0, candidate_count=100)
    cmp = DxfComparator()

    # LINE: A in A-space, B = rotated+translated A
    a_line = _line(1000.0, 2000.0, 1100.0, 2000.0, "GRID")
    bs = _rot(1000.0, 2000.0, theta, DX, DY)
    be = _rot(1100.0, 2000.0, theta, DX, DY)
    b_line = _line(bs[0], bs[1], be[0], be[1], "GRID")
    # Build DxfChange-like via compare path: use internal helpers directly
    from src.services.comparison.dxf_comparator import DxfChange
    d = DxfChange(change_type=DxfChangeType.DELETED, entity_type="LINE", layer="GRID",
                  old_data=a_line.data, location=a_line.location)
    a = DxfChange(change_type=DxfChangeType.ADDED, entity_type="LINE", layer="GRID",
                  new_data=b_line.data, location=b_line.location)
    res = cmp._registered_geometry_unchanged(d, a, align, tol=0.1)
    print(f"  LINE rotated-unchanged recognized: {res} (expect True if rotation handled)")

    # ARC
    a_arc = NormalizedEntity(hash="ARC:a", entity_type="ARC", layer="GRID",
                             data={"center": (1000.0, 2000.0), "radius": 50.0,
                                   "start_angle": 0.0, "end_angle": 90.0},
                             location=(1000.0, 2000.0))
    bc = _rot(1000.0, 2000.0, theta, DX, DY)
    # B arc rotated: center moves, angles rotate by +90
    b_arc = NormalizedEntity(hash="ARC:b", entity_type="ARC", layer="GRID",
                             data={"center": bc, "radius": 50.0,
                                   "start_angle": 0.0, "end_angle": 90.0},  # raw B angles
                             location=bc)
    da = DxfChange(change_type=DxfChangeType.DELETED, entity_type="ARC", layer="GRID",
                   old_data=a_arc.data, location=a_arc.location)
    aa = DxfChange(change_type=DxfChangeType.ADDED, entity_type="ARC", layer="GRID",
                   new_data=b_arc.data, location=b_arc.location)
    res_arc = cmp._registered_geometry_unchanged(da, aa, align, tol=0.1)
    print(f"  ARC rotated-unchanged recognized: {res_arc} (B angles raw 0/90, A 0/90; _angle_close folds +90)")

    # CIRCLE (no angle, should work)
    a_circ = NormalizedEntity(hash="C:a", entity_type="CIRCLE", layer="GRID",
                              data={"center": (1000.0, 2000.0), "radius": 50.0},
                              location=(1000.0, 2000.0))
    b_circ = NormalizedEntity(hash="C:b", entity_type="CIRCLE", layer="GRID",
                              data={"center": bc, "radius": 50.0}, location=bc)
    dc = DxfChange(change_type=DxfChangeType.DELETED, entity_type="CIRCLE", layer="GRID",
                   old_data=a_circ.data, location=a_circ.location)
    ac = DxfChange(change_type=DxfChangeType.ADDED, entity_type="CIRCLE", layer="GRID",
                   new_data=b_circ.data, location=b_circ.location)
    res_circ = cmp._registered_geometry_unchanged(dc, ac, align, tol=0.1)
    print(f"  CIRCLE rotated-unchanged recognized: {res_circ} (expect True - position only)")


# ---------------------------------------------------------------------------
# (c) Empty / None / single-entity / all-added (no deleted)
# ---------------------------------------------------------------------------
def test_empty_none_single_alladded():
    print("\n=== (c) EMPTY / NONE / SINGLE / ALL-ADDED ===")
    cmp = DxfComparator()

    # empty both
    try:
        r = cmp.compare_with_modified_detection({}, {})
        print(f"  empty/empty: OK, changes={len(r.changes)}")
    except Exception as e:
        print(f"  empty/empty: CRASH {e!r}")
        traceback.print_exc()

    # all-added (A empty, B has 30 lines) — re-origin can't trigger (no deleted)
    try:
        b = _by_type(_grid_lines(30))
        r = cmp.compare_with_modified_detection({}, b)
        print(f"  all-added: OK, changes={len(r.changes)}, "
              f"reorigin={r.metadata.get('reorigin_unchanged_removed')}")
    except Exception as e:
        print(f"  all-added: CRASH {e!r}")
        traceback.print_exc()

    # all-deleted
    try:
        a = _by_type(_grid_lines(30))
        r = cmp.compare_with_modified_detection(a, {})
        print(f"  all-deleted: OK, changes={len(r.changes)}, "
              f"reorigin={r.metadata.get('reorigin_unchanged_removed')}")
    except Exception as e:
        print(f"  all-deleted: CRASH {e!r}")
        traceback.print_exc()

    # single entity each, re-origin shift
    try:
        DX, DY = 150000.0, -90000.0
        a = _by_type([_line(0, 0, 100, 0)])
        b = _by_type([_line(DX, DY, DX + 100, DY)])
        r = cmp.compare_with_modified_detection(a, b)
        print(f"  single/single reorigin: OK, changes={len(r.changes)}, "
              f"reorigin={r.metadata.get('reorigin_unchanged_removed')}")
    except Exception as e:
        print(f"  single/single: CRASH {e!r}")
        traceback.print_exc()

    # None location entities mixed in
    try:
        DX, DY = 150000.0, -90000.0
        none_a = NormalizedEntity(hash="X:a", entity_type="LINE", layer="0",
                                  data={"start": (0, 0), "end": (1, 1)}, location=None)
        none_b = NormalizedEntity(hash="X:b", entity_type="LINE", layer="0",
                                  data={"start": (DX, DY), "end": (DX + 1, DY + 1)}, location=None)
        base = _grid_lines(30)
        a = _by_type(base + [none_a])
        bb = [_line(ln.data["start"][0] + DX, ln.data["start"][1] + DY,
                    ln.data["end"][0] + DX, ln.data["end"][1] + DY, ln.layer) for ln in base]
        b = _by_type(bb + [none_b])
        r = cmp.compare_with_modified_detection(a, b)
        print(f"  none-location mixed: OK, changes={len(r.changes)}, "
              f"reorigin={r.metadata.get('reorigin_unchanged_removed')}")
    except Exception as e:
        print(f"  none-location mixed: CRASH {e!r}")
        traceback.print_exc()


if __name__ == "__main__":
    test_rotated_reorigin_90deg()
    test_rotated_reorigin_5deg()
    test_registered_geometry_unchanged_rotation()
    test_empty_none_single_alladded()
    print("\nDONE")
