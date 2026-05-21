"""Phase Q7 (RV-20260509-002) — title-block layer 패턴 single source of truth.

Phase O 까지의 ``ChangeZoneOptions.title_block_layer_patterns`` 가
fnmatch 와일드카드 (``*REV*``, ``*BORDER*`` etc.) 만 사용. 이 와일드
카드는 substring match 라 critical false-positive 발생:

- ``*REV*`` 가 ``REVERSE-PUMP``, ``REVENUE``, ``REVOLUTION-AXIS``,
  ``OVERRIDE-LAYER``, ``REVAMP-2024``, ``REVERT-MARK`` 같은 layer 와
  매칭 → ignore_title_block_layers=True 일 때 실제 구조 변경이
  silent drop. 사용자 보고 "변경사항 미탐지가 많다" 의 직접 원인.
- ``*BORDER*`` 가 ``BORDERLINE-WALL`` (있을 가능성 낮지만 발생 가능)
  과 매칭.
- ``*SHEET*`` 가 ``SHEETPILE-WALL`` (강널말뚝) 또는 ``SHEET-METAL``
  과 매칭 → 구조 부재 silent drop.
- ``*STAMP*`` 가 ``STAMPED-CONCRETE`` (스탬프 콘크리트 마감) 와
  매칭 — 마감 변경이 무시됨.

Q7 의 해결:

1. **Word-boundary regex SSoT** — fnmatch substring 대신 word boundary
   (``\b``) + 일반 separator (hyphen, underscore) 기반 anchored regex.
2. **명시적 키워드 화이트리스트** — TITLE_BLOCK, TITLEBLOCK,
   BORDER (whole word), DRAWING_BORDER, REV (whole word),
   REVISION (whole word), STAMP (whole word), TITLE (whole word),
   SHEET 가 단독 또는 separator 와 결합한 형태만 매칭.
3. **한국어 키워드** — 표제란, 도면틀, 도장, 개정 도 매칭.
4. **Helper API** — ``is_title_block_layer(layer)`` 가 zone classifier,
   change_zones, export profile 모두 동일 SSoT 사용.

기존 ``ChangeZoneOptions.title_block_layer_patterns`` (fnmatch) 는
backward-compat 을 위해 유지하되 helper 가 우선 적용.
"""
from __future__ import annotations

import re
from typing import Iterable, Tuple


# CAD layer naming 은 ``_``, ``-`` 를 word separator 로 사용. ``\b``
# 는 ``_`` 를 word character 로 취급해 ``REV_NEW`` 의 ``REV`` 끝에
# boundary 를 안 만듦. 따라서 명시적 separator 클래스 사용.
#
# Custom boundary helper:
#   ``_TB_LB`` (lookbehind start): 문자열 시작, hyphen, underscore,
#     숫자 (앞에) 이후만 매칭
#   ``_TB_RB`` (lookahead end): 문자열 끝, hyphen, underscore (뒤에) 만 매칭
#
# 주의: SHEET_PILE 같은 합성어는 false-positive — SHEET 다음의 PILE
# 이 구조 부재 키워드라 SHEET 만 보고 title-block 으로 판단하면 안됨.
# 따라서 SHEET 뒤에 구조 키워드 (PILE, METAL, ROCK, WORK) 가 오면
# 매칭 제외 — negative lookahead 사용.
_TB_LB = r"(?:^|[-_\s\d])"   # boundary 앞 (문자열 시작 / 일반 separator / 숫자)
_TB_RB = r"(?:$|[-_\s])"     # boundary 뒤 (문자열 끝 / 일반 separator)

# SHEET 뒤에 와서 false-positive 만드는 키워드 (negative lookahead 안에서 사용).
# 합성어 패턴: SHEET-PILE, SHEET_METAL, SHEETMETAL, SHEETROCK, WORKSHEET,
# 그리고 ``SHEET PILE-WALL`` / ``SHEET METAL`` 같은 공백 separator.
# Codex Q7 round-1 P1 fix — _TB_RB 가 \s 를 separator 로 받아주는데
# negative lookahead 가 [-_]만 보던 mismatch.
_SHEET_FALSE_POSITIVE_SUFFIX = r"(?![-_\s]?(?:PILE|METAL|ROCK|WORK))"

# REV 도 false-positive suffix 가 있을 수 있지만 ``_TB_RB`` 가 separator
# 강제하므로 REVERT/REVERSE 등은 boundary 없음 → 자동 제외.

TITLE_BLOCK_REGEX_EN: re.Pattern[str] = re.compile(
    rf"{_TB_LB}TITLE[_-]?BLOCK{_TB_RB}|"
    rf"{_TB_LB}TITLEBLOCK{_TB_RB}|"
    rf"{_TB_LB}TITLE{_TB_RB}|"
    rf"{_TB_LB}BORDER{_TB_RB}|"
    rf"{_TB_LB}DRAWING[_-]?BORDER{_TB_RB}|"
    rf"{_TB_LB}DWG[_-]?BORDER{_TB_RB}|"
    rf"{_TB_LB}REV{_TB_RB}|"
    rf"{_TB_LB}REVISION{_TB_RB}|"
    rf"{_TB_LB}REVISION[_-]?BLOCK{_TB_RB}|"
    rf"{_TB_LB}SHEET{_SHEET_FALSE_POSITIVE_SUFFIX}{_TB_RB}|"
    rf"{_TB_LB}STAMP{_TB_RB}|"
    rf"{_TB_LB}DRAWING[_-]?FRAME{_TB_RB}|"
    rf"{_TB_LB}DWG[_-]?FRAME{_TB_RB}",
    re.IGNORECASE,
)


# 한국어 substring (regex 도 잡지만 빠른 short-circuit). 한국어는
# 단어 boundary 가 영문과 달라 substring match 가 안전 (조사 추가는
# 일반적으로 layer 이름에 안 함).
TITLE_BLOCK_PATTERNS_KOREAN: Tuple[str, ...] = (
    "표제란",   # title block (Korean)
    "도면틀",   # drawing frame
    "도장",     # stamp/seal
    "개정",     # revision
    "도면번호", # drawing number
    "도면제목", # drawing title
)


def is_title_block_layer(layer: str) -> bool:
    """Layer 이름이 title-block / 표제란 패턴에 매칭되는지 검사.

    Word-boundary anchored regex 사용 → ``REVERSE``, ``OVERRIDE``,
    ``REVENUE``, ``SHEETPILE`` 같은 false-positive 미발생.

    Args:
        layer: layer 이름. 케이스 민감 X.

    Returns:
        True if layer 가 title-block 패턴에 매칭.
    """
    if not layer:
        return False
    layer_str = str(layer).strip()
    if not layer_str:
        return False
    # 한국어 substring (빠른 short-circuit)
    for kr in TITLE_BLOCK_PATTERNS_KOREAN:
        if kr in layer_str:
            return True
    # 영문 word-boundary regex
    return bool(TITLE_BLOCK_REGEX_EN.search(layer_str))


def any_title_block_layer(layers: Iterable[str]) -> bool:
    """layer 시퀀스 중 하나라도 title-block 패턴이면 True."""
    return any(is_title_block_layer(layer) for layer in layers)


__all__ = [
    "TITLE_BLOCK_REGEX_EN",
    "TITLE_BLOCK_PATTERNS_KOREAN",
    "is_title_block_layer",
    "any_title_block_layer",
]
