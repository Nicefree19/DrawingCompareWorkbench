# -*- coding: utf-8 -*-
"""Tests for the Phase B1 zone-only SVG vector renderer.

The contract these tests pin: a small ``zone_world_bbox`` produces a
self-contained SVG with FULL fidelity (INSERT/HATCH/MTEXT included)
because the spatial filter cuts entity volume below the explosion
threshold that previously broke ezdxf-based renderers on industrial
71 MB drawings.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.services.comparison.zone_vector_renderer import (
    SVG_RENDERER_AVAILABLE,
    ZoneVectorRenderResult,
    render_zone_svg,
    _pad_bbox,
)


pytestmark = pytest.mark.skipif(
    not SVG_RENDERER_AVAILABLE,
    reason="ezdxf SVG backend not importable in this environment",
)


@pytest.fixture()
def quadrant_dxf(tmp_path: Path) -> Path:
    """A DXF with one distinct primitive in each quadrant of a 200×200 area
    so we can test that bbox filtering selects only the requested quadrant.

        upper-left  (0..100, 100..200)   : LINE
        upper-right (100..200, 100..200) : CIRCLE
        lower-left  (0..100, 0..100)     : LWPOLYLINE rectangle
        lower-right (100..200, 0..100)   : MTEXT  ← would be expensive
                                                    in fast-mode, must
                                                    show in vector zoom
    """

    import ezdxf

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()

    # Upper-left quadrant: a line (50, 150) -> (50, 180)
    msp.add_line((50, 150), (50, 180))

    # Upper-right quadrant: a circle centered (150, 150), radius 20
    msp.add_circle((150, 150), 20)

    # Lower-left quadrant: a rectangle polyline
    msp.add_lwpolyline(
        [(20, 20), (80, 20), (80, 80), (20, 80), (20, 20)], close=True
    )

    # Lower-right quadrant: full-fidelity-only entity (MTEXT)
    msp.add_mtext("DETAIL TAG", dxfattribs={"insert": (130, 50), "char_height": 5})

    # Plus a far-outside entity that must be excluded by spatial filter
    msp.add_line((1000, 1000), (1500, 1500))

    path = tmp_path / "quadrant.dxf"
    doc.saveas(str(path))
    return path


def test_renders_self_contained_svg(quadrant_dxf: Path, tmp_path: Path) -> None:
    """Smoke: produces a valid SVG file that survives a basic structural
    check (xml declaration + <svg> root + non-empty body)."""

    out = tmp_path / "zone.svg"
    result = render_zone_svg(
        quadrant_dxf,
        zone_world_bbox=(0, 0, 200, 200),  # whole drawing
        output_svg=out,
    )

    assert isinstance(result, ZoneVectorRenderResult)
    assert result.svg_path == str(out)
    assert out.exists()
    assert out.stat().st_size > 0

    text = out.read_text(encoding="utf-8")
    assert text.startswith("<?xml") or text.lstrip().startswith("<svg")
    assert "<svg" in text
    assert "</svg>" in text


def test_spatial_filter_excludes_far_entities(
    quadrant_dxf: Path, tmp_path: Path
) -> None:
    """The far-outside line at (1000,1000)->(1500,1500) must NOT be drawn
    when the zone bbox is clamped to (0..200). Without spatial filtering
    we'd see the line stretching way beyond the quadrant."""

    out = tmp_path / "zone_no_far.svg"
    result = render_zone_svg(
        quadrant_dxf,
        zone_world_bbox=(0, 0, 200, 200),  # excludes (1000,1500) point
        output_svg=out,
    )

    assert result.svg_path
    # Direct entity-count check: the fixture has 5 top-level entities; only
    # 4 are inside the zone bbox.
    assert result.entity_count == 4, (
        f"expected 4 entities (excluding far line), got {result.entity_count}"
    )


def test_zone_to_one_quadrant_keeps_only_that_quadrant(
    quadrant_dxf: Path, tmp_path: Path
) -> None:
    """When the zone bbox clamps to the upper-right quadrant, only the
    circle at (150,150) is in scope. With ``padding_ratio=0`` the spatial
    filter should accept exactly one entity (others' bboxes don't overlap
    even when padding is included)."""

    out = tmp_path / "zone_ur.svg"
    result = render_zone_svg(
        quadrant_dxf,
        zone_world_bbox=(101, 101, 199, 199),  # tightly upper-right
        output_svg=out,
        padding_ratio=0.0,
    )

    assert result.svg_path
    assert result.entity_count == 1, (
        f"expected 1 entity (the circle), got {result.entity_count}"
    )


def test_full_fidelity_includes_mtext(quadrant_dxf: Path, tmp_path: Path) -> None:
    """MTEXT is in the fast-mode skip list (because typesetting is
    expensive) but must appear in the vector zone output — otherwise
    the reviewer can't read beam tags / dimension values, which is the
    whole point of zone vector inspection."""

    out = tmp_path / "zone_with_mtext.svg"
    result = render_zone_svg(
        quadrant_dxf,
        zone_world_bbox=(101, 1, 199, 99),  # lower-right quadrant
        output_svg=out,
        padding_ratio=0.0,
    )
    assert result.svg_path

    # The MTEXT was placed at (130, 50). One entity should be picked up.
    assert result.entity_count >= 1


def test_truncation_when_exceeding_max_entities(
    quadrant_dxf: Path, tmp_path: Path
) -> None:
    """Setting ``max_entities=2`` on a zone that contains 4 entities must
    surface ``truncated=True`` so the GUI can show a 'partial render'
    indicator. The SVG itself should still produce."""

    out = tmp_path / "zone_truncated.svg"
    result = render_zone_svg(
        quadrant_dxf,
        zone_world_bbox=(0, 0, 200, 200),
        output_svg=out,
        max_entities=2,
    )
    assert result.svg_path
    assert result.truncated is True
    assert result.entity_count <= 2


def test_pads_bbox_outward() -> None:
    """The padding helper expands by the requested ratio on each side."""

    padded = _pad_bbox((10, 20, 110, 120), ratio=0.1)
    # width=100, height=100 → pad_x=pad_y=10
    assert padded == (0.0, 10.0, 120.0, 130.0)


def test_pads_degenerate_bbox_to_unit_square() -> None:
    """A zero-area bbox (a point) gets padded to a 1×1 square so the SVG
    viewBox math doesn't divide by zero."""

    padded = _pad_bbox((50, 50, 50, 50), ratio=0.1)
    width = padded[2] - padded[0]
    height = padded[3] - padded[1]
    assert width == 1.0 and height == 1.0


def test_handles_nonexistent_dxf_gracefully(tmp_path: Path) -> None:
    """A missing DXF must not raise — return a result object with an
    empty svg_path and a populated skipped_reason. Same contract as
    Phase A renderers so the GUI's error display path stays uniform."""

    result = render_zone_svg(
        tmp_path / "does_not_exist.dxf",
        zone_world_bbox=(0, 0, 100, 100),
        output_svg=tmp_path / "out.svg",
    )
    assert result.svg_path == ""
    assert "not found" in result.skipped_reason.lower()


def test_padding_includes_neighbor_entities(quadrant_dxf: Path, tmp_path: Path) -> None:
    """Padding is essential for context — a tightly-cropped zone misses
    nearby reference geometry. Use a moderately-tight bbox + larger
    padding and verify more entities are picked up than with no padding.
    Both calls must succeed without crashing (empty-content render is a
    valid outcome for the no-pad case)."""

    # Bbox that catches the line endpoint at (50, 150), with neighbors at
    # (50, 180) just outside. Padding pulls them in.
    bbox_tight = (45, 145, 55, 155)

    no_pad = render_zone_svg(
        quadrant_dxf,
        zone_world_bbox=bbox_tight,
        output_svg=tmp_path / "tight.svg",
        padding_ratio=0.0,
    )
    with_pad = render_zone_svg(
        quadrant_dxf,
        zone_world_bbox=bbox_tight,
        output_svg=tmp_path / "padded.svg",
        padding_ratio=10.0,  # huge expansion: includes everything
    )

    # Padding should not REDUCE entity count; should typically increase it.
    assert with_pad.entity_count >= no_pad.entity_count
    # With huge padding the result must capture multiple entities.
    assert with_pad.entity_count >= 3
