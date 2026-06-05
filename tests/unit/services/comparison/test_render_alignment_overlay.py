"""P0-2b — pure pixel-space alignment math (render_alignment) + RigidTransform.from_dict.

These tests are the primary safeguard for the HIGH-risk full visual alignment
(translation + rotation): a sign / direction / double-apply error here would
itself be a *silent visual error* — the exact class the feature fights. So the
direction is asserted empirically against the real ``transform_for_window``
output and the documented B->A convention.
"""

import math

import pytest

from src.services.comparison import render_alignment as ra
from src.services.comparison.global_alignment import RigidTransform
from src.services.comparison.zone_render_service import (
    WorldWindow,
    transform_for_window,
)


# --------------------------------------------------------------------------- #
# RigidTransform.from_dict (B1)
# --------------------------------------------------------------------------- #


def test_from_dict_round_trips_to_dict():
    t = RigidTransform(dx=12.5, dy=-3.0, theta_rad=0.123, inlier_ratio=0.9, candidate_count=42)
    back = RigidTransform.from_dict(t.to_dict())
    assert back.dx == pytest.approx(12.5)
    assert back.dy == pytest.approx(-3.0)
    assert back.theta_rad == pytest.approx(0.123)
    assert back.inlier_ratio == pytest.approx(0.9)
    assert back.candidate_count == 42


def test_from_dict_ignores_derived_keys_and_defaults_missing():
    # theta_deg / translation_magnitude are derived; missing canonical -> 0.0
    t = RigidTransform.from_dict({"theta_deg": 999.0, "dx": 5.0})
    assert t.dx == pytest.approx(5.0)
    assert t.dy == pytest.approx(0.0)
    assert t.theta_rad == pytest.approx(0.0)  # NOT derived from theta_deg
    assert t.inlier_ratio == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# fixtures: a known window transform (before == after, as the pipeline produces)
# --------------------------------------------------------------------------- #


@pytest.fixture
def window_tf():
    win = WorldWindow(0.0, 0.0, 100.0, 100.0)
    tf = transform_for_window(win, output_width=200, output_height=200)
    return tf


def _apply_affine(af, px, py):
    a, b, c, d, e, f = af
    return (a * px + b * py + e, c * px + d * py + f)


def _manual_after_pixel_to_before_pixel(before_tf, after_tf, rigid, px, py):
    """Reference chain: after-pixel -> after-world -> (T) before-world -> before-pixel."""
    p2w = after_tf["pixel_to_world"]
    wx = p2w["a"] * px + p2w["b"] * py + p2w["e"]
    wy = p2w["c"] * px + p2w["d"] * py + p2w["f"]
    bwx, bwy = rigid.apply(wx, wy)
    w2p = before_tf["world_to_pixel"]
    bpx = w2p["a"] * bwx + w2p["b"] * bwy + w2p["e"]
    bpy = w2p["c"] * bwx + w2p["d"] * bwy + w2p["f"]
    return (bpx, bpy)


# --------------------------------------------------------------------------- #
# compose_after_pixel_affine — direction / sign
# --------------------------------------------------------------------------- #


def test_compose_translation_direction(window_tf):
    # T maps AFTER -> BEFORE: dx=+10mm. Window scale 2px/mm -> +20px in x.
    t = RigidTransform(dx=10.0, dy=0.0, theta_rad=0.0)
    M = ra.compose_after_pixel_affine(window_tf, window_tf, t)
    assert M is not None
    # after-pixel (40,100) -> before-pixel (60,100)
    assert _apply_affine(M, 40.0, 100.0) == pytest.approx((60.0, 100.0))


def test_compose_matches_manual_chain_translation(window_tf):
    t = RigidTransform(dx=7.0, dy=-4.0, theta_rad=0.0)
    M = ra.compose_after_pixel_affine(window_tf, window_tf, t)
    for px, py in [(0.0, 0.0), (40.0, 100.0), (199.0, 1.0), (123.0, 77.0)]:
        assert _apply_affine(M, px, py) == pytest.approx(
            _manual_after_pixel_to_before_pixel(window_tf, window_tf, t, px, py)
        )


def test_compose_matches_manual_chain_rotation(window_tf):
    # 90 degree rotation — the real-world landscape<->portrait case.
    t = RigidTransform(dx=0.0, dy=0.0, theta_rad=math.pi / 2.0)
    M = ra.compose_after_pixel_affine(window_tf, window_tf, t)
    assert M is not None
    for px, py in [(0.0, 0.0), (40.0, 100.0), (150.0, 30.0), (200.0, 200.0)]:
        assert _apply_affine(M, px, py) == pytest.approx(
            _manual_after_pixel_to_before_pixel(window_tf, window_tf, t, px, py)
        )


def test_compose_returns_none_when_not_significant(window_tf):
    tiny = RigidTransform(dx=0.001, dy=0.0, theta_rad=0.0)  # below 0.05mm
    assert tiny.is_significant is False
    assert ra.compose_after_pixel_affine(window_tf, window_tf, tiny) is None


def test_compose_returns_none_for_missing_transform_or_none_rigid(window_tf):
    t = RigidTransform(dx=10.0, dy=0.0, theta_rad=0.0)
    assert ra.compose_after_pixel_affine(window_tf, window_tf, None) is None
    assert ra.compose_after_pixel_affine({}, window_tf, t) is None
    assert ra.compose_after_pixel_affine(window_tf, {}, t) is None


# --------------------------------------------------------------------------- #
# lockstep proof: marker pixel == warped-raster pixel for the same world point
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "t",
    [
        RigidTransform(dx=10.0, dy=-6.0, theta_rad=0.0),
        RigidTransform(dx=3.0, dy=2.0, theta_rad=math.radians(15.0)),
    ],
)
def test_marker_and_raster_stay_in_lockstep(window_tf, t):
    """A change at after-world W: its position in the warped raster (M applied to
    its after-pixel) must equal its marker pixel (T then before world_to_pixel).
    Both derive from the same T, so they must agree."""
    M = ra.compose_after_pixel_affine(window_tf, window_tf, t)
    # pick an after-world point, find its after-pixel
    w2p_a = window_tf["world_to_pixel"]
    aw = (35.0, 62.0)
    ap = (
        w2p_a["a"] * aw[0] + w2p_a["b"] * aw[1] + w2p_a["e"],
        w2p_a["c"] * aw[0] + w2p_a["d"] * aw[1] + w2p_a["f"],
    )
    raster_pixel = _apply_affine(M, ap[0], ap[1])

    # marker path: align world point by T, then before world_to_pixel
    bw = ra.align_world_point(aw, t)
    w2p_b = window_tf["world_to_pixel"]
    marker_pixel = (
        w2p_b["a"] * bw[0] + w2p_b["b"] * bw[1] + w2p_b["e"],
        w2p_b["c"] * bw[0] + w2p_b["d"] * bw[1] + w2p_b["f"],
    )
    assert raster_pixel == pytest.approx(marker_pixel)


# --------------------------------------------------------------------------- #
# align_world_point / align_world_bbox
# --------------------------------------------------------------------------- #


def test_align_world_point_identity_when_inactive():
    assert ra.align_world_point((5.0, 9.0), None) == (5.0, 9.0)
    tiny = RigidTransform(dx=0.0, dy=0.0, theta_rad=0.0)
    assert ra.align_world_point((5.0, 9.0), tiny) == (5.0, 9.0)


def test_align_world_point_applies_T():
    t = RigidTransform(dx=10.0, dy=2.0, theta_rad=0.0)
    assert ra.align_world_point((1.0, 1.0), t) == pytest.approx((11.0, 3.0))


def test_align_world_bbox_translation():
    t = RigidTransform(dx=10.0, dy=-5.0, theta_rad=0.0)
    out = ra.align_world_bbox([0.0, 0.0, 4.0, 4.0], t)
    assert list(out) == pytest.approx([10.0, -5.0, 14.0, -1.0])


def test_align_world_bbox_rotation_reenvelopes():
    # 90deg about origin: corners of [0,0,2,2] -> envelope [-2,0,0,2]
    t = RigidTransform(dx=0.0, dy=0.0, theta_rad=math.pi / 2.0)
    out = ra.align_world_bbox([0.0, 0.0, 2.0, 2.0], t)
    assert list(out) == pytest.approx([-2.0, 0.0, 0.0, 2.0])


def test_align_world_bbox_identity_and_defensive():
    t = RigidTransform(dx=10.0, dy=0.0, theta_rad=0.0)
    assert ra.align_world_bbox(None, t) is None
    # not active -> unchanged object
    bbox = [1.0, 2.0, 3.0, 4.0]
    assert ra.align_world_bbox(bbox, None) is bbox
    # malformed -> returned unchanged (never fabricate)
    bad = [1.0, 2.0]
    assert ra.align_world_bbox(bad, t) is bad


# --------------------------------------------------------------------------- #
# aligned_after_transform
# --------------------------------------------------------------------------- #


def test_aligned_after_transform_becomes_before_when_active(window_tf):
    after_tf = dict(window_tf)
    after_tf["min_x"] = 999.0  # make it distinguishable
    t = RigidTransform(dx=10.0, dy=0.0, theta_rad=0.0)
    out = ra.aligned_after_transform(window_tf, after_tf, t)
    assert out["world_to_pixel"] == window_tf["world_to_pixel"]
    assert out["min_x"] == window_tf["min_x"]  # before's value, not 999


def test_aligned_after_transform_unchanged_when_inactive(window_tf):
    after_tf = dict(window_tf)
    out = ra.aligned_after_transform(window_tf, after_tf, None)
    assert out is after_tf


# --------------------------------------------------------------------------- #
# warp_after_image (needs numpy + cv2)
# --------------------------------------------------------------------------- #


def test_warp_identity_when_affine_none():
    sentinel = object()
    assert ra.warp_after_image(sentinel, None, (10, 10)) is sentinel


def test_warp_translation_and_determinism():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    img = np.zeros((20, 20, 3), dtype=np.uint8)
    img[5, 5] = (255, 255, 255)  # a white pixel
    # pure +3px x shift affine
    affine = (1.0, 0.0, 0.0, 1.0, 3.0, 0.0)
    out1 = ra.warp_after_image(img, affine, (20, 20))
    out2 = ra.warp_after_image(img, affine, (20, 20))
    assert out1.shape == (20, 20, 3)
    assert np.array_equal(out1, out2)  # deterministic
    # the bright pixel moved from x=5 to x=8 (row stays 5)
    assert out1[5, 8].sum() > out1[5, 5].sum()


# --------------------------------------------------------------------------- #
# zone_render_service._apply_after_alignment — wiring integration
# --------------------------------------------------------------------------- #


def _make_job(tmp_path, alignment):
    from src.services.comparison.zone_render_service import RenderJob

    return RenderJob(
        pair_uuid="p",
        zone_id="z",
        source_before=tmp_path / "a.dxf",
        source_after=tmp_path / "b.dxf",
        world_window=WorldWindow(0.0, 0.0, 100.0, 100.0),
        cache_root=tmp_path,
        dxf_cache_dir=tmp_path,
        alignment=alignment,
    )


def _write_png(path, np, Image):
    arr = np.zeros((200, 200, 3), dtype=np.uint8)
    arr[100, 40] = (255, 255, 255)
    Image.fromarray(arr).save(path)
    return arr


def test_apply_after_alignment_warps_and_emits_marker_transform(tmp_path, window_tf):
    from src.services.comparison import zone_render_service as zrs

    np = pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")
    after_png = tmp_path / "after.png"
    original = _write_png(after_png, np, Image)
    t = RigidTransform(dx=10.0, dy=0.0, theta_rad=0.0)
    job = _make_job(tmp_path, t.to_dict())
    warnings: list = []

    new_tf, marker = zrs._apply_after_alignment(job, window_tf, window_tf, after_png, warnings)

    assert marker is not None and marker["dx"] == pytest.approx(10.0)  # emits T, not inverse
    assert new_tf["world_to_pixel"] == window_tf["world_to_pixel"]
    assert "after_alignment_applied" in warnings
    with Image.open(after_png) as im:
        warped = np.asarray(im.convert("RGB"))
    assert not np.array_equal(original, warped)  # raster actually changed


def test_apply_after_alignment_noop_without_alignment(tmp_path, window_tf):
    from src.services.comparison import zone_render_service as zrs

    np = pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")
    after_png = tmp_path / "after.png"
    original = _write_png(after_png, np, Image)
    job = _make_job(tmp_path, None)
    warnings: list = []

    new_tf, marker = zrs._apply_after_alignment(job, window_tf, window_tf, after_png, warnings)

    assert marker is None
    assert new_tf is window_tf
    assert warnings == []
    with Image.open(after_png) as im:
        assert np.array_equal(original, np.asarray(im.convert("RGB")))  # untouched


def test_apply_after_alignment_insignificant_is_noop(tmp_path, window_tf):
    from src.services.comparison import zone_render_service as zrs

    tiny = RigidTransform(dx=0.001, dy=0.0, theta_rad=0.0)
    job = _make_job(tmp_path, tiny.to_dict())
    warnings: list = []
    new_tf, marker = zrs._apply_after_alignment(
        job, window_tf, window_tf, tmp_path / "missing.png", warnings
    )
    assert marker is None and new_tf is window_tf and warnings == []


def test_apply_after_alignment_warp_failure_is_honest_fallback(tmp_path, window_tf):
    from src.services.comparison import zone_render_service as zrs

    t = RigidTransform(dx=10.0, dy=0.0, theta_rad=0.0)
    job = _make_job(tmp_path, t.to_dict())
    warnings: list = []
    # after image does not exist -> PIL open fails -> honest fallback, no raise
    new_tf, marker = zrs._apply_after_alignment(
        job, window_tf, window_tf, tmp_path / "does_not_exist.png", warnings
    )
    assert marker is None
    assert new_tf is window_tf
    assert any(w.startswith("after_alignment_warp_failed") for w in warnings)


def test_alignment_changes_cache_key(tmp_path):
    from src.services.comparison import zone_render_service as zrs

    base = _make_job(tmp_path, None)
    aligned = _make_job(
        tmp_path, RigidTransform(dx=10.0, dy=0.0, theta_rad=0.0).to_dict()
    )
    assert zrs.render_cache_key(base) != zrs.render_cache_key(aligned)
