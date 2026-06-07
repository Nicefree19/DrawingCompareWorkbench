# -*- coding: utf-8 -*-
"""Adversarial coordinate-integrity probe for the re-origin fix (a7b2c7d).

Synthetic whole-drawing re-origin: B = A + (DX,DY) with DX,DY huge so
translation_magnitude > 1000mm triggers the re-origin path. Contents:
  - many unchanged LINEs (removed by registered-identity)
  - genuine MODIFIED DIMENSION (same registered position, measurement changed)
  - genuine MODIFIED LINE (endpoint moved a few mm in registered space)
  - genuine ADDED LINE (B-only, INSIDE the cloud extent)
  - genuine DELETED LINE (A-only, INSIDE the cloud extent)

Asserts the HARD coordinate constraint on EVERY emitted change:
  MODIFIED.location  == native B  ; MODIFIED.old_location == native A
  MODIFIED.new_data abs coords == native B (overlay reads these)
  ADDED.location / ADDED.new_data == native B
  DELETED.location / DELETED.old_data == native A
and that no original added DxfChange / NormalizedEntity.data got mutated.
"""
import copy
import math
import random

from src.services.comparison.dxf_comparator import DxfComparator, DxfChangeType
from src.services.comparison.dxf_entity_extractor import NormalizedEntity

DX, DY = 128347.0, 315950.0
LAYER = "S-BEAM"  # structural -> exercises layer-aware Q6 0.1mm threshold too


def line(tag, x0, y0, x1, y1, layer=LAYER):
    start = (round(x0, 2), round(y0, 2))
    end = (round(x1, 2), round(y1, 2))
    cx, cy = round((x0 + x1) / 2, 1), round((y0 + y1) / 2, 1)
    return NormalizedEntity(
        hash=f"LINE:{tag}:{sorted([start, end])}",
        entity_type="LINE", layer=layer,
        data={"start": start, "end": end}, location=(cx, cy),
    )


def dim(tag, x, y, meas, layer=LAYER):
    return NormalizedEntity(
        hash=f"DIM:{tag}:{meas}:{(round(x,1),round(y,1))}",
        entity_type="DIMENSION", layer=layer,
        data={"defpoint": (round(x, 1), round(y, 1)), "measurement": meas, "text_override": ""},
        location=(round(x, 1), round(y, 1)),
    )


def by_type(ents):
    out = {}
    for e in ents:
        out.setdefault(e.entity_type, []).append(e)
    return out


random.seed(11)
# Anchors scattered over [0,20000]^2 with varied orientation.
ANCH = []
for i in range(60):
    x0 = random.uniform(1000, 19000)
    y0 = random.uniform(1000, 19000)
    ang = random.uniform(0, math.pi)
    L = random.uniform(80, 400)
    ANCH.append((f"u{i}", x0, y0, x0 + L * math.cos(ang), y0 + L * math.sin(ang)))

A, B = [], []
for (tag, x0, y0, x1, y1) in ANCH:
    A.append(line(tag, x0, y0, x1, y1))
    B.append(line(tag, x0 + DX, y0 + DY, x1 + DX, y1 + DY))

# genuine MODIFIED dimension: same registered position, measurement 1500 -> 1600
A.append(dim("m1", 5000.0, 5000.0, 1500.0))
B.append(dim("m1", 5000.0 + DX, 5000.0 + DY, 1600.0))
# genuine MODIFIED line: endpoint +2mm in registered space (centroid +1mm) -- inside cloud
A.append(line("m2", 8000.0, 8000.0, 8000.0, 8100.0))
B.append(line("m2", 8000.0 + DX, 8000.0 + DY, 8000.0 + DX, 8102.0 + DY))
# genuine DELETED line (A only) -- inside cloud extent
A.append(line("delonly", 12000.0, 12000.0, 12000.0, 12100.0))
# genuine ADDED line (B only) -- inside cloud extent (registered)
B.append(line("addonly", 15000.0 + DX, 15000.0 + DY, 15000.0 + DX, 15100.0 + DY))

B_data_snapshot = {e.hash: copy.deepcopy(e.data) for e in B}
B_loc_snapshot = {e.hash: tuple(e.location) for e in B}

cmp = DxfComparator()
result = cmp.compare_with_modified_detection(by_type(A), by_type(B))

print("=== ALIGNMENT ===")
print("alignment:", result.metadata.get("alignment"))
print("refined:", result.metadata.get("alignment_refined"))
print("reorigin_unchanged_removed:", result.metadata.get("reorigin_unchanged_removed"))
print("stats:", {k: result.stats.get(k) for k in (
    "added", "deleted", "modified", "total_changes", "reorigin_unchanged_removed",
    "modified_detected", "alignment_suppressed")})

mods = [c for c in result.changes if c.change_type == DxfChangeType.MODIFIED]
adds = [c for c in result.changes if c.change_type == DxfChangeType.ADDED]
dels = [c for c in result.changes if c.change_type == DxfChangeType.DELETED]
print(f"\n=== EMITTED: {len(mods)} MODIFIED, {len(adds)} ADDED, {len(dels)} DELETED ===")

problems = []
HUGE = 100000.0


def native_B(loc):  # native B coords are huge (~DX,DY)
    return loc is not None and loc[0] > 50000.0 and loc[1] > 50000.0


def native_A(loc):  # native A coords are small (< extents, never near DX/DY)
    return loc is not None and -HUGE < loc[0] < 50000.0 and -HUGE < loc[1] < 50000.0


for c in mods:
    nd = c.new_data or {}
    od = c.old_data or {}
    print(f"MOD {c.entity_type} loc={c.location} old_loc={c.old_location} "
          f"detail={c.change_detail!r}")
    print(f"    new_data={nd}")
    print(f"    old_data={od}")
    if not native_B(c.location):
        problems.append(f"MODIFIED.location NOT native-B: {c.location}")
    if not native_A(c.old_location):
        problems.append(f"MODIFIED.old_location NOT native-A: {c.old_location}")
    if c.entity_type == "LINE":
        s = nd.get("start")
        if s is not None and not native_B(s):
            problems.append(f"MODIFIED.new_data.start NOT native-B (LEAK): {s}")
        os_ = od.get("start")
        if os_ is not None and not native_A(os_):
            problems.append(f"MODIFIED.old_data.start NOT native-A (LEAK): {os_}")
    if c.entity_type == "DIMENSION":
        dp = nd.get("defpoint")
        if dp is not None and not native_B(dp):
            problems.append(f"MODIFIED.new_data.defpoint NOT native-B (LEAK): {dp}")

for c in adds:
    nd = c.new_data or {}
    print(f"ADD {c.entity_type} loc={c.location} new_data={nd}")
    if not native_B(c.location):
        problems.append(f"ADDED.location NOT native-B (LEAK): {c.location}")
    s = nd.get("start")
    if s is not None and not native_B(s):
        problems.append(f"ADDED.new_data.start NOT native-B (LEAK): {s}")

for c in dels:
    od = c.old_data or {}
    print(f"DEL {c.entity_type} loc={c.location} old_data={od}")
    if not native_A(c.location):
        problems.append(f"DELETED.location NOT native-A (LEAK): {c.location}")
    s = od.get("start")
    if s is not None and not native_A(s):
        problems.append(f"DELETED.old_data.start NOT native-A (LEAK): {s}")

print("\n=== MUTATION CHECK (original B NormalizedEntity) ===")
for e in B:
    if e.data != B_data_snapshot[e.hash]:
        problems.append(f"ORIGINAL B .data MUTATED {e.hash}: {e.data} != {B_data_snapshot[e.hash]}")
    if tuple(e.location) != B_loc_snapshot[e.hash]:
        problems.append(f"ORIGINAL B .location MUTATED {e.hash}: {e.location} != {B_loc_snapshot[e.hash]}")
print("original B entities unchanged:", all(
    e.data == B_data_snapshot[e.hash] and tuple(e.location) == B_loc_snapshot[e.hash] for e in B))

print("\n=== EXPECTATIONS ===")
print(f"  expect reorigin path engaged (removed ~{len(ANCH)} unchanged): "
      f"{result.metadata.get('reorigin_unchanged_removed')}")
print(f"  expect 2 genuine MODIFIED (dim measurement, line endpoint): got {len(mods)}")
print(f"  expect 1 genuine ADDED (addonly): got {len(adds)}")
print(f"  expect 1 genuine DELETED (delonly): got {len(dels)}")

print("\n=== RESULT ===")
if problems:
    print("PROBLEMS FOUND:")
    for p in problems:
        print("  -", p)
else:
    print("NO coordinate-integrity problems in this scenario.")
