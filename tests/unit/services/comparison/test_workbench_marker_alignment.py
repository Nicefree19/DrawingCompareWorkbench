"""P0-2b — monolith-side marker lockstep: when the zone render reports it warped
the after raster (``after_marker_world_transform`` present), the after-side change
markers must be moved by the SAME transform so they land on the warped raster.

Uses the offscreen-Qt conftest under tests/unit/services/comparison/.
"""

from __future__ import annotations

import math

import pytest

from src.gui.drawing_compare_workbench import (
    _local_overlays_for_zone,
    zone_bbox_to_pixel_rect,
)
from src.services.comparison.global_alignment import RigidTransform
from src.services.comparison.zone_render_service import (
    WorldWindow,
    transform_for_window,
)


def _payload(after_marker_world_transform):
    tf = transform_for_window(WorldWindow(0.0, 0.0, 100.0, 100.0), output_width=200, output_height=200)
    return {
        "before_transform": tf,
        "after_transform": tf,
        "after_marker_world_transform": after_marker_world_transform,
        "world_window": {"xmin": 0.0, "ymin": 0.0, "xmax": 100.0, "ymax": 100.0},
        "visual_fidelity": "cad_render",
        "render_lifecycle": "ready",
    }


def _overlays():
    item = {"zone_id": "z1", "bbox": [10.0, 10.0, 20.0, 20.0], "old_bbox": [10.0, 10.0, 20.0, 20.0]}
    return [item], dict(item)


def test_marker_bbox_shifts_when_after_raster_was_warped():
    overlays, overlay = _overlays()
    t = RigidTransform(dx=10.0, dy=0.0, theta_rad=0.0)  # significant translation
    aligned = _local_overlays_for_zone(overlays, overlay, "z1", _payload(t.to_dict()), viewer_pair=None)
    unaligned = _local_overlays_for_zone(overlays, overlay, "z1", _payload(None), viewer_pair=None)

    a_px = aligned[0]["after_bbox_px"]
    u_px = unaligned[0]["after_bbox_px"]
    # after markers moved (raster was warped); before markers unaffected
    assert a_px != u_px
    assert aligned[0]["before_bbox_px"] == unaligned[0]["before_bbox_px"]


def test_marker_bbox_unchanged_without_transform_matches_direct_rect():
    overlays, overlay = _overlays()
    tf = transform_for_window(WorldWindow(0.0, 0.0, 100.0, 100.0), output_width=200, output_height=200)
    unaligned = _local_overlays_for_zone(overlays, overlay, "z1", _payload(None), viewer_pair=None)
    expected = zone_bbox_to_pixel_rect([10.0, 10.0, 20.0, 20.0], tf)
    assert unaligned[0]["after_bbox_px"] == expected


def test_marker_bbox_rotation_moves_after_only():
    overlays, overlay = _overlays()
    t = RigidTransform(dx=0.0, dy=0.0, theta_rad=math.radians(30.0))
    aligned = _local_overlays_for_zone(overlays, overlay, "z1", _payload(t.to_dict()), viewer_pair=None)
    unaligned = _local_overlays_for_zone(overlays, overlay, "z1", _payload(None), viewer_pair=None)
    assert aligned[0]["after_bbox_px"] != unaligned[0]["after_bbox_px"]
