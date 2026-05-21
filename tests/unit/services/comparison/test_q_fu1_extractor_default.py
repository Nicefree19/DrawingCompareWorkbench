"""Phase Q-FU-1 (RV-20260510-001) — extractor default expand_blocks=True 통일.

Phase Q3 가 ``ComparisonConfig.expand_blocks=True`` (default) 로 변경
했지만 ``DxfEntityExtractor.extract()`` 자체 default 는 여전히 False.
verify pipeline 의 ``extract_from_file()`` + 기타 no-config caller (예:
간단 분석 도구, 단위 테스트) 가 Q3 효과 미반영 → block-internal change
silent drop. Q8 fixture 11 이 이 한계 직접 expose.

Q-FU-1: ``extract()`` default 를 True 로 변경. backward-compat 위해
explicit ``expand_blocks=False`` 전달은 그대로 동작.
"""
from __future__ import annotations

import ezdxf
import pytest

from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor


@pytest.fixture
def block_with_internal_geometry(tmp_path):
    """INSERT 1개 + block 내부 LINE 1개 (expand_blocks 차이 surface)."""
    doc = ezdxf.new(dxfversion="R2010", setup=True)
    msp = doc.modelspace()
    if "Q_FU1_BLOCK" not in doc.blocks:
        block = doc.blocks.new(name="Q_FU1_BLOCK")
        block.add_line((0, 0), (50, 0))
    msp.add_blockref("Q_FU1_BLOCK", insert=(100, 100))
    path = tmp_path / "block.dxf"
    doc.saveas(str(path))
    return path


class TestNewDefaultExpandsBlocks:
    """Q-FU-1 — extract() no-arg + extract_from_file() 가 expand 활성."""

    def test_extract_no_arg_expands_blocks(self, block_with_internal_geometry):
        """extract(doc) (no expand_blocks arg) 시 block 내부 LINE 이
        result["LINE"] 에 expanded."""
        doc = ezdxf.readfile(str(block_with_internal_geometry))
        extractor = DxfEntityExtractor()
        entities = extractor.extract(doc)  # no expand_blocks arg
        # block 내부 LINE 이 expanded 되어 result["LINE"] 에 1개 이상 존재
        assert len(entities.get("LINE", [])) >= 1, (
            "Q-FU-1: extract() default expand_blocks=True 로 변경되어 "
            "block 내부 LINE 이 result['LINE'] 에 surface"
        )

    def test_extract_from_file_expands_blocks(
        self, block_with_internal_geometry
    ):
        """extract_from_file() 도 새 default 적용."""
        extractor = DxfEntityExtractor()
        entities = extractor.extract_from_file(block_with_internal_geometry)
        assert len(entities.get("LINE", [])) >= 1
        # block_geometry_skipped_count = 0 (Q3 silent drop 카운터 부재)
        assert extractor.last_stats.get("block_geometry_skipped_count", 0) == 0


class TestBackwardCompatExplicitFalse:
    """Q-FU-1 — explicit expand_blocks=False 전달은 기존 동작 유지."""

    def test_explicit_false_skips_block_geometry(
        self, block_with_internal_geometry
    ):
        """expand_blocks=False 명시 시 block 내부 LINE 미수집 + 카운터 누적."""
        doc = ezdxf.readfile(str(block_with_internal_geometry))
        extractor = DxfEntityExtractor()
        entities = extractor.extract(doc, expand_blocks=False)
        # block 내부 LINE 은 수집 안됨 (LINE 0개)
        assert len(entities.get("LINE", [])) == 0
        # block_geometry_skipped_count 카운터에 누적 (1 INSERT)
        assert extractor.last_stats["block_geometry_skipped_count"] == 1


class TestFixture11ActivationE2E:
    """Q-FU-1 — fixture 11 이 active baseline 으로 동작."""

    def test_fixture_11_no_longer_expected_to_fail(self):
        """fixture 11 의 truth.json 에서 expected_to_fail 제거됨."""
        import json
        from pathlib import Path

        truth_path = Path(
            "tests/data/comparison/golden/dxf/11_block_geometry_change/truth.json"
        )
        data = json.loads(truth_path.read_text(encoding="utf-8"))
        assert data.get("expected_to_fail", False) is False, (
            "Q-FU-1: fixture 11 이 active baseline 으로 동작 — "
            "expected_to_fail=False"
        )
