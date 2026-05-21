# -*- coding: utf-8 -*-
"""Sanitize strings for safe UTF-8 serialization.

Background: when the GUI renders zone crops on a Windows Korean locale,
file paths can carry **lone surrogate codepoints** (U+D800–U+DFFF) that
crawled in via filesystem APIs translating CP949↔UTF-16. Those code
points are valid in Python str (they survive in-process) but raise
``UnicodeEncodeError: surrogates not allowed`` the moment we try to
write them as UTF-8 to a JSON manifest.

The user-visible symptom is::

    선택 구역 렌더 실패 - 상대 위치 표시를 유지합니다.
    'utf-8' codec can't encode character 'Wudced' in position 99: surrogates not allowed

This module gives us one place to defang those characters before any
JSON / file write. It is intentionally lossy: if we hit a lone
surrogate we already lost information at the OS layer; the priority is
not propagating the crash to the rest of the pipeline.
"""

from __future__ import annotations

from typing import Any


def safe_unicode(value: Any) -> Any:
    """Return a UTF-8-safe version of ``value``.

    - ``str``: re-encode with the ``replace`` error handler so any lone
      surrogates become U+FFFD REPLACEMENT CHARACTER. Python str instances
      that already round-trip cleanly through utf-8 come back unchanged.
    - ``bytes``: decode with ``replace`` so the caller never has to think
      about which codec produced them.
    - ``Mapping`` / ``Sequence`` (excluding ``str``/``bytes``): recurse so
      we sanitize every leaf in nested viewer-manifest payloads.
    - Anything else: returned untouched (numbers, ``None``, dataclasses,
      etc. are not at risk).

    The implementation uses the cheapest possible round-trip for the
    common case — strings without surrogates re-encode and decode to the
    same object identity-equal string. We only allocate when sanitization
    actually changes content.
    """

    if value is None:
        return None
    if isinstance(value, str):
        return _safe_str(value)
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("utf-8", "replace")
    if isinstance(value, dict):
        # Sanitize both keys and values; JSON object keys must be str.
        return {_safe_str(str(k)): safe_unicode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        sanitized = [safe_unicode(item) for item in value]
        return type(value)(sanitized) if isinstance(value, tuple) else sanitized
    return value


def _safe_str(text: str) -> str:
    """Replace lone surrogate codepoints in ``text`` with U+FFFD.

    Fast path: if the string is already utf-8 clean (the overwhelmingly
    common case in Korean industrial CAD pipelines that DO normalize
    paths), we return it unchanged. Otherwise we round-trip through
    utf-8 with ``replace`` so the caller's downstream write succeeds.
    """

    try:
        text.encode("utf-8")
        return text
    except UnicodeEncodeError:
        return text.encode("utf-8", "replace").decode("utf-8")
