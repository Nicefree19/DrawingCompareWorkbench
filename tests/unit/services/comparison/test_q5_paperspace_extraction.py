"""Phase Q5 (RV-20260509-002) — paperspace 자동 추출 회귀 가드.

사용자 보고: "변경사항 미탐지가 많다." Phase Q1-Q4 는 modelspace 만
가정한 normalizer 동작 보강. 사용자가 paperspace 도면을 사용하면
``DxfEntityExtractor.extract`` 가 modelspace 만 처리하여 paperspace
entity 가 silent drop. Q5 는:

1. ``extract_all_layouts: bool = True`` 인자 추가 (default 활성).
2. ``_process_paperspace_layouts`` helper 가 doc.layouts 를 iterate
   해 modelspace 외 layout 의 entity 를 result 에 merge.
3. False 일 땐 카운터만 (silent drop 가시화).
4. ``last_stats`` 에 paperspace_entities_extracted_count /
   paperspace_entities_skipped_count / paperspace_layouts_processed
   surface.
"""
from __future__ import annotations

import ezdxf
import pytest

from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor
from src.services.comparison.suppression_audit import build_suppression_audit


@pytest.fixture
def doc_with_paperspace(tmp_path):
    """modelspace + paperspace 양쪽에 entity 가 있는 합성 DXF."""
    doc = ezdxf.new(dxfversion="R2010", setup=True)
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 10))  # modelspace LINE 1개

    # paperspace layout 추가 (Layout1 자동 생성 + 사용자 정의 1개)
    layout1 = doc.layouts.get("Layout1")
    layout1.add_circle(center=(50, 50), radius=10)
    layout1.add_text("PAPERSPACE LABEL", dxfattribs={"insert": (60, 60), "height": 2})

    # 추가 paperspace layout
    layout2 = doc.layouts.new("DETAIL_VIEW")
    layout2.add_arc(center=(0, 0), radius=20, start_angle=0, end_angle=90)

    path = tmp_path / "with_paperspace.dxf"
    doc.saveas(str(path))
    return path


class TestExtractAllLayoutsDefault:
    """Q5 — default True 검증."""

    def test_extract_with_default_picks_paperspace_too(self, doc_with_paperspace):
        doc = ezdxf.readfile(str(doc_with_paperspace))
        extractor = DxfEntityExtractor()
        # default extract_all_layouts=True
        entities = extractor.extract(doc)
        # modelspace LINE + paperspace CIRCLE + TEXT + ARC
        assert len(entities.get("LINE", [])) == 1
        assert len(entities.get("CIRCLE", [])) >= 1, (
            "paperspace CIRCLE 가 default 에서 추출되어야 함"
        )
        assert len(entities.get("ARC", [])) >= 1, (
            "DETAIL_VIEW layout 의 ARC 가 추출되어야 함"
        )
        assert len(entities.get("TEXT", [])) >= 1
        # last_stats 에 paperspace 통계 노출
        assert extractor.last_stats["paperspace_entities_extracted_count"] >= 3, (
            "추출된 paperspace entity 수가 카운터에 누적되어야 함"
        )
        assert extractor.last_stats["paperspace_entities_skipped_count"] == 0
        assert "Layout1" in extractor.last_stats["paperspace_layouts_processed"]
        assert "DETAIL_VIEW" in extractor.last_stats["paperspace_layouts_processed"]


class TestExtractAllLayoutsDisabled:
    """Q5 — extract_all_layouts=False 시 paperspace skip + 카운터 유지."""

    def test_extract_with_disabled_skips_paperspace(self, doc_with_paperspace):
        doc = ezdxf.readfile(str(doc_with_paperspace))
        extractor = DxfEntityExtractor()
        entities = extractor.extract(doc, extract_all_layouts=False)
        # modelspace LINE 만 추출
        assert len(entities.get("LINE", [])) == 1
        assert len(entities.get("CIRCLE", [])) == 0, (
            "extract_all_layouts=False 면 paperspace CIRCLE 가 skip"
        )
        assert len(entities.get("ARC", [])) == 0
        # 그러나 카운터로 silent drop 가시화
        assert extractor.last_stats["paperspace_entities_skipped_count"] >= 3, (
            "skipped count 로 surface 되어야 함 (Q5 의 핵심)"
        )
        assert extractor.last_stats["paperspace_entities_extracted_count"] == 0


class TestNoPaperspaceContent:
    """Q5 — paperspace 가 비어 있는 도면 (대다수 케이스)."""

    def test_empty_paperspace_no_extraction_no_skip(self, tmp_path):
        doc = ezdxf.new(dxfversion="R2010")
        doc.modelspace().add_line((0, 0), (5, 5))
        path = tmp_path / "msp_only.dxf"
        doc.saveas(str(path))

        extractor = DxfEntityExtractor()
        extractor.extract_from_file(path)
        # paperspace 비어있으므로 양쪽 카운터 0
        assert extractor.last_stats["paperspace_entities_extracted_count"] == 0
        assert extractor.last_stats["paperspace_entities_skipped_count"] == 0


class TestAuditSurfacesPaperspaceSkip:
    """Q5 — audit dialog 가 skipped paperspace entity 를 entry 로 surface."""

    def test_audit_surfaces_skipped_paperspace(self):
        report = build_suppression_audit(
            extraction_stats_a={
                "paperspace_entities_skipped_count": 12,
                "unsupported_counts": {},
            },
            visible_change_count=10,
        )
        ext_entries = [e for e in report.entries if e.category == "extraction"]
        ps_entries = [e for e in ext_entries if "paperspace" in e.label_ko]
        assert len(ps_entries) == 1
        assert ps_entries[0].count == 12
        assert "extract_all_layouts" in ps_entries[0].fix_hint_ko

    def test_audit_no_entry_when_extracted(self):
        """extract_all_layouts=True 로 정상 추출 시 audit entry 안 만듦
        (extracted_count 는 silent drop 이 아님)."""
        report = build_suppression_audit(
            extraction_stats_a={
                "paperspace_entities_extracted_count": 12,
                "paperspace_entities_skipped_count": 0,
                "unsupported_counts": {},
            },
            visible_change_count=10,
        )
        ps_entries = [e for e in report.entries if "paperspace" in e.label_ko]
        assert len(ps_entries) == 0


class TestCodexRound1Fixes:
    """Phase Q5 Codex round-1 follow-up — 3 finding regression guards."""

    def test_p1_paperspace_entity_namespaced_by_layout(self, tmp_path):
        """[P1] 같은 좌표의 entity 가 다른 layout 에 있으면 hash 가
        달라야 layout 이동/추가/삭제가 detect 됨."""
        doc = ezdxf.new(dxfversion="R2010", setup=True)
        # Layout1 에 LINE 추가
        l1 = doc.layouts.get("Layout1")
        l1.add_line((0, 0), (10, 10))
        # 같은 좌표의 LINE 을 다른 layout 에 추가
        l2 = doc.layouts.new("DETAIL_VIEW")
        l2.add_line((0, 0), (10, 10))
        path = tmp_path / "two_layouts.dxf"
        doc.saveas(str(path))

        ext = DxfEntityExtractor()
        entities = ext.extract_from_file(path)
        lines = entities.get("LINE", [])
        # 두 LINE 모두 추출되어야 함
        assert len(lines) == 2
        # layout namespace 가 hash 에 포함되어 hash 가 달라야 함
        assert lines[0].hash != lines[1].hash, (
            "같은 좌표지만 다른 paperspace layout 에 있는 entity 는 "
            "hash 가 달라야 layout 이동을 detect 가능 — Codex P1 fix"
        )
        # data 에 _paperspace_layout 키가 설정되어 있어야 함
        layout_a = (lines[0].data or {}).get("_paperspace_layout")
        layout_b = (lines[1].data or {}).get("_paperspace_layout")
        assert layout_a in ("Layout1", "DETAIL_VIEW")
        assert layout_b in ("Layout1", "DETAIL_VIEW")
        assert layout_a != layout_b

    def test_p2_paperspace_loop_respects_max_entities_limit(self, tmp_path):
        """[P2] paperspace 루프도 max_entities 한계 도달 시 중단해야 함.
        modelspace 와 동작 일치."""
        doc = ezdxf.new(dxfversion="R2010", setup=True)
        # modelspace 에 LINE 1개
        doc.modelspace().add_line((0, 0), (1, 1))
        # paperspace 에 LINE 100개
        l1 = doc.layouts.get("Layout1")
        for i in range(100):
            l1.add_line((i, 0), (i, 10))
        path = tmp_path / "max_test.dxf"
        doc.saveas(str(path))

        # max_entities=5 → modelspace 1 + paperspace 4 후 limit 도달
        ext = DxfEntityExtractor(max_entities=5)
        entities = ext.extract_from_file(path)
        total = sum(len(v) for v in entities.values())
        assert total <= 5, (
            f"max_entities=5 면 paperspace 루프도 5개 이상 추출 안 함. "
            f"실제: {total}"
        )

    def test_p2_get_entity_layers_includes_paperspace(self, tmp_path):
        """[P2] paperspace-only 레이어가 layer-filter 콤보에 노출되어야
        함. Q5 default-on 으로 paperspace entity 가 비교에 포함되니까."""
        doc = ezdxf.new(dxfversion="R2010", setup=True)
        # modelspace 에 BEAM 레이어 entity
        doc.layers.add("BEAM")
        doc.modelspace().add_line((0, 0), (10, 10), dxfattribs={"layer": "BEAM"})
        # paperspace 에 SHEETBORDER 레이어 entity (paperspace-only)
        doc.layers.add("SHEETBORDER")
        l1 = doc.layouts.get("Layout1")
        l1.add_line((0, 0), (100, 0), dxfattribs={"layer": "SHEETBORDER"})
        path = tmp_path / "ps_layer.dxf"
        doc.saveas(str(path))

        doc2 = ezdxf.readfile(str(path))
        ext = DxfEntityExtractor()
        layers = ext.get_entity_layers(doc2)
        assert "BEAM" in layers
        assert "SHEETBORDER" in layers, (
            "paperspace-only 레이어가 layer 발견에 포함되어야 함 — "
            "Codex P2 fix"
        )


class TestExtractFromFileUsesDefault:
    """``extract_from_file`` wrapper 가 새 default 적용."""

    def test_extract_from_file_picks_paperspace(self, doc_with_paperspace):
        extractor = DxfEntityExtractor()
        entities = extractor.extract_from_file(doc_with_paperspace)
        # extract_from_file → extract(doc) → default extract_all_layouts=True
        assert len(entities.get("CIRCLE", [])) >= 1
        assert extractor.last_stats["paperspace_entities_extracted_count"] >= 1
