# -*- coding: utf-8 -*-
"""Regression test for the 2026-05-08 CRITICAL bug fix in
``DxfEntityExtractor`` — cosmetic fields (color/lineweight/linetype)
must round-trip from DXF source through ``extract_from_file`` to the
``NormalizedEntity`` passed into ``DxfComparator``.

Pre-fix history: ``DxfEntityExtractor._normalize`` (line 789-796)
delegated geometric normalisation to ``NormalizerFactory`` which DID
extract cosmetic fields, but the conversion code that mapped the
factory's ``NormalizedEntity`` to the extractor's local
``NormalizedEntity`` dropped color/lineweight/linetype on the floor.
Phase O3 cosmetic detection was therefore a silent no-op in
production — the unit tests in ``test_dxf_cosmetic_channel.py``
constructed entities directly with cosmetic fields populated, so
they could not detect this gap.

This test file plugs that hole by exercising the FULL extract path
on a synthetic DXF where cosmetic fields are explicitly varied.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ezdxf = pytest.importorskip("ezdxf")


@pytest.fixture
def cosmetic_dxf_pair(tmp_path):
    """Create a tiny before/after DXF pair where:
      * Coordinates are identical (so geometry hash matches)
      * Two LINE entities have differing color (7 vs 8)
      * Layer + entity type are identical
    """
    before = tmp_path / "before.dxf"
    after = tmp_path / "after.dxf"

    def _build(path: Path, lines):
        doc = ezdxf.new("R2010", setup=True)
        msp = doc.modelspace()
        # Register layer if not present
        if "BEAM" not in doc.layers:
            doc.layers.add(name="BEAM", color=7)
        for (x1, y1, x2, y2, color) in lines:
            msp.add_line(
                (x1, y1),
                (x2, y2),
                dxfattribs={"layer": "BEAM", "color": color},
            )
        doc.saveas(str(path))

    _build(before, [(0.0, 0.0, 100.0, 0.0, 7), (0.0, 50.0, 100.0, 50.0, 7)])
    _build(after, [(0.0, 0.0, 100.0, 0.0, 8), (0.0, 50.0, 100.0, 50.0, 8)])
    return before, after


def test_extract_from_file_populates_color_field(cosmetic_dxf_pair):
    """Pre-fix this test would fail because color was always None.

    The fix (entity_normalizers cosmetic → local NormalizedEntity
    pass-through in ``_normalize``) ensures the extractor surfaces the
    color value the comparator needs to detect cosmetic-only changes.
    """
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    before, after = cosmetic_dxf_pair
    extractor = DxfEntityExtractor()
    eb = extractor.extract_from_file(before)
    ea = extractor.extract_from_file(after)

    assert "LINE" in eb and len(eb["LINE"]) == 2
    assert "LINE" in ea and len(ea["LINE"]) == 2

    for entity in eb["LINE"]:
        assert entity.color == 7, f"before color must be 7, got {entity.color!r}"
    for entity in ea["LINE"]:
        assert entity.color == 8, f"after color must be 8, got {entity.color!r}"


def test_dxf_comparator_detects_cosmetic_changes_end_to_end(cosmetic_dxf_pair):
    """End-to-end: extract → compare with default config (cosmetic
    detection on, suppress off) must surface the 2 cosmetic changes
    in result.changes. Pre-fix, this returned 0 changes silently."""
    from dataclasses import replace

    from src.services.comparison.comparison_config import get_default_config
    from src.services.comparison.dxf_comparator import DxfComparator
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    before, after = cosmetic_dxf_pair
    extractor = DxfEntityExtractor()
    eb = extractor.extract_from_file(before)
    ea = extractor.extract_from_file(after)

    base = get_default_config()
    sens = replace(
        base.sensitivity,
        cosmetic_detection_enabled=True,
        suppress_cosmetic_only=False,
    )
    config = replace(base, sensitivity=sens)
    comparator = DxfComparator(config=config)
    result = comparator.compare_with_modified_detection(eb, ea)

    cosmetic_changes = [
        c for c in result.changes
        if getattr(c, "change_category", None) == "cosmetic"
    ]
    assert len(cosmetic_changes) >= 1, (
        "compare_with_modified_detection must surface at least 1 cosmetic "
        "change when both LINE entities differ only in color. Pre-fix "
        "returned 0 because extract_from_file dropped the color field."
    )


def test_suppress_cosmetic_only_actually_suppresses(cosmetic_dxf_pair):
    """When suppress_cosmetic_only=True, the cosmetic changes detected
    by the previous test must vanish from result.changes — proving the
    dialog's suppress toggle has end-to-end effect."""
    from dataclasses import replace

    from src.services.comparison.comparison_config import get_default_config
    from src.services.comparison.dxf_comparator import DxfComparator
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    before, after = cosmetic_dxf_pair
    extractor = DxfEntityExtractor()
    eb = extractor.extract_from_file(before)
    ea = extractor.extract_from_file(after)

    base = get_default_config()
    sens = replace(
        base.sensitivity,
        cosmetic_detection_enabled=True,
        suppress_cosmetic_only=True,
    )
    config = replace(base, sensitivity=sens)
    comparator = DxfComparator(config=config)
    result = comparator.compare_with_modified_detection(eb, ea)

    cosmetic_changes = [
        c for c in result.changes
        if getattr(c, "change_category", None) == "cosmetic"
    ]
    assert len(cosmetic_changes) == 0, (
        "suppress_cosmetic_only=True must filter cosmetic-only changes "
        "from result.changes. Suppression breakage masks the user's "
        "configured noise filter."
    )
