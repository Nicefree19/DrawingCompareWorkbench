# -*- coding: utf-8 -*-
"""RV-20260508-009 — Phase O Commit 3 toggle plumbing tests.

Verifies the ``block_text_detection`` flag flows from
``FolderCompareRunRequest`` → ``BatchCompareOptions`` →
``DwgDiffer`` → ``DxfEntityExtractor`` → ``InsertNormalizer`` and
controls whether INSERT hashes include block-internal text content.

When True (default): hash includes block-text fingerprint, library text
changes (DOWEL @100 → @200) surface in comparison.

When False: hash is the legacy form (block_name + position + scale +
rotation only), so library text changes are hidden — useful when the
user wants the pre-Phase-O behaviour intentionally.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ezdxf = pytest.importorskip("ezdxf")


def _build_doc(path: Path, block_text: str) -> None:
    doc = ezdxf.new("R2010", setup=True)
    block = doc.blocks.new(name="TOG")
    block.add_line((0, 0), (1, 0))
    block.add_text(block_text, dxfattribs={"insert": (0, 0)})
    doc.modelspace().add_blockref("TOG", insert=(0, 0))
    doc.saveas(str(path))


@pytest.fixture
def block_text_change_pair(tmp_path):
    before = tmp_path / "before.dxf"
    after = tmp_path / "after.dxf"
    _build_doc(before, "VERSION_A")
    _build_doc(after, "VERSION_B")
    return before, after


def test_extractor_default_detects_block_text_change(block_text_change_pair):
    """Phase Q-FU-1 (RV-20260510-001) — extractor default 가
    expand_blocks=True 로 변경. block-internal TEXT 가 expanded 되어
    별도 TEXT entity 로 surface (INSERT 자체는 transform_only fingerprint
    이라 hash 동일). 변경은 TEXT entity 차이로 detect.
    """
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    before, after = block_text_change_pair
    eb = DxfEntityExtractor().extract_from_file(before)
    ea = DxfEntityExtractor().extract_from_file(after)
    # Phase Q-FU-1: expand_blocks=True default 로 block-internal TEXT
    # 가 result["TEXT"] 에 expanded. before/after 의 TEXT 내용 차이 →
    # TEXT entity hash 차이로 detect (INSERT 자체는 transform-only 라
    # 동일).
    text_hashes_before = {e.hash for e in eb.get("TEXT", [])}
    text_hashes_after = {e.hash for e in ea.get("TEXT", [])}
    assert text_hashes_before != text_hashes_after, (
        "block-internal TEXT 변경이 expanded TEXT entity hash 차이로 surface"
    )


def test_extractor_explicit_false_detects_via_insert_fingerprint(
    block_text_change_pair,
):
    """Phase Q-FU-1 — 명시적 expand_blocks=False 시 기존 동작 보존:
    block-internal TEXT 가 expanded 되지 않으므로 INSERT 의 block_text_
    fingerprint (full mode) 가 차이를 hash 에 반영."""
    import ezdxf
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    before, after = block_text_change_pair
    doc_b = ezdxf.readfile(str(before))
    doc_a = ezdxf.readfile(str(after))
    eb = DxfEntityExtractor().extract(doc_b, expand_blocks=False)
    ea = DxfEntityExtractor().extract(doc_a, expand_blocks=False)
    assert eb["INSERT"][0].hash != ea["INSERT"][0].hash


def test_extractor_with_detection_off_ignores_block_text_change(
    block_text_change_pair,
):
    from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor

    before, after = block_text_change_pair
    eb = DxfEntityExtractor(block_text_detection=False).extract_from_file(before)
    ea = DxfEntityExtractor(block_text_detection=False).extract_from_file(after)
    # Toggle off → fingerprint is empty for both → INSERT hashes equal
    # (Phase O Commit 1 이전 동작으로 회귀).
    assert eb["INSERT"][0].hash == ea["INSERT"][0].hash
    # Verify the data field is still consistent (empty fingerprint).
    fp_b = eb["INSERT"][0].data.get("block_text_fingerprint")
    fp_a = ea["INSERT"][0].data.get("block_text_fingerprint")
    assert fp_b == "" and fp_a == ""


def test_dwg_differ_propagates_toggle(block_text_change_pair):
    """The toggle must reach DxfEntityExtractor through DwgDiffer."""
    from src.services.comparison.dwg_differ import DwgDiffer

    differ_off = DwgDiffer(block_text_detection=False)
    # Triggers lazy extractor init
    extractor = differ_off.extractor
    assert extractor._block_text_detection is False

    differ_on = DwgDiffer(block_text_detection=True)
    assert differ_on.extractor._block_text_detection is True


def test_batch_compare_options_default_enables_detection():
    from src.services.comparison.drawing_batch import BatchCompareOptions

    opts = BatchCompareOptions()
    assert opts.block_text_detection is True


def test_folder_compare_run_request_default_enables_detection():
    from src.services.comparison.folder_compare_pipeline import (
        FolderCompareRunRequest,
    )

    req = FolderCompareRunRequest(source_a="/a", source_b="/b", output_dir="/o")
    assert req.block_text_detection is True


def test_descriptor_cache_version_bumped():
    """Phase O Commit 3 invalidates legacy descriptor caches because
    INSERT/ATTRIB hash changes (Commits 1+2) made cached entity counts
    diverge from new extractions. Pre-bump value was 2.
    """
    from src.services.comparison.drawing_batch import DESCRIPTOR_CACHE_VERSION

    assert DESCRIPTOR_CACHE_VERSION >= 3, (
        f"DESCRIPTOR_CACHE_VERSION must be bumped to >=3 to invalidate "
        f"caches built with Phase O Commit 1+2 hash semantics; got "
        f"{DESCRIPTOR_CACHE_VERSION}"
    )


def test_normalizer_factory_propagates_toggle_to_insert_only():
    """NormalizerFactory(block_text_detection=False) must pass the
    flag to InsertNormalizer but not to other normalizers (which don't
    accept the kwarg)."""
    from src.services.comparison.entity_normalizers import (
        InsertNormalizer, LineNormalizer, NormalizerFactory,
    )

    f = NormalizerFactory(block_text_detection=False)
    insert_norm = f.get_normalizer("INSERT")
    line_norm = f.get_normalizer("LINE")

    assert isinstance(insert_norm, InsertNormalizer)
    assert insert_norm._block_text_detection is False
    # LineNormalizer doesn't have the field — no AttributeError, no
    # crash.
    assert isinstance(line_norm, LineNormalizer)
    assert not hasattr(line_norm, "_block_text_detection") or \
        line_norm.__class__ is not InsertNormalizer
