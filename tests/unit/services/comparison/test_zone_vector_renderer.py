# -*- coding: utf-8 -*-
"""Tests for the Phase B1 zone-only SVG vector renderer.

The contract these tests pin: a small ``zone_world_bbox`` produces a
self-contained SVG with FULL fidelity (INSERT/HATCH/MTEXT included)
because the spatial filter cuts entity volume below the explosion
threshold that previously broke ezdxf-based renderers on industrial
71 MB drawings.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.comparison.dwg_importer import DwgJsonFixtureAdapter
from src.services.comparison.zone_vector_renderer import (
    SVG_RENDERER_AVAILABLE,
    ZoneVectorRenderResult,
    render_zone_svg,
    resolve_dxf_path,
    _append_safe_mleader_primitives,
    _is_fragile_vector_entity,
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


def test_renders_dxf_with_missing_lwpolyline_subclass(tmp_path: Path) -> None:
    source = Path("tests/data/comparison/cad_samples/dxf/simple_base.dxf")
    out = tmp_path / "malformed_zone.svg"

    result = render_zone_svg(
        source,
        zone_world_bbox=(0, 0, 120, 140),
        output_svg=out,
        padding_ratio=0.0,
    )

    assert result.svg_path == str(out)
    assert out.exists()
    assert result.entity_count > 0


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


def test_resolve_dwg_path_uses_canonical_debug_export_without_oda(tmp_path: Path) -> None:
    source = tmp_path / "native-free.dwg"
    source.write_bytes(
        b"AC1015"
        + DwgJsonFixtureAdapter.MARKER
        + json.dumps(
            {
                "layers": [{"name": "BEAM", "color": 3}],
                "model_space": [
                    {
                        "type": "LINE",
                        "handle": "L1",
                        "layer": "BEAM",
                        "geometry": {
                            "start": {"x": 0, "y": 0, "z": 0},
                            "end": {"x": 100, "y": 0, "z": 0},
                        },
                    }
                ],
            }
        ).encode("utf-8")
    )

    resolved = resolve_dxf_path(source, cache_dir=tmp_path / "cache")

    assert resolved.suffix.lower() == ".dxf"
    text = resolved.read_text(encoding="utf-8")
    assert "0\nSECTION\n" in text
    assert "0\nLINE\n" in text


def test_resolve_dwg_path_falls_back_to_same_stem_cached_dxf(tmp_path: Path) -> None:
    source = tmp_path / "unsupported_detail.dwg"
    source.write_bytes(b"AC1032 unsupported native fixture")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cached = cache_dir / "unsupported_detail.previous.dxf"
    cached.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")

    resolved = resolve_dxf_path(source, cache_dir=cache_dir)

    assert resolved == cached


def test_resolve_dwg_path_prefers_exact_shared_dwg_cache(tmp_path: Path) -> None:
    from src.services.comparison.dwg_differ import DwgDiffer

    source = tmp_path / "shared_detail.dwg"
    source.write_bytes(b"AC1032 shared cache fixture")
    cache_dir = tmp_path / "cache"
    differ = DwgDiffer(
        config={
            "use_canonical_pipeline": False,
            "use_legacy_ezdxf_pipeline": True,
        },
        dxf_cache_dir=cache_dir,
    )
    cached = differ._dxf_cache_path(source)
    cached.parent.mkdir(parents=True)
    cached.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")

    resolved = resolve_dxf_path(source, cache_dir=cache_dir)

    assert resolved == cached


def test_resolve_dwg_path_falls_back_to_legacy_dxf_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "unsupported_detail.dwg"
    source.write_bytes(b"AC1032 unsupported native fixture")
    normal_cache = tmp_path / "cache" / "normalize"
    legacy_cache = tmp_path / "dxf_cache"
    legacy_cache.mkdir()
    cached = legacy_cache / "unsupported_detail.previous.dxf"
    cached.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")
    monkeypatch.setattr(
        "src.services.comparison.cache_paths.workbench_data_root",
        lambda: tmp_path,
    )

    resolved = resolve_dxf_path(source, cache_dir=normal_cache)

    assert resolved == cached


def test_fragile_multileader_entities_are_identified() -> None:
    class Entity:
        def dxftype(self) -> str:
            return "MULTILEADER"

    assert _is_fragile_vector_entity(Entity()) is True


def test_safe_multileader_fallback_creates_visible_primitives() -> None:
    import ezdxf

    class Point:
        def __init__(self, x: float, y: float) -> None:
            self.x = x
            self.y = y

    class LeaderLine:
        vertices = [Point(0, 0)]

    class Leader:
        lines = [LeaderLine()]
        last_leader_point = Point(10, 0)
        dogleg_vector = Point(1, 0)
        dogleg_length = 5

    class MText:
        default_content = "TAG-1"
        insert = Point(20, 0)

    class Context:
        leaders = [Leader()]
        mtext = MText()
        char_height = 2.5

    class Dxf:
        layer = "ANNO"

    class Entity:
        context = Context()
        dxf = Dxf()

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()

    added = _append_safe_mleader_primitives(Entity(), msp)

    assert added >= 2
    assert [entity.dxftype() for entity in msp]


def test_render_zone_svg_returns_failure_when_frontend_crashes(
    quadrant_dxf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.services.comparison import zone_vector_renderer

    def raise_render_error(self, *args, **kwargs) -> None:
        raise RuntimeError("bad cad entity")

    monkeypatch.setattr(
        zone_vector_renderer.Frontend,
        "draw_entities",
        raise_render_error,
    )

    result = render_zone_svg(
        quadrant_dxf,
        zone_world_bbox=(0, 0, 200, 200),
        output_svg=tmp_path / "zone.svg",
    )

    assert result.svg_path == ""
    assert "SVG draw failed" in result.skipped_reason
    assert "bad cad entity" in result.skipped_reason


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


# ---------------------------------------------------------------------------
# S1.3.2 — Silent fallback visibility
# ---------------------------------------------------------------------------


def test_zone_vector_render_result_default_failure_codes_is_empty() -> None:
    """S1.3.2 backward compat: omitting failure_codes yields an empty tuple."""

    result = ZoneVectorRenderResult(
        svg_path="/tmp/test.svg",
        entity_count=5,
        elapsed_ms=10.0,
        world_bbox=(0.0, 0.0, 100.0, 100.0),
    )
    assert result.failure_codes == ()


def test_zone_vector_render_result_to_dict_exposes_failure_codes_as_list() -> None:
    """S1.3.2: serialised payload includes failure_codes as a JSON-friendly list."""

    result = ZoneVectorRenderResult(
        svg_path="",
        entity_count=0,
        elapsed_ms=5.0,
        world_bbox=(0.0, 0.0, 50.0, 50.0),
        failure_codes=("dwg_using_cached_dxf", "vector_draw_partial"),
    )

    payload = result.to_dict()

    assert payload["failure_codes"] == ["dwg_using_cached_dxf", "vector_draw_partial"]


def test_resolve_dxf_path_appends_dwg_vector_normalise_failed_on_fallback(
    tmp_path: Path,
) -> None:
    """S1.3.2 Point 6b: same-stem cache fallback after normalisation failure
    appends ``dwg_vector_normalise_failed`` (warn).

    A ``detail.previous.dxf`` next to an unsupported DWG is only reached
    via the ``_cached_dxf_fallback`` path that runs **after** the live
    canonical import has failed — semantically a degraded reuse, not a
    normal cache hit.
    """

    source = tmp_path / "detail.dwg"
    source.write_bytes(b"AC1032 unsupported native fixture")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cached = cache_dir / "detail.previous.dxf"
    cached.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")

    collected: list = []
    resolved = resolve_dxf_path(source, cache_dir=cache_dir, failure_codes=collected)

    assert resolved == cached
    assert "dwg_vector_normalise_failed" in collected
    # Point 6a code must NOT appear — this is the warn variant, not the
    # info-level normal-cache-reuse path.
    assert "dwg_using_cached_dxf" not in collected


def test_resolve_dxf_path_appends_dwg_using_cached_dxf_on_shared_cache(
    tmp_path: Path,
) -> None:
    """S1.3.2 Point 6a: shared DwgDiffer cache reuse also appends the code."""

    from src.services.comparison.dwg_differ import DwgDiffer

    source = tmp_path / "shared_detail.dwg"
    source.write_bytes(b"AC1032 shared cache fixture")
    cache_dir = tmp_path / "cache"
    differ = DwgDiffer(
        config={
            "use_canonical_pipeline": False,
            "use_legacy_ezdxf_pipeline": True,
        },
        dxf_cache_dir=cache_dir,
    )
    cached = differ._dxf_cache_path(source)
    cached.parent.mkdir(parents=True)
    cached.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")

    collected: list = []
    resolved = resolve_dxf_path(source, cache_dir=cache_dir, failure_codes=collected)

    assert resolved == cached
    assert collected == ["dwg_using_cached_dxf"]


def test_resolve_dxf_path_does_not_append_when_failure_codes_is_none(
    tmp_path: Path,
) -> None:
    """S1.3.2 backward compat: omitting the optional list is a no-op."""

    source = tmp_path / "detail.dwg"
    source.write_bytes(b"AC1032 unsupported native fixture")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cached = cache_dir / "detail.previous.dxf"
    cached.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")

    # Existing callers pass no failure_codes — must keep working.
    resolved = resolve_dxf_path(source, cache_dir=cache_dir)

    assert resolved == cached


def test_render_zone_svg_emits_vector_draw_failed_when_dxf_missing(
    tmp_path: Path,
) -> None:
    """S1.3.2 Point 1: missing source DXF produces vector_draw_failed."""

    result = render_zone_svg(
        dxf_path=tmp_path / "does_not_exist.dxf",
        zone_world_bbox=(0.0, 0.0, 100.0, 100.0),
        output_svg=tmp_path / "out.svg",
    )

    assert result.svg_path == ""
    assert "vector_draw_failed" in result.failure_codes


def test_render_zone_svg_emits_vector_draw_failed_on_frontend_crash(
    quadrant_dxf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S1.3.2 Point 1: SVG draw exception emits vector_draw_failed.

    Complements ``test_render_zone_svg_returns_failure_when_frontend_crashes``
    by asserting the new failure_codes field is populated alongside
    skipped_reason.
    """

    from src.services.comparison import zone_vector_renderer

    def raise_render_error(self, *args, **kwargs) -> None:
        raise RuntimeError("bad cad entity")

    monkeypatch.setattr(
        zone_vector_renderer.Frontend,
        "draw_entities",
        raise_render_error,
    )

    result = render_zone_svg(
        quadrant_dxf,
        zone_world_bbox=(0, 0, 200, 200),
        output_svg=tmp_path / "crash.svg",
    )

    assert result.svg_path == ""
    assert "vector_draw_failed" in result.failure_codes


def test_render_zone_svg_success_has_no_failure_codes_for_dxf_input(
    quadrant_dxf: Path, tmp_path: Path,
) -> None:
    """S1.3.2: a clean DXF render emits no codes (no DWG cache, no failure)."""

    result = render_zone_svg(
        quadrant_dxf,
        zone_world_bbox=(0, 0, 200, 200),
        output_svg=tmp_path / "clean.svg",
    )

    # The rendered SVG should exist and no fallback should have triggered.
    assert result.svg_path != ""
    assert result.failure_codes == ()


def test_resolve_dxf_path_reuses_oda_autoconvert_cache_on_native_failure(
    tmp_path: Path,
) -> None:
    """2026-06-12 — THE recurring "미리보기 뷰어 실패" root: AC1018+ DWG zone
    renders failed native normalisation and never looked at the ODA
    auto-convert cache the comparison pipeline had already produced (now
    OBJECTS-slimmed too). The fallback chain must reuse that artifact so
    zone vectors share the compare's effective drawing."""

    from src.services.comparison.dwg_dxf_fallback import (
        source_cache_stem,
        source_signature_hash,
    )

    source = tmp_path / "detail.dwg"
    source.write_bytes(b"AC1032 unsupported native fixture")
    cache_dir = tmp_path / "cache"
    oda_dir = cache_dir / "oda_auto"
    oda_dir.mkdir(parents=True)
    oda_cached = oda_dir / (
        f"{source_cache_stem(source)}__{source_signature_hash(source)[:16]}.dxf"
    )
    oda_cached.write_text(
        "0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8"
    )

    collected: list = []
    resolved = resolve_dxf_path(source, cache_dir=cache_dir, failure_codes=collected)

    assert resolved == oda_cached
    assert "dwg_vector_normalise_failed" in collected


def test_resolve_dxf_path_prefers_oda_cache_over_stale_same_stem(
    tmp_path: Path,
) -> None:
    """The exact-signature ODA conversion beats the same-stem heuristic."""

    from src.services.comparison.dwg_dxf_fallback import (
        source_cache_stem,
        source_signature_hash,
    )

    source = tmp_path / "detail.dwg"
    source.write_bytes(b"AC1032 unsupported native fixture")
    cache_dir = tmp_path / "cache"
    oda_dir = cache_dir / "oda_auto"
    oda_dir.mkdir(parents=True)
    stale = cache_dir / "detail.previous.dxf"
    stale.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")
    oda_cached = oda_dir / (
        f"{source_cache_stem(source)}__{source_signature_hash(source)[:16]}.dxf"
    )
    oda_cached.write_text(
        "0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8"
    )

    resolved = resolve_dxf_path(source, cache_dir=cache_dir, failure_codes=[])

    assert resolved == oda_cached


def test_resolve_dxf_path_actively_converts_via_oda_when_caches_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live test 2026-06-18 (AC1027 pair): native import fails AND every cache
    READ misses — e.g. the compare resolved the pair via a ``dxf_registered``
    sibling, so the ``oda_auto`` cache was never populated. resolve_dxf_path must
    ACTIVELY convert the DWG via the configured converter rather than raise
    DWG_UNSUPPORTED_VERSION; that hard raise was the "미리보기 실패" the user saw
    while the compare itself had succeeded (all four render paths route here)."""

    source = tmp_path / "detail.dwg"
    source.write_bytes(b"AC1027 unsupported native fixture")
    cache_dir = tmp_path / "cache"  # empty: no oda_auto / shared / legacy hit

    produced = tmp_path / "actively_converted.dxf"
    produced.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")

    calls: dict = {"n": 0}

    def fake_auto_convert(src, cdir, **kwargs):
        calls["n"] += 1
        return produced, True, "oda_converted_slimmed"

    monkeypatch.setattr(
        "src.services.comparison.dwg_dxf_fallback.auto_convert_unsupported_dwg",
        fake_auto_convert,
    )

    collected: list = []
    resolved = resolve_dxf_path(source, cache_dir=cache_dir, failure_codes=collected)

    assert calls["n"] == 1, "active ODA conversion must be attempted on cache miss"
    assert resolved == produced
    assert "dwg_vector_normalise_failed" in collected  # honest: native fell back


def test_resolve_dxf_path_still_raises_when_oda_unavailable_and_caches_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No silent stub: if active conversion cannot produce a DXF (ODA not
    installed / fails), resolve_dxf_path raises the honest error so the caller
    surfaces an accurate failure instead of a blank-but-OK render."""

    source = tmp_path / "detail.dwg"
    source.write_bytes(b"AC1027 unsupported native fixture")

    def fake_no_convert(src, cdir, **kwargs):
        return src, False, "converter_module_unavailable"

    monkeypatch.setattr(
        "src.services.comparison.dwg_dxf_fallback.auto_convert_unsupported_dwg",
        fake_no_convert,
    )

    with pytest.raises(OSError, match="DWG canonical import/export failed"):
        resolve_dxf_path(source, cache_dir=tmp_path / "cache")


def test_patch_text_styles_for_legibility_remaps_only_unrenderable_fonts(
    tmp_path: Path,
) -> None:
    """2026-06-12 live failure: empty / SHX-bigfont / latin-only style
    fonts made Hangul text collapse into overlapping blobs in zone SVGs.
    Those remap to Malgun Gothic; real TTFs with glyph coverage stay."""

    import ezdxf

    from src.services.comparison.zone_vector_renderer import (
        KOREAN_SAFE_FONT,
        patch_text_styles_for_legibility,
    )

    doc = ezdxf.new()
    doc.styles.add("EMPTY", font="")
    shxbig = doc.styles.add("SHXBIG", font="romans.shx")
    shxbig.dxf.bigfont = "whtgtxt.shx"
    doc.styles.add("LATIN", font="arial.ttf")
    doc.styles.add("KOREANTTF", font="malgunbd.ttf")

    patched = patch_text_styles_for_legibility(doc)

    assert patched >= 3
    assert doc.styles.get("EMPTY").dxf.font == KOREAN_SAFE_FONT
    assert doc.styles.get("SHXBIG").dxf.font == KOREAN_SAFE_FONT
    assert doc.styles.get("SHXBIG").dxf.bigfont == ""
    assert doc.styles.get("LATIN").dxf.font == KOREAN_SAFE_FONT
    assert doc.styles.get("KOREANTTF").dxf.font == "malgunbd.ttf"  # untouched


def test_render_zone_svg_does_not_pollute_the_shared_read_cache(
    tmp_path: Path,
) -> None:
    """The legibility remap must run on a PRIVATE document: a later cached
    read of the same file must still see the original style fonts."""

    import ezdxf

    from src.services.comparison.dxf_read import (
        dxf_document_cache_scope,
        read_dxf_document_result,
    )

    src = tmp_path / "textstyle.dxf"
    doc = ezdxf.new()
    ghs = doc.styles.add("GHS", font="romans.shx")
    ghs.dxf.bigfont = "whtgtxt.shx"
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 100))
    msp.add_text("간섭 검토", dxfattribs={"style": "GHS", "height": 5.0}).set_placement((10, 10))
    doc.saveas(src)

    with dxf_document_cache_scope():
        render_zone_svg(src, (0.0, 0.0, 120.0, 120.0), tmp_path / "z.svg")
        cached = read_dxf_document_result(src, ezdxf_module=ezdxf).doc
        assert cached.styles.get("GHS").dxf.font == "romans.shx", (
            "shared cache document must keep the ORIGINAL font"
        )
