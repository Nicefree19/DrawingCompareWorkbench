# -*- coding: utf-8 -*-
"""RV-20260508-007 — INSERT block-internal text fingerprint regression
tests (Phase O Commit 2).

Pre-fix, ``InsertNormalizer.normalize`` only fingerprinted block_name +
position + scale + rotation. A block library edit that changed the
text *inside* the block definition (e.g. dowel callout block updated
from "DOWEL @100" to "DOWEL @200" at the library level) was invisible
because every INSERT instance kept the same identity hash.

Post-fix the hash also incorporates a fingerprint of the block
definition's text content (TEXT/MTEXT/ATTDEF). The fingerprint is
cached per (doc, block_name) so N INSERTs of the same block do one
walk total.

Phase Q-FU-1 (RV-20260510-001) note — extractor default 가
``expand_blocks=True`` 로 변경됨. expand_blocks=True 시 INSERT 는
transform-only fingerprint 를 사용하고 block-internal TEXT 는 별도
TEXT entity 로 expanded 됨 (Q3 round-3 도입). 이 파일의 테스트는
**Phase O Commit 2 의 fingerprint 메커니즘** (expand_blocks=False 경로)
을 명시적으로 검증하므로 ``_extract`` helper 가 explicit
``expand_blocks=False`` 를 전달하여 그 코드 경로를 그대로 exercise.
expand_blocks=True 경로의 검증은 별도 ``test_q3_block_geometry_detection.py``
가 담당.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ezdxf = pytest.importorskip("ezdxf")


def _extract(extractor, path):
    """Phase Q-FU-1 helper — explicit expand_blocks=False 로 Phase O
    Commit 2 의 INSERT fingerprint 코드 경로 직접 exercise."""
    doc = ezdxf.readfile(str(path))
    return extractor.extract(doc, expand_blocks=False)


def _build_doc_with_block_text(path: Path, block_text: str) -> None:
    """Create a DXF where ONE block named ``CALLOUT`` has a TEXT
    entity inside its definition with the supplied content. Insert
    that block once at origin so the modelspace has one INSERT.
    """
    doc = ezdxf.new("R2010", setup=True)
    if "TEXT_LAYER" not in doc.layers:
        doc.layers.add(name="TEXT_LAYER", color=2)
    block = doc.blocks.new(name="CALLOUT")
    block.add_line((0, 0), (1, 0))  # geometry anchor
    block.add_text(
        block_text,
        dxfattribs={"insert": (5, 5), "layer": "TEXT_LAYER"},
    )
    doc.modelspace().add_blockref("CALLOUT", insert=(100, 100))
    doc.saveas(str(path))


def test_insert_hash_changes_when_block_text_changes(tmp_path):
    """Different text inside the same block definition must produce
    different INSERT hashes — otherwise downstream comparison cannot
    flag the change."""
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    before = tmp_path / "before.dxf"
    after = tmp_path / "after.dxf"
    _build_doc_with_block_text(before, "DOWEL @100")
    _build_doc_with_block_text(after, "DOWEL @200")

    extractor_a = DxfEntityExtractor()
    extractor_b = DxfEntityExtractor()
    eb = _extract(extractor_a, before)
    ea = _extract(extractor_b, after)

    assert len(eb["INSERT"]) == 1
    assert len(ea["INSERT"]) == 1
    # Hash MUST differ; pre-fix they were identical.
    assert eb["INSERT"][0].hash != ea["INSERT"][0].hash


def test_block_text_fingerprint_stored_in_data(tmp_path):
    """Fingerprint is exposed in ``data["block_text_fingerprint"]`` so
    UIs can show "block library text changed" diagnostics later.
    Empty string means the block has no text entities (still valid).
    """
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    path = tmp_path / "with_text.dxf"
    _build_doc_with_block_text(path, "DOWEL @100")
    entities = _extract(DxfEntityExtractor(), path)

    fp = entities["INSERT"][0].data.get("block_text_fingerprint")
    assert isinstance(fp, str)
    assert len(fp) == 16  # md5 16-hex truncation


def test_block_with_no_text_yields_empty_fingerprint(tmp_path):
    """A block without TEXT/MTEXT/ATTDEF should produce empty
    fingerprint — no false hash diff for pure-geometry blocks."""
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    path = tmp_path / "no_text.dxf"
    doc = ezdxf.new("R2010", setup=True)
    block = doc.blocks.new(name="GEOM_ONLY")
    block.add_line((0, 0), (1, 0))
    block.add_circle((2, 2), radius=1.0)
    doc.modelspace().add_blockref("GEOM_ONLY", insert=(0, 0))
    doc.saveas(str(path))

    entities = _extract(DxfEntityExtractor(), path)
    fp = entities["INSERT"][0].data.get("block_text_fingerprint")
    assert fp == ""


def test_fingerprint_cached_across_multiple_inserts(tmp_path):
    """Many INSERT instances of the same block should walk the block
    definition once. We can't directly observe walk count from
    outside, but the cache key (id(doc), block_name) means the second
    call must return the exact same fingerprint as the first.
    """
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    path = tmp_path / "many_inserts.dxf"
    doc = ezdxf.new("R2010", setup=True)
    block = doc.blocks.new(name="REUSE")
    block.add_text("LIBRARY_TEXT", dxfattribs={"insert": (0, 0)})
    msp = doc.modelspace()
    for i in range(50):
        msp.add_blockref("REUSE", insert=(i * 10.0, 0))
    doc.saveas(str(path))

    entities = _extract(DxfEntityExtractor(), path)
    inserts = entities["INSERT"]
    assert len(inserts) == 50
    fps = {ins.data.get("block_text_fingerprint") for ins in inserts}
    # All 50 must share the SAME fingerprint (same block, same library
    # text). Pre-cache implementation would still work but slower.
    assert len(fps) == 1
    only_fp = fps.pop()
    assert isinstance(only_fp, str) and len(only_fp) == 16


def test_attdef_in_block_definition_contributes_to_fingerprint(tmp_path):
    """ATTDEF default text changes are part of the fingerprint.
    Library editor flips a block's default ATTRIB value; modelspace
    INSERTs without their own ATTRIB must reflect this."""
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    before = tmp_path / "before.dxf"
    after = tmp_path / "after.dxf"

    def _build(p, attdef_text):
        doc = ezdxf.new("R2010", setup=True)
        block = doc.blocks.new(name="ADBLK")
        block.add_line((0, 0), (1, 0))
        block.add_attdef(tag="DOWEL", text=attdef_text, insert=(0, 0))
        doc.modelspace().add_blockref("ADBLK", insert=(0, 0))
        doc.saveas(str(p))

    _build(before, "DEFAULT_OLD")
    _build(after, "DEFAULT_NEW")

    eb = _extract(DxfEntityExtractor(), before)
    ea = _extract(DxfEntityExtractor(), after)
    assert eb["INSERT"][0].hash != ea["INSERT"][0].hash


def test_insert_hash_unchanged_when_only_geometry_in_block_changes(tmp_path):
    """Geometry-only changes inside a block definition should NOT flip
    the INSERT hash — that's the responsibility of expand_blocks=True.
    Otherwise we'd over-trigger on lineweight tweaks etc.
    """
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    before = tmp_path / "before.dxf"
    after = tmp_path / "after.dxf"

    def _build(p, line_endpoint):
        doc = ezdxf.new("R2010", setup=True)
        block = doc.blocks.new(name="GEOM")
        block.add_line((0, 0), line_endpoint)  # geometry differs
        doc.modelspace().add_blockref("GEOM", insert=(0, 0))
        doc.saveas(str(p))

    _build(before, (1.0, 0.0))
    _build(after, (5.0, 5.0))  # different geometry, no text

    eb = _extract(DxfEntityExtractor(), before)
    ea = _extract(DxfEntityExtractor(), after)
    # Both blocks have ZERO text → fingerprint == "" for both → INSERT
    # hashes equal. Pure-geometry block changes are out of scope.
    assert eb["INSERT"][0].hash == ea["INSERT"][0].hash


def test_modelspace_text_outside_block_does_not_pollute_fingerprint(tmp_path):
    """Modelspace TEXT entities outside the block definition must NOT
    affect the block's fingerprint. Only the block's own definition
    contents count.
    """
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    before = tmp_path / "before.dxf"
    after = tmp_path / "after.dxf"

    def _build(p, msp_text):
        doc = ezdxf.new("R2010", setup=True)
        block = doc.blocks.new(name="STABLE")
        block.add_text("STABLE_LIBRARY_TEXT", dxfattribs={"insert": (0, 0)})
        msp = doc.modelspace()
        msp.add_blockref("STABLE", insert=(0, 0))
        # Add a TEXT entity in modelspace that differs between revisions
        msp.add_text(msp_text, dxfattribs={"insert": (100, 100)})
        doc.saveas(str(p))

    _build(before, "outside_text_v1")
    _build(after, "outside_text_v2")

    eb = _extract(DxfEntityExtractor(), before)
    ea = _extract(DxfEntityExtractor(), after)

    # The INSERT hash itself stays equal (block definition unchanged).
    assert eb["INSERT"][0].hash == ea["INSERT"][0].hash
    # The modelspace TEXT change is captured separately (TEXT entity).
    assert len(eb["TEXT"]) == 1 and len(ea["TEXT"]) == 1
    assert eb["TEXT"][0].hash != ea["TEXT"][0].hash


def test_extractor_reuse_does_not_leak_block_fingerprint_across_docs(tmp_path):
    """RV-20260508-008 (Codex P1) — id(doc) cache must not poison
    cross-doc extraction. Using one extractor instance for two docs
    with the same block name but different library text MUST yield
    different INSERT hashes. Pre-fix CPython could reuse the freed
    doc's memory address, returning stale fingerprint and hiding the
    library text change.

    Even on systems where address reuse doesn't happen
    deterministically, the fix (per-extraction cache reset) is the
    contract that guarantees correctness.
    """
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    before = tmp_path / "before.dxf"
    after = tmp_path / "after.dxf"
    _build_doc_with_block_text(before, "DOWEL @100")
    _build_doc_with_block_text(after, "DOWEL @200")

    # Single extractor — the same NormalizerFactory and InsertNormalizer
    # are reused for both extractions. This is exactly the path tools/
    # verify_phase_o_accuracy.py and the production pipeline take.
    extractor = DxfEntityExtractor()
    eb = _extract(extractor, before)
    ea = _extract(extractor, after)

    # Hashes must differ — the library text change must surface.
    assert eb["INSERT"][0].hash != ea["INSERT"][0].hash, (
        "extractor reuse caused stale fingerprint cache; "
        "Codex P1 RV-20260508-008 regression"
    )

    # Fingerprint payloads must also differ — sanity that the cache
    # was actually reset rather than the test passing because the
    # md5 truncation happened to differ on geometry alone.
    fp_before = eb["INSERT"][0].data.get("block_text_fingerprint")
    fp_after = ea["INSERT"][0].data.get("block_text_fingerprint")
    assert fp_before != fp_after


def test_block_text_change_surfaces_as_modified_or_addremove(tmp_path):
    """End-to-end via ``DxfComparator``: a block library text change
    must surface as some kind of change (MODIFIED or ADD+DELETE) in
    the comparison result. Pre-fix it surfaced as nothing.
    """
    from src.services.comparison.dxf_comparator import DxfComparator
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    before = tmp_path / "before.dxf"
    after = tmp_path / "after.dxf"
    _build_doc_with_block_text(before, "DOWEL @100")
    _build_doc_with_block_text(after, "DOWEL @200")

    extractor = DxfEntityExtractor()
    eb = _extract(extractor, before)
    ea = _extract(extractor, after)
    result = DxfComparator().compare(eb, ea)

    insert_changes = [c for c in result.changes if c.entity_type == "INSERT"]
    assert insert_changes, (
        "INSERT change must be visible after block-text fingerprint "
        "fix; pre-fix this list was empty"
    )
