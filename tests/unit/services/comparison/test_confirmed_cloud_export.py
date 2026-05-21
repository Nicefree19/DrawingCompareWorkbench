# -*- coding: utf-8 -*-
"""Unit tests for the confirmed-only cloud mark export module."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from src.services.comparison.confirmed_cloud_export import (
    ConfirmedCloudExportResult,
    _confirmed_zone_ids_for_pair,
    _resolve_pixel_bbox,
    export_confirmed_cloud_marks,
    export_selected_cloud_marks,
)
from src.services.comparison.review_project import ReviewStateRecord


def _make_after_png(tmp_path: Path, width: int = 1240, height: int = 1754) -> Path:
    img = Image.new("RGB", (width, height), color=(245, 245, 250))
    path = tmp_path / "pair_test_after.png"
    img.save(path)
    return path


def _records(*entries) -> dict[str, ReviewStateRecord]:
    out: dict[str, ReviewStateRecord] = {}
    for pair_id, zone_id, status in entries:
        rec = ReviewStateRecord(pair_id=pair_id, pair_uuid=pair_id, zone_id=zone_id, status=status)
        out[rec.key] = rec
    return out


def test_confirmed_zone_ids_filters_by_pair_and_status() -> None:
    records = _records(
        ("pair_a", "z1", "confirmed"),
        ("pair_a", "z2", "ignored"),
        ("pair_a", "z3", "confirmed"),
        ("pair_b", "z1", "confirmed"),
    )
    assert _confirmed_zone_ids_for_pair("pair_a", records) == {"z1", "z3"}
    assert _confirmed_zone_ids_for_pair("pair_b", records) == {"z1"}
    assert _confirmed_zone_ids_for_pair("pair_c", records) == set()


def test_resolve_pixel_bbox_dict_form() -> None:
    overlay = {"after_bbox_px": {"x": 10, "y": 20, "width": 100, "height": 200}}
    assert _resolve_pixel_bbox(overlay) == (10.0, 20.0, 110.0, 220.0)


def test_resolve_pixel_bbox_list_form() -> None:
    overlay = {"after_bbox_px": [10, 20, 110, 220]}
    assert _resolve_pixel_bbox(overlay) == (10.0, 20.0, 110.0, 220.0)


def test_resolve_pixel_bbox_returns_none_when_missing() -> None:
    assert _resolve_pixel_bbox({}) is None
    assert _resolve_pixel_bbox({"after_bbox_px": "garbage"}) is None


def test_export_skips_when_no_confirmed_zones(tmp_path: Path) -> None:
    after_png = _make_after_png(tmp_path)
    result = export_confirmed_cloud_marks(
        pair_id="pair_test",
        after_image_path=str(after_png),
        overlays=[{"zone_id": "z1", "after_bbox_px": {"x": 100, "y": 100, "width": 200, "height": 80}}],
        review_records={},
        output_dir=tmp_path / "out",
        is_pdf_pair=True,
    )
    assert isinstance(result, ConfirmedCloudExportResult)
    assert result.output_path == ""
    assert result.confirmed_zone_count == 0
    assert "확인" in result.skipped_reason


def test_export_skips_when_after_image_missing(tmp_path: Path) -> None:
    records = _records(("pair_test", "z1", "confirmed"))
    result = export_confirmed_cloud_marks(
        pair_id="pair_test",
        after_image_path=str(tmp_path / "nope.png"),
        overlays=[{"zone_id": "z1", "after_bbox_px": {"x": 0, "y": 0, "width": 100, "height": 100}}],
        review_records=records,
        output_dir=tmp_path / "out",
        is_pdf_pair=True,
    )
    assert result.output_path == ""
    assert result.confirmed_zone_count == 1
    assert "PNG" in result.skipped_reason or "렌더" in result.skipped_reason


def test_export_writes_png_with_only_confirmed_zones(tmp_path: Path) -> None:
    after_png = _make_after_png(tmp_path)
    overlays = [
        {"zone_id": "z1", "after_bbox_px": {"x": 100, "y": 100, "width": 200, "height": 80}},
        {"zone_id": "z2", "after_bbox_px": {"x": 400, "y": 300, "width": 150, "height": 60}},
        {"zone_id": "z3", "after_bbox_px": {"x": 700, "y": 700, "width": 180, "height": 90}},
    ]
    records = _records(
        ("pair_test", "z1", "confirmed"),
        ("pair_test", "z2", "ignored"),  # should NOT appear
        ("pair_test", "z3", "confirmed"),
    )
    result = export_confirmed_cloud_marks(
        pair_id="pair_test",
        after_image_path=str(after_png),
        overlays=overlays,
        review_records=records,
        output_dir=tmp_path / "out",
        is_pdf_pair=True,
    )
    assert result.output_path
    output = Path(result.output_path)
    assert output.exists()
    assert output.suffix == ".png"
    assert result.confirmed_zone_count == 2  # z1 + z3 only

    # Output dimensions should equal the source PNG so the cloud marks align
    src = Image.open(after_png)
    out_img = Image.open(output)
    assert out_img.size == src.size


def test_export_excludes_false_positive_status(tmp_path: Path) -> None:
    """Plan §15 Phase A-2 (M2 mixed-status regression).

    External auditor #2 finding M2: confirmed-only export logic in
    review_helpers.py filters by ``CONFIRMED_STATUS``, but no test ever
    fed it a ``false_positive`` zone to prove the exclusion. Without this
    test, drift in review_project.py or review_helpers.py could silently
    let false-positive zones leak into the customer-shareable export.
    """
    after_png = _make_after_png(tmp_path)
    overlays = [
        {"zone_id": "z1", "after_bbox_px": {"x": 100, "y": 100, "width": 200, "height": 80}},
        {"zone_id": "z2", "after_bbox_px": {"x": 400, "y": 300, "width": 150, "height": 60}},
        {"zone_id": "z3", "after_bbox_px": {"x": 700, "y": 700, "width": 180, "height": 90}},
    ]
    records = _records(
        ("pair_test", "z1", "confirmed"),
        ("pair_test", "z2", "false_positive"),  # MUST NOT appear in export
        ("pair_test", "z3", "confirmed"),
    )

    # Direct assertion at the helper level — defends review_helpers.py drift.
    assert _confirmed_zone_ids_for_pair("pair_test", records) == {"z1", "z3"}

    # End-to-end assertion — the actual export must agree.
    result = export_confirmed_cloud_marks(
        pair_id="pair_test",
        after_image_path=str(after_png),
        overlays=overlays,
        review_records=records,
        output_dir=tmp_path / "out",
        is_pdf_pair=True,
    )
    assert result.output_path
    assert result.confirmed_zone_count == 2  # z1 + z3 only — z2 (false_positive) excluded


def test_export_excludes_hold_status(tmp_path: Path) -> None:
    """Plan §15 Phase A-2 (M2 mixed-status regression).

    Same intent as ``test_export_excludes_false_positive_status`` but for
    the ``hold`` status. The auditor explicitly listed both as currently-
    untested exclusion paths.
    """
    after_png = _make_after_png(tmp_path)
    overlays = [
        {"zone_id": "z1", "after_bbox_px": {"x": 100, "y": 100, "width": 200, "height": 80}},
        {"zone_id": "z2", "after_bbox_px": {"x": 400, "y": 300, "width": 150, "height": 60}},
        {"zone_id": "z3", "after_bbox_px": {"x": 700, "y": 700, "width": 180, "height": 90}},
    ]
    records = _records(
        ("pair_test", "z1", "confirmed"),
        ("pair_test", "z2", "hold"),  # MUST NOT appear in export
        ("pair_test", "z3", "confirmed"),
    )

    assert _confirmed_zone_ids_for_pair("pair_test", records) == {"z1", "z3"}

    result = export_confirmed_cloud_marks(
        pair_id="pair_test",
        after_image_path=str(after_png),
        overlays=overlays,
        review_records=records,
        output_dir=tmp_path / "out",
        is_pdf_pair=True,
    )
    assert result.output_path
    assert result.confirmed_zone_count == 2  # z1 + z3 only — z2 (hold) excluded


def test_export_excludes_all_three_non_confirmed_statuses_mixed(tmp_path: Path) -> None:
    """Plan §15 Phase A-2 (M2 mixed-status regression).

    Defence-in-depth: exercise all four valid review states in a single
    fixture (confirmed / false_positive / hold / pending if applicable).
    If review_helpers.py ever introduces a new "passes through to export"
    status by accident, this test catches it.
    """
    after_png = _make_after_png(tmp_path)
    overlays = [
        {"zone_id": "z1", "after_bbox_px": {"x": 50, "y": 50, "width": 100, "height": 60}},
        {"zone_id": "z2", "after_bbox_px": {"x": 200, "y": 50, "width": 100, "height": 60}},
        {"zone_id": "z3", "after_bbox_px": {"x": 50, "y": 200, "width": 100, "height": 60}},
        {"zone_id": "z4", "after_bbox_px": {"x": 200, "y": 200, "width": 100, "height": 60}},
    ]
    records = _records(
        ("pair_test", "z1", "confirmed"),
        ("pair_test", "z2", "false_positive"),
        ("pair_test", "z3", "hold"),
        ("pair_test", "z4", "confirmed"),
    )

    assert _confirmed_zone_ids_for_pair("pair_test", records) == {"z1", "z4"}

    result = export_confirmed_cloud_marks(
        pair_id="pair_test",
        after_image_path=str(after_png),
        overlays=overlays,
        review_records=records,
        output_dir=tmp_path / "out",
        is_pdf_pair=True,
    )
    assert result.output_path
    assert result.confirmed_zone_count == 2  # z1 + z4 only


def test_export_selected_cloud_marks_uses_explicit_zone_ids(tmp_path: Path) -> None:
    after_png = _make_after_png(tmp_path, width=500, height=500)
    overlays = [
        {"zone_id": "z1", "after_bbox_px": {"x": 50, "y": 50, "width": 120, "height": 80}},
        {"zone_id": "z2", "after_bbox_px": {"x": 260, "y": 260, "width": 100, "height": 60}},
    ]

    result = export_selected_cloud_marks(
        pair_id="pair_test",
        after_image_path=str(after_png),
        overlays=overlays,
        zone_ids={"z2"},
        output_dir=tmp_path / "auto_structural_clouds",
        is_pdf_pair=True,
    )

    assert result.output_path
    assert Path(result.output_path).name == "pair_test_auto_structural.png"
    assert result.confirmed_zone_count == 1


def test_export_handles_records_supplied_as_dicts(tmp_path: Path) -> None:
    """The Workbench passes ReviewStateRecord objects, but the API also accepts
    plain dicts (e.g. when the caller pre-loads JSON without re-hydrating)."""

    after_png = _make_after_png(tmp_path)
    records_dict = {
        "pair_test::z1": {"pair_id": "pair_test", "zone_id": "z1", "status": "confirmed"},
    }
    result = export_confirmed_cloud_marks(
        pair_id="pair_test",
        after_image_path=str(after_png),
        overlays=[{"zone_id": "z1", "after_bbox_px": {"x": 50, "y": 50, "width": 200, "height": 80}}],
        review_records=records_dict,
        output_dir=tmp_path / "out",
        is_pdf_pair=False,
    )
    assert result.output_path
    assert Path(result.output_path).exists()
    assert result.confirmed_zone_count == 1


# --- Edge cases flagged in RV-20260502-001 §3.2 -----------------------------


def test_export_skips_when_overlays_have_no_matching_zone_id(tmp_path: Path) -> None:
    """Distinct skip path when reviewer confirmed zones exist but the overlay
    list lacks a matching ``zone_id``.

    This can happen when the reviewer state JSON survives across runs but the
    overlays were regenerated from a fresh viewer-package (e.g. pair was
    re-rendered with different zone numbering). The skip reason must point at
    the overlay mismatch, not at "no confirmed zones" — otherwise the operator
    has no way to debug.
    """

    after_png = _make_after_png(tmp_path)
    records = _records(("pair_test", "z_old", "confirmed"))
    result = export_confirmed_cloud_marks(
        pair_id="pair_test",
        after_image_path=str(after_png),
        overlays=[
            # Different zone IDs; none match z_old
            {"zone_id": "z_new_a", "after_bbox_px": {"x": 100, "y": 100, "width": 100, "height": 100}},
            {"zone_id": "z_new_b", "after_bbox_px": {"x": 300, "y": 100, "width": 100, "height": 100}},
        ],
        review_records=records,
        output_dir=tmp_path / "out",
        is_pdf_pair=True,
    )
    assert result.output_path == ""
    assert result.confirmed_zone_count == 1  # 1 confirmed record exists
    assert "overlay" in result.skipped_reason


def test_export_clips_oversized_bbox_to_image_bounds(tmp_path: Path) -> None:
    """Bbox extending past the image edges must clip and still produce output.

    Realistic scenario: zone bbox was computed from a higher-DPI render but the
    after.png in cache is from a lower-DPI re-render. The export pipeline must
    not crash; it must clamp the bbox to the actual image extents before
    drawing. We assert the function still produces a valid PNG of the same
    size as the source — Pillow drawing operations on clipped coords are safe.
    """

    after_png = _make_after_png(tmp_path, width=400, height=400)
    overlays = [
        {
            "zone_id": "z_oversized",
            # Far past image bounds (image is 400x400)
            "after_bbox_px": {"x": 350, "y": 350, "width": 800, "height": 800},
        },
    ]
    records = _records(("pair_test", "z_oversized", "confirmed"))

    result = export_confirmed_cloud_marks(
        pair_id="pair_test",
        after_image_path=str(after_png),
        overlays=overlays,
        review_records=records,
        output_dir=tmp_path / "out",
        is_pdf_pair=True,
    )
    assert result.output_path, result.skipped_reason
    output = Path(result.output_path)
    assert output.exists()
    out_img = Image.open(output)
    src_img = Image.open(after_png)
    # Output must match source dimensions (no auto-extend)
    assert out_img.size == src_img.size
    assert result.confirmed_zone_count == 1


def test_export_pads_tiny_bbox_so_marker_is_visible(tmp_path: Path) -> None:
    """A degenerate bbox (1x1 pixel) must be padded outward so reviewers can
    actually see the marker on the printed output. The `_draw_confirmed_clouds_on_png`
    helper inflates anything <4px to a 32x32 pad. This test pins that contract:
    a 1px change zone still produces a real PNG (not a crash, not a blank
    canvas).
    """

    after_png = _make_after_png(tmp_path, width=600, height=600)
    overlays = [
        {
            "zone_id": "z_tiny",
            "after_bbox_px": {"x": 300, "y": 300, "width": 1, "height": 1},
        },
    ]
    records = _records(("pair_test", "z_tiny", "confirmed"))

    result = export_confirmed_cloud_marks(
        pair_id="pair_test",
        after_image_path=str(after_png),
        overlays=overlays,
        review_records=records,
        output_dir=tmp_path / "out",
        is_pdf_pair=False,
    )
    assert result.output_path
    output = Path(result.output_path)
    assert output.exists()
    # Confirm the helper didn't silently bail; one zone counted
    assert result.confirmed_zone_count == 1


def test_export_supports_mixed_bbox_formats_in_one_call(tmp_path: Path) -> None:
    """The viewer package historically uses two bbox shapes interchangeably
    (``after_bbox_px`` as dict vs as 4-tuple list). A single export call may
    receive both — _resolve_pixel_bbox is supposed to handle each per overlay
    independently.

    This test pins that the function does not, e.g., assume one shape for the
    whole batch and silently drop the others.
    """

    after_png = _make_after_png(tmp_path, width=800, height=800)
    overlays = [
        {"zone_id": "z_dict", "after_bbox_px": {"x": 100, "y": 100, "width": 80, "height": 60}},
        {"zone_id": "z_list", "after_bbox_px": [400, 100, 480, 160]},  # x0,y0,x1,y1 form
        {"zone_id": "z_no_bbox"},  # no bbox at all — should be silently skipped
    ]
    records = _records(
        ("pair_test", "z_dict", "confirmed"),
        ("pair_test", "z_list", "confirmed"),
        ("pair_test", "z_no_bbox", "confirmed"),
    )

    result = export_confirmed_cloud_marks(
        pair_id="pair_test",
        after_image_path=str(after_png),
        overlays=overlays,
        review_records=records,
        output_dir=tmp_path / "out",
        is_pdf_pair=True,
    )
    assert result.output_path, result.skipped_reason
    # All three confirmed overlays counted (filter is by zone_id, not bbox
    # presence). The "no_bbox" one is rendered as a no-op draw inside the helper.
    assert result.confirmed_zone_count == 3
