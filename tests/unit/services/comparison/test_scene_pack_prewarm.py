# -*- coding: utf-8 -*-
"""Detached scene-pack prewarmer (2026-06-12 viewer-lightness root cause).

The prewarmer must write into the SAME global cache location the GUI's
lazy ``viewer_session._try_load_cached_pack`` reads, so a pipeline-time
prewarm turns the first pair-select into a cache HIT.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ezdxf = pytest.importorskip("ezdxf")

from src.services.comparison.scene_pack_prewarm import (
    launch_detached_prewarm,
    prewarm_enabled,
    prewarm_scene_packs,
)


def _write_line_dxf(path: Path) -> None:
    doc = ezdxf.new()
    doc.modelspace().add_line((0.0, 0.0), (120.0, 60.0))
    doc.saveas(path)


def test_prewarm_builds_into_the_gui_lazy_cache_location(
    tmp_path: Path, monkeypatch
) -> None:
    import src.services.comparison.viewer_session as vs

    cache_root = tmp_path / "preview_cache"
    monkeypatch.setattr(vs, "preview_cache_dir", lambda: cache_root)

    source = tmp_path / "detail.dxf"
    _write_line_dxf(source)

    built = prewarm_scene_packs([str(source)])
    assert built == 1

    # The GUI lazy path must now be a cache HIT for the same source.
    ref = vs._try_load_cached_pack(source)
    assert ref is not None
    assert ref.overview_lod0_path
    overview = Path(ref.overview_lod0_path)
    assert overview.exists()
    import json

    primitives = json.loads(overview.read_text(encoding="utf-8")).get("primitives")
    assert primitives, "warm cache overview must carry the skeleton"

    # Idempotent: a second prewarm sees the warm cache and does not rebuild.
    assert prewarm_scene_packs([str(source)]) == 1


def test_prewarm_skips_missing_and_non_dxf_sources(tmp_path: Path, monkeypatch) -> None:
    import src.services.comparison.viewer_session as vs

    monkeypatch.setattr(vs, "preview_cache_dir", lambda: tmp_path / "cache")
    assert prewarm_scene_packs([
        str(tmp_path / "absent.dxf"),
        str(tmp_path / "drawing.dwg"),
        "",
    ]) == 0


def test_launch_detached_respects_opt_out_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DRAWING_COMPARE_PREWARM_SCENE_PACKS", "0")
    assert prewarm_enabled() is False
    source = tmp_path / "a.dxf"
    _write_line_dxf(source)
    assert launch_detached_prewarm([str(source)]) is None


def test_launch_detached_skips_frozen_builds(monkeypatch, tmp_path: Path) -> None:
    import sys

    monkeypatch.delenv("DRAWING_COMPARE_PREWARM_SCENE_PACKS", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    source = tmp_path / "a.dxf"
    _write_line_dxf(source)
    assert launch_detached_prewarm([str(source)]) is None


def test_launch_detached_skips_when_no_usable_sources(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DRAWING_COMPARE_PREWARM_SCENE_PACKS", raising=False)
    assert launch_detached_prewarm([str(tmp_path / "missing.dxf"), ""]) is None
