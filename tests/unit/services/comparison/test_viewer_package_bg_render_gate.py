# -*- coding: utf-8 -*-
"""Eager raster-background render feasibility gate (speed, 2026-06-14).

Measured root cause: on real CAD pairs the eager ``package_background_render``
blows the fast-first-review ~30 s budget and is killed mid-after-side, leaving a
useless before-only PNG (the zone-crop path needs BOTH sides). The default
lightweight viewer draws the vector scene-pack skeleton, not this raster, so the
render is wasted blocking wall-clock. The gate skips it only for large pure-CAD
pairs under a short budget; PDF / hybrid-PDF / small-CAD / generous-budget all
keep rendering. The diff result is computed upstream and is unaffected.
"""

from __future__ import annotations

from pathlib import Path

from src.services.comparison import viewer_package as vp


def _sized(path: Path, size_bytes: int) -> Path:
    with open(path, "wb") as fp:
        if size_bytes > 0:
            fp.seek(size_bytes - 1)
            fp.write(b"\0")
    return path


def test_large_cad_pair_under_short_budget_skips_eager_render(tmp_path: Path) -> None:
    a = _sized(tmp_path / "before.dxf", 26 * 1024 * 1024)
    b = _sized(tmp_path / "after.dxf", 26 * 1024 * 1024)
    assert vp._eager_background_render_infeasible(
        a, b, is_pdf_pair=False, is_hybrid_pdf_visual_pair=False,
        render_timeout_seconds=30,
    ) is True


def test_large_cad_pair_with_generous_budget_still_renders(tmp_path: Path) -> None:
    a = _sized(tmp_path / "before.dxf", 26 * 1024 * 1024)
    b = _sized(tmp_path / "after.dxf", 26 * 1024 * 1024)
    assert vp._eager_background_render_infeasible(
        a, b, is_pdf_pair=False, is_hybrid_pdf_visual_pair=False,
        render_timeout_seconds=180,
    ) is False


def test_small_cad_pair_under_short_budget_still_renders(tmp_path: Path) -> None:
    a = _sized(tmp_path / "before.dxf", 2 * 1024 * 1024)
    b = _sized(tmp_path / "after.dxf", 2 * 1024 * 1024)
    assert vp._eager_background_render_infeasible(
        a, b, is_pdf_pair=False, is_hybrid_pdf_visual_pair=False,
        render_timeout_seconds=30,
    ) is False


def test_pdf_pair_is_never_skipped_even_when_large(tmp_path: Path) -> None:
    a = _sized(tmp_path / "before.pdf", 26 * 1024 * 1024)
    b = _sized(tmp_path / "after.pdf", 26 * 1024 * 1024)
    assert vp._eager_background_render_infeasible(
        a, b, is_pdf_pair=True, is_hybrid_pdf_visual_pair=False,
        render_timeout_seconds=30,
    ) is False


def test_hybrid_pdf_visual_pair_is_never_skipped(tmp_path: Path) -> None:
    a = _sized(tmp_path / "before.dxf", 26 * 1024 * 1024)
    b = _sized(tmp_path / "after.dxf", 26 * 1024 * 1024)
    assert vp._eager_background_render_infeasible(
        a, b, is_pdf_pair=False, is_hybrid_pdf_visual_pair=True,
        render_timeout_seconds=30,
    ) is False


def test_unbounded_inline_budget_is_not_skipped(tmp_path: Path) -> None:
    # timeout_seconds <= 0 means "render inline, unbounded" — never gate it.
    a = _sized(tmp_path / "before.dxf", 26 * 1024 * 1024)
    b = _sized(tmp_path / "after.dxf", 26 * 1024 * 1024)
    assert vp._eager_background_render_infeasible(
        a, b, is_pdf_pair=False, is_hybrid_pdf_visual_pair=False,
        render_timeout_seconds=0,
    ) is False
