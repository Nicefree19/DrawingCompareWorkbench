#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase O1 — golden DXF fixture 생성기.

reproducible synthetic DXF 페어를 생성. ezdxf를 사용해 단순한 도면
(LINE / CIRCLE / TEXT) 만 그리고 `before` / `after` 사이의 차이를
명시적으로 통제. 결과 .dxf 파일과 짝이 되는 `truth.json` 도 함께
출력.

생성되는 페어:

* dxf/01_identical/         — 변경 0건 (정상 동작 sanity check)
* dxf/02_single_modification/ — LINE 1개 좌표 5mm 이동
* dxf/03_micro_shift_global/ — 전체 도면이 0.5mm 시프트
                                (현재 시스템에서는 false positive
                                폭증이 예상되는 회귀 baseline)
* dxf/04_added_deleted/      — LINE 1개 추가 + LINE 1개 삭제
* dxf/05_cosmetic_only/      — color 만 변경 (Phase O3 대비 baseline —
                                현재는 변경 0건으로 보고됨)

사용법:
    python scripts/build_golden_dxf_fixtures.py
        [--output-dir tests/data/comparison/golden]
        [--clean]   # 기존 fixture 삭제 후 재생성

본 스크립트는 idempotent — 같은 입력에 대해 동일한 .dxf 를 생성한다.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

import ezdxf


# ---------------------------------------------------------------------------
# 한 페어를 정의하는 dataclass
# ---------------------------------------------------------------------------


@dataclass
class FixturePair:
    pair_id: str
    comment: str
    before_lines: List[Tuple[float, float, float, float, str, int]]
    after_lines: List[Tuple[float, float, float, float, str, int]]
    truth: List[dict]
    # Phase O Commit 4 [RV-20260508-010] — INSERT + ATTRIB 시나리오를
    # 위해 모델스페이스 직접 후처리 hook. None 이면 LINE-only fixture
    # 와 동일 동작 (기존 06개 fixture 호환).
    # 시그니처: ``Callable[[ezdxf doc, modelspace], None]``
    build_extras_before: Optional[Any] = None
    build_extras_after: Optional[Any] = None
    # Phase Q8 round-1 (Codex follow-up) — fixture 가 verify pipeline
    # 의 알려진 한계 (예: Q6 가 GUI dialog 활성화 필요한데 verify
    # 는 default config 만 사용) 때문에 임시로 detect 못 하는 경우
    # ``True``. baseline gate 는 expected_to_fail=True 인 fixture 의
    # FN 을 무시하고 다른 fixture 의 회귀만 감시.
    expected_to_fail: bool = False
    expected_to_fail_reason: str = ""


# ---------------------------------------------------------------------------
# 페어 정의 — (x1, y1, x2, y2, layer, color)
# ---------------------------------------------------------------------------


_DEFAULT_LINES: List[Tuple[float, float, float, float, str, int]] = [
    # 단순 사각형 — 외곽선
    (0.0, 0.0, 1000.0, 0.0, "BEAM", 7),
    (1000.0, 0.0, 1000.0, 800.0, "BEAM", 7),
    (1000.0, 800.0, 0.0, 800.0, "BEAM", 7),
    (0.0, 800.0, 0.0, 0.0, "BEAM", 7),
    # 내부 BEAM — 가로 1개
    (0.0, 400.0, 1000.0, 400.0, "BEAM", 7),
    # 내부 GRID — 세로 1개
    (500.0, 0.0, 500.0, 800.0, "GRID", 3),
]


def _shift_lines(
    lines: List[Tuple[float, float, float, float, str, int]],
    dx: float,
    dy: float,
) -> List[Tuple[float, float, float, float, str, int]]:
    return [
        (x1 + dx, y1 + dy, x2 + dx, y2 + dy, layer, color)
        for (x1, y1, x2, y2, layer, color) in lines
    ]


def _add_block_with_geometry(
    doc: Any,
    msp: Any,
    *,
    block_name: str,
    line_end: Tuple[float, float],
    insert_at: Tuple[float, float] = (500.0, 400.0),
) -> None:
    """Phase Q8 — Q3 block geometry change fixture helper.

    Block 정의 안에 LINE 1개 (no ATTRIB). before/after 가 같은 block
    이름을 다른 geometry 로 정의 → expand_blocks=True (Q3 default) 가
    block-internal 변경을 detect.
    """
    if "Q3_LAYER" not in doc.layers:
        doc.layers.add(name="Q3_LAYER", color=5)
    if block_name not in doc.blocks:
        block = doc.blocks.new(name=block_name)
        block.add_line((0, 0), line_end, dxfattribs={"layer": "Q3_LAYER"})
    msp.add_blockref(block_name, insert=insert_at)


def _add_ocs_circle(
    doc: Any,
    msp: Any,
    *,
    center_ocs: Tuple[float, float, float],
    radius: float,
    extrusion: Tuple[float, float, float],
    layer: str = "Q4_OCS",
) -> None:
    """Phase Q8 — Q4 OCS extrusion CIRCLE fixture helper.

    extrusion=(0, 0, -1) 같은 non-default OCS 의 CIRCLE. Q4 의 _to_wcs
    가 OCS center 를 WCS 로 변환해 동일 위치라도 hash 정확히 매칭
    (extrusion 만 다른 경우는 false-different 안 됨).
    """
    if layer not in doc.layers:
        doc.layers.add(name=layer, color=2)
    msp.add_circle(
        center=center_ocs,
        radius=radius,
        dxfattribs={
            "layer": layer,
            "extrusion": extrusion,
        },
    )


def _add_paperspace_circle(
    doc: Any,
    *,
    layout_name: str,
    center: Tuple[float, float],
    radius: float = 10.0,
    layer: str = "Q5_PSL",
) -> None:
    """Phase Q8 — Q5 paperspace fixture helper.

    paperspace layout 에 CIRCLE 추가. Q5 의 extract_all_layouts=True
    가 paperspace entity 도 추출하고 layout namespace 로 hash 분리.
    """
    if layer not in doc.layers:
        doc.layers.add(name=layer, color=4)
    if layout_name == "Layout1":
        layout = doc.layouts.get("Layout1")
    else:
        if layout_name not in {l.name for l in doc.layouts}:
            layout = doc.layouts.new(layout_name)
        else:
            layout = doc.layouts.get(layout_name)
    layout.add_circle(center=center, radius=radius, dxfattribs={"layer": layer})


def _add_dowel_block(doc: Any, msp: Any, dowel_text: str) -> None:
    """Phase O Commit 4 [RV-20260508-010] — 사용자 사례 (dowel callout
    블록) 의 모델스페이스 INSERT + ATTRIB 추가 helper.

    DOWEL_BLOCK 정의 (geometry + ATTDEF 템플릿) 를 등록하고, 모델
    스페이스에 INSERT 1개 + ATTRIB 1개 (지정 텍스트) 를 추가. before/
    after 사이에는 ATTRIB.text 만 다르게 되어 좌표/스케일/태그/위치
    모두 동일. Phase O Commit 1 (ATTRIB 정식 지원) 이 동작해야
    이 텍스트 변경이 비교 결과에 surface.
    """
    if "TEXT_LAYER" not in doc.layers:
        doc.layers.add(name="TEXT_LAYER", color=2)

    if "DOWEL_BLOCK" not in doc.blocks:
        block = doc.blocks.new(name="DOWEL_BLOCK")
        block.add_line((0, 0), (10, 0))
        block.add_attdef(
            tag="DOWEL",
            insert=(0, 0),
            text="DEFAULT_DOWEL",
            dxfattribs={"layer": "TEXT_LAYER"},
        )

    insert = msp.add_blockref(
        "DOWEL_BLOCK",
        insert=(500.0, 400.0),  # truth.location 과 일치
    )
    # RV-20260508-012 — ATTRIB.dxf.insert 는 parent INSERT 기준 local
    # 좌표. local (0, 0) + parent (500, 400) = modelspace (500, 400)
    # 으로 truth.location 과 정확히 일치시킴.
    insert.add_attrib(
        tag="DOWEL",
        text=dowel_text,
        insert=(0.0, 0.0),
        dxfattribs={"layer": "TEXT_LAYER"},
    )


PAIRS: List[FixturePair] = [
    FixturePair(
        pair_id="01_identical",
        comment="동일 도면 — 변경 0건이 정답 (sanity check)",
        before_lines=_DEFAULT_LINES,
        after_lines=list(_DEFAULT_LINES),
        truth=[],
    ),
    FixturePair(
        pair_id="02_single_modification",
        comment="LINE 1개 (내부 BEAM) 의 끝점이 5mm 이동",
        before_lines=_DEFAULT_LINES,
        after_lines=[
            (0.0, 0.0, 1000.0, 0.0, "BEAM", 7),
            (1000.0, 0.0, 1000.0, 800.0, "BEAM", 7),
            (1000.0, 800.0, 0.0, 800.0, "BEAM", 7),
            (0.0, 800.0, 0.0, 0.0, "BEAM", 7),
            # 내부 BEAM 끝점 y 좌표 405 (5mm 이동)
            (0.0, 405.0, 1000.0, 405.0, "BEAM", 7),
            (500.0, 0.0, 500.0, 800.0, "GRID", 3),
        ],
        truth=[
            # NormalizedEntity.location 은 LINE 의 midpoint —
            # before midpoint (500, 400), after midpoint (500, 405).
            # tolerance 50mm 로 어느 쪽이 매칭되어도 허용.
            # Phase O2 가 deleted+added → modified 변환에 성공하면 1건,
            # 실패하면 2건 (FP=1) — 어느 쪽이든 truth 1건과 매칭.
            {
                "location": [500.0, 402.5],  # midpoint of before/after midpoints
                "change_type": "modified",
                "layer": "BEAM",
                "tolerance_mm": 50.0,
                "notes": "내부 BEAM 5mm 평행이동 — modified 또는 deleted+added 페어",
            },
        ],
    ),
    FixturePair(
        pair_id="03_micro_shift_global",
        comment="전체 도면이 (+0.5, +0.5) mm 시프트 — Phase O2 alignment 효과 baseline",
        before_lines=_DEFAULT_LINES,
        after_lines=_shift_lines(_DEFAULT_LINES, 0.5, 0.5),
        # 정답: 변경 0건 (의미적으로는 동일 도면이 미세 이동)
        # 현재 시스템은 added/deleted 폭증 예상 — Phase O2 commit 이후
        # 이 truth를 만족시키기 위한 회귀 가드.
        truth=[],
    ),
    FixturePair(
        pair_id="04_added_deleted",
        comment="LINE 1개 추가 (대각선) + LINE 1개 삭제 (내부 GRID)",
        before_lines=_DEFAULT_LINES,
        after_lines=[
            (0.0, 0.0, 1000.0, 0.0, "BEAM", 7),
            (1000.0, 0.0, 1000.0, 800.0, "BEAM", 7),
            (1000.0, 800.0, 0.0, 800.0, "BEAM", 7),
            (0.0, 800.0, 0.0, 0.0, "BEAM", 7),
            (0.0, 400.0, 1000.0, 400.0, "BEAM", 7),
            # GRID 라인 삭제, 대각선 새 LINE 추가
            (0.0, 0.0, 1000.0, 800.0, "DIAG", 1),
        ],
        truth=[
            {
                "location": [500.0, 400.0],
                "change_type": "deleted",
                "layer": "GRID",
                "tolerance_mm": 50.0,
                "notes": "GRID 라인 삭제 (중점 기준)",
            },
            {
                "location": [500.0, 400.0],
                "change_type": "added",
                "layer": "DIAG",
                "tolerance_mm": 50.0,
                "notes": "대각선 LINE 추가 (중점 기준)",
            },
        ],
    ),
    FixturePair(
        pair_id="05_cosmetic_only",
        comment="좌표 동일, color 만 변경 — Phase O3 cosmetic 분리 baseline",
        before_lines=_DEFAULT_LINES,
        after_lines=[
            (x1, y1, x2, y2, layer, (color + 1) % 256)
            for (x1, y1, x2, y2, layer, color) in _DEFAULT_LINES
        ],
        # 정답: 변경 0건 (좌표는 동일, cosmetic 만 변경)
        # Phase O3 commit 이후 suppress_cosmetic_only=True 시 만족.
        truth=[],
    ),
    # Phase O Commit 4 [RV-20260508-010] — 사용자 사례 직접 재현
    # fixture. 블록 attribute 텍스트가 변경된 경우 (DOWEL BAR ...
    # @100 → @200) 가 추출/비교 단계에서 정확히 1 MODIFIED ATTRIB
    # 으로 surface 하는지 확인. before/after 모두 동일 LINE 외곽선 +
    # DOWEL_BLOCK INSERT 1개; ATTRIB tag/pos 동일, text 만 다름.
    FixturePair(
        pair_id="07_block_attribute_text_change",
        comment=(
            "사용자 사례 — 블록 attribute (ATTRIB) text 변경 "
            "(DOWEL BAR (2)SHD13@100 → @200). LINE/coordinate 동일, "
            "INSERT 동일, ATTRIB tag/position 동일, ATTRIB text 만 다름. "
            "Phase O Commit 1+2 가 동작하면 1 MODIFIED ATTRIB 으로 "
            "surface, 동작 안하면 변경 0건 (사용자 보고 사례)."
        ),
        before_lines=[
            (0.0, 0.0, 1000.0, 0.0, "BEAM", 7),
            (1000.0, 0.0, 1000.0, 800.0, "BEAM", 7),
        ],
        after_lines=[
            (0.0, 0.0, 1000.0, 0.0, "BEAM", 7),
            (1000.0, 0.0, 1000.0, 800.0, "BEAM", 7),
        ],
        # before: ATTRIB text "DOWEL BAR (2)SHD13@100"
        build_extras_before=lambda doc, msp: _add_dowel_block(
            doc, msp, "DOWEL BAR (2)SHD13@100"
        ),
        # after: ATTRIB text "DOWEL BAR (2)SHD13@200"
        build_extras_after=lambda doc, msp: _add_dowel_block(
            doc, msp, "DOWEL BAR (2)SHD13@200"
        ),
        truth=[
            {
                "location": [500.0, 400.0],
                "change_type": "modified",
                "entity_type": "ATTRIB",
                "layer": "TEXT_LAYER",
                "tolerance_mm": 1.0,
                "notes": (
                    "ATTRIB text @100 → @200 — 사용자 사례 직접 재현. "
                    "Phase O Commit 1 (ATTRIB 정식 지원) 만으로 검출됨 "
                    "— 이 fixture 는 ATTRIB-realized text 변경이라 "
                    "Commit 1 만 필요. Commit 2 (INSERT block-internal "
                    "text fingerprint) 는 ATTRIB 으로 realize 되지 않은 "
                    "block 정의 내부 TEXT 변경 케이스용 (별도 시나리오)."
                ),
            },
        ],
    ),
    # Phase P [RV-20260508-013] — 사용자가 의도적으로 한 zone (보 1개)
    # 만 50mm 이동시킨 시나리오. RANSAC 가 alignment 추정 시 inlier 이
    # 50% 이하 (도면 절반 이상이 그대로) 가 되어 alignment 흡수 비활성
    # → 진짜 변경이 보존되어야 함. 추가로 BEAM layer 보호 가드도 동시
    # 작동 (이중 안전망).
    FixturePair(
        pair_id="08_intentional_zone_shift_beam",
        comment=(
            "한 zone (보 1개) 만 50mm 시프트 — Phase P 회복 검증. "
            "Phase O2 까지는 alignment artifact 로 흡수되어 silent drop "
            "되던 회귀를 차단. structural layer (BEAM) 보호 가드가 핵심."
        ),
        before_lines=[
            # 외곽선 (변경 없음 — alignment inlier 풀)
            (0.0, 0.0, 1000.0, 0.0, "BEAM", 7),
            (1000.0, 0.0, 1000.0, 800.0, "BEAM", 7),
            (1000.0, 800.0, 0.0, 800.0, "BEAM", 7),
            (0.0, 800.0, 0.0, 0.0, "BEAM", 7),
            # 내부 GRID (변경 없음)
            (500.0, 0.0, 500.0, 800.0, "GRID", 3),
            # 의도적 시프트 대상: 내부 BEAM 가로 1개 — y=400 (before)
            (100.0, 400.0, 900.0, 400.0, "BEAM", 7),
        ],
        after_lines=[
            (0.0, 0.0, 1000.0, 0.0, "BEAM", 7),
            (1000.0, 0.0, 1000.0, 800.0, "BEAM", 7),
            (1000.0, 800.0, 0.0, 800.0, "BEAM", 7),
            (0.0, 800.0, 0.0, 0.0, "BEAM", 7),
            (500.0, 0.0, 500.0, 800.0, "GRID", 3),
            # 동일 BEAM 이 y=450 으로 50mm shift (의도된 zone-level 변경)
            (100.0, 450.0, 900.0, 450.0, "BEAM", 7),
        ],
        truth=[
            {
                "location": [500.0, 425.0],
                "change_type": "modified",
                "entity_type": "LINE",
                "layer": "BEAM",
                "tolerance_mm": 100.0,
                "notes": (
                    "BEAM 1개 50mm 시프트 — 도면의 5/6 (외곽 4 + GRID 1) "
                    "는 그대로이므로 RANSAC inlier_ratio ≈ 0.83 < 0.85 → "
                    "alignment 흡수 비활성. 보 layer 보호 가드도 함께 작동."
                ),
            },
        ],
    ),
    # Phase P [RV-20260508-013] — 한국어 layer 의 단일 entity 변경.
    # ``ChangeZoneOptions.structural_layer_patterns`` 의 fnmatch 영문 패턴은
    # "기둥-1F" 매칭 실패 → noise_score >= 0.7 → recommended (min=2) 와
    # 결합 시 zone 폐기. SSoT helper ``is_structural_layer`` 가 한국어
    # substring 매칭 추가하여 회복.
    FixturePair(
        pair_id="09_korean_layer_single_change",
        comment=(
            "한국어 layer (\"기둥-1F\") + 단일 column 5mm 이동 — "
            "Phase P SSoT 회복 검증. Phase O 까지는 noise_score >= 0.7 + "
            "recommended (min_changes_per_zone=2) 결합으로 zone 폐기."
        ),
        before_lines=[
            # 외곽선 (변경 없음)
            (0.0, 0.0, 1000.0, 0.0, "외곽선", 7),
            (1000.0, 0.0, 1000.0, 800.0, "외곽선", 7),
            # 한국어 layer 의 column 1개
            (300.0, 200.0, 300.0, 600.0, "기둥-1F", 7),
            # 비교 anchor 용 GRID
            (500.0, 0.0, 500.0, 800.0, "축선", 3),
        ],
        after_lines=[
            (0.0, 0.0, 1000.0, 0.0, "외곽선", 7),
            (1000.0, 0.0, 1000.0, 800.0, "외곽선", 7),
            # 한국어 layer column 이 +5mm 이동 (304.0 → 305.0 으로 5mm shift)
            (305.0, 200.0, 305.0, 600.0, "기둥-1F", 7),
            (500.0, 0.0, 500.0, 800.0, "축선", 3),
        ],
        truth=[
            {
                "location": [302.5, 400.0],
                "change_type": "modified",
                "entity_type": "LINE",
                "layer": "기둥-1F",
                "tolerance_mm": 50.0,
                "notes": (
                    "기둥(한국어) 단일 entity 5mm shift — Phase P 까지는 "
                    "noise filter recommended 프리셋에서 silent drop. "
                    "is_structural_layer SSoT 헬퍼 적용 후 회복."
                ),
            },
        ],
    ),
    # Phase P [RV-20260508-013] — TEXT entity 의 좌표 시프트 + 내용 변경
    # 동시 시나리오. Phase O 까지는 near_match_radius (10mm 또는 alignment
    # 확장값) 안 잡히면 added+deleted 분리. P3 의 text_near_match_radius
    # (default 50mm) 적용 후 1 MODIFIED 으로 surface.
    FixturePair(
        pair_id="10_dimension_text_shifted",
        comment=(
            "TEXT entity 가 좌표 30mm 시프트 + 내용 변경 동시 — Phase P "
            "P3 text_near_match_radius 회복 검증. Phase O 까지는 added+"
            "deleted 분리 (사용자 보고: 치수가 두 개로 표시됨)."
        ),
        before_lines=[
            (0.0, 0.0, 1000.0, 0.0, "BEAM", 7),
            (1000.0, 0.0, 1000.0, 800.0, "BEAM", 7),
        ],
        after_lines=[
            (0.0, 0.0, 1000.0, 0.0, "BEAM", 7),
            (1000.0, 0.0, 1000.0, 800.0, "BEAM", 7),
        ],
        # before: TEXT "1500" at (200, 100)
        build_extras_before=lambda doc, msp: msp.add_text(
            "1500",
            dxfattribs={"layer": "DIM", "insert": (200.0, 100.0), "height": 50.0},
        ),
        # after: TEXT "1550" at (230, 100) — 30mm shift + content 변경
        build_extras_after=lambda doc, msp: msp.add_text(
            "1550",
            dxfattribs={"layer": "DIM", "insert": (230.0, 100.0), "height": 50.0},
        ),
        truth=[
            {
                "location": [215.0, 100.0],
                "change_type": "modified",
                "entity_type": "TEXT",
                "layer": "DIM",
                "tolerance_mm": 50.0,
                "notes": (
                    "TEXT 좌표 30mm shift + 내용 1500→1550. Phase O 의 1mm "
                    "tolerance 로는 added+deleted, Phase P 의 50mm text "
                    "radius 로는 modified."
                ),
            },
        ],
    ),
    # --- Phase Q8 [RV-20260509-002] — Q3-Q7 회귀 가드 fixture (11-15) ---
    # 각 fixture 는 직전 phase 의 silent-drop 결함을 정확히 재현 →
    # 해당 phase 적용 후 정확히 detect 되는지 검증.

    # Q3: Block geometry change — INSERT 의 referenced block 정의가
    # before/after 사이에 변경됨. expand_blocks=True (Q3 default) 가
    # block-internal LINE 변경을 detect 해야 함.
    FixturePair(
        pair_id="11_block_geometry_change",
        comment=(
            "Q3 회귀 가드 — block 정의 안의 LINE geometry 가 before "
            "(0,0)→(10,0) → after (0,0)→(20,0) 로 변경. expand_blocks=True "
            "(Q3 default) 가 block-internal 변경을 detect. Phase Q2 까지는 "
            "INSERT fingerprint 가 transform 만 봐서 silent."
        ),
        # Phase Q-FU-1 (RV-20260510-001) — extractor default 통일 완료.
        # DxfEntityExtractor.extract() default 가 expand_blocks=True 로
        # 변경되어 verify pipeline 의 extract_from_file() 도 Q3 효과 적용
        # → fixture 11 active 전환 (recall 정상).
        before_lines=[
            (0.0, 0.0, 1000.0, 0.0, "BEAM", 7),
            (1000.0, 0.0, 1000.0, 800.0, "BEAM", 7),
        ],
        after_lines=[
            (0.0, 0.0, 1000.0, 0.0, "BEAM", 7),
            (1000.0, 0.0, 1000.0, 800.0, "BEAM", 7),
        ],
        build_extras_before=lambda doc, msp: _add_block_with_geometry(
            doc, msp, block_name="Q3_BLOCK", line_end=(10.0, 0.0)
        ),
        build_extras_after=lambda doc, msp: _add_block_with_geometry(
            doc, msp, block_name="Q3_BLOCK", line_end=(20.0, 0.0)
        ),
        # Phase Q8 round-1 (Codex follow-up) — actual behavior:
        # expand_blocks=True 가 block 내부 LINE 을 펼치므로 변경은
        # ADDED LINE (after, end=20) + DELETED LINE (before, end=10)
        # 으로 surface (block 의 modelspace 위치 = INSERT.insert =
        # (500, 400) 기준 + LINE start/end 의 평균). location 은
        # block 내부 LINE 의 modelspace 좌표 (insert + (5, 0) 또는
        # (10, 0)).
        truth=[
            {
                "location": [505.0, 400.0],
                "change_type": "deleted",
                "entity_type": "LINE",
                "layer": "Q3_LAYER",
                "tolerance_mm": 50.0,
                "notes": (
                    "Q3 block-internal LINE (end=10) deleted — "
                    "expand_blocks=True 가 block geometry 펼쳐서 "
                    "ADDED+DELETED 페어로 surface."
                ),
            },
            {
                "location": [510.0, 400.0],
                "change_type": "added",
                "entity_type": "LINE",
                "layer": "Q3_LAYER",
                "tolerance_mm": 50.0,
                "notes": (
                    "Q3 block-internal LINE (end=20) added."
                ),
            },
        ],
    ),

    # Q4: OCS extrusion CIRCLE — extrusion 만 (0,0,1) 에서 (0,0,-1)
    # 로 flip. center_ocs 는 동일하지만 WCS 는 X 좌표 반전. Q4 의
    # _to_wcs + _extrusion_key 가 동일 entity 인지 정확히 식별.
    FixturePair(
        pair_id="12_ocs_circle_extrusion_flip",
        comment=(
            "Q4 회귀 가드 — CIRCLE extrusion (0,0,1)→(0,0,-1) flip. "
            "center_ocs 동일이지만 WCS 좌표는 X 반전. Q4 의 OCS→WCS 변환 "
            "+ extrusion key 가 hash 에 포함되어 정확히 'modified' detect. "
            "Phase Q3 까지는 OCS 만 비교해 silent (false-equal)."
        ),
        before_lines=[],  # CIRCLE 만 존재
        after_lines=[],
        build_extras_before=lambda doc, msp: _add_ocs_circle(
            doc, msp,
            center_ocs=(100.0, 200.0, 0.0),
            radius=50.0,
            extrusion=(0.0, 0.0, 1.0),  # default OCS
        ),
        build_extras_after=lambda doc, msp: _add_ocs_circle(
            doc, msp,
            center_ocs=(100.0, 200.0, 0.0),
            radius=50.0,
            extrusion=(0.0, 0.0, -1.0),  # flipped — WCS X 가 -100 으로 변경
        ),
        truth=[
            {
                "location": [0.0, 200.0],
                "change_type": "modified",
                "entity_type": "CIRCLE",
                "layer": "Q4_OCS",
                "tolerance_mm": 200.0,  # WCS X 가 100→-100 이라 Δ=200
                "notes": (
                    "Q4 OCS→WCS — extrusion flip 으로 WCS center 가 "
                    "(100, 200) → (-100, 200). Q4 normalizer 가 _to_wcs "
                    "변환 + extrusion key 를 hash 에 포함하므로 deleted "
                    "(extrusion +1) + added (extrusion -1) 분리 또는 "
                    "modified 로 surface."
                ),
            },
        ],
    ),

    # Q5: paperspace CIRCLE — Layout1 → DETAIL_VIEW 로 이동.
    # extract_all_layouts=True (Q5 default) + layout namespace
    # (`:PSL:layout` hash 접미) 로 layout 이동 detect.
    FixturePair(
        pair_id="13_paperspace_layout_move",
        comment=(
            "Q5 회귀 가드 — 같은 좌표의 CIRCLE 이 Layout1 (before) 에서 "
            "DETAIL_VIEW (after) 로 이동. extract_all_layouts=True 가 "
            "paperspace 추출 + layout namespace hash 가 layout 변경 detect."
        ),
        before_lines=[
            (0.0, 0.0, 1000.0, 0.0, "BEAM", 7),  # modelspace anchor
        ],
        after_lines=[
            (0.0, 0.0, 1000.0, 0.0, "BEAM", 7),
        ],
        build_extras_before=lambda doc, msp: _add_paperspace_circle(
            doc, layout_name="Layout1", center=(50.0, 50.0)
        ),
        build_extras_after=lambda doc, msp: _add_paperspace_circle(
            doc, layout_name="DETAIL_VIEW", center=(50.0, 50.0)
        ),
        # Phase Q8 round-1 (Codex follow-up) — actual behavior:
        # comparator 의 modified detection 이 좌표 동일 + radius 동일
        # 두 CIRCLE 을 매칭해 MODIFIED 로 surface (data 의 _paperspace_
        # layout 차이로 modified). deleted+added 가 아닌 1 modified.
        truth=[
            {
                "location": [50.0, 50.0],
                "change_type": "modified",
                "entity_type": "CIRCLE",
                "layer": "Q5_PSL",
                "tolerance_mm": 100.0,
                "notes": (
                    "Q5 paperspace 추출 + layout namespace 가 동작. "
                    "comparator 의 modified detection 이 좌표/radius "
                    "동일 + _paperspace_layout 차이를 modified 로 surface."
                ),
            },
        ],
    ),

    # Q6: structural BEAM 0.5mm shift — sub-mm 변경이 default 1.0mm
    # 임계값 미만이지만 structural_position_threshold=0.1mm 에서
    # detect. Q6 의 layer-aware threshold 핵심.
    FixturePair(
        pair_id="14_structural_submm_shift",
        comment=(
            "Q6 회귀 가드 — BEAM layer 의 LINE 이 0.5mm shift. default "
            "position_threshold=1.0mm 미만이지만 Q6 의 structural_position_"
            "threshold=0.1mm 가 적용되어 detect. Phase Q5 까지는 silent."
        ),
        # Phase Q-FU-2 (RV-20260510-001) — _is_pure_alignment_artifact 가
        # 이제 layer-aware threshold (structural layer = 0.1mm) 사용 →
        # BEAM 의 0.5mm shift 가 alignment 흡수되지 않고 detect.
        # fixture 14 active 전환.
        before_lines=[
            # 외곽선 (변경 없음 — alignment inlier)
            (0.0, 0.0, 1000.0, 0.0, "BEAM", 7),
            (1000.0, 0.0, 1000.0, 800.0, "BEAM", 7),
            (1000.0, 800.0, 0.0, 800.0, "BEAM", 7),
            (0.0, 800.0, 0.0, 0.0, "BEAM", 7),
            # 내부 GRID
            (500.0, 0.0, 500.0, 800.0, "GRID", 3),
            # 내부 BEAM — y=400 (sub-mm shift 대상)
            (100.0, 400.0, 900.0, 400.0, "BEAM", 7),
        ],
        after_lines=[
            (0.0, 0.0, 1000.0, 0.0, "BEAM", 7),
            (1000.0, 0.0, 1000.0, 800.0, "BEAM", 7),
            (1000.0, 800.0, 0.0, 800.0, "BEAM", 7),
            (0.0, 800.0, 0.0, 0.0, "BEAM", 7),
            (500.0, 0.0, 500.0, 800.0, "GRID", 3),
            # 동일 BEAM 이 y=400.5 으로 0.5mm shift (sub-mm)
            (100.0, 400.5, 900.0, 400.5, "BEAM", 7),
        ],
        truth=[
            {
                "location": [500.0, 400.25],
                "change_type": "modified",
                "entity_type": "LINE",
                "layer": "BEAM",
                "tolerance_mm": 5.0,  # sub-mm shift 라 매칭 tolerance 너그러이
                "notes": (
                    "BEAM 0.5mm sub-mm shift — Q6 structural_position_"
                    "threshold=0.1mm 적용 시 detect. default 1.0mm 만 "
                    "사용하던 Phase Q5 까지는 silent (사용자 보고 누락 "
                    "케이스 직접 재현)."
                ),
            },
        ],
    ),

    # Q7: REVERSE-PUMP layer change — *REV* fnmatch substring 이
    # 잘못 매칭하던 false-positive. Q7 SSoT 가 word-boundary 매칭으로
    # silent drop 방지.
    #
    # 주의: 이 fixture 는 ignore_title_block_layers 옵션이 활성된
    # 환경 (사용자가 노이즈 필터를 켠 시나리오) 을 가정. verify_phase_o_
    # accuracy.py 가 이 옵션을 켜고 비교 → REVERSE-PUMP layer 의
    # 변경이 silent drop 안 되어야 함.
    FixturePair(
        pair_id="15_reverse_layer_false_positive_guard",
        comment=(
            "Q7 회귀 가드 — REVERSE-PUMP layer 의 LINE 5mm 이동. "
            "*REV* fnmatch substring 이 'REVERSE' 까지 잡아 silent drop "
            "하던 false-positive. Q7 의 word-boundary regex SSoT 가 "
            "REVERSE 를 title-block 으로 분류 안 함 → 정상 detect."
        ),
        before_lines=[
            # 외곽선 (변경 없음)
            (0.0, 0.0, 1000.0, 0.0, "BEAM", 7),
            (1000.0, 0.0, 1000.0, 800.0, "BEAM", 7),
            # REVERSE-PUMP layer 의 LINE (5mm shift 대상)
            (200.0, 200.0, 800.0, 200.0, "REVERSE-PUMP", 7),
        ],
        after_lines=[
            (0.0, 0.0, 1000.0, 0.0, "BEAM", 7),
            (1000.0, 0.0, 1000.0, 800.0, "BEAM", 7),
            # 5mm shift (default 1mm threshold 충분히 초과)
            (200.0, 205.0, 800.0, 205.0, "REVERSE-PUMP", 7),
        ],
        truth=[
            {
                "location": [500.0, 202.5],
                "change_type": "modified",
                "entity_type": "LINE",
                "layer": "REVERSE-PUMP",
                "tolerance_mm": 50.0,
                "notes": (
                    "REVERSE-PUMP layer 의 5mm shift — Q7 까지는 *REV* "
                    "fnmatch 가 REVERSE 까지 매칭해 ignore_title_block_"
                    "layers=True 시 silent drop. Q7 SSoT 의 word-boundary "
                    "regex 가 REVERSE 를 title-block 분류 안 함 → 정상 "
                    "detect (사용자 보고 false-positive 케이스 재현)."
                ),
            },
        ],
    ),
    FixturePair(
        pair_id="06_cosmetic_heavy_with_real",
        comment=(
            "다수 cosmetic 변경 (4개 LINE color flip) + 실제 1개 추가 — "
            "dialog 추천 프리셋이 cosmetic noise 만 제거하고 real 변경은 "
            "보존함을 검증 (verify --compare 의 핵심 데모 케이스)"
        ),
        before_lines=_DEFAULT_LINES,
        # 4개 LINE 의 color 만 변경 (좌표/layer 동일) + 1개 real LINE 추가
        after_lines=[
            (0.0, 0.0, 1000.0, 0.0, "BEAM", 8),         # color 7 → 8
            (1000.0, 0.0, 1000.0, 800.0, "BEAM", 8),    # color 7 → 8
            (1000.0, 800.0, 0.0, 800.0, "BEAM", 7),     # 변경 없음
            (0.0, 800.0, 0.0, 0.0, "BEAM", 7),          # 변경 없음
            (0.0, 400.0, 1000.0, 400.0, "BEAM", 8),     # color 7 → 8
            (500.0, 0.0, 500.0, 800.0, "GRID", 4),      # color 3 → 4
            # NEW — real 추가 (대각선)
            (0.0, 0.0, 1000.0, 800.0, "DIAG", 1),
        ],
        truth=[
            # 정답: cosmetic 4건은 무시(suppress=True), 실제 추가 1건만
            {
                "location": [500.0, 400.0],
                "change_type": "added",
                "layer": "DIAG",
                "tolerance_mm": 50.0,
                "notes": "대각선 LINE 추가 (real change — cosmetic noise 와 함께)",
            },
        ],
    ),
]


# ---------------------------------------------------------------------------
# DXF 파일 생성
# ---------------------------------------------------------------------------


_FIXED_DXF_TIMESTAMP = "2451545.0000000000"
_FIXED_FINGERPRINT_GUID = "{00000000-0000-0000-0000-000000000001}"
_FIXED_VERSION_GUID = "{00000000-0000-0000-0000-000000000002}"
_FIXED_EZDXF_METADATA = "fixture @ 2000-01-01T00:00:00+00:00"
_DYNAMIC_TIME_HEADERS = {"$TDCREATE", "$TDUCREATE", "$TDUPDATE", "$TDUUPDATE"}


def _write_utf8_text(path: Path, text: str, *, newline: str = "\n", errors: str = "strict") -> None:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if newline != "\n":
        normalized = normalized.replace("\n", newline)
    with path.open("w", encoding="utf-8", errors=errors, newline="") as handle:
        handle.write(normalized)


def _normalize_dxf_dynamic_metadata(path: Path) -> None:
    """Remove ezdxf save-time metadata so golden fixtures stay idempotent."""

    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()

    for index, line in enumerate(lines):
        token = line.strip()
        if token in _DYNAMIC_TIME_HEADERS and index + 2 < len(lines):
            lines[index + 2] = _FIXED_DXF_TIMESTAMP
        elif token == "$FINGERPRINTGUID" and index + 2 < len(lines):
            lines[index + 2] = _FIXED_FINGERPRINT_GUID
        elif token == "$VERSIONGUID" and index + 2 < len(lines):
            lines[index + 2] = _FIXED_VERSION_GUID
        elif re.fullmatch(r"\d+(?:\.\d+){1,3} @ .+", token):
            lines[index] = _FIXED_EZDXF_METADATA

    trailing_newline = newline if text.endswith(("\n", "\r\n")) else ""
    _write_utf8_text(path, newline.join(lines) + trailing_newline, newline=newline, errors="surrogateescape")


def _write_dxf(
    path: Path,
    lines: List[Tuple[float, float, float, float, str, int]],
    *,
    build_extras: Optional[Callable[[Any, Any], None]] = None,
) -> None:
    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()

    # 필요한 layer 등록
    layers_seen: dict[str, int] = {}
    for (_, _, _, _, layer, color) in lines:
        if layer not in layers_seen:
            layers_seen[layer] = color
            if layer not in doc.layers:
                doc.layers.add(name=layer, color=color)

    for (x1, y1, x2, y2, layer, color) in lines:
        msp.add_line(
            (x1, y1),
            (x2, y2),
            dxfattribs={"layer": layer, "color": color},
        )

    # Phase O Commit 4 — fixture 별 INSERT/ATTRIB 등 추가 entity 후처리
    if build_extras is not None:
        build_extras(doc, msp)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(path))
    _normalize_dxf_dynamic_metadata(path)


def _write_truth(
    path: Path,
    truth: List[dict],
    comment: str,
    *,
    expected_to_fail: bool = False,
    expected_to_fail_reason: str = "",
) -> None:
    payload: dict = {
        "comment": comment,
        "expected_changes": truth,
    }
    if expected_to_fail:
        payload["expected_to_fail"] = True
        if expected_to_fail_reason:
            payload["expected_to_fail_reason"] = expected_to_fail_reason
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_utf8_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _write_manifest(output_dir: Path, pairs: List[FixturePair]) -> None:
    """YAML 작성 — 의존성 최소화 위해 손으로 직렬화 (PyYAML 불필요)."""
    lines = ["version: 1", "pairs:"]
    for p in pairs:
        rel = f"dxf/{p.pair_id}"
        lines.append(f"  - pair_id: {p.pair_id}")
        lines.append(f"    format: dxf")
        lines.append(f"    before_path: {rel}/before.dxf")
        lines.append(f"    after_path: {rel}/after.dxf")
        lines.append(f"    expected_changes_path: {rel}/truth.json")
        lines.append(f"    comment: {json.dumps(p.comment, ensure_ascii=False)}")
    manifest = "\n".join(lines) + "\n"

    manifest_path = output_dir / "manifest.yaml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    _write_utf8_text(manifest_path, manifest)


def build(output_dir: Path, *, clean: bool = False) -> int:
    if clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for p in PAIRS:
        pair_dir = output_dir / "dxf" / p.pair_id
        _write_dxf(
            pair_dir / "before.dxf", p.before_lines,
            build_extras=p.build_extras_before,
        )
        _write_dxf(
            pair_dir / "after.dxf", p.after_lines,
            build_extras=p.build_extras_after,
        )
        _write_truth(
            pair_dir / "truth.json", p.truth, p.comment,
            expected_to_fail=p.expected_to_fail,
            expected_to_fail_reason=p.expected_to_fail_reason,
        )
        print(f"  [OK] {p.pair_id}  (truth: {len(p.truth)} change(s))")

    _write_manifest(output_dir, PAIRS)
    print(f"\n[done] {len(PAIRS)} pairs written, manifest: {output_dir / 'manifest.yaml'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase O1 golden DXF fixture builder")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tests/data/comparison/golden"),
        help="출력 디렉토리 (default: tests/data/comparison/golden)",
    )
    parser.add_argument("--clean", action="store_true", help="기존 fixture 삭제 후 재생성")
    args = parser.parse_args()

    print(f"Building Phase O1 golden DXF fixtures → {args.output_dir}")
    return build(args.output_dir.resolve(), clean=args.clean)


if __name__ == "__main__":
    sys.exit(main())
