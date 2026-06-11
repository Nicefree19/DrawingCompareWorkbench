# -*- coding: utf-8 -*-
"""Paint-behavior contract for the lightweight viewport Canvas (T1).

Locks the three behaviors that make interaction light (measured at 100k
segments: 30-tick burst 3,751 ms -> 178 ms, zoomed paint 100k -> 22 drawn):

1. cheap-pan  — rapid camera ticks do NOT trigger repaints; one settle
                repaint fires after input pauses.
2. culling    — segments outside the visible world rect are skipped
                (counted in lastPaintCulledSegments).
3. ink        — the settled render still actually draws (visual smoke).
"""

from __future__ import annotations

import time

import pytest


def _pump(qapp, ms: float) -> None:
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        qapp.processEvents()
        time.sleep(0.002)


def _wait_paint(qapp, quick, root, min_count: int, timeout_s: float = 4.0) -> bool:
    deadline = time.perf_counter() + timeout_s
    forced = False
    while time.perf_counter() < deadline:
        if int(root.property("paintCount") or 0) >= min_count:
            return True
        qapp.processEvents()
        time.sleep(0.003)
        if not forced and time.perf_counter() > deadline - timeout_s / 2:
            try:  # force one synchronous render pass if the loop is idle
                quick.grabFramebuffer()
            except Exception:  # noqa: BLE001
                pass
            forced = True
    return int(root.property("paintCount") or 0) >= min_count


@pytest.fixture()
def viewport(qapp):
    from src.gui.lightweight_viewport import LightweightDrawingViewport

    vp = LightweightDrawingViewport()
    vp.resize(800, 600)
    vp.show()
    quick = getattr(vp, "_quick", None)
    root = quick.rootObject() if quick is not None and hasattr(quick, "rootObject") else None
    if root is None:
        vp.close()
        pytest.skip("QML root unavailable in this environment (fallback widget)")
    yield qapp, vp, quick, root
    vp.close()


def _load_scene(qapp, quick, root) -> int:
    # 60 segments around world (5000, 3000) + 1 far-away outlier for culling.
    geometry = []
    for i in range(60):
        x = 4000.0 + i * 30.0
        geometry.append([x, 2800.0, x + 25.0, 3200.0])
    prims = [
        {"type": "lines", "geometry": geometry, "properties": {}},
        {"type": "lines", "geometry": [[90000.0, 90000.0, 90100.0, 90100.0]], "properties": {}},
    ]
    root.setProperty("worldBbox", [0.0, 0.0, 100000.0, 100000.0])
    root.setProperty("cameraCenterX", 5000.0)
    root.setProperty("cameraCenterY", 3000.0)
    root.setProperty("unitsPerPixel", 10.0)  # view ~8000x6000 around content
    root.setProperty("primitives", prims)
    _pump(qapp, 200)  # let the settle timer from the camera pushes elapse
    assert _wait_paint(qapp, quick, root, 1), "initial paint never happened"
    return int(root.property("paintCount") or 0)


def test_rapid_camera_ticks_do_not_repaint_until_settle(viewport):
    qapp, _vp, quick, root = viewport
    base = _load_scene(qapp, quick, root)

    for i in range(10):  # rapid interaction burst (no settle gap)
        root.setProperty("cameraCenterX", 5000.0 + i * 40.0)
        qapp.processEvents()
    during = int(root.property("paintCount") or 0) - base
    assert during == 0, f"cheap-pan violated: {during} repaints during the burst"

    assert _wait_paint(qapp, quick, root, base + 1), "settle repaint never fired"
    _pump(qapp, 250)
    total = int(root.property("paintCount") or 0) - base
    assert total <= 2, f"settle should repaint once (got {total})"


def test_settled_paint_culls_offscreen_segments_and_draws_ink(viewport):
    qapp, _vp, quick, root = viewport
    _load_scene(qapp, quick, root)

    # The far-away outlier segment must be culled at this camera.
    assert int(root.property("lastPaintCulledSegments") or 0) >= 1
    assert int(root.property("lastPaintDrawnSegments") or 0) >= 60

    # Visual smoke: the framebuffer contains non-background ink. Full-frame
    # grayscale scan — sparse pixel sampling missed ~1px strokes under load.
    try:
        image = quick.grabFramebuffer()
    except Exception:  # noqa: BLE001
        image = None
    if image is None or image.isNull():
        pytest.skip("framebuffer grab unavailable in this environment")
    from PySide6.QtGui import QImage

    gray = image.convertToFormat(QImage.Format_Grayscale8)
    buf = gray.constBits()
    darkest = min(buf) if len(buf) else 255
    assert darkest < 140, (
        f"settled render produced no visible strokes (darkest gray={darkest})"
    )


def test_zoomed_in_paint_skips_most_of_the_sheet(viewport):
    qapp, _vp, quick, root = viewport
    base = _load_scene(qapp, quick, root)

    # Zoom to a tiny window around ONE segment cluster edge.
    root.setProperty("cameraCenterX", 4000.0)
    root.setProperty("cameraCenterY", 3000.0)
    root.setProperty("unitsPerPixel", 0.5)  # ~400x300 world units visible
    _pump(qapp, 200)
    assert _wait_paint(qapp, quick, root, base + 1)

    drawn = int(root.property("lastPaintDrawnSegments") or 0)
    culled = int(root.property("lastPaintCulledSegments") or 0)
    assert culled > drawn, f"culling ineffective when zoomed (drawn={drawn}, culled={culled})"
    assert drawn >= 1, "zoomed view must still draw the local content"


def test_focused_zone_also_wears_its_revision_cloud(viewport):
    # Live-review feedback (2026-06-11): the picked change must ALSO show as
    # a (never-dimmed) revision cloud — the focus pin alone left the selected
    # change the least-marked thing on screen while all other clouds dimmed.
    qapp, vp, _quick, root = viewport
    overlays = [
        {"zone_id": "C-001", "change_type": "deleted",
         "old_bbox": {"min_x": 100.0, "min_y": 100.0, "max_x": 200.0, "max_y": 200.0},
         "bbox": None},
        {"zone_id": "C-005", "change_type": "moved",
         "old_bbox": {"min_x": 900.0, "min_y": 100.0, "max_x": 950.0, "max_y": 150.0},
         "bbox": {"min_x": 900.0, "min_y": 100.0, "max_x": 950.0, "max_y": 150.0}},
    ]
    vp.push_change_overlays_from_v1(overlays, side="before", focus_zone_id="C-001")
    _pump(qapp, 50)
    cloud = root.property("overlaysCloud") or []
    focus = root.property("overlaysFocus") or []
    cloud_ids = {(o.get("zoneId") if isinstance(o, dict) else o.property("zoneId")): o for o in cloud}
    assert "C-001" in cloud_ids, "focused zone must ALSO be in the cloud layer"
    c1 = cloud_ids["C-001"]
    dimmed = c1.get("dimmed") if isinstance(c1, dict) else c1.property("dimmed")
    assert not dimmed, "the focused zone's cloud must never be dimmed"
    assert any(
        (o.get("zoneId") if isinstance(o, dict) else o.property("zoneId")) == "C-001"
        for o in focus
    ), "focus marker must still exist"


def test_zoom_band_lod_skips_subpixel_segments_until_zoomed_in(viewport):
    """T2-B — segments shorter than ~0.75 px at the settled zoom are
    skipped (they read as noise, not ink, yet dominated dense sheets:
    real pack measured 65,711 of 65,902 segments sub-pixel at
    fit-to-view). Zooming in shrinks the threshold so the same
    segments draw again."""

    qapp, vp, quick, root = viewport
    tiny = [[1000.0 + i * 5.0, 1000.0, 1001.0 + i * 5.0, 1000.5]
            for i in range(50)]  # |dx|+|dy| = 1.5 world units
    prims = [
        {"type": "lines", "geometry": tiny, "properties": {}},
        {"type": "lines", "geometry": [[0.0, 0.0, 4000.0, 3000.0]],
         "properties": {}},
    ]
    root.setProperty("worldBbox", [0.0, 0.0, 8000.0, 6000.0])
    root.setProperty("cameraCenterX", 1100.0)
    root.setProperty("cameraCenterY", 1000.0)
    root.setProperty("unitsPerPixel", 10.0)  # lodMin = 7.5 > 1.5
    root.setProperty("primitives", prims)
    _pump(qapp, 200)
    assert _wait_paint(qapp, quick, root, 1)
    drawn = int(root.property("lastPaintDrawnSegments") or 0)
    culled = int(root.property("lastPaintCulledSegments") or 0)
    assert drawn == 1, f"only the long line should draw, got {drawn}"
    assert culled >= 50, f"tiny segments must be LOD-culled, got {culled}"

    # Zoom into the tiny cluster: lodMin = 0.3 < 1.5 → they draw. The
    # camera window (800px * 0.4 = 320 world units, centred on the
    # 246-unit-wide cluster) keeps every tiny segment inside the
    # viewport so culling doesn't mask the LOD behaviour under test.
    before = int(root.property("paintCount") or 0)
    root.setProperty("cameraCenterX", 1123.0)
    root.setProperty("unitsPerPixel", 0.4)
    _pump(qapp, 200)
    assert _wait_paint(qapp, quick, root, before + 1)
    drawn_zoomed = int(root.property("lastPaintDrawnSegments") or 0)
    assert drawn_zoomed >= 45, (
        f"zoomed-in paint must include the tiny segments, got {drawn_zoomed}"
    )
