"""Phase Q-FU-1 round-1 [Codex P2-1/P2-2/P2-3] — default flip 회귀 가드.

Phase Q-FU-1 가 ``DxfEntityExtractor.extract()`` default 를
``expand_blocks=True`` 로 변경하면서 도입된 3가지 silent 회귀 :

- **P2-1**: include/exclude_layers 가 block child entity 에 미적용 →
  부모 INSERT 가 통과하면 block 내부의 제외 layer entity 도
  result 에 포함됨 → false positive 변경.
- **P2-2**: ``block_text_detection=False`` opt-out 이 expansion 경로에서
  무력화 → caller 가 명시적으로 끈 TEXT/MTEXT/ATTDEF 가 그래도
  emit 됨 → legacy 동작 위반.
- **P2-3**: block 확장 중 ``max_entities`` 도달 시 ``last_stats
  ['limit_exceeded']`` 미 set → silent truncation, audit 미 surface.

각 회귀에 대해 1개 이상의 단위 테스트로 가드.
"""
from __future__ import annotations

import ezdxf
import pytest

from src.services.comparison.dxf_entity_extractor import DxfEntityExtractor


@pytest.fixture
def block_with_mixed_layers(tmp_path):
    """parent INSERT 는 BEAM_LAYER, block 내부 LINE 두 개:
    하나는 BEAM_LAYER, 하나는 IGNORE_LAYER (보조선)."""
    doc = ezdxf.new(dxfversion="R2010", setup=True)
    msp = doc.modelspace()
    if "MIXED_LAYER_BLOCK" not in doc.blocks:
        block = doc.blocks.new(name="MIXED_LAYER_BLOCK")
        # block 내부에 두 layer 의 LINE 정의
        block.add_line((0, 0), (50, 0), dxfattribs={"layer": "BEAM_LAYER"})
        block.add_line((0, 10), (50, 10), dxfattribs={"layer": "IGNORE_LAYER"})
    msp.add_blockref(
        "MIXED_LAYER_BLOCK", insert=(100, 100),
        dxfattribs={"layer": "BEAM_LAYER"},
    )
    path = tmp_path / "mixed_layer.dxf"
    doc.saveas(str(path))
    return path


@pytest.fixture
def block_with_text(tmp_path):
    """parent INSERT 안에 LINE 하나 + TEXT 하나 + ATTDEF 하나."""
    doc = ezdxf.new(dxfversion="R2010", setup=True)
    msp = doc.modelspace()
    if "TEXT_BLOCK" not in doc.blocks:
        block = doc.blocks.new(name="TEXT_BLOCK")
        block.add_line((0, 0), (50, 0))
        block.add_text(
            "DOWEL @100", dxfattribs={"insert": (10, 5), "height": 2.0},
        )
        block.add_attdef(
            tag="MARK", insert=(20, 8), text="A1", dxfattribs={"height": 2.0},
        )
    msp.add_blockref("TEXT_BLOCK", insert=(100, 100))
    path = tmp_path / "text_block.dxf"
    doc.saveas(str(path))
    return path


@pytest.fixture
def huge_block(tmp_path):
    """500개 LINE 을 가진 block — max_entities=100 으로 truncation 유도."""
    doc = ezdxf.new(dxfversion="R2010", setup=True)
    msp = doc.modelspace()
    if "HUGE_BLOCK" not in doc.blocks:
        block = doc.blocks.new(name="HUGE_BLOCK")
        for i in range(500):
            block.add_line((0, i), (50, i))
    msp.add_blockref("HUGE_BLOCK", insert=(0, 0))
    path = tmp_path / "huge_block.dxf"
    doc.saveas(str(path))
    return path


class TestP2_1_LayerFilterAppliedToBlockChildren:
    """P2-1 — include/exclude_layers 가 block child layer 에도 적용."""

    def test_exclude_layer_filters_block_internal_entity(
        self, block_with_mixed_layers
    ):
        """exclude_layers=['IGNORE_LAYER'] 면 block 내부 IGNORE_LAYER
        LINE 이 제외되어 result['LINE'] 에 1개만 (BEAM_LAYER) 남아야 함."""
        doc = ezdxf.readfile(str(block_with_mixed_layers))
        extractor = DxfEntityExtractor()
        entities = extractor.extract(doc, exclude_layers=["IGNORE_LAYER"])
        # BEAM_LAYER LINE 1개만 남아야 함 — IGNORE_LAYER LINE 은 필터됨
        assert len(entities.get("LINE", [])) == 1, (
            "Codex P2-1: exclude_layers 가 block child layer 에 적용되어야 함"
        )
        # 남은 LINE 은 BEAM_LAYER
        line = entities["LINE"][0]
        assert getattr(line, "layer", "") == "BEAM_LAYER"

    def test_include_layer_filters_block_internal_entity(
        self, block_with_mixed_layers
    ):
        """include_layers=['BEAM_LAYER'] 면 IGNORE_LAYER 가 제외되어 1개만."""
        doc = ezdxf.readfile(str(block_with_mixed_layers))
        extractor = DxfEntityExtractor()
        entities = extractor.extract(doc, include_layers=["BEAM_LAYER"])
        assert len(entities.get("LINE", [])) == 1, (
            "Codex P2-1: include_layers 가 block child layer 에 적용되어야 함"
        )

    def test_no_filter_keeps_all_block_children(self, block_with_mixed_layers):
        """필터 없으면 두 LINE 모두 result 에 포함 (회귀 baseline)."""
        doc = ezdxf.readfile(str(block_with_mixed_layers))
        extractor = DxfEntityExtractor()
        entities = extractor.extract(doc)
        assert len(entities.get("LINE", [])) == 2


class TestP2_2_BlockTextDetectionOptOut:
    """P2-2 — block_text_detection=False 면 expansion 경로에서도
    TEXT/MTEXT/ATTDEF skip."""

    def test_text_detection_false_skips_expanded_text(self, block_with_text):
        """block_text_detection=False extractor 로 expand_blocks=True 호출
        시 block 내부 TEXT/ATTDEF 가 result 에서 제외되어야 함.

        Phase Q-FU-1 default flip 후 회귀 — 이전엔 toggle 무시되어
        TEXT/ATTDEF 가 그대로 emit 됨."""
        doc = ezdxf.readfile(str(block_with_text))
        extractor = DxfEntityExtractor(block_text_detection=False)
        entities = extractor.extract(doc)  # expand_blocks=True default
        # LINE 은 emit (text 가 아니므로)
        assert len(entities.get("LINE", [])) == 1
        # TEXT 는 skip — block_text_detection=False opt-out 적용
        assert len(entities.get("TEXT", [])) == 0, (
            "Codex P2-2: block_text_detection=False 시 expanded TEXT 도 skip"
        )
        # ATTDEF 도 skip
        assert len(entities.get("ATTDEF", [])) == 0, (
            "Codex P2-2: block_text_detection=False 시 ATTDEF 도 skip"
        )

    def test_text_detection_true_default_emits_text(self, block_with_text):
        """default block_text_detection=True 면 expanded TEXT/ATTDEF emit
        (P2-2 fix 가 다른 경로에 회귀 만들지 않는지 확인)."""
        doc = ezdxf.readfile(str(block_with_text))
        extractor = DxfEntityExtractor()  # block_text_detection=True default
        entities = extractor.extract(doc)
        assert len(entities.get("LINE", [])) == 1
        assert len(entities.get("TEXT", [])) >= 1, (
            "block_text_detection=True default 는 expanded TEXT 보존"
        )


class TestP2_3_BlockTruncationSurfacedViaLimitExceeded:
    """P2-3 — block 확장 중 max_entities 초과 시 last_stats 에 surface."""

    def test_block_truncation_sets_limit_exceeded(self, huge_block):
        """max_entities=100 인 extractor 로 500-LINE block 을 expand 시
        last_stats['limit_exceeded']=True 가 set 되고
        block_truncated_count > 0 카운터에 누적되어야 함.

        Phase Q-FU-1 default flip 전엔 후속 top-level entity 가 한도
        체크를 다시 trigger 해야만 limit_exceeded 가 True 가 되어
        silent truncation 발생 가능."""
        doc = ezdxf.readfile(str(huge_block))
        extractor = DxfEntityExtractor(max_entities=100)
        entities = extractor.extract(doc)
        # truncation 발생했으므로 LINE 은 ~99 개 (parent INSERT 1개 +
        # 99개 LINE 후 도달)
        assert len(entities.get("LINE", [])) < 500
        # limit_exceeded 가 True 로 설정되어야 함 (silent truncation 방지)
        assert extractor.last_stats.get("limit_exceeded", False) is True, (
            "Codex P2-3: block 확장 중 truncation 시 limit_exceeded set"
        )
        assert extractor.last_stats.get("block_truncated_count", 0) > 0, (
            "Codex P2-3: block_truncated_count 카운터 누적"
        )

    def test_no_truncation_no_flag(self, tmp_path):
        """truncation 없을 때 limit_exceeded=False 회귀 baseline."""
        doc = ezdxf.new(dxfversion="R2010", setup=True)
        msp = doc.modelspace()
        if "SMALL_BLOCK" not in doc.blocks:
            block = doc.blocks.new(name="SMALL_BLOCK")
            block.add_line((0, 0), (50, 0))
        msp.add_blockref("SMALL_BLOCK", insert=(0, 0))
        path = tmp_path / "small.dxf"
        doc.saveas(str(path))

        doc2 = ezdxf.readfile(str(path))
        extractor = DxfEntityExtractor(max_entities=100)
        extractor.extract(doc2)
        assert extractor.last_stats.get("limit_exceeded", False) is False
        assert extractor.last_stats.get("block_truncated_count", 0) == 0


# ---------------------------------------------------------------------------
# Phase Q-FU-1 round-2 [Codex P2-NEW-1, P2-NEW-2] — round-1 fix 의 회귀
# ---------------------------------------------------------------------------


@pytest.fixture
def block_with_layer_zero_geometry(tmp_path):
    """parent INSERT 는 BEAM_LAYER, block 내부 LINE 은 layer "0" (BYBLOCK).

    표준 DXF semantics: layer "0" 은 부모 INSERT 의 visible layer 를
    상속. 사용자 도면에서 매우 흔한 패턴 (block 정의는 layer-neutral
    하게 만들고, INSERT 시점에 layer 결정).
    """
    doc = ezdxf.new(dxfversion="R2010", setup=True)
    msp = doc.modelspace()
    if "BYBLOCK_BLOCK" not in doc.blocks:
        block = doc.blocks.new(name="BYBLOCK_BLOCK")
        block.add_line((0, 0), (50, 0), dxfattribs={"layer": "0"})
    msp.add_blockref(
        "BYBLOCK_BLOCK", insert=(100, 100),
        dxfattribs={"layer": "BEAM_LAYER"},
    )
    path = tmp_path / "byblock.dxf"
    doc.saveas(str(path))
    return path


@pytest.fixture
def nested_block_excluded_inner(tmp_path):
    """outer block (layer BEAM_LAYER) 안에 inner INSERT (layer
    IGNORE_LAYER) + outer-direct LINE.

    inner INSERT 가 가리키는 inner block 안에는 LINE 1개 (layer "0",
    부모 effective layer 따라감).

    exclude_layers=['IGNORE_LAYER'] 로 호출 시 inner 의 LINE 은
    inner INSERT 가 IGNORE_LAYER 라서 자손까지 모두 skip 되어야 함.
    """
    doc = ezdxf.new(dxfversion="R2010", setup=True)
    msp = doc.modelspace()
    if "INNER_BLK" not in doc.blocks:
        inner = doc.blocks.new(name="INNER_BLK")
        inner.add_line((0, 0), (50, 0))  # layer 미지정 → "0" 기본
    if "OUTER_BLK" not in doc.blocks:
        outer = doc.blocks.new(name="OUTER_BLK")
        outer.add_line((0, 0), (100, 0), dxfattribs={"layer": "0"})
        outer.add_blockref(
            "INNER_BLK", insert=(0, 0),
            dxfattribs={"layer": "IGNORE_LAYER"},
        )
    msp.add_blockref(
        "OUTER_BLK", insert=(0, 0),
        dxfattribs={"layer": "BEAM_LAYER"},
    )
    path = tmp_path / "nested.dxf"
    doc.saveas(str(path))
    return path


class TestP2NEW_1_LayerZeroBYBLOCKInheritance:
    """Codex round-2 [P2-NEW-1] — layer "0" child 는 부모 INSERT 의
    effective layer 로 필터링."""

    def test_layer_zero_child_inherits_parent_for_include(
        self, block_with_layer_zero_geometry
    ):
        """include_layers=['BEAM_LAYER'] 시 layer "0" LINE 이 부모
        BEAM_LAYER 를 상속하여 통과해야 함.

        Codex round-1 fix 직후엔 child layer "0" 을 그대로 비교해서
        BEAM_LAYER 화이트리스트에서 false negative drop. round-2 fix
        로 effective layer 사용 → 정상 통과."""
        doc = ezdxf.readfile(str(block_with_layer_zero_geometry))
        extractor = DxfEntityExtractor()
        entities = extractor.extract(doc, include_layers=["BEAM_LAYER"])
        assert len(entities.get("LINE", [])) == 1, (
            "Codex P2-NEW-1: BYBLOCK (layer '0') child 는 부모 INSERT "
            "의 effective layer (BEAM_LAYER) 로 평가되어 include 통과"
        )

    def test_layer_zero_child_inherits_parent_for_exclude(
        self, block_with_layer_zero_geometry
    ):
        """exclude_layers=['BEAM_LAYER'] 시 layer "0" LINE 이 부모
        BEAM_LAYER 를 상속하여 같이 제외되어야 함."""
        doc = ezdxf.readfile(str(block_with_layer_zero_geometry))
        extractor = DxfEntityExtractor()
        entities = extractor.extract(doc, exclude_layers=["BEAM_LAYER"])
        # parent INSERT + child LINE 모두 BEAM_LAYER 로 평가 → 둘 다 제외
        assert len(entities.get("LINE", [])) == 0, (
            "Codex P2-NEW-1: BYBLOCK child 가 부모 BEAM_LAYER 와 함께 "
            "제외되어야 함"
        )

    def test_explicit_child_layer_unaffected_by_byblock_logic(self, tmp_path):
        """child 가 명시 layer ('IGNORE') 면 BYBLOCK 무관 — 기존 P2-1
        동작 그대로. Round-2 fix 가 P2-1 회귀 만들지 않는지 확인."""
        doc = ezdxf.new(dxfversion="R2010", setup=True)
        msp = doc.modelspace()
        block = doc.blocks.new(name="EXPLICIT_BLK")
        block.add_line((0, 0), (50, 0), dxfattribs={"layer": "IGNORE"})
        msp.add_blockref(
            "EXPLICIT_BLK", insert=(0, 0),
            dxfattribs={"layer": "BEAM_LAYER"},
        )
        path = tmp_path / "explicit.dxf"
        doc.saveas(str(path))

        doc2 = ezdxf.readfile(str(path))
        ents = DxfEntityExtractor().extract(
            doc2, include_layers=["BEAM_LAYER"]
        )
        # child 명시 layer "IGNORE" 는 부모 layer 무관 → include 실패
        assert len(ents.get("LINE", [])) == 0


class TestP2NEW_2_NestedInsertPreRecursionFilter:
    """Codex round-2 [P2-NEW-2] — 중첩 INSERT recursion 전에 자기
    layer 로 필터 검증."""

    def test_excluded_nested_insert_does_not_emit_children(
        self, nested_block_excluded_inner
    ):
        """outer block (BEAM_LAYER) 안의 inner INSERT (IGNORE_LAYER) 는
        exclude_layers=['IGNORE_LAYER'] 시 자손 LINE 도 emit 안 됨.

        Round-1 fix 직후엔 child layer 필터가 recursion 후에 와서
        nested INSERT 의 LINE 들이 그대로 result 에 들어옴. Round-2
        fix: nested INSERT recursion *전*에 자신의 effective layer 로
        검증 → 제외 시 자손 전체 skip."""
        doc = ezdxf.readfile(str(nested_block_excluded_inner))
        extractor = DxfEntityExtractor()
        entities = extractor.extract(
            doc,
            exclude_layers=["IGNORE_LAYER"],
            block_recursion_depth=2,
        )
        # outer-direct LINE (BYBLOCK → BEAM_LAYER) 은 보존 → 1개
        # inner LINE (IGNORE_LAYER → 자손까지 skip) → 0개
        # 총 1개여야 함
        assert len(entities.get("LINE", [])) == 1, (
            "Codex P2-NEW-2: 제외된 nested INSERT 는 자손 LINE 도 "
            f"emit 하지 않아야 함 (got {len(entities.get('LINE', []))})"
        )

    def test_included_nested_insert_emits_children(
        self, nested_block_excluded_inner
    ):
        """필터 없을 때 outer-direct + inner LINE 모두 추출 — 회귀 baseline."""
        doc = ezdxf.readfile(str(nested_block_excluded_inner))
        extractor = DxfEntityExtractor()
        entities = extractor.extract(doc, block_recursion_depth=2)
        assert len(entities.get("LINE", [])) == 2


# ---------------------------------------------------------------------------
# Phase Q-FU-1 round-3 [Codex P2-NEW3-1, P2-NEW3-2] — round-2 fix 회귀
# ---------------------------------------------------------------------------


@pytest.fixture
def nested_container_with_explicit_child(tmp_path):
    """outer INSERT (BEAM_LAYER) → inner INSERT (AUX_LAYER) → LINE
    명시적으로 BEAM_LAYER.

    include_layers=['BEAM_LAYER'] 시 LINE 은 통과해야 함 — 부모 INSERT
    는 통과 못해도 children 의 명시 layer 가 매치하므로. round-2 fix 가
    pre-recursion 에서 nested INSERT 의 effective layer (AUX_LAYER) 만
    보고 자손 전체 skip → false negative.
    """
    doc = ezdxf.new(dxfversion="R2010", setup=True)
    msp = doc.modelspace()
    if "INNER_BLK_EXPLICIT" not in doc.blocks:
        inner = doc.blocks.new(name="INNER_BLK_EXPLICIT")
        # 명시적으로 BEAM_LAYER — include 화이트리스트와 매치
        inner.add_line((0, 0), (50, 0), dxfattribs={"layer": "BEAM_LAYER"})
    if "OUTER_BLK_AUX" not in doc.blocks:
        outer = doc.blocks.new(name="OUTER_BLK_AUX")
        outer.add_blockref(
            "INNER_BLK_EXPLICIT", insert=(0, 0),
            dxfattribs={"layer": "AUX_LAYER"},
        )
    msp.add_blockref(
        "OUTER_BLK_AUX", insert=(0, 0),
        dxfattribs={"layer": "BEAM_LAYER"},
    )
    path = tmp_path / "container_aux.dxf"
    doc.saveas(str(path))
    return path


class TestP2NEW3_1_NestedIncludeFilterAsymmetric:
    """Codex round-3 [P2-NEW3-1] — pre-recursion check 는 exclude 만,
    include_layers 는 nested children 에 다시 적용."""

    def test_nested_aux_container_with_beam_child_passes_include(
        self, nested_container_with_explicit_child
    ):
        """outer (BEAM_LAYER) → inner INSERT (AUX_LAYER) → LINE (BEAM_LAYER).
        include_layers=['BEAM_LAYER'] 로 호출 시:
        - inner INSERT 가 AUX_LAYER 라서 자체는 include 매치 안 함
        - 하지만 그 안의 LINE 은 명시적 BEAM_LAYER → 통과해야 함
        - round-2 fix 까지는 pre-recursion 에서 inner INSERT skip → LINE 누락
        - round-3 fix 후 include 만 있을 때 항상 recurse → LINE 통과"""
        doc = ezdxf.readfile(str(nested_container_with_explicit_child))
        extractor = DxfEntityExtractor()
        entities = extractor.extract(
            doc,
            include_layers=["BEAM_LAYER"],
            block_recursion_depth=2,
        )
        # 명시적 BEAM_LAYER LINE 1 개는 emit 되어야 함
        assert len(entities.get("LINE", [])) == 1, (
            "Codex P2-NEW3-1: include filter 시 nested AUX container 안의 "
            "명시적 BEAM_LAYER LINE 이 통과해야 함 "
            f"(got {len(entities.get('LINE', []))})"
        )

    def test_nested_excluded_still_short_circuits(
        self, nested_block_excluded_inner
    ):
        """round-2 fix 의 exclude short-circuit 동작은 보존되어야 함
        (P2-NEW-2 회귀 가드).

        outer (BEAM) → inner INSERT (IGNORE_LAYER) → LINE.
        exclude_layers=['IGNORE_LAYER'] 시 inner LINE 은 자손까지 skip."""
        doc = ezdxf.readfile(str(nested_block_excluded_inner))
        extractor = DxfEntityExtractor()
        entities = extractor.extract(
            doc,
            exclude_layers=["IGNORE_LAYER"],
            block_recursion_depth=2,
        )
        # outer-direct LINE 1개만 (inner 자손은 exclude short-circuit)
        assert len(entities.get("LINE", [])) == 1


class TestP2NEW3_2_EffectiveLayerOnEmittedChild:
    """Codex round-3 [P2-NEW3-2] — BYBLOCK child 가 emit 될 때
    NormalizedEntity.layer 가 effective layer (부모 상속) 로 저장."""

    def test_byblock_child_layer_overridden_to_parent(
        self, block_with_layer_zero_geometry
    ):
        """parent INSERT layer=BEAM_LAYER, block 내부 LINE layer="0".
        emit 된 NormalizedEntity 는 layer="BEAM_LAYER" (effective) 여야
        downstream by_layer 통계/priority/threshold 가 올바르게 동작.
        round-2 fix 까지는 layer="0" 그대로 → BEAM 변경이 "layer 0"
        변경으로 잘못 분류."""
        doc = ezdxf.readfile(str(block_with_layer_zero_geometry))
        extractor = DxfEntityExtractor()
        entities = extractor.extract(doc)  # 필터 없음
        lines = entities.get("LINE", [])
        assert len(lines) == 1
        assert lines[0].layer == "BEAM_LAYER", (
            "Codex P2-NEW3-2: BYBLOCK (layer '0') child 의 effective "
            f"layer 가 NormalizedEntity 에 저장되어야 함 (got '{lines[0].layer}')"
        )

    def test_explicit_child_layer_not_overridden(self, tmp_path):
        """child 가 명시 layer 면 effective layer override 하지 않음 —
        round-3 fix 가 명시 layer 케이스 회귀 안 만드는지 확인."""
        doc = ezdxf.new(dxfversion="R2010", setup=True)
        msp = doc.modelspace()
        block = doc.blocks.new(name="EXPLICIT_AUX_BLK")
        # child 가 명시적으로 AUX_DETAIL — 부모 BEAM_LAYER 와 다름
        block.add_line(
            (0, 0), (50, 0), dxfattribs={"layer": "AUX_DETAIL"}
        )
        msp.add_blockref(
            "EXPLICIT_AUX_BLK", insert=(0, 0),
            dxfattribs={"layer": "BEAM_LAYER"},
        )
        path = tmp_path / "explicit_aux.dxf"
        doc.saveas(str(path))

        doc2 = ezdxf.readfile(str(path))
        ents = DxfEntityExtractor().extract(doc2)
        lines = ents.get("LINE", [])
        assert len(lines) == 1
        # 명시 layer 그대로 보존 — BYBLOCK 무관
        assert lines[0].layer == "AUX_DETAIL", (
            "명시 layer 는 부모 layer override 안 됨 (BYBLOCK 무관)"
        )
