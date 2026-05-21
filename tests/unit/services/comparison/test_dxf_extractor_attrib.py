# -*- coding: utf-8 -*-
"""RV-20260508-003 regression tests — ATTRIB / ATTDEF extraction.

Pre-fix (commit 73756e27 baseline): ``DxfEntityExtractor`` did not
support ATTRIB / ATTDEF. Block attribute text changes (e.g. user-
reported ``DOWEL BAR (2)SHD13@100 -> ...@200``) were silently dropped
by the extractor and therefore invisible to the comparator.

Post-fix:
  * ``SUPPORTED_TYPES`` includes ``ATTRIB`` and ``ATTDEF``
  * ``NormalizerFactory`` registers ``AttribNormalizer`` /
    ``AttdefNormalizer``
  * ``_process_single_entity`` calls ``_extract_insert_attribs`` after
    every INSERT (regardless of ``expand_blocks``) so visible block
    attributes always surface

These tests exercise the FULL extract path against real DXF files
(round-trip via ``ezdxf.saveas`` → ``readfile``) so any future
regression in the extractor / factory / normaliser interaction is
caught end-to-end.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ezdxf = pytest.importorskip("ezdxf")


def _build_block_with_attribs(doc, block_name: str, dowel_text: str) -> None:
    """Create a block definition with one ATTDEF, then insert at origin
    with one ATTRIB realising it. Mimics user's structural drawing
    (dowel-bar callout block reused at many positions).
    """
    if block_name not in doc.blocks:
        block = doc.blocks.new(name=block_name)
        # Block geometry — a small line so the block has visible content
        block.add_line((0, 0), (10, 0))
        # ATTDEF in block definition (template)
        block.add_attdef(
            tag="DOWEL",
            insert=(0, 0),
            text="DEFAULT",
            dxfattribs={"layer": "TEXT_LAYER"},
        )

    msp = doc.modelspace()
    insert = msp.add_blockref(
        block_name,
        insert=(0, 0),
        dxfattribs={"layer": "BEAM_LAYER"},
    )
    # Realize the ATTDEF as an ATTRIB on this INSERT
    insert.add_attrib(
        tag="DOWEL",
        text=dowel_text,
        insert=(0, 0),
        dxfattribs={"layer": "TEXT_LAYER"},
    )


@pytest.fixture
def attrib_text_change_pair(tmp_path):
    """before/after DXF where the only diff is one ATTRIB text:
    ``DOWEL BAR (2)SHD13@100`` → ``DOWEL BAR (2)SHD13@200``.

    This is the user's exact reported case from RV-20260508-003.
    """
    before = tmp_path / "before.dxf"
    after = tmp_path / "after.dxf"

    def _build(path: Path, dowel_text: str) -> None:
        doc = ezdxf.new("R2010", setup=True)
        # Register layers
        if "BEAM_LAYER" not in doc.layers:
            doc.layers.add(name="BEAM_LAYER", color=7)
        if "TEXT_LAYER" not in doc.layers:
            doc.layers.add(name="TEXT_LAYER", color=2)
        _build_block_with_attribs(doc, "DOWEL_BLOCK", dowel_text)
        doc.saveas(str(path))

    _build(before, "DOWEL BAR (2)SHD13@100")
    _build(after, "DOWEL BAR (2)SHD13@200")
    return before, after


def test_extract_surfaces_attrib_entities(attrib_text_change_pair):
    """Pre-fix this returned 0 ATTRIBs — extractor silently dropped
    them. Post-fix the extractor's per-INSERT hook iterates
    ``insert.attribs`` and normalises each one.
    """
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    before, _ = attrib_text_change_pair
    extractor = DxfEntityExtractor()
    entities = extractor.extract_from_file(before)

    assert "ATTRIB" in entities, "ATTRIB key must exist in result dict"
    assert len(entities["ATTRIB"]) == 1, (
        "expected exactly 1 ATTRIB extracted from one INSERT, got "
        f"{len(entities['ATTRIB'])}"
    )
    attrib = entities["ATTRIB"][0]
    assert attrib.entity_type == "ATTRIB"
    assert attrib.data["tag"] == "DOWEL"
    assert attrib.data["text"] == "DOWEL BAR (2)SHD13@100"
    # parent_block must be set by the extractor hook so reviewers can
    # locate the source block.
    assert attrib.parent_block == "DOWEL_BLOCK"


def test_attrib_text_change_detected_end_to_end(attrib_text_change_pair):
    """End-to-end the user's case: feed before/after into DxfComparator
    and confirm the @100 → @200 change shows up as a MODIFIED ATTRIB
    rather than silently disappearing.
    """
    from src.services.comparison.dxf_comparator import DxfChangeType, DxfComparator
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    before, after = attrib_text_change_pair
    extractor = DxfEntityExtractor()
    eb = extractor.extract_from_file(before)
    ea = extractor.extract_from_file(after)

    # Sanity: identical INSERT (same block + position) so the only
    # signal-bearing diff is the ATTRIB.text.
    assert len(eb["INSERT"]) == 1 and len(ea["INSERT"]) == 1
    assert eb["INSERT"][0].hash == ea["INSERT"][0].hash

    # ATTRIB hash is stable identity (tag+pos); text differences should
    # surface as MODIFIED content, not ADD + DELETE.
    assert len(eb["ATTRIB"]) == 1 and len(ea["ATTRIB"]) == 1
    assert eb["ATTRIB"][0].hash == ea["ATTRIB"][0].hash

    comparator = DxfComparator()
    result = comparator.compare(eb, ea)

    attrib_changes = [
        c for c in result.changes
        if c.entity_type == "ATTRIB" and c.change_type == DxfChangeType.MODIFIED
    ]
    assert len(attrib_changes) == 1
    change = attrib_changes[0]
    assert change.change_category == "content"
    assert (change.old_data or {}).get("text") == "DOWEL BAR (2)SHD13@100"
    assert (change.new_data or {}).get("text") == "DOWEL BAR (2)SHD13@200"
    assert result.modified_count == 1
    assert result.stats["by_type"]["ATTRIB"]["modified"] == 1
    assert result.stats["by_layer"]["TEXT_LAYER"]["modified"] == 1


def test_attdef_in_block_definition_extracted_when_expanded(tmp_path):
    """ATTDEFs live in BLOCK definitions. They show up via the block-
    expansion path (``expand_blocks=True``). Pre-fix they were silently
    dropped because ATTDEF was not in ``SUPPORTED_TYPES``.

    Also covers the rare-but-real case where the BLOCK library was
    edited (different default text) between revisions — comparator
    needs to see the ATTDEF to flag it.
    """
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    path = tmp_path / "with_attdef.dxf"
    doc = ezdxf.new("R2010", setup=True)
    if "TEXT_LAYER" not in doc.layers:
        doc.layers.add(name="TEXT_LAYER", color=2)
    block = doc.blocks.new(name="DOWEL_DEF")
    block.add_line((0, 0), (10, 0))
    block.add_attdef(
        tag="DOWEL",
        insert=(5, 5),
        text="DEFAULT_VALUE",
        dxfattribs={"layer": "TEXT_LAYER"},
    )
    doc.modelspace().add_blockref("DOWEL_DEF", insert=(0, 0))
    doc.saveas(str(path))

    # ``extract_from_file`` is a thin wrapper without expand_blocks
    # toggle, so call ``extract`` directly to exercise the BLOCK
    # definition iteration path (where ATTDEFs live).
    import ezdxf as _ezdxf
    doc_loaded = _ezdxf.readfile(str(path))
    extractor = DxfEntityExtractor()
    entities = extractor.extract(doc_loaded, expand_blocks=True)

    assert "ATTDEF" in entities
    # Adversarial review L3 — 정확한 카운트로 pin (>= 1 은 추후
    # block-expansion 코드가 ATTDEF 를 중복으로 추가해도 통과해버림).
    assert len(entities["ATTDEF"]) == 1, (
        "expected exactly one ATTDEF extracted with expand_blocks=True; "
        f"got {len(entities['ATTDEF'])}"
    )
    attdef = entities["ATTDEF"][0]
    assert attdef.entity_type == "ATTDEF"
    assert attdef.data["tag"] == "DOWEL"
    assert attdef.data["text"] == "DEFAULT_VALUE"


def test_insert_without_attribs_does_not_crash(tmp_path):
    """Defensive: a plain INSERT (no ATTRIBs) must not break the new
    sub-attrib hook. Regression guard against ``insert.attribs``
    returning None / raising on certain entity variants.
    """
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    path = tmp_path / "plain_insert.dxf"
    doc = ezdxf.new("R2010", setup=True)
    block = doc.blocks.new(name="PLAIN")
    block.add_line((0, 0), (10, 10))
    doc.modelspace().add_blockref("PLAIN", insert=(50, 50))
    doc.saveas(str(path))

    extractor = DxfEntityExtractor()
    entities = extractor.extract_from_file(path)

    assert len(entities["INSERT"]) == 1
    assert len(entities["ATTRIB"]) == 0
    # No exception was raised — the test passing means the hook is
    # null-safe.


def test_factory_supports_attrib_and_attdef():
    """Factory-level smoke: ``NormalizerFactory.supported_types()``
    must include the new types so downstream consumers (e.g. the
    comparator) that use it for filtering pick them up.
    """
    from src.services.comparison.entity_normalizers import NormalizerFactory

    types = NormalizerFactory.supported_types()
    assert "ATTRIB" in types
    assert "ATTDEF" in types


# ---------------------------------------------------------------------------
# Codex review P2 follow-ups (RV-20260508-004) — layer filter + layout path
# ---------------------------------------------------------------------------


@pytest.fixture
def insert_with_attrib_on_separate_layer(tmp_path):
    """INSERT on BEAM_LAYER, ATTRIB on TEXT_LAYER. Lets us prove
    that include/exclude filters target the ATTRIB's *own* layer
    rather than its parent INSERT's layer.
    """
    path = tmp_path / "split_layers.dxf"
    doc = ezdxf.new("R2010", setup=True)
    if "BEAM_LAYER" not in doc.layers:
        doc.layers.add(name="BEAM_LAYER", color=7)
    if "TEXT_LAYER" not in doc.layers:
        doc.layers.add(name="TEXT_LAYER", color=2)
    block = doc.blocks.new(name="SPLIT")
    block.add_line((0, 0), (1, 0))
    insert = doc.modelspace().add_blockref(
        "SPLIT", insert=(0, 0), dxfattribs={"layer": "BEAM_LAYER"}
    )
    insert.add_attrib(
        tag="A", text="value", insert=(0, 0),
        dxfattribs={"layer": "TEXT_LAYER"},
    )
    doc.saveas(str(path))
    return path


def test_attrib_layer_filter_include_layers_targets_attrib_layer(
    insert_with_attrib_on_separate_layer,
):
    """RV-20260508-004 P2-1 — when caller asks ``include_layers=
    ['TEXT_LAYER']``, the ATTRIB on TEXT_LAYER must be kept even
    though its parent INSERT is on BEAM_LAYER. Pre-fix: returned 0
    (filter only saw the INSERT layer)."""
    import ezdxf as _ezdxf
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    doc = _ezdxf.readfile(str(insert_with_attrib_on_separate_layer))
    extractor = DxfEntityExtractor(max_entities=1000)
    entities = extractor.extract(doc, include_layers=["TEXT_LAYER"])
    assert len(entities["ATTRIB"]) == 1


def test_attrib_layer_filter_exclude_layers_targets_attrib_layer(
    insert_with_attrib_on_separate_layer,
):
    """RV-20260508-004 P2-1 — when caller asks ``exclude_layers=
    ['TEXT_LAYER']``, the ATTRIB on TEXT_LAYER must be dropped even
    though its parent INSERT is on BEAM_LAYER. Pre-fix: returned 1
    (filter ignored ATTRIB layer)."""
    import ezdxf as _ezdxf
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    doc = _ezdxf.readfile(str(insert_with_attrib_on_separate_layer))
    extractor = DxfEntityExtractor(max_entities=1000)
    entities = extractor.extract(doc, exclude_layers=["TEXT_LAYER"])
    assert len(entities["ATTRIB"]) == 0


def test_extract_layout_surfaces_attrib_entities(tmp_path):
    """RV-20260508-004 P2-2 — paper-space (layout) extraction must
    also iterate ``insert.attribs``. ``DwgDiffer.compare_layouts``
    calls ``extract_layout`` directly, so ATTRIBs would otherwise be
    invisible there even after the modelspace fix.
    """
    import ezdxf as _ezdxf
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    path = tmp_path / "with_layout.dxf"
    doc = ezdxf.new("R2010", setup=True)
    if "BEAM_LAYER" not in doc.layers:
        doc.layers.add(name="BEAM_LAYER", color=7)
    if "TEXT_LAYER" not in doc.layers:
        doc.layers.add(name="TEXT_LAYER", color=2)
    block = doc.blocks.new(name="L_BLOCK")
    block.add_line((0, 0), (1, 0))
    layout = doc.layouts.new("PaperA1")
    insert = layout.add_blockref(
        "L_BLOCK", insert=(0, 0), dxfattribs={"layer": "BEAM_LAYER"}
    )
    insert.add_attrib(
        tag="DOWEL", text="DOWEL @100", insert=(0, 0),
        dxfattribs={"layer": "TEXT_LAYER"},
    )
    doc.saveas(str(path))

    doc_loaded = _ezdxf.readfile(str(path))
    extractor = DxfEntityExtractor(max_entities=1000)
    entities = extractor.extract_layout(doc_loaded, "PaperA1")

    assert len(entities["INSERT"]) == 1
    assert len(entities["ATTRIB"]) == 1
    attrib = entities["ATTRIB"][0]
    assert attrib.data["text"] == "DOWEL @100"
    assert attrib.parent_block == "L_BLOCK"


# ---------------------------------------------------------------------------
# Codex review P2 round 2 (RV-20260508-005)
# ---------------------------------------------------------------------------


def test_extract_insert_attribs_respects_entity_limit(tmp_path):
    """RV-20260508-005 P2-1 — many ATTRIBs in one INSERT must not
    bypass ``max_entities``. Pre-fix the loop appended unconditionally
    so a 10-entity cap could be exceeded by 20+ ATTRIBs from a single
    INSERT, with ``limit_exceeded=False``.
    """
    import ezdxf as _ezdxf
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    path = tmp_path / "many_attribs.dxf"
    doc = ezdxf.new("R2010", setup=True)
    doc.layers.add(name="TEXT_LAYER", color=2)
    block = doc.blocks.new(name="MANY")
    block.add_line((0, 0), (1, 0))
    insert = doc.modelspace().add_blockref("MANY", insert=(0, 0))
    # 25 attributes — would bypass a max_entities=10 cap pre-fix
    for i in range(25):
        insert.add_attrib(
            tag=f"T{i}", text=f"v{i}", insert=(i, 0),
            dxfattribs={"layer": "TEXT_LAYER"},
        )
    doc.saveas(str(path))

    doc_loaded = _ezdxf.readfile(str(path))
    extractor = DxfEntityExtractor(max_entities=10)
    entities = extractor.extract(doc_loaded)

    total = sum(len(v) for v in entities.values())
    assert total <= 10, (
        f"max_entities=10 must cap total extracted; got {total} "
        f"(ATTRIB={len(entities['ATTRIB'])}, INSERT={len(entities['INSERT'])})"
    )
    # The cap-exceeded flag must be set — without it, downstream
    # reporting silently believes the result is complete.
    assert extractor.last_stats.get("limit_exceeded") is True


def test_get_entity_layers_includes_attrib_only_layers(tmp_path):
    """RV-20260508-005 P2-2 — ``get_entity_layers`` must surface the
    layer of any ATTRIB hosted inside an INSERT so the GUI layer
    filter can offer ATTRIB-only layers (e.g. text layer that only
    holds block attributes).
    """
    import ezdxf as _ezdxf
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    path = tmp_path / "attrib_only_layer.dxf"
    doc = ezdxf.new("R2010", setup=True)
    doc.layers.add(name="BEAM_LAYER", color=7)
    doc.layers.add(name="TEXT_LAYER", color=2)
    block = doc.blocks.new(name="X")
    block.add_line((0, 0), (1, 0))
    insert = doc.modelspace().add_blockref(
        "X", insert=(0, 0), dxfattribs={"layer": "BEAM_LAYER"}
    )
    insert.add_attrib(
        tag="X", text="x", insert=(0, 0),
        dxfattribs={"layer": "TEXT_LAYER"},
    )
    doc.saveas(str(path))

    doc_loaded = _ezdxf.readfile(str(path))
    extractor = DxfEntityExtractor()
    layers = extractor.get_entity_layers(doc_loaded)

    assert "BEAM_LAYER" in layers
    # Pre-fix this would FAIL — TEXT_LAYER only existed as the ATTRIB
    # layer, never as a top-level modelspace entity layer.
    assert "TEXT_LAYER" in layers


# ---------------------------------------------------------------------------
# Codex review P2 round 3 (RV-20260508-006) — stats consistency after
# cosmetic suppression
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Adversarial review (RV-20260508-011) — ATTRIB hash 충돌 방지
# ---------------------------------------------------------------------------


def test_attrib_hash_unique_across_multiple_inserts_of_same_block(tmp_path):
    """RV-20260508-011 P1 BLOCKER — 동일 block 의 여러 INSERT 인스턴스
    에서 같은 tag/local position 의 ATTRIB 들은 hash 가 달라야 한다.

    pre-fix: ezdxf 의 ``attrib.dxf.insert`` 가 LOCAL 좌표 (parent INSERT
    기준) 라 두 INSERT 가 modelspace 의 다른 위치에 있어도 ATTRIB hash
    가 동일했음. 사용자 사례 (실 도면에 dowel callout 수십~수백 개)
    에서 모든 ATTRIB 이 같은 hash 슬롯에 모이고 FIFO deque 매칭으로
    변경이 잘못된 callout 에 attribute 됨 → 사용자 목적 무력화.

    post-fix: ``_extract_insert_attribs`` 가 normalize 직후 parent
    INSERT 의 modelspace 좌표를 합쳐 hash 를 재계산.
    """
    import ezdxf as _ezdxf
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    path = tmp_path / "many_callouts.dxf"
    doc = ezdxf.new("R2010", setup=True)
    if "TEXT_LAYER" not in doc.layers:
        doc.layers.add(name="TEXT_LAYER", color=2)
    block = doc.blocks.new(name="DOWEL")
    block.add_line((0, 0), (10, 0))

    # 5개 INSERT, 각 modelspace 다른 위치, 같은 block + 같은 ATTRIB tag
    msp = doc.modelspace()
    insert_positions = [(100.0, 100.0), (200.0, 100.0), (300.0, 100.0),
                        (100.0, 200.0), (100.0, 300.0)]
    for x, y in insert_positions:
        ins = msp.add_blockref("DOWEL", insert=(x, y))
        ins.add_attrib(
            tag="DOWEL", text="DOWEL @100",
            insert=(0, 0),  # LOCAL coord — same for every insert
            dxfattribs={"layer": "TEXT_LAYER"},
        )
    doc.saveas(str(path))

    doc_loaded = _ezdxf.readfile(str(path))
    extractor = DxfEntityExtractor()
    entities = extractor.extract(doc_loaded)

    attribs = entities["ATTRIB"]
    assert len(attribs) == 5
    hashes = {a.hash for a in attribs}
    assert len(hashes) == 5, (
        f"expected 5 distinct ATTRIB hashes (one per parent INSERT); "
        f"got {len(hashes)} unique — collision means changes will be "
        f"attributed to the wrong callout in user's dowel-callout case"
    )
    # location 도 modelspace 좌표 (parent insert point) 여야 함 —
    # spatial index / change zone bbox 가 이 좌표를 사용.
    locations = {a.location for a in attribs}
    assert locations == set(insert_positions)


def test_attrib_text_change_at_one_callout_does_not_pollute_others(tmp_path):
    """RV-20260508-011 P1 BLOCKER end-to-end — 5개의 dowel callout 중
    1개 (#3) 의 text 만 변경. comparator 결과는 정확히 1 MODIFIED ATTRIB
    이어야 하고 변경 위치는 callout #3 의 modelspace 좌표.

    pre-fix: hash 가 모두 같아 FIFO 로 임의 매칭 → modified 1 건은 맞지만
    location 은 callout #1 으로 잘못 표시.
    """
    import ezdxf as _ezdxf
    from src.services.comparison.dxf_comparator import (
        DxfChangeType, DxfComparator,
    )
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    before = tmp_path / "before.dxf"
    after = tmp_path / "after.dxf"

    def _build(path: Path, callout3_text: str):
        doc = ezdxf.new("R2010", setup=True)
        if "TEXT_LAYER" not in doc.layers:
            doc.layers.add(name="TEXT_LAYER", color=2)
        block = doc.blocks.new(name="DOWEL")
        block.add_line((0, 0), (10, 0))
        msp = doc.modelspace()
        positions = [(100.0, 100.0), (200.0, 100.0), (300.0, 100.0),
                     (100.0, 200.0), (100.0, 300.0)]
        for idx, (x, y) in enumerate(positions):
            ins = msp.add_blockref("DOWEL", insert=(x, y))
            text = callout3_text if idx == 2 else "DOWEL @100"
            ins.add_attrib(
                tag="DOWEL", text=text, insert=(0, 0),
                dxfattribs={"layer": "TEXT_LAYER"},
            )
        doc.saveas(str(path))

    _build(before, "DOWEL @100")
    _build(after, "DOWEL @200")  # only callout #3 differs

    eb = DxfEntityExtractor().extract_from_file(before)
    ea = DxfEntityExtractor().extract_from_file(after)
    result = DxfComparator().compare(eb, ea)

    attrib_modified = [
        c for c in result.changes
        if c.entity_type == "ATTRIB" and c.change_type == DxfChangeType.MODIFIED
    ]
    assert len(attrib_modified) == 1, (
        f"expected exactly 1 MODIFIED ATTRIB; got {len(attrib_modified)} — "
        f"hash collision would inflate count or split into ADD+DELETE"
    )
    change = attrib_modified[0]
    assert (change.old_data or {}).get("text") == "DOWEL @100"
    assert (change.new_data or {}).get("text") == "DOWEL @200"
    # CRITICAL — location 이 callout #3 의 modelspace 좌표 (300, 100) 이
    # 어야 함. pre-fix 에서는 (100, 100) 등 다른 callout 위치로 잘못
    # 표시됨.
    assert change.location == (300.0, 100.0), (
        f"expected modelspace coords (300, 100) of callout #3; "
        f"got {change.location} — hash collision attributed change to "
        f"wrong callout"
    )


def test_attrib_location_uses_modelspace_not_parent_only(tmp_path):
    """RV-20260508-012 (Codex P2) — 동일 INSERT 가 여러 ATTRIB 를 각자
    다른 local 좌표로 가지면 각 ATTRIB 의 ``location`` 은 *parent
    insert + transformed local pos* 의 modelspace 좌표여야 함.

    pre-fix (RV-20260508-011 의 1차 시도): location = parent_insert_point
    만 — 같은 INSERT 의 모든 ATTRIB 이 한 점에 누적되어 spatial index /
    change marker 가 정확한 위치 표시 못함.

    post-fix: scale + rotation 적용된 affine transform 으로 modelspace
    좌표 산출.
    """
    import ezdxf as _ezdxf
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    path = tmp_path / "multi_attribs.dxf"
    doc = ezdxf.new("R2010", setup=True)
    if "TEXT_LAYER" not in doc.layers:
        doc.layers.add(name="TEXT_LAYER", color=2)
    block = doc.blocks.new(name="MULTI")
    block.add_line((0, 0), (10, 0))

    msp = doc.modelspace()
    # Parent INSERT at (1000, 1000), no rotation/scale
    insert = msp.add_blockref("MULTI", insert=(1000.0, 1000.0))
    # Three ATTRIBs at different local offsets
    insert.add_attrib(tag="A", text="va", insert=(0.0, 0.0),
                       dxfattribs={"layer": "TEXT_LAYER"})
    insert.add_attrib(tag="B", text="vb", insert=(50.0, 0.0),
                       dxfattribs={"layer": "TEXT_LAYER"})
    insert.add_attrib(tag="C", text="vc", insert=(0.0, 75.0),
                       dxfattribs={"layer": "TEXT_LAYER"})
    doc.saveas(str(path))

    doc_loaded = _ezdxf.readfile(str(path))
    entities = DxfEntityExtractor().extract(doc_loaded)

    attribs = entities["ATTRIB"]
    assert len(attribs) == 3
    # location 이 ATTRIB 별로 다른 modelspace 좌표여야 함 (단순 누적 X)
    locations_by_tag = {a.data["tag"]: a.location for a in attribs}
    assert locations_by_tag["A"] == (1000.0, 1000.0)
    assert locations_by_tag["B"] == (1050.0, 1000.0)
    assert locations_by_tag["C"] == (1000.0, 1075.0)


def test_attrib_location_respects_parent_rotation_and_scale(tmp_path):
    """RV-20260508-012 — parent INSERT 의 rotation 90° + xscale=2 가
    적용되면 local pos (10, 0) 의 ATTRIB 은 modelspace 에서 parent +
    (10*2*cos(90), 10*2*sin(90)) = parent + (0, 20) 위치.
    """
    import ezdxf as _ezdxf
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    path = tmp_path / "rot_attrib.dxf"
    doc = ezdxf.new("R2010", setup=True)
    if "TEXT_LAYER" not in doc.layers:
        doc.layers.add(name="TEXT_LAYER", color=2)
    block = doc.blocks.new(name="ROT")
    block.add_line((0, 0), (1, 0))
    msp = doc.modelspace()
    insert = msp.add_blockref(
        "ROT", insert=(500.0, 500.0),
        dxfattribs={"rotation": 90.0, "xscale": 2.0, "yscale": 2.0},
    )
    insert.add_attrib(tag="T", text="v", insert=(10.0, 0.0),
                       dxfattribs={"layer": "TEXT_LAYER"})
    doc.saveas(str(path))

    doc_loaded = _ezdxf.readfile(str(path))
    entities = DxfEntityExtractor().extract(doc_loaded)
    a = entities["ATTRIB"][0]
    # Expected: (500, 500) + 90° rotation of (10*2, 0) = (500, 500+20) = (500, 520)
    assert a.location == (500.0, 520.0), (
        f"rotation/scale not applied to ATTRIB location; got {a.location}"
    )


def test_stats_by_type_consistent_after_cosmetic_suppression(tmp_path):
    """RV-20260508-006 P2 — when ``suppress_cosmetic_only=True`` is
    active, ``stats['by_type']`` and ``stats['by_layer']`` must stay
    in sync with ``change_counts.modified``. Pre-fix the per-type/per-
    layer counters were computed BEFORE cosmetic suppression so a
    cosmetic-only drawing reported `modified == 0` at the top level
    while `by_type[...]['modified'] == 1`, polluting every downstream
    report.
    """
    from src.services.comparison.comparison_config import (
        ComparisonConfig, SensitivityConfig,
    )
    from src.services.comparison.dxf_comparator import DxfComparator
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    before = tmp_path / "before.dxf"
    after = tmp_path / "after.dxf"

    def _build(p, color):
        doc = ezdxf.new("R2010", setup=True)
        if "BEAM" not in doc.layers:
            doc.layers.add(name="BEAM", color=7)
        doc.modelspace().add_line(
            (0, 0), (100, 0), dxfattribs={"layer": "BEAM", "color": color},
        )
        doc.saveas(str(p))

    _build(before, 7)
    _build(after, 8)  # cosmetic-only diff

    extractor = DxfEntityExtractor()
    eb = extractor.extract_from_file(before)
    ea = extractor.extract_from_file(after)

    cfg = ComparisonConfig(sensitivity=SensitivityConfig(suppress_cosmetic_only=True))
    comparator = DxfComparator(config=cfg)
    result = comparator.compare(eb, ea)

    counts = result.stats.get("change_counts", {})
    by_type = result.stats.get("by_type", {})
    by_layer = result.stats.get("by_layer", {})

    # Top-level says 0 modified after suppression — by_type/by_layer
    # must agree.
    assert counts.get("modified", 0) == 0
    for entry in by_type.values():
        assert entry.get("modified", 0) == 0, by_type
    for entry in by_layer.values():
        assert entry.get("modified", 0) == 0, by_layer
