"""Phase Q3 (RV-20260509-002) — block geometry detection regression guard.

사용자 보고: "변경사항 미탐지가 많다." Q1 + Q2 가 entity normalizer
와 audit 진단을 추가했지만, INSERT 의 block 정의 내부 geometry 변경
(예: dowel callout block 의 LINE 가 길이 변경) 은 default ``expand_blocks
=False`` 경로에서 silent drop 되었음.

Q3 의 두 가지 fix:
1. ``ComparisonConfig.expand_blocks`` default 를 ``False → True`` 로 flip.
2. expand_blocks=False 경로에서 INSERT 처리 시 ``block_geometry_skipped_count``
   카운터 누적 → audit dialog 가 surface 가능.
"""
from __future__ import annotations

import ezdxf
import pytest

from src.services.comparison.comparison_config import ComparisonConfig
from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor
from src.services.comparison.suppression_audit import build_suppression_audit


class TestExpandBlocksDefault:
    """Q3 — default flip 검증."""

    def test_comparison_config_default_is_true(self):
        cfg = ComparisonConfig()
        assert cfg.expand_blocks is True, (
            "Phase Q3 expand_blocks default must be True so block "
            "geometry changes are detected by default."
        )

    def test_from_dict_default_is_true(self):
        # Caller가 expand_blocks 키를 전달하지 않으면 True 가 default.
        cfg = ComparisonConfig.from_dict({})
        assert cfg.expand_blocks is True

    def test_from_dict_explicit_false_preserved(self):
        # 명시적 disable 은 그대로 유지 — backward-compat.
        cfg = ComparisonConfig.from_dict({"expand_blocks": False})
        assert cfg.expand_blocks is False


class TestGuiDefaultsAlignedWithBackend:
    """Phase Q3 Codex follow-up [P2] regression guard:
    GUI checkbox / settings default 가 backend default 와 동기화."""

    def test_comparison_settings_default_is_true(self):
        from src.gui.unified_load_module.utils.comparison_settings import (
            ComparisonSettingsManager,
        )
        assert ComparisonSettingsManager.DEFAULT_SETTINGS["expand_blocks"] is True


class TestSettingsSchemaMigration:
    """Phase Q3 Codex round-2 [P2] regression guard:
    legacy stored expand_blocks=False 가 schema v2 migration 으로 reset."""

    def test_legacy_settings_migrated_to_v2(self, tmp_path):
        import json
        from src.gui.unified_load_module.utils.comparison_settings import (
            ComparisonSettingsManager,
        )

        # Pre-Q3 saved settings: schema_version 미명시 = v1 + expand_blocks=False
        config_dir = tmp_path / ".tekla_mcp"
        config_dir.mkdir()
        config_file = config_dir / "comparison_settings.json"
        config_file.write_text(
            json.dumps({
                "auto_align": True,
                "expand_blocks": False,  # legacy default 였던 값
                "page": 0,
            }),
            encoding="utf-8",
        )

        mgr = ComparisonSettingsManager(config_dir=config_dir)
        # migration 후 새 default (True) 로 reset
        assert mgr.get("expand_blocks") is True, (
            "legacy stored expand_blocks=False 가 schema v2 로 migration 시 "
            "True 로 reset 되어야 함"
        )
        assert mgr.get("schema_version") == 2

    def test_v2_settings_preserved(self, tmp_path):
        """이미 v2 인 사용자 설정은 그대로 유지 (user override 보호)."""
        import json
        from src.gui.unified_load_module.utils.comparison_settings import (
            ComparisonSettingsManager,
        )

        config_dir = tmp_path / ".tekla_mcp"
        config_dir.mkdir()
        config_file = config_dir / "comparison_settings.json"
        config_file.write_text(
            json.dumps({
                "schema_version": 2,
                "expand_blocks": False,  # explicit v2 user override
            }),
            encoding="utf-8",
        )

        mgr = ComparisonSettingsManager(config_dir=config_dir)
        # v2 이상이면 migration skip → user override 보존
        assert mgr.get("expand_blocks") is False, (
            "v2 user override 는 migration 으로 reset 되면 안 됨"
        )


class TestExpandBlockTextNotDoubleCounted:
    """Phase Q3 Codex round-2 [P2] regression guard:
    expand_blocks=True 시 block 정의 내 TEXT 변경이 parent INSERT hash 와
    expanded TEXT child 양쪽에서 double-count 되지 않도록 parent 가
    transform-only mode 로 normalize 되어야 함."""

    def test_parent_insert_uses_transform_only_in_expand_path(self, tmp_path):
        # Before: block 안에 TEXT "OLD" + LINE
        before = ezdxf.new(dxfversion="R2010")
        b1 = before.blocks.new(name="LABELED_BLOCK")
        b1.add_text("OLD", dxfattribs={"insert": (5, 5), "height": 2.0})
        b1.add_line((0, 0), (10, 0))
        before.modelspace().add_blockref("LABELED_BLOCK", insert=(50, 50))
        before_path = tmp_path / "before.dxf"
        before.saveas(str(before_path))

        # After: block 정의의 TEXT 만 "NEW" 로 변경 (transform 동일)
        after = ezdxf.new(dxfversion="R2010")
        b2 = after.blocks.new(name="LABELED_BLOCK")
        b2.add_text("NEW", dxfattribs={"insert": (5, 5), "height": 2.0})
        b2.add_line((0, 0), (10, 0))
        after.modelspace().add_blockref("LABELED_BLOCK", insert=(50, 50))
        after_path = tmp_path / "after.dxf"
        after.saveas(str(after_path))

        ext_a = DxfEntityExtractor()
        entities_a = ext_a.extract(ezdxf.readfile(str(before_path)),
                                    expand_blocks=True)
        ext_b = DxfEntityExtractor()
        entities_b = ext_b.extract(ezdxf.readfile(str(after_path)),
                                    expand_blocks=True)

        # Parent INSERT 양쪽 모두 추출되어야 함 (P1 fix 보존)
        assert len(entities_a.get("INSERT", [])) == 1
        assert len(entities_b.get("INSERT", [])) == 1

        # CRITICAL: transform 동일하므로 parent INSERT hash 도 동일해야 함.
        # 만약 block_text_fingerprint 가 hash 에 포함되면 INSERT hash 가
        # 달라져 (a) parent INSERT 변경 + (b) expanded TEXT 변경 = double
        # count. transform_only mode 가 적용되면 parent hash 동일 → child
        # TEXT 변경만 single count.
        a_insert_hash = entities_a["INSERT"][0].hash
        b_insert_hash = entities_b["INSERT"][0].hash
        assert a_insert_hash == b_insert_hash, (
            f"expand_blocks=True 에서 parent INSERT 는 transform-only "
            f"hash 여야 함 (text 변경은 expanded child 가 단독 surface). "
            f"hash mismatch: {a_insert_hash[:8]} vs {b_insert_hash[:8]}"
        )

        # Expanded TEXT child 는 양쪽이 다른 content 라서 hash 다름 → 변경
        # detect 는 child level 에서 single count.
        a_texts = entities_a.get("TEXT", [])
        b_texts = entities_b.get("TEXT", [])
        # 적어도 1개씩은 있어야 함 (block 펼친 결과)
        assert len(a_texts) >= 1 and len(b_texts) >= 1
        # TEXT content 다름 → hash 다름
        a_text_hashes = {t.hash for t in a_texts}
        b_text_hashes = {t.hash for t in b_texts}
        assert a_text_hashes != b_text_hashes, (
            "expanded TEXT child 가 'OLD' vs 'NEW' content 차이로 hash "
            "달라야 single count 변경 detection 가능"
        )

    def test_block_recursion_depth_zero_uses_full_mode(self, tmp_path):
        """Phase Q3 Codex round-3 [P2] regression guard:
        ``expand_blocks=True`` + ``block_recursion_depth=0`` 케이스에서는
        children 이 emit 되지 않으므로 parent INSERT 가 full fingerprint
        mode 로 normalize 되어야 함. transform_only mode 면 block-internal
        TEXT/ATTDEF 변경이 silent drop."""
        # Before: block 안에 TEXT "OLD"
        before = ezdxf.new(dxfversion="R2010")
        b1 = before.blocks.new(name="LABEL")
        b1.add_text("OLD", dxfattribs={"insert": (5, 5), "height": 2.0})
        before.modelspace().add_blockref("LABEL", insert=(50, 50))
        before_path = tmp_path / "before.dxf"
        before.saveas(str(before_path))

        # After: TEXT "NEW" (transform 동일)
        after = ezdxf.new(dxfversion="R2010")
        b2 = after.blocks.new(name="LABEL")
        b2.add_text("NEW", dxfattribs={"insert": (5, 5), "height": 2.0})
        after.modelspace().add_blockref("LABEL", insert=(50, 50))
        after_path = tmp_path / "after.dxf"
        after.saveas(str(after_path))

        # expand_blocks=True + block_recursion_depth=0 → no children emitted.
        # Parent INSERT 가 full mode 여야 block_text_fp 차이로 변경 detect.
        ext_a = DxfEntityExtractor()
        entities_a = ext_a.extract(
            ezdxf.readfile(str(before_path)),
            expand_blocks=True, block_recursion_depth=0,
        )
        ext_b = DxfEntityExtractor()
        entities_b = ext_b.extract(
            ezdxf.readfile(str(after_path)),
            expand_blocks=True, block_recursion_depth=0,
        )

        # children 미emit (depth=0) — TEXT 가 result["TEXT"] 에 없음
        assert len(entities_a.get("TEXT", [])) == 0
        assert len(entities_b.get("TEXT", [])) == 0
        # 그러나 parent INSERT 의 hash 는 block_text_fingerprint 포함 (full
        # mode) 으로 OLD → NEW 변경 surface
        a_hash = entities_a["INSERT"][0].hash
        b_hash = entities_b["INSERT"][0].hash
        assert a_hash != b_hash, (
            "block_recursion_depth=0 케이스에서 parent INSERT 가 full "
            "mode 여야 block-internal text 변경 detect (Codex round-3 P2)"
        )

    def test_transform_change_still_detected_in_transform_only_mode(self, tmp_path):
        """transform_only mode 가 transform 변경 detection 을 죽이지 않는지
        regression guard."""
        before = ezdxf.new(dxfversion="R2010")
        b1 = before.blocks.new(name="ARROW")
        b1.add_line((0, 0), (10, 0))
        before.modelspace().add_blockref(
            "ARROW", insert=(50, 50),
            dxfattribs={"xscale": 1.0},
        )
        before_path = tmp_path / "before.dxf"
        before.saveas(str(before_path))

        after = ezdxf.new(dxfversion="R2010")
        b2 = after.blocks.new(name="ARROW")
        b2.add_line((0, 0), (10, 0))
        after.modelspace().add_blockref(
            "ARROW", insert=(50, 50),
            dxfattribs={"xscale": 2.0},
        )
        after_path = tmp_path / "after.dxf"
        after.saveas(str(after_path))

        ext_a = DxfEntityExtractor()
        entities_a = ext_a.extract(ezdxf.readfile(str(before_path)),
                                    expand_blocks=True)
        ext_b = DxfEntityExtractor()
        entities_b = ext_b.extract(ezdxf.readfile(str(after_path)),
                                    expand_blocks=True)

        a_hash = entities_a["INSERT"][0].hash
        b_hash = entities_b["INSERT"][0].hash
        # xscale 변경이 transform_only mode 에서도 detect 되어야 함
        assert a_hash != b_hash


class TestBlockGeometrySkippedCounter:
    """Q3 — expand_blocks=False 경로에서 INSERT 처리 시 silent drop 카운터."""

    def _make_doc_with_block_insert(self, tmp_path):
        doc = ezdxf.new(dxfversion="R2010")
        # 블록 정의: 안에 LINE 두 개
        block = doc.blocks.new(name="MY_BLOCK")
        block.add_line((0, 0), (10, 0))
        block.add_line((0, 0), (0, 10))
        # modelspace 에 INSERT 두 개
        m = doc.modelspace()
        m.add_blockref("MY_BLOCK", insert=(50, 50))
        m.add_blockref("MY_BLOCK", insert=(100, 100))
        path = tmp_path / "with_inserts.dxf"
        doc.saveas(str(path))
        return path

    def test_counter_zero_when_no_inserts(self, tmp_path):
        doc = ezdxf.new(dxfversion="R2010")
        m = doc.modelspace()
        m.add_line((0, 0), (10, 10))
        path = tmp_path / "no_insert.dxf"
        doc.saveas(str(path))

        extractor = DxfEntityExtractor()
        extractor.extract_from_file(path)
        assert extractor.last_stats.get("block_geometry_skipped_count", 0) == 0

    def test_counter_increments_when_expand_blocks_false(self, tmp_path):
        path = self._make_doc_with_block_insert(tmp_path)

        # Phase Q-FU-1 (RV-20260510-001) — extractor default 가
        # expand_blocks=True 로 변경됨. False 동작 테스트는 명시 전달.
        import ezdxf
        doc = ezdxf.readfile(str(path))
        extractor = DxfEntityExtractor()
        extractor.extract(doc, expand_blocks=False)
        assert extractor.last_stats["block_geometry_skipped_count"] == 2, (
            "expand_blocks=False 경로에서 모든 INSERT 마다 카운터가 "
            "누적되어야 함."
        )

    def test_counter_zero_when_expand_blocks_true(self, tmp_path):
        path = self._make_doc_with_block_insert(tmp_path)

        import ezdxf
        doc = ezdxf.readfile(str(path))
        extractor = DxfEntityExtractor()
        extractor.extract(doc, expand_blocks=True)
        assert extractor.last_stats.get("block_geometry_skipped_count", 0) == 0, (
            "expand_blocks=True 경로에서는 block geometry 가 펼쳐져 "
            "수집되므로 silent drop 없음 → 카운터 0."
        )


class TestAuditSurfacesBlockGeometrySkip:
    """Q3 — audit report 가 block_geometry_skipped_count 를 entry 로 surface."""

    def test_audit_surfaces_block_geometry_skipped(self):
        report = build_suppression_audit(
            extraction_stats_a={
                "block_geometry_skipped_count": 3,
                "unsupported_counts": {},
            },
            visible_change_count=10,
        )
        ext_entries = [e for e in report.entries if e.category == "extraction"]
        # 6 normalizer (Q1) + Q3 block geometry → at least 1 entry
        block_entries = [
            e for e in ext_entries if "block geometry" in e.label_ko
        ]
        assert len(block_entries) == 1
        assert block_entries[0].count == 3
        assert "expand_blocks" in block_entries[0].fix_hint_ko

    def test_audit_no_entry_when_zero(self):
        report = build_suppression_audit(
            extraction_stats_a={
                "block_geometry_skipped_count": 0,
                "unsupported_counts": {},
            },
            visible_change_count=10,
        )
        block_entries = [
            e for e in report.entries if "block geometry" in e.label_ko
        ]
        assert len(block_entries) == 0


class TestEndToEndBlockGeometryChange:
    """Q3 — end-to-end: block 정의 내부 LINE 길이 변경이 expand_blocks=True
    에서 detect 되는지 (default 경로)."""

    def test_block_internal_line_change_detected_with_expand_true(self, tmp_path):
        # Before: block 안에 LINE (0,0)→(10,0)
        before = ezdxf.new(dxfversion="R2010")
        b1 = before.blocks.new(name="DOWEL")
        b1.add_line((0, 0), (10, 0))
        before.modelspace().add_blockref("DOWEL", insert=(50, 50))
        before_path = tmp_path / "before.dxf"
        before.saveas(str(before_path))

        # After: block 안에 LINE (0,0)→(20,0) — length 변경
        after = ezdxf.new(dxfversion="R2010")
        b2 = after.blocks.new(name="DOWEL")
        b2.add_line((0, 0), (20, 0))
        after.modelspace().add_blockref("DOWEL", insert=(50, 50))
        after_path = tmp_path / "after.dxf"
        after.saveas(str(after_path))

        # extract with expand_blocks=True — geometry 가 펼쳐져 비교 가능
        extractor_a = DxfEntityExtractor()
        before_doc = ezdxf.readfile(str(before_path))
        entities_a = extractor_a.extract(before_doc, expand_blocks=True)

        extractor_b = DxfEntityExtractor()
        after_doc = ezdxf.readfile(str(after_path))
        entities_b = extractor_b.extract(after_doc, expand_blocks=True)

        # LINE 종류가 양쪽 모두 1개씩 추출되어야 함 (block 펼친 결과)
        assert len(entities_a.get("LINE", [])) >= 1
        assert len(entities_b.get("LINE", [])) >= 1
        # geometry 다르므로 hash 도 달라야 함
        a_hashes = {e.hash for e in entities_a.get("LINE", [])}
        b_hashes = {e.hash for e in entities_b.get("LINE", [])}
        assert a_hashes != b_hashes, (
            "expand_blocks=True 로 block geometry 변경이 hash 차이를 "
            "만들어야 함 (Q3 default behavior)."
        )

    def test_xscale_change_detected_in_expand_blocks_true(self, tmp_path):
        """Phase Q3 Codex follow-up [P1] regression guard:
        INSERT 의 transform-only 변경 (xscale 1.0 → 2.0) 이
        expand_blocks=True 경로에서 silent drop 되면 안 됨. parent
        INSERT 가 result["INSERT"] 에 함께 추가되도록 보장."""
        # Before: xscale=1.0
        before = ezdxf.new(dxfversion="R2010")
        b1 = before.blocks.new(name="DOWEL")
        b1.add_line((0, 0), (10, 0))
        before.modelspace().add_blockref(
            "DOWEL", insert=(50, 50),
            dxfattribs={"xscale": 1.0, "yscale": 1.0, "rotation": 0.0},
        )
        before_path = tmp_path / "before.dxf"
        before.saveas(str(before_path))

        # After: xscale=2.0 (block 정의 동일)
        after = ezdxf.new(dxfversion="R2010")
        b2 = after.blocks.new(name="DOWEL")
        b2.add_line((0, 0), (10, 0))
        after.modelspace().add_blockref(
            "DOWEL", insert=(50, 50),
            dxfattribs={"xscale": 2.0, "yscale": 1.0, "rotation": 0.0},
        )
        after_path = tmp_path / "after.dxf"
        after.saveas(str(after_path))

        before_doc = ezdxf.readfile(str(before_path))
        after_doc = ezdxf.readfile(str(after_path))

        ext_a = DxfEntityExtractor()
        entities_a = ext_a.extract(before_doc, expand_blocks=True)
        ext_b = DxfEntityExtractor()
        entities_b = ext_b.extract(after_doc, expand_blocks=True)

        # 양쪽 모두 INSERT 1개씩 추출되어야 함 (parent preserved)
        assert len(entities_a.get("INSERT", [])) == 1, (
            "expand_blocks=True 시에도 parent INSERT 가 result 에 "
            "포함되어야 함 (Codex P1 fix)"
        )
        assert len(entities_b.get("INSERT", [])) == 1
        # xscale 다르므로 hash 도 달라야 함 (transform 변경 detect)
        a_hash = entities_a["INSERT"][0].hash
        b_hash = entities_b["INSERT"][0].hash
        assert a_hash != b_hash, (
            "xscale 1.0 → 2.0 변경이 INSERT hash 차이로 surface "
            "되어야 함 — Codex P1 silent drop regression guard"
        )

    def test_rotation_change_detected_in_expand_blocks_true(self, tmp_path):
        """rotation-only 변경도 동일하게 보존되어야 함."""
        before = ezdxf.new(dxfversion="R2010")
        b1 = before.blocks.new(name="ARROW")
        b1.add_line((0, 0), (10, 0))
        before.modelspace().add_blockref(
            "ARROW", insert=(50, 50),
            dxfattribs={"rotation": 0.0},
        )
        before_path = tmp_path / "before.dxf"
        before.saveas(str(before_path))

        after = ezdxf.new(dxfversion="R2010")
        b2 = after.blocks.new(name="ARROW")
        b2.add_line((0, 0), (10, 0))
        after.modelspace().add_blockref(
            "ARROW", insert=(50, 50),
            dxfattribs={"rotation": 90.0},
        )
        after_path = tmp_path / "after.dxf"
        after.saveas(str(after_path))

        ext_a = DxfEntityExtractor()
        entities_a = ext_a.extract(ezdxf.readfile(str(before_path)),
                                    expand_blocks=True)
        ext_b = DxfEntityExtractor()
        entities_b = ext_b.extract(ezdxf.readfile(str(after_path)),
                                    expand_blocks=True)

        a_insert = entities_a["INSERT"][0]
        b_insert = entities_b["INSERT"][0]
        assert a_insert.hash != b_insert.hash, (
            "rotation 0 → 90 변경이 INSERT hash 차이로 surface 되어야 함"
        )

    def test_block_internal_line_change_silent_when_expand_false(self, tmp_path):
        # 같은 fixture 로 expand_blocks=False 시 INSERT hash 동일 → 변경
        # invisible (Q3 가 audit 카운터로 surface).
        before = ezdxf.new(dxfversion="R2010")
        b1 = before.blocks.new(name="DOWEL")
        b1.add_line((0, 0), (10, 0))
        before.modelspace().add_blockref("DOWEL", insert=(50, 50))
        before_path = tmp_path / "before.dxf"
        before.saveas(str(before_path))

        after = ezdxf.new(dxfversion="R2010")
        b2 = after.blocks.new(name="DOWEL")
        b2.add_line((0, 0), (20, 0))
        after.modelspace().add_blockref("DOWEL", insert=(50, 50))
        after_path = tmp_path / "after.dxf"
        after.saveas(str(after_path))

        extractor_a = DxfEntityExtractor()
        before_doc = ezdxf.readfile(str(before_path))
        entities_a = extractor_a.extract(before_doc, expand_blocks=False)

        extractor_b = DxfEntityExtractor()
        after_doc = ezdxf.readfile(str(after_path))
        entities_b = extractor_b.extract(after_doc, expand_blocks=False)

        # INSERT 한 개 양쪽 — block 정의 내 geometry 차이 invisible
        assert len(entities_a.get("INSERT", [])) == 1
        assert len(entities_b.get("INSERT", [])) == 1
        # block_text fingerprint 가 빈 문자열 (TEXT 없음) 이므로 hash 동일
        a_hash = entities_a["INSERT"][0].hash
        b_hash = entities_b["INSERT"][0].hash
        # 동일 block_name + insert_point + scale + rotation + 빈 text fp
        # → INSERT hash 동일 → geometry 변경 silent drop
        assert a_hash == b_hash, "Q3 가정 검증 — expand_blocks=False 시 silent drop"

        # 그러나 Q3 의 카운터가 silent drop 을 audit 에 surface
        assert extractor_a.last_stats["block_geometry_skipped_count"] >= 1
        assert extractor_b.last_stats["block_geometry_skipped_count"] >= 1
