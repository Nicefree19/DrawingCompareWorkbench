"""Phase Q7 (RV-20260509-002) — title-block layer 패턴 anchored regex 회귀 가드.

사용자 보고: "변경사항 미탐지가 많다." Phase Q1-Q6 완료 후에도
``ignore_title_block_layers=True`` 시 fnmatch substring 매칭이
``REVERSE``, ``OVERRIDE``, ``REVENUE``, ``SHEETPILE-WALL`` 같은
실제 구조 layer 까지 silent drop. Q7 의 SSoT 모듈
(``title_block_layer_patterns.py``) 가 word-boundary regex 로
false-positive 제거.

Q7 변경:
1. ``title_block_layer_patterns`` (신규 SSoT 모듈) — 영문 word-boundary
   regex + 한국어 substring 둘 다 지원.
2. ``ChangeZoneOptions.title_block_layer_patterns`` default 가
   ``("*TITLE*", "*BORDER*", ...)`` → ``()`` 빈 tuple. SSoT 가
   default 모든 표준 패턴 처리. 사용자 커스텀 fnmatch 만 OR 추가.
3. ``_change_ignored`` 가 SSoT helper 우선 적용 → backward-compat
   fnmatch fallback.
"""
from __future__ import annotations

import pytest

from src.services.comparison.title_block_layer_patterns import (
    is_title_block_layer,
    any_title_block_layer,
    TITLE_BLOCK_REGEX_EN,
    TITLE_BLOCK_PATTERNS_KOREAN,
)


class TestTitleBlockMatchPositive:
    """Q7 — 정상 title-block layer 가 매칭되어야 함."""

    @pytest.mark.parametrize("layer", [
        "TITLE", "TITLE_BLOCK", "TITLEBLOCK", "TITLE-BLOCK",
        "title", "title_block", "Title-Block",  # case-insensitive
        "TITLE-BAR", "DRAWING-TITLE", "DWG-TITLE",
        "BORDER", "DRAWING-BORDER", "DWG-BORDER", "DWG_BORDER",
        "REV", "REV-A", "REV_NEW", "01-REV", "REV-1",
        "REVISION", "REVISION-NOTE", "REVISION_BLOCK",
        "SHEET", "SHEET-1", "SHEET_INDEX",
        "STAMP", "ENGINEER-STAMP", "STAMP-AREA",
        "DRAWING-FRAME", "DRAWING_FRAME", "DWG-FRAME", "DWG_FRAME",
    ])
    def test_standard_title_block_layers_match(self, layer):
        assert is_title_block_layer(layer), f"{layer!r} should match title-block"

    @pytest.mark.parametrize("layer", [
        "표제란", "도면틀", "도장", "개정", "도면번호", "도면제목",
        "표제란-A", "도면틀_1F", "개정-001",  # with separator
    ])
    def test_korean_title_block_layers_match(self, layer):
        assert is_title_block_layer(layer), (
            f"한국어 {layer!r} should match title-block (substring)"
        )


class TestTitleBlockMatchNegativeCriticalFalsePositive:
    """Q7 — 사용자 보고 누락 핵심: 구조/일반 layer 가 잘못 매칭 안 되어야."""

    @pytest.mark.parametrize("layer", [
        # *REV* 가 substring 으로 잡던 critical false-positive
        "REVERSE", "REVERSE-PUMP", "REVERSE_LAYER",
        "REVENUE", "REVENUE-CHART",
        "REVOLUTION", "REVOLUTION-AXIS",
        "OVERRIDE", "OVERRIDE-MARK",
        "REVAMP", "REVAMP-2024",
        "REVERT", "REVERT-COMMIT",
        "REVERSAL",
        # *BORDER* 가 substring 으로 잡던 false-positive
        "BORDERLINE", "BORDERLINE-WALL", "BORDERED-BEAM",
        # *SHEET* 가 substring 으로 잡던 false-positive
        "SHEETPILE", "SHEETPILE-WALL", "SHEETPILE_RETAINING",
        "SHEETROCK", "SHEET-METAL", "SHEETMETAL",
        "WORKSHEET", "WORKSHEET-LAYER",
        # *STAMP* 가 substring 으로 잡던 false-positive
        "STAMPED", "STAMPED-CONCRETE", "STAMPED_FOUNDATION",
        # *TITLE* 가 substring 으로 잡던 false-positive
        "SUBTITLE", "SUBTITLE-TEXT",  # 가능성 낮지만 가드
        "ENTITLED", "ENTITLED-AREA",
        # 일반 구조 layer (혼동 가능 케이스)
        "BEAM-1F", "COL-2F", "WALL-EW",
        "FOUNDATION-MAT", "PILE-CAP",
        "기둥-1F", "보-2F", "벽-A1",  # 한국어 구조
    ])
    def test_non_title_block_layers_do_not_match(self, layer):
        assert not is_title_block_layer(layer), (
            f"{layer!r} should NOT match title-block — Codex/사용자 reported "
            f"false-positive (Phase Q7)"
        )


class TestEmptyAndEdgeCases:
    """Q7 — empty/None/whitespace edge case."""

    def test_empty_string(self):
        assert is_title_block_layer("") is False

    def test_whitespace_only(self):
        assert is_title_block_layer("   ") is False

    def test_none_returns_false(self):
        assert is_title_block_layer(None) is False  # type: ignore[arg-type]

    def test_layer_with_leading_trailing_whitespace(self):
        assert is_title_block_layer("  TITLE_BLOCK  ") is True
        assert is_title_block_layer("  REVERSE  ") is False


class TestAnyHelper:
    """Q7 — any_title_block_layer batch helper."""

    def test_any_returns_true_when_one_matches(self):
        layers = ["BEAM", "COL", "TITLE_BLOCK", "WALL"]
        assert any_title_block_layer(layers) is True

    def test_any_returns_false_when_none_match(self):
        layers = ["BEAM", "COL", "REVERSE", "WALL"]  # REVERSE 는 false-positive 가드
        assert any_title_block_layer(layers) is False

    def test_any_empty_list(self):
        assert any_title_block_layer([]) is False


class TestRegexExposed:
    """Q7 — 외부 caller (export profile 등) 가 regex 직접 참조 가능."""

    def test_regex_compiled(self):
        assert TITLE_BLOCK_REGEX_EN.flags & 2  # re.IGNORECASE = 2

    def test_korean_patterns_tuple(self):
        assert "표제란" in TITLE_BLOCK_PATTERNS_KOREAN
        assert "도면틀" in TITLE_BLOCK_PATTERNS_KOREAN
        assert "개정" in TITLE_BLOCK_PATTERNS_KOREAN


class TestChangeZonesIntegration:
    """Q7 — change_zones._change_ignored 가 SSoT 사용해서 false-positive 제거."""

    def test_change_zone_options_default_is_empty_tuple(self):
        """Q7: ChangeZoneOptions.title_block_layer_patterns default 는
        빈 tuple (SSoT 가 표준 패턴 처리). 기존엔 5개 fnmatch 패턴."""
        from src.services.comparison.change_zones import ChangeZoneOptions
        opts = ChangeZoneOptions()
        assert opts.title_block_layer_patterns == (), (
            "Q7: default 는 빈 tuple. 기존 ('*TITLE*', '*BORDER*', "
            "'*SHEET*', '*REV*', '*STAMP*') 가 substring 매칭으로 "
            "REVERSE/SHEETPILE/STAMPED 같은 layer false-positive 발생"
        )

    def test_change_ignored_uses_ssot_for_title(self):
        """ignore_title_block_layers=True 시 TITLE_BLOCK layer 가 무시됨."""
        from src.services.comparison.base import ChangeRecord, ChangeType
        from src.services.comparison.change_zones import (
            ChangeZoneOptions,
            _change_ignored,
        )
        opts = ChangeZoneOptions(ignore_title_block_layers=True)
        record = ChangeRecord(
            key="r1",
            change_type=ChangeType.MODIFIED,
            metadata={"layer": "TITLE_BLOCK"},
        )
        assert _change_ignored(record, opts) is True

    def test_change_ignored_does_not_drop_reverse_layer(self):
        """[CRITICAL Q7] REVERSE-PUMP layer 가 SSoT 로 매칭 안 돼서
        실제 구조 변경이 silent drop 되지 않음."""
        from src.services.comparison.base import ChangeRecord, ChangeType
        from src.services.comparison.change_zones import (
            ChangeZoneOptions,
            _change_ignored,
        )
        opts = ChangeZoneOptions(ignore_title_block_layers=True)
        record = ChangeRecord(
            key="r2",
            change_type=ChangeType.MODIFIED,
            metadata={"layer": "REVERSE-PUMP"},
        )
        assert _change_ignored(record, opts) is False, (
            "REVERSE-PUMP 는 title-block 아님 — Q7 의 핵심 false-positive "
            "방지. 기존 *REV* fnmatch 가 silent drop 하던 케이스."
        )

    def test_change_ignored_does_not_drop_sheetpile_layer(self):
        """[CRITICAL Q7] SHEETPILE-WALL (강널말뚝벽) 이 silent drop 안 됨."""
        from src.services.comparison.base import ChangeRecord, ChangeType
        from src.services.comparison.change_zones import (
            ChangeZoneOptions,
            _change_ignored,
        )
        opts = ChangeZoneOptions(ignore_title_block_layers=True)
        record = ChangeRecord(
            key="r3",
            change_type=ChangeType.MODIFIED,
            metadata={"layer": "SHEETPILE-WALL"},
        )
        assert _change_ignored(record, opts) is False, (
            "SHEETPILE-WALL 는 강구조 부재 — Q7 false-positive 방지"
        )

    def test_change_ignored_does_not_drop_stamped_concrete(self):
        """STAMPED-CONCRETE 가 silent drop 안 됨."""
        from src.services.comparison.base import ChangeRecord, ChangeType
        from src.services.comparison.change_zones import (
            ChangeZoneOptions,
            _change_ignored,
        )
        opts = ChangeZoneOptions(ignore_title_block_layers=True)
        record = ChangeRecord(
            key="r4",
            change_type=ChangeType.MODIFIED,
            metadata={"layer": "STAMPED-CONCRETE"},
        )
        assert _change_ignored(record, opts) is False

    def test_change_ignored_korean_title_block(self):
        """한국어 표제란 layer 가 SSoT 로 ignore."""
        from src.services.comparison.base import ChangeRecord, ChangeType
        from src.services.comparison.change_zones import (
            ChangeZoneOptions,
            _change_ignored,
        )
        opts = ChangeZoneOptions(ignore_title_block_layers=True)
        record = ChangeRecord(
            key="r5",
            change_type=ChangeType.MODIFIED,
            metadata={"layer": "표제란"},
        )
        assert _change_ignored(record, opts) is True

    def test_change_ignored_with_user_custom_fnmatch(self):
        """사용자 커스텀 fnmatch 패턴이 backward-compat 으로 동작."""
        from src.services.comparison.base import ChangeRecord, ChangeType
        from src.services.comparison.change_zones import (
            ChangeZoneOptions,
            _change_ignored,
        )
        opts = ChangeZoneOptions(
            ignore_title_block_layers=True,
            title_block_layer_patterns=("*MY_COMPANY_TB*",),
        )
        record = ChangeRecord(
            key="r6",
            change_type=ChangeType.MODIFIED,
            metadata={"layer": "MY_COMPANY_TB_HEADER"},
        )
        assert _change_ignored(record, opts) is True

    def test_change_ignored_disabled_when_flag_off(self):
        """ignore_title_block_layers=False 면 TITLE_BLOCK 도 무시 안 됨."""
        from src.services.comparison.base import ChangeRecord, ChangeType
        from src.services.comparison.change_zones import (
            ChangeZoneOptions,
            _change_ignored,
        )
        opts = ChangeZoneOptions(ignore_title_block_layers=False)
        record = ChangeRecord(
            key="r7",
            change_type=ChangeType.MODIFIED,
            metadata={"layer": "TITLE_BLOCK"},
        )
        assert _change_ignored(record, opts) is False


class TestCodexRound1Fixes:
    """Phase Q7 Codex round-1 follow-up — 1 P1 finding regression guard."""

    @pytest.mark.parametrize("layer", [
        # [P1] 공백 separator 가 negative lookahead 를 우회하던 케이스
        "SHEET PILE", "SHEET PILE-WALL", "SHEET PILE_RETAINING",
        "SHEET METAL", "SHEET METAL-PANEL", "SHEET METAL_DECK",
        "SHEET ROCK", "SHEET WORK",
        # 변형: 양옆 공백 strip 후도 잡혀야 함
        " SHEET PILE ",
        # 케이스 무관
        "sheet pile", "Sheet Pile-Wall",
    ])
    def test_p1_sheet_with_whitespace_not_matched(self, layer):
        """[P1] ``SHEET PILE-WALL`` (공백 separator) 도 false-positive
        가드 (Codex Q7 round-1 finding). _TB_RB 가 \\s 받아주는데
        negative lookahead 가 [-_]만 보던 mismatch 수정."""
        assert not is_title_block_layer(layer), (
            f"{layer!r} 는 SHEET 합성어 (공백 separator) — title-block "
            "false-positive 가 되면 안됨 — Codex Q7 round-1 P1 fix"
        )

    def test_p1_change_ignored_does_not_drop_sheet_space_pile(self):
        """End-to-end: ignore_title_block_layers=True 시에도
        ``SHEET PILE-WALL`` 의 변경이 silent drop 안 됨."""
        from src.services.comparison.base import ChangeRecord, ChangeType
        from src.services.comparison.change_zones import (
            ChangeZoneOptions,
            _change_ignored,
        )
        opts = ChangeZoneOptions(ignore_title_block_layers=True)
        record = ChangeRecord(
            key="r-codex-p1",
            change_type=ChangeType.MODIFIED,
            metadata={"layer": "SHEET PILE-WALL"},
        )
        assert _change_ignored(record, opts) is False, (
            "SHEET PILE-WALL 은 강널말뚝벽 (구조 부재) — 공백 separator "
            "있어도 silent drop 되면 안됨. Codex Q7 round-1 P1 fix."
        )
