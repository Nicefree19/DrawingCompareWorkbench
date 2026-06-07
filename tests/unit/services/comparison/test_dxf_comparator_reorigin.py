# -*- coding: utf-8 -*-
"""Phase O2b — re-origin precision tests.

A whole-drawing re-origin (revision re-inserted at a far origin) must:
- remove entities that are geometrically IDENTICAL after registration (false changes),
- classify genuine same-position edits as MODIFIED with NATIVE coords (overlay-safe),
- keep genuine add/delete,
- and NOT trigger for sub-threshold shifts (legacy path unchanged).
"""
from __future__ import annotations

from src.services.comparison.dxf_comparator import DxfComparator, DxfChangeType
from src.services.comparison.dxf_entity_extractor import NormalizedEntity

SHIFT = (150000.0, -90000.0)  # > _REORIGIN_TRANSLATION_MM (1000)


def _line(x1, y1, x2, y2, layer="0"):
    start, end = (x1, y1), (x2, y2)
    return NormalizedEntity(
        hash=f"LINE:{tuple(sorted([start, end]))}",
        entity_type="LINE",
        layer=layer,
        data={"start": start, "end": end},
        location=((x1 + x2) / 2, (y1 + y2) / 2),
    )


def _text(x, y, content, layer="TEXT"):
    return NormalizedEntity(
        hash=f"TEXT:{(x, y)}:{content}",
        entity_type="TEXT",
        layer=layer,
        data={"position": (x, y), "content": content},
        location=(x, y),
    )


def _shift_line(ln, dx, dy):
    s = ln.data["start"]
    e = ln.data["end"]
    return _line(s[0] + dx, s[1] + dy, e[0] + dx, e[1] + dy, ln.layer)


def _grid_lines(n=30):
    out = []
    for i in range(n):
        x = 1000.0 + i * 137.0
        y = 2000.0 + (i % 7) * 211.0
        out.append(_line(x, y, x + 100.0, y, layer="GRID"))
    return out


def _by_type(entities):
    d = {}
    for e in entities:
        d.setdefault(e.entity_type, []).append(e)
    return d


def test_reorigin_removes_identical_and_classifies_genuine_modified():
    dx, dy = SHIFT
    # A: 30 identical grid lines + 1 TEXT "OLD" + 1 line that will be deleted.
    # Keep the genuine add/delete/text WITHIN the grid footprint so the coarse
    # bbox-centre estimate (p1/p99) is dominated by the 30 grid lines.
    base = _grid_lines(30)
    a_text = _text(1500.0, 2500.0, "OLD")
    a_deleted = _line(3000.0, 2500.0, 3050.0, 2500.0, layer="GRID")
    entities_a = _by_type(base + [a_text, a_deleted])

    # B = A shifted by SHIFT, EXCEPT:
    #  - TEXT content changed "OLD" -> "NEW" (same position after registration)
    #  - a_deleted has no counterpart (deleted)
    #  - one extra line only in B (added)
    b_lines = [_shift_line(ln, dx, dy) for ln in base]
    b_text = _text(1500.0 + dx, 2500.0 + dy, "NEW")           # genuine content modify
    b_added = _line(3500.0 + dx, 2600.0 + dy, 3550.0 + dx, 2600.0 + dy, layer="GRID")
    entities_b = _by_type(b_lines + [b_text, b_added])

    r = DxfComparator().compare_with_modified_detection(entities_a, entities_b)

    # Re-origin detected and the 30 identical grid lines removed as false changes.
    assert r.metadata.get("reorigin_unchanged_removed") == 30
    assert "alignment_refined" in r.metadata

    by = {}
    for c in r.changes:
        by.setdefault(c.change_type, []).append(c)
    modified = by.get(DxfChangeType.MODIFIED, [])
    added = by.get(DxfChangeType.ADDED, [])
    deleted = by.get(DxfChangeType.DELETED, [])

    # Genuine content change -> exactly one MODIFIED TEXT, NATIVE coords.
    text_mods = [c for c in modified if c.entity_type == "TEXT"]
    assert len(text_mods) == 1
    m = text_mods[0]
    assert m.location == (1500.0 + dx, 2500.0 + dy)      # B-space (native)
    assert m.old_location == (1500.0, 2500.0)            # A-space (native)
    assert "content" in (m.change_category or "")

    # Genuine add/delete survive.
    assert any(c.entity_type == "LINE" for c in added)
    assert any(c.entity_type == "LINE" for c in deleted)
    # The 30 identical lines are NOT reported as changes.
    assert sum(1 for c in r.changes if c.entity_type == "LINE"
               and c.change_type == DxfChangeType.MODIFIED) == 0


def test_reorigin_not_triggered_below_threshold():
    # A 500mm shift is below _REORIGIN_TRANSLATION_MM -> legacy path; the
    # registered removal must NOT run (no reorigin stat).
    base = _grid_lines(30)
    entities_a = _by_type(base)
    entities_b = _by_type([_shift_line(ln, 500.0, 0.0) for ln in base])

    r = DxfComparator().compare_with_modified_detection(entities_a, entities_b)
    assert "reorigin_unchanged_removed" not in r.metadata
    assert "reorigin_unchanged_removed" not in r.stats


def test_reorigin_preserves_structural_submm_shift():
    # A genuine structural sub-mm shift (0.5mm > 0.1mm structural threshold) inside
    # a re-origined drawing must SURVIVE (not be removed as unchanged), while the
    # identical non-structural lines ARE removed.
    dx, dy = SHIFT
    base = _grid_lines(30)                                  # 30 GRID (non-structural)
    beam = _line(7000.0, 7000.0, 7200.0, 7000.0, layer="BEAM")
    entities_a = _by_type(base + [beam])
    b_lines = [_shift_line(ln, dx, dy) for ln in base]
    b_beam = _shift_line(beam, dx + 0.5, dy)               # extra 0.5mm structural shift
    entities_b = _by_type(b_lines + [b_beam])

    r = DxfComparator(
        sensitivity={"position": 1.0, "structural_position": 0.1,
                     "dimension": 1.0, "dimension_rel": 0.1, "rotation": 0.1, "scale": 0.1}
    ).compare_with_modified_detection(entities_a, entities_b)
    # Only the 30 identical GRID lines removed; the 0.5mm BEAM shift is preserved.
    assert r.metadata.get("reorigin_unchanged_removed") == 30
    beam_mods = [
        c for c in r.changes
        if c.entity_type == "LINE" and c.layer == "BEAM"
        and c.change_type == DxfChangeType.MODIFIED
    ]
    assert len(beam_mods) == 1  # the genuine structural shift surfaced as MODIFIED
