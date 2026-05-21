# -*- coding: utf-8 -*-
"""Regression tests for the lone-surrogate UTF-8 sanitizer.

The user-reported symptom these tests pin against:

    선택 구역 렌더 실패 - 상대 위치 표시를 유지합니다.
    'utf-8' codec can't encode character 'Wudced' in position 99:
    surrogates not allowed

Trigger: Korean filenames from Windows file APIs sometimes carry lone
surrogate codepoints (U+D800–U+DFFF) that survive ``str`` operations
but explode at the moment of UTF-8 encoding for JSON serialization.
"""

from __future__ import annotations

import json

import pytest

from src.services.comparison.safe_unicode import safe_unicode


# A clean Korean string round-trips unchanged.
def test_safe_unicode_passthrough_for_clean_korean() -> None:
    text = "평택_복합 5동 건축_구조 지상 3층"
    assert safe_unicode(text) == text


# Lone surrogate must NOT raise; gets replaced with U+FFFD.
def test_safe_unicode_replaces_lone_surrogate() -> None:
    bad = "before\ud800after"  # U+D800 is a lone high surrogate
    sanitized = safe_unicode(bad)
    assert "\ud800" not in sanitized
    # Most importantly: the result encodes to UTF-8 cleanly
    sanitized.encode("utf-8")  # would raise if surrogates remained


# Manifest-shape payload (the actual failure mode) survives JSON serialization.
def test_safe_unicode_makes_manifest_with_surrogate_path_serializable() -> None:
    manifest = {
        "schema_version": 1,
        "pairs": [
            {
                "pair_id": "pair_x",
                # Simulates a Korean filename that pulled in a CP949 surrogate
                "source_a": "C:\\drawings\\\ud83cWudced_평택_3층.dwg",
                "source_b": "C:\\drawings\\B\\\udc00 변경 후.dwg",
                "before_image": "",
                "after_image": "",
                "render_status": "rendered",
            }
        ],
    }
    safe = safe_unicode(manifest)
    # Round-trip via JSON would have raised "surrogates not allowed" without
    # the sanitizer — that's the regression we're pinning.
    text = json.dumps(safe, ensure_ascii=False, indent=2)
    assert "pair_x" in text
    assert "rendered" in text
    # Reload to confirm valid JSON
    reloaded = json.loads(text)
    assert reloaded["pairs"][0]["pair_id"] == "pair_x"


# Recursive sanitization through nested structures.
def test_safe_unicode_recurses_through_dict_and_list() -> None:
    payload = {
        "outer": {
            "list": ["clean", "bad\ud800"],
            "tuple_value": ("ok", "broken\ud8ff"),
        },
        "list_of_dicts": [
            {"path": "ok"},
            {"path": "lone\udead"},
        ],
        "number": 42,
        "none": None,
    }
    safe = safe_unicode(payload)
    json.dumps(safe, ensure_ascii=False)  # would raise without recursion
    assert safe["number"] == 42
    assert safe["none"] is None
    # Tuples should stay tuples (some downstream consumers depend on it)
    assert isinstance(safe["outer"]["tuple_value"], tuple)


# Bytes input gets decoded with replacement for non-utf8 bytes.
def test_safe_unicode_handles_bytes_input() -> None:
    raw = b"\xed\x95\x9c\xea\xb5\xad\xff\xfe"  # "한국" + invalid bytes
    out = safe_unicode(raw)
    assert isinstance(out, str)
    out.encode("utf-8")  # must encode cleanly


# Non-string scalars are passed through untouched.
def test_safe_unicode_passes_through_scalars() -> None:
    assert safe_unicode(42) == 42
    assert safe_unicode(3.14) == 3.14
    assert safe_unicode(True) is True
    assert safe_unicode(None) is None


# Dict keys with surrogates are also sanitized (JSON requires str keys).
def test_safe_unicode_sanitizes_dict_keys() -> None:
    bad = {"clean_key": 1, "bad\ud8ee_key": 2}
    safe = safe_unicode(bad)
    json.dumps(safe, ensure_ascii=False)  # encode must succeed
    # Both entries survive (with sanitized key for the bad one)
    assert len(safe) == 2
