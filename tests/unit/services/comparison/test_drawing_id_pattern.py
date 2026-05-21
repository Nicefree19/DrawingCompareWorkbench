# -*- coding: utf-8 -*-
"""Unit tests for the shared drawing-id pattern (Phase H1 follow-up).

Confirms file-level (drawing_batch) and page-level (page_descriptor)
both behave consistently against the same regex source.
"""

from __future__ import annotations

import re

import pytest

from src.services.comparison.drawing_id_pattern import (
    DRAWING_NUMBER_PATTERN,
    DRAWING_NUMBER_PATTERN_STR,
    PROJECT_DRAWING_NUMBER_PATTERN,
    extract_drawing_number,
)


def test_compiled_and_string_patterns_agree() -> None:
    # The compiled Pattern's .pattern attribute exposes the original string
    assert DRAWING_NUMBER_PATTERN.pattern == DRAWING_NUMBER_PATTERN_STR


def test_back_compat_alias_is_string() -> None:
    # drawing_batch.py historically exported PROJECT_DRAWING_NUMBER_PATTERN
    # as a string — preserve that for any external code that imports it
    assert isinstance(PROJECT_DRAWING_NUMBER_PATTERN, str)
    assert PROJECT_DRAWING_NUMBER_PATTERN == DRAWING_NUMBER_PATTERN_STR


def test_extract_basic_code() -> None:
    assert extract_drawing_number("S20-0002") == "S20-0002"


def test_extract_normalises_separator() -> None:
    assert extract_drawing_number("S20.0002") == "S20-0002"
    assert extract_drawing_number("S20_0002") == "S20-0002"
    assert extract_drawing_number("S20 0002") == "S20-0002"


def test_extract_uppercases() -> None:
    assert extract_drawing_number("s20-0002") == "S20-0002"


def test_extract_with_korean_context() -> None:
    text = "도면번호: S20-0002 (3층 평면도)"
    assert extract_drawing_number(text) == "S20-0002"


def test_extract_first_match_only() -> None:
    text = "S20-0001 referenced from S20-0002"
    assert extract_drawing_number(text) == "S20-0001"


def test_extract_returns_empty_when_none_found() -> None:
    assert extract_drawing_number("아무 번호도 없는 도면") == ""


def test_extract_handles_none_and_empty() -> None:
    assert extract_drawing_number("") == ""
    assert extract_drawing_number(None) == ""  # type: ignore[arg-type]


def test_extract_rejects_too_many_digits() -> None:
    # Pattern allows 3-5 digits; 6 digits should not match the second group
    assert extract_drawing_number("S20-123456") == ""


def test_extract_with_suffix_letter() -> None:
    assert extract_drawing_number("S20-0001A") == "S20-0001A"


def test_extract_with_long_prefix() -> None:
    assert extract_drawing_number("ABCD20-0001") == "ABCD20-0001"


def test_compiled_pattern_works_with_re_finditer() -> None:
    text = "도면 S20-0001 부터 S20-0003 까지"
    matches = [m.group(0) for m in DRAWING_NUMBER_PATTERN.finditer(text)]
    assert len(matches) == 2


def test_string_pattern_works_with_re_fullmatch() -> None:
    # drawing_batch.py uses re.fullmatch(PROJECT_DRAWING_NUMBER_PATTERN, text)
    assert re.fullmatch(PROJECT_DRAWING_NUMBER_PATTERN, "S20-0002") is not None
    assert re.fullmatch(PROJECT_DRAWING_NUMBER_PATTERN, "S20-0002 extra") is None


def test_consistency_across_modules() -> None:
    """The same drawing number string must produce the same result whether
    extracted via page_descriptor or the shared helper."""

    from src.services.comparison.page_descriptor import (
        extract_drawing_number as page_extract,
    )

    cases = [
        "S20-0002",
        "도면번호: S20-0002 평택 5동",
        "S20.0002",
        "S20_0002",
        "s20-0002",
        "ABCD20-12345",
        "",
    ]
    for text in cases:
        assert extract_drawing_number(text) == page_extract(text), (
            f"divergence at {text!r}: shared={extract_drawing_number(text)!r} "
            f"vs page={page_extract(text)!r}"
        )
