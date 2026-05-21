# -*- coding: utf-8 -*-
"""Single source of truth for the project drawing-number regex.

The same pattern is used in two distinct call sites:

  * ``drawing_batch.py`` — file-level matching (extracts code from
    filename + title text fingerprints, then scores file pairs)
  * ``page_descriptor.py`` — page-level matching for multi-page PDFs
    (extracts code from per-page title-block text)

Both consumers used to define their own copy of the regex. Code review
flagged this as a maintainability hazard — if the pattern ever needs to
support a new code shape, both copies have to change in lockstep or
matching behaviour silently diverges between file-level and page-level
paths.

This module exports:
  - ``DRAWING_NUMBER_PATTERN_STR``  raw regex string
  - ``DRAWING_NUMBER_PATTERN``      pre-compiled :class:`re.Pattern`
  - ``PROJECT_DRAWING_NUMBER_PATTERN``  back-compat alias for
    ``drawing_batch.py``'s historical name (string form)
  - ``extract_drawing_number(text)``  shared helper that both consumers
    can call without re-implementing the regex search

The pattern matches codes like ``S20-0002``, ``B30 1234A``, ``A1.567``
— two parts separated by ``-``, ``_``, ``.``, or space, with the
prefix ``[A-Z]{1,4}[0-9]{2}`` and suffix ``[0-9]{3,5}[A-Z]?``.
"""

from __future__ import annotations

import re
from typing import Optional

#: Raw regex string. Mirrors the historical drawing_batch.py constant so
#: any external script that imports the *string* form still works.
DRAWING_NUMBER_PATTERN_STR: str = (
    r"(?<![A-Z0-9])([A-Z]{1,4}[0-9]{2})[-_. ]+([0-9]{3,5}[A-Z]?)(?![A-Z0-9])"
)

#: Pre-compiled pattern — preferred for performance when the same regex
#: is used many times in a tight loop (the page matcher's score matrix).
DRAWING_NUMBER_PATTERN: re.Pattern[str] = re.compile(DRAWING_NUMBER_PATTERN_STR)

#: Back-compat alias. ``drawing_batch.py`` historically exported this
#: name as a string; existing callers like ``re.fullmatch(PROJECT_...,
#: text)`` keep working when imported from here.
PROJECT_DRAWING_NUMBER_PATTERN: str = DRAWING_NUMBER_PATTERN_STR


def extract_drawing_number(text: str) -> str:
    """Find the first drawing code in ``text`` and return it canonicalised.

    The project regex is uppercase-only (``[A-Z]{1,4}[0-9]{2}``); we
    upper-case the input first so ``"s20-0002"`` in lowercase title text
    still matches. The returned code uses ``-`` as the canonical
    separator regardless of whether the input used ``_``, ``.``, or
    space.

    Returns ``""`` when no code is found or input is empty / None.
    """

    if not text:
        return ""
    match = DRAWING_NUMBER_PATTERN.search(text.upper())
    if not match:
        return ""
    return f"{match.group(1)}-{match.group(2)}".upper()


__all__ = [
    "DRAWING_NUMBER_PATTERN_STR",
    "DRAWING_NUMBER_PATTERN",
    "PROJECT_DRAWING_NUMBER_PATTERN",
    "extract_drawing_number",
]
