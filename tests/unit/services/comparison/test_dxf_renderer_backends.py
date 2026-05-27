# -*- coding: utf-8 -*-
"""Backend-aware tests for ``DxfRenderer``.

Pins the contracts introduced by the Phase A hot-fix
(docs/collab/REVIEWS.md RV-20260502-001 §3.1):

- PyMuPDF and Matplotlib backends both return a 3-channel uint8 array plus
  a transform dict containing the analytic world→pixel mapping.
- ``transform["backend_used"]`` records which backend actually rendered, so
  ``viewer_perf.json`` can surface fallback events to operators.
- "auto" mode silently falls back to Matplotlib when PyMuPDF raises, with
  ``transform["fallback_reason"]`` populated for diagnostics.
- The ``TEKLA_MCP_DXF_BACKEND`` env-var rolls the entire process back to
  Matplotlib without code changes (operator escape hatch).

The tests render a tiny synthetic DXF (a few primitives) so they finish in
~1s on CI without any external fixtures.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from src.services.comparison import dxf_renderer as renderer_mod
from src.services.comparison.dxf_renderer import (
    DxfRenderer,
    PYMUPDF_AVAILABLE,
    RENDERER_AVAILABLE,
    _resolve_backend_choice,
)


pytestmark = pytest.mark.skipif(
    not RENDERER_AVAILABLE, reason="ezdxf/matplotlib not importable in this environment"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tiny_dxf(tmp_path: Path) -> Path:
    """Synthesize a small but visually non-empty DXF.

    Includes a line, a circle, and a text label so each backend has a chance
    to exercise both stroke and glyph paths. Total file size <2KB.
    """

    import ezdxf

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 100))
    msp.add_circle((50, 50), 25)
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 80), (0, 80), (0, 0)])
    text = msp.add_text("TEST", dxfattribs={"height": 5})
    text.set_placement((10, 10))

    path = tmp_path / "tiny.dxf"
    doc.saveas(str(path))
    return path


# ---------------------------------------------------------------------------
# Backend-choice resolution helper
# ---------------------------------------------------------------------------


def test_resolve_backend_explicit_override(monkeypatch) -> None:
    """Explicit arg always wins; env var is ignored."""

    monkeypatch.setenv("TEKLA_MCP_DXF_BACKEND", "matplotlib")
    assert _resolve_backend_choice("pymupdf") == "pymupdf"
    assert _resolve_backend_choice("matplotlib") == "matplotlib"


def test_resolve_backend_env_var_used_when_auto(monkeypatch) -> None:
    """When user passes 'auto', env var pins the actual backend."""

    monkeypatch.setenv("TEKLA_MCP_DXF_BACKEND", "matplotlib")
    assert _resolve_backend_choice("auto") == "matplotlib"
    monkeypatch.setenv("TEKLA_MCP_DXF_BACKEND", "pymupdf")
    assert _resolve_backend_choice("auto") == "pymupdf"


def test_resolve_backend_default_is_auto(monkeypatch) -> None:
    """No env var, no explicit choice → 'auto' (= primary pymupdf, fallback mpl)."""

    monkeypatch.delenv("TEKLA_MCP_DXF_BACKEND", raising=False)
    assert _resolve_backend_choice("auto") == "auto"
    assert _resolve_backend_choice("") == "auto"
    assert _resolve_backend_choice(None) == "auto"  # type: ignore[arg-type]


def test_resolve_backend_unknown_value_falls_back_to_auto(monkeypatch, caplog) -> None:
    monkeypatch.delenv("TEKLA_MCP_DXF_BACKEND", raising=False)
    with caplog.at_level("WARNING"):
        assert _resolve_backend_choice("svg") == "auto"
    assert any("svg" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Real-render smoke tests — both backends produce valid output
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "backend",
    [
        pytest.param(
            "pymupdf",
            marks=pytest.mark.skipif(
                not PYMUPDF_AVAILABLE, reason="PyMuPDF backend unavailable"
            ),
        ),
        "matplotlib",
    ],
)
def test_renderer_produces_valid_output(tiny_dxf: Path, backend: str) -> None:
    """Each backend must return an RGB uint8 array + a populated transform.

    This is the load-bearing test for the Phase A hot-fix: it pins that
    PyMuPdfBackend output is shape-compatible with the existing pipeline so
    no QML / overlay-positioning regressions sneak in alongside the speedup.
    """

    img, transform = DxfRenderer(backend=backend).render_with_transform(
        tiny_dxf, dpi=72, max_edge_px=512
    )

    # Image shape contract
    assert img.ndim == 3, f"expected (h, w, 3) but got shape={img.shape}"
    assert img.shape[2] == 3, f"expected 3 channels but got {img.shape[2]}"
    assert img.dtype == np.uint8, f"expected uint8 but got {img.dtype}"
    assert max(img.shape[:2]) <= 512, (
        f"max_edge_px=512 not honored: shape={img.shape}"
    )
    assert min(img.shape[:2]) > 0, "rendered image has zero dimension"

    # Drew something — not pure white
    assert not np.all(img == 255), (
        f"backend {backend} produced an all-white image; primitives didn't render"
    )

    # Transform dict contract
    required_keys = {
        "min_x",
        "min_y",
        "max_x",
        "max_y",
        "img_width",
        "img_height",
        "scale_x",
        "scale_y",
        "offset_x",
        "offset_y",
        "backend_used",
        "render_elapsed_ms",
    }
    assert required_keys <= transform.keys(), (
        f"transform missing keys: {required_keys - transform.keys()}"
    )
    assert transform["backend_used"] == backend
    assert transform["img_width"] == img.shape[1]
    assert transform["img_height"] == img.shape[0]
    assert transform["scale_x"] > 0
    assert transform["scale_y"] > 0
    assert transform["render_elapsed_ms"] >= 0


# ---------------------------------------------------------------------------
# Auto-fallback chain
# ---------------------------------------------------------------------------


def test_auto_falls_back_through_chain_when_primaries_raise(
    tiny_dxf: Path, monkeypatch
) -> None:
    """Pin the silent-fallback contract.

    The auto chain is fast → pymupdf → matplotlib. A pathological DXF that
    breaks both fast and pymupdf paths (e.g. unusual entity types, broken
    font) must NOT propagate the error to the user — the renderer should
    keep falling back through the chain and surface the failure via
    ``transform["fallback_reason"]`` for telemetry.
    """

    sentinel_fast = "synthetic fast failure for fallback test"
    def _fast_boom(self, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError(sentinel_fast)

    def _pymupdf_boom(self, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("PyMuPDF must not run in customer-safe auto mode")

    monkeypatch.setattr(DxfRenderer, "_render_fast", _fast_boom)
    monkeypatch.setattr(DxfRenderer, "_render_pymupdf", _pymupdf_boom)

    img, transform = DxfRenderer(backend="auto").render_with_transform(
        tiny_dxf, dpi=72, max_edge_px=512
    )

    assert img.ndim == 3 and img.dtype == np.uint8
    assert transform["backend_used"] == "matplotlib", (
        "expected fall-through to matplotlib after fast failed"
    )
    assert "fallback_reason" in transform
    # The reason should reference whichever backend failed last in the chain
    # before the final success — i.e., pymupdf's sentinel.
    assert sentinel_fast in transform["fallback_reason"]


def test_auto_uses_fast_backend_by_default(tiny_dxf: Path) -> None:
    """The Phase A3 hot-fix made `fast` the auto-chain primary so the GUI
    default path bypasses ezdxf Frontend (which exploded INSERT entities to
    178k virtual entities and 22GB RAM on a real customer DXF). This test
    pins that primary choice."""

    img, transform = DxfRenderer(backend="auto").render_with_transform(
        tiny_dxf, dpi=72, max_edge_px=512
    )
    assert img.ndim == 3 and img.dtype == np.uint8
    assert transform["backend_used"] == "fast", (
        "auto chain must try `fast` first, not pymupdf/matplotlib"
    )


def test_explicit_fast_backend_works_standalone(tiny_dxf: Path) -> None:
    """`backend='fast'` is the explicit, no-fallback variant — useful when
    operators want to confirm the fast path renders their drawing without
    silent fallback masking issues."""

    img, transform = DxfRenderer(backend="fast").render_with_transform(
        tiny_dxf, dpi=72, max_edge_px=512
    )
    assert transform["backend_used"] == "fast"
    assert img.shape[2] == 3 and img.dtype == np.uint8
    assert img.min() < 100  # something drawn


def test_renderer_sanitizes_missing_lwpolyline_subclass_before_dispatch() -> None:
    source = Path("tests/data/comparison/cad_samples/dxf/simple_base.dxf")

    img, transform = DxfRenderer(backend="fast").render_with_transform(
        source, dpi=72, max_edge_px=512
    )

    assert img.ndim == 3 and img.dtype == np.uint8
    assert img.min() < 255
    assert transform["dxf_read_sanitized"] is True
    assert transform["dxf_read_repair_count"] >= 1


def test_fast_renderer_recovers_extents_when_ezdxf_bbox_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """Large DWG->DXF conversions can make ezdxf bbox fail.

    The renderer must not fall back to the old 0..2000 default because that
    clips real geometry and saves an all-white preview image.
    """

    import ezdxf

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_line((100_000, -50_000), (101_000, -49_000))
    msp.add_lwpolyline(
        [(100_000, -50_000), (101_000, -50_000), (101_000, -49_000), (100_000, -50_000)]
    )
    path = tmp_path / "distant.dxf"
    doc.saveas(str(path))

    def _bbox_failure(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("synthetic bbox failure")

    monkeypatch.setattr(renderer_mod.ezdxf_bbox, "extents", _bbox_failure)

    img, transform = DxfRenderer(backend="fast").render_with_transform(
        path, dpi=72, max_edge_px=512
    )

    assert transform["extent_source"] == "simple_entity_fallback"
    assert transform["min_x"] == pytest.approx(100_000)
    assert transform["min_y"] == pytest.approx(-50_000)
    assert transform["max_x"] == pytest.approx(101_000)
    assert transform["max_y"] == pytest.approx(-49_000)
    assert img.ndim == 3 and img.dtype == np.uint8
    assert img.min() < 100, "fallback extents should keep distant geometry visible"


def test_explicit_pymupdf_does_not_fallback(tiny_dxf: Path, monkeypatch) -> None:
    """When the operator pins ``backend='pymupdf'``, failure must surface as
    an exception — not be silently masked by Matplotlib output. This lets
    diagnostic runs (e.g. reproducing a rendering bug) get clean signal.
    """

    if not PYMUPDF_AVAILABLE:
        pytest.skip("PyMuPDF not importable; explicit-mode test cannot run")

    sentinel = "explicit-mode pymupdf failure"

    def _boom(self, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError(sentinel)

    monkeypatch.setattr(DxfRenderer, "_render_pymupdf", _boom)

    with pytest.raises(RuntimeError, match=sentinel):
        DxfRenderer(backend="pymupdf").render_with_transform(
            tiny_dxf, dpi=72, max_edge_px=512
        )


def test_env_var_pins_matplotlib(tiny_dxf: Path, monkeypatch) -> None:
    """Operator escape hatch: setting the env var must take the PyMuPDF path
    out of the picture entirely — no fallback chain involved."""

    monkeypatch.setenv("TEKLA_MCP_DXF_BACKEND", "matplotlib")

    # Spy on PyMuPDF — it must NOT be called when env var pins matplotlib.
    pymupdf_calls: list = []

    def _trap(self, **kwargs):  # type: ignore[no-untyped-def]
        pymupdf_calls.append(kwargs)
        raise AssertionError("PyMuPDF backend should not run when env var pins matplotlib")

    monkeypatch.setattr(DxfRenderer, "_render_pymupdf", _trap)

    img, transform = DxfRenderer(backend="auto").render_with_transform(
        tiny_dxf, dpi=72, max_edge_px=512
    )

    assert pymupdf_calls == []
    assert transform["backend_used"] == "matplotlib"
    assert "fallback_reason" not in transform  # no fallback happened


# ---------------------------------------------------------------------------
# Bug-fix regression: matplotlib path must return a copy of the rgba buffer
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Light-mode entity filter — RV-20260502-001 follow-up
# ---------------------------------------------------------------------------
#
# Pins the contract that prevents matplotlib's INSERT-recursive memory
# explosion (measured 22 GB / 16 min on a real 71 MB customer DXF) and
# PyMuPDF's similar 5 GB+ explosion on the same file. Without this filter
# the comparison viewer ends up with empty PNG paths and the user sees
# only zone overlays floating on a blank canvas — the exact symptom the
# user reported after Phase A landed.


def _build_dxf_with_heavy_entities(tmp_path: Path) -> Path:
    """A DXF that contains BOTH light entities (LINE/CIRCLE) and heavy
    entities (INSERT block reference, HATCH, MTEXT) so we can test the
    skip filter end to end.
    """

    import ezdxf

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()

    # Light geometry that must always appear
    msp.add_line((0, 0), (100, 100))
    msp.add_circle((50, 50), 25)

    # Heavy geometry that the light_mode filter must drop
    block = doc.blocks.new(name="HEAVY_BLOCK")
    block.add_line((0, 0), (10, 10))
    msp.add_blockref("HEAVY_BLOCK", insert=(20, 20))  # INSERT
    msp.add_hatch().paths.add_polyline_path([(60, 0), (100, 0), (100, 40), (60, 40)])  # HATCH
    msp.add_mtext("MULTILINE\nTEXT BLOCK")  # MTEXT

    path = tmp_path / "heavy.dxf"
    doc.saveas(str(path))
    return path


def test_light_mode_renders_simple_entities_skipping_heavy(tmp_path: Path) -> None:
    """light_mode=True must render LINE/CIRCLE while silently dropping
    INSERT/HATCH/MTEXT. We can't pixel-diff the two outputs reliably
    without golden images, so we settle for: both produce non-empty
    output, light_mode finishes, and the rendered image contains visible
    drawing primitives (mean < 250 = not nearly all white)."""

    dxf = _build_dxf_with_heavy_entities(tmp_path)

    img_light, tx_light = DxfRenderer(
        backend="auto", light_mode=True
    ).render_with_transform(dxf, dpi=72, max_edge_px=512)
    assert img_light.ndim == 3 and img_light.dtype == np.uint8
    # The line + circle take up only a small fraction of the canvas, so we
    # don't assert on `mean`. Instead we assert that *some* dark pixels
    # exist — enough to prove geometry was drawn after the heavy entities
    # were filtered out.
    assert img_light.min() < 100, (
        f"light_mode produced no dark pixels — light entities were filtered too; "
        f"min={img_light.min()} mean={img_light.mean():.1f}"
    )

    # Sanity: light_mode doesn't blow up the output dict
    assert tx_light["backend_used"] in ("fast", "pymupdf", "matplotlib")


def test_light_mode_default_is_true(tmp_path: Path) -> None:
    """Default constructor enables light_mode so the GUI default path
    benefits from the filter without explicit opt-in. RV-20260502-001
    rationale: user-facing default has to be the safe path; power-users
    opt out via env var or explicit ``light_mode=False``."""

    renderer = DxfRenderer()
    assert renderer.light_mode is True


def test_light_mode_env_var_can_disable(tmp_path: Path, monkeypatch) -> None:
    """``TEKLA_MCP_DXF_LIGHT_MODE=0`` flips light_mode off without code
    changes — diagnostic / full-fidelity path for support engineers."""

    for value in ("0", "false", "off", "no"):
        monkeypatch.setenv("TEKLA_MCP_DXF_LIGHT_MODE", value)
        renderer = DxfRenderer(light_mode=True)
        assert renderer.light_mode is False, (
            f"env value {value!r} should disable light_mode"
        )


def test_matplotlib_render_returns_independent_buffer(tiny_dxf: Path) -> None:
    """The matplotlib backend used to return a numpy view into the figure
    canvas, which becomes invalid after ``plt.close(fig)``. The Phase A
    refactor explicitly takes a ``.copy()``; this test pins that behavior so
    a future "optimization" to drop the copy doesn't reintroduce stale-buffer
    crashes downstream (PIL sees garbled data, viewer manifest writes fail).
    """

    img, _ = DxfRenderer(backend="matplotlib").render_with_transform(
        tiny_dxf, dpi=72, max_edge_px=256
    )
    # If the buffer were a view into a closed figure, np.ascontiguousarray
    # would either crash or return zeros. We assert non-zero non-white pixels
    # remain accessible after the renderer call returned.
    assert not np.all(img == 255)
    assert img.flags.owndata or img.base is None  # detached from figure
