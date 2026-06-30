# -*- coding: utf-8 -*-
"""AC1027 (R2013) clean-room native decode — golden GT + opt-in product E2E.

DoD-V1 (own-viewer completion harness): back-expand the clean-room native DWG
reader to AC1027 (R2013). R2013 shares the SAME R2004+ container, R2010+ Common
Entity Data, and R2007+ string stream as R2018 (AC1032), so the existing AC1032
decode chain applies once the version gate accepts AC1027.

GROUND TRUTH PROVENANCE (validation-only, never product code):
  1. ODAFileConverter "26.10.0" (offline golden oracle) converted the local
     git-ignored sample ``.local/native_cad_real_samples/acadsharp/sample_AC1027.dwg``
     to an ACAD2018 DXF.
  2. ezdxf (1.3.x, validation-only) read the modelspace entities; the constants
     below are the exact geometry ODA reports, keyed by the entity handle (== the
     common-entity-data H field). The native clean-room decoder must reproduce
     them with ZERO ODA/ezdxf call at runtime.
Tolerances match the AC1032 golden test (coords 1e-6, angles 1e-4, meas 1e-5) —
NOT weakened. The sample is the same drawing content saved in 2013 format, so the
handles match the AC1032 golden handles exactly (this is the cross-format check).

CI coverage note: these real-file tests require the local git-ignored AC1027
corpus and SKIP in CI — real-file verification is local-only, matching
``test_dwg_r2018_reader`` / ``test_dwg_native_ac1032_adapter``.

Harness selector: the test names contain ``ac1027`` so the harness
``-k "ac1027 or version_v_"`` selection picks them up.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from src.services.comparison.base import ComparisonResult
from src.services.comparison.change_zones import ChangeZoneOptions, build_change_zones
from src.services.comparison.drawing_compare_engine import (
    DrawingCompareEngine,
    DrawingCompareOptions,
)
from src.services.comparison.dwg_importer import DwgImporter, DwgVersionDetector, DwgVersionInfo
from src.services.comparison.dwg_native_ac1032_adapter import (
    AC1032_NATIVE_OPT_IN_ENV,
    DwgNativeAc1032Adapter,
)
from src.services.comparison.dwg_native_reader import DwgNativeAc1015Adapter
from src.services.comparison.dwg_r2018_reader import (
    R2013_VERSION_CODE,
    inspect_r2018_container,
    read_r2018_entities,
)
from src.services.comparison.revision_marker import revcloud_geometry_from_bbox

# Local-only real AC1027 corpus (git-ignored). The acadsharp sample is the same
# drawing as sample_AC1032.dwg saved as R2013, so the ODA handles match the
# AC1032 golden test handles 1:1 (the cross-format equivalence proof).
_AC1027_SAMPLE = Path(".local/native_cad_real_samples/acadsharp/sample_AC1027.dwg")
_AC1027 = DwgVersionInfo("AC1027", "AutoCAD 2013/2014/2015/2016/2017", "R2013", False)


# Ground truth extracted OFFLINE from the ODA-converted DXF (see module docstring).
_GT_LINES = {
    0x2C7: (
        (3.592533998909389, 1.477241896180196, 0.0),
        (6.863547033979557, 1.477241896180196, 0.0),
    ),
    0x517: (
        (330.2890594765796, 2.941179455067987, 0.0),
        (364.4872644138525, 37.13938439234094, 0.0),
    ),
    # true colour (0x8000) + non-zero Z flag.
    0x99E: (
        (18.96130506894906, -124.7749383365533, 0.0),
        (23.96130506894906, -119.7749383365533, 0.0),
    ),
}
_GT_CIRCLES = {
    0x51D: ((569.6764940374901, 25.73998274658328), 11.39940164575765),
    # AcDbColor reference colour (0x4000) — handle in the handle stream.
    0x99F: ((30.20357222512345, -119.9668664522385), 2.382841759818497),
}
_GT_ARC = {
    0x320: (
        (56.35179242595231, 4.697601732518876),
        3.044494390598889,
        341.0435453511963,
        161.0435453511958,
    ),
}
_GT_ELLIPSE = {
    0x321: {
        "center": (68.00051689353697, 4.185210497355278),
        "major": (-5.101255306291787, 0.0),
        "ratio": 0.6403727385155383,
        "start": 0.0,
        "end": 6.283185307179586,
    },
}
_GT_POINT = {
    0x28E: (1.494404150136852, 1.491325898678436, 0.0),
}
_GT_LWPOLY = {
    0x2E2: {"closed": False, "nverts": 5, "v0": (12.14051619543163, 1.379831194356633)},
    0x2E4: {"closed": True, "nverts": 7, "v0": (28.00563684412818, 0.3613048628239071)},
}
_GT_TEXT = {
    0x3B9: {
        "insert": (147.8194471194604, 2.854975299808757),
        "height": 1.0,
        "text": "Hello this is a single line text",
    },
    0x915: {
        "insert": (21.23207980849656, -70.79326034621432),
        "height": 1.0,
        "text": "XData 3Real",
    },  # EED/XData path
}
_GT_MTEXT = {
    0x3EC: {
        "insert": (183.5889280790414, 5.226370718413136),
        "height": 1.0,
        "text": "this is a Mtext\nwith multiple lines in it",
    },
    0x513: {
        "insert": (741.0237500252849, 14.69681240225556),
        "height": 1.424925205719706,
        "text": "Sample annotation",
    },
}
_GT_INSERT = {
    0x704: {
        "insert": (920.6796266627233, 16.35285377389053),
        "xscale": 0.3633736948472006,
        "rotation": 0.0,
        "block_name": "MyBlock",
    },
    0x783: {
        "insert": (-208.1495327696078, 8.990124365984101),
        "xscale": 1.217889264582384,
        "rotation": 0.0,
        "block_name": "my_block_v2",
    },
}
_GT_DIM = {
    0x514: {
        "dimtype": 0,
        "tm": (339.0659510877516, 34.45477396013389),
        "meas": 46.71561670814134,
    },  # 0x514=1300 LINEAR
    0x527: {
        "dimtype": 1,
        "tm": (390.7573692587501, 33.66808283895858),
        "meas": 48.36356523110595,
    },  # 0x527=1319 ALIGNED
}
_GT_SPLINE = {
    0x433: {
        "degree": 3,
        "n_ctrl": 4,
        "ctrl": [
            (250.4587907832241, 1.157147022223159, 0.0),
            (254.7137711095261, 11.98610420237003, 0.0),
            (259.3879640869438, 1.234244352797873, 0.0),
            (259.2245511100041, 11.0186273898506, 0.0),
        ],
        "knots": [0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0],
    },
}
_GT_LEADER = {
    # The DWG stores 3 leader-line vertices; ODA's DXF reports 4 (derived hookline
    # vertex). The native decode reproduces the 3 STORED vertices (DWG truth).
    0x512: {
        "n_points": 3,
        "points": [
            (717.8687154323397, 2.941179455067987, 0.0),
            (729.2681170780972, 48.53878603809859, 0.0),
            (740.3987500252849, 14.07181240225556, 0.0),
        ],
    },
}
_GT_LAYER = {
    0x517: "Layer2",  # LINE
    0x513: "Layer1",  # MTEXT
}


def _approx(actual, expected, tol=1e-6):
    return abs(actual - expected) <= tol


def test_ac1027_container_is_navigable_when_gate_accepts_r2013() -> None:
    # The gate must ACCEPT AC1027 (not return wrong_version) and navigate it. A
    # truly wrong version (AC1015) must still be rejected — the relaxation is
    # scoped to the genuinely-shared R2013 format, not a blanket bypass.
    if not _AC1027_SAMPLE.exists():
        pytest.skip(f"local AC1027 sample not present: {_AC1027_SAMPLE}")
    diag = inspect_r2018_container(_AC1027_SAMPLE.read_bytes(), version_code=R2013_VERSION_CODE)
    assert diag.status == "navigable", diag.to_dict()
    assert diag.magic_ok is True
    # Wrong version still rejected (default version_code is AC1032).
    rejected = inspect_r2018_container(b"AC1015" + b"\x00" * 0x200)
    assert rejected.status == "wrong_version"


def test_real_ac1027_decode_matches_ground_truth() -> None:
    # The native clean-room decoder reproduces the ODA-converted DXF geometry for
    # AC1027 (R2013) across every supported entity type, within ε. R2013 reuses
    # the R2018 decode path verbatim — this proves the format is genuinely shared.
    if not _AC1027_SAMPLE.exists():
        pytest.skip(f"local AC1027 sample not present: {_AC1027_SAMPLE}")

    table = read_r2018_entities(_AC1027_SAMPLE.read_bytes(), version_code=R2013_VERSION_CODE)
    assert table.status == "decoded", table.message
    assert table.decoded_count >= 200, table.type_counts
    for kind in (
        "LINE",
        "CIRCLE",
        "ARC",
        "POINT",
        "LWPOLYLINE",
        "TEXT",
        "MTEXT",
        "INSERT",
        "DIMENSION",
        "HATCH",
        "ELLIPSE",
        "SPLINE",
        "LEADER",
    ):
        assert table.type_counts.get(kind, 0) > 0, table.type_counts

    by = {e.handle: e for e in table.entities}

    for handle, (start, end) in _GT_LINES.items():
        e = by[handle]
        assert e.type_name == "LINE"
        assert all(_approx(a, b) for a, b in zip(e.geometry["start"], start)), e.geometry
        assert all(_approx(a, b) for a, b in zip(e.geometry["end"], end)), e.geometry

    for handle, (center, radius) in _GT_CIRCLES.items():
        e = by[handle]
        assert e.type_name == "CIRCLE"
        assert _approx(e.geometry["center"][0], center[0])
        assert _approx(e.geometry["center"][1], center[1])
        assert _approx(e.geometry["radius"], radius)

    for handle, (center, radius, sdeg, edeg) in _GT_ARC.items():
        e = by[handle]
        assert e.type_name == "ARC"
        assert _approx(e.geometry["center"][0], center[0])
        assert _approx(e.geometry["radius"], radius)
        assert _approx(e.geometry["start_angle_deg"], sdeg, tol=1e-4)
        assert _approx(e.geometry["end_angle_deg"], edeg, tol=1e-4)

    for handle, exp in _GT_ELLIPSE.items():
        e = by[handle]
        assert e.type_name == "ELLIPSE"
        assert _approx(e.geometry["center"][0], exp["center"][0])
        assert _approx(e.geometry["major_axis"][0], exp["major"][0])
        assert _approx(e.geometry["ratio"], exp["ratio"])
        assert _approx(e.geometry["end_param"], exp["end"], tol=1e-4)

    for handle, location in _GT_POINT.items():
        e = by[handle]
        assert e.type_name == "POINT"
        assert _approx(e.geometry["location"][0], location[0])
        assert _approx(e.geometry["location"][1], location[1])

    for handle, exp in _GT_LWPOLY.items():
        e = by[handle]
        assert e.type_name == "LWPOLYLINE"
        assert len(e.geometry["vertices"]) == exp["nverts"]
        assert e.geometry["closed"] is exp["closed"]
        assert _approx(e.geometry["vertices"][0][0], exp["v0"][0])
        assert _approx(e.geometry["vertices"][0][1], exp["v0"][1])

    for handle, exp in _GT_TEXT.items():
        e = by[handle]
        assert e.type_name == "TEXT"
        assert _approx(e.geometry["insert"][0], exp["insert"][0])
        assert _approx(e.geometry["height"], exp["height"])
        assert e.geometry["text"] == exp["text"]

    for handle, exp in _GT_MTEXT.items():
        e = by[handle]
        assert e.type_name == "MTEXT"
        assert _approx(e.geometry["insert"][0], exp["insert"][0])
        assert _approx(e.geometry["height"], exp["height"])
        assert e.geometry["text"] == exp["text"]

    for handle, exp in _GT_INSERT.items():
        e = by[handle]
        assert e.type_name == "INSERT"
        assert _approx(e.geometry["insert"][0], exp["insert"][0])
        assert _approx(e.geometry["scale"][0], exp["xscale"])
        assert _approx(e.geometry["rotation_deg"], exp["rotation"], tol=1e-4)
        assert e.geometry["block_name"] == exp["block_name"]

    for handle, exp in _GT_DIM.items():
        e = by[handle]
        assert e.type_name == "DIMENSION"
        assert e.geometry["dimtype"] == exp["dimtype"], e.geometry
        assert _approx(e.geometry["text_midpoint"][0], exp["tm"][0])
        assert _approx(e.geometry["measurement"], exp["meas"], tol=1e-5)

    for handle, exp in _GT_SPLINE.items():
        e = by[handle]
        assert e.type_name == "SPLINE"
        assert e.geometry["degree"] == exp["degree"]
        ctrl = e.geometry["control_points"]
        assert len(ctrl) == exp["n_ctrl"]
        for got, want in zip(ctrl, exp["ctrl"]):
            assert all(_approx(a, b) for a, b in zip(got, want)), (f"{handle:#x}", got, want)
        for got, want in zip(e.geometry["knots"], exp["knots"]):
            assert _approx(got, want)

    for handle, exp in _GT_LEADER.items():
        e = by[handle]
        assert e.type_name == "LEADER"
        pts = e.geometry["points"]
        assert len(pts) == exp["n_points"]
        for got, want in zip(pts, exp["points"]):
            assert all(_approx(a, b) for a, b in zip(got, want)), (f"{handle:#x}", got, want)

    for handle, expected_layer in _GT_LAYER.items():
        assert by[handle].layer == expected_layer, by[handle]


def test_real_ac1027_opt_in_product_path_diffs_and_clouds(monkeypatch: pytest.MonkeyPatch) -> None:
    # End-to-end: a real AC1027 imported through the OPT-IN PRODUCT path (the SAME
    # opt-in env as AC1032) feeds the real compare engine -> change zones ->
    # revision clouds, with ZERO ODA. Mirrors the AC1032 capstone.
    if not _AC1027_SAMPLE.exists():
        pytest.skip(f"local AC1027 sample not present: {_AC1027_SAMPLE}")
    monkeypatch.setenv(AC1032_NATIVE_OPT_IN_ENV, "1")

    version = DwgVersionDetector.detect_file(_AC1027_SAMPLE)
    assert version.code == "AC1027"

    adapter = DwgNativeAc1032Adapter(fallback_adapter=DwgNativeAc1015Adapter())
    assert adapter.supports_version(version) is True  # handled under the opt-in

    before = DwgImporter(adapter=adapter).import_file(_AC1027_SAMPLE)
    assert before["import_report"]["adapter"]["name"] == "native-ac1032"  # ZERO ODA
    assert len(before["entities"]) > 200

    engine = DrawingCompareEngine(DrawingCompareOptions(include_unchanged=False))

    # (1) self-compare => no false positives.
    self_diff = engine.compare(before, copy.deepcopy(before))
    self_edits = (
        self_diff.summary_counts["added"]
        + self_diff.summary_counts["removed"]
        + self_diff.summary_counts["modified"]
    )
    assert self_edits == 0, self_diff.summary_counts

    # (2) one edit detected + isolated.
    after = copy.deepcopy(before)
    moved = next(e for e in after["entities"] if e["type"] == "line")
    moved["geometry"]["end"]["x"] += 40.0
    moved["geometry"]["end"]["y"] += 30.0
    sx, ex = moved["geometry"]["start"]["x"], moved["geometry"]["end"]["x"]
    sy, ey = moved["geometry"]["start"]["y"], moved["geometry"]["end"]["y"]
    moved["bbox"] = {
        "min_x": min(sx, ex),
        "min_y": min(sy, ey),
        "max_x": max(sx, ex),
        "max_y": max(sy, ey),
    }
    centroid = ((min(sx, ex) + max(sx, ex)) / 2.0, (min(sy, ey) + max(sy, ey)) / 2.0)

    diff = engine.compare(before, after)
    edits = (
        diff.summary_counts["added"]
        + diff.summary_counts["removed"]
        + diff.summary_counts["modified"]
    )
    assert 1 <= edits <= 4, diff.summary_counts

    # (3) edit drives a change zone + a revision cloud covering it.
    result = ComparisonResult(source_a="before.dwg", source_b="after.dwg")
    for record in diff.to_change_records():
        result.add_change(record)
    zones = build_change_zones(
        result, pair_id="P1", drawing_number="P1", options=ChangeZoneOptions(cluster_distance=120.0)
    )
    covering = [
        z
        for z in zones
        if z.bbox[0] <= centroid[0] <= z.bbox[2] and z.bbox[1] <= centroid[1] <= z.bbox[3]
    ]
    assert covering, f"no change zone covers the moved line at {centroid}"
    cloud = revcloud_geometry_from_bbox(covering[0].bbox)
    assert len(cloud.vertices) >= 4


def test_ac1027_adapter_default_off_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    # The opt-in default-OFF posture must hold for AC1027 too: with the env unset
    # an AC1027 file reports unsupported (falls through to the existing default
    # path), exactly like AC1032.
    monkeypatch.delenv(AC1032_NATIVE_OPT_IN_ENV, raising=False)
    adapter = DwgNativeAc1032Adapter(fallback_adapter=DwgNativeAc1015Adapter())
    assert adapter.supports_version(_AC1027) is False
