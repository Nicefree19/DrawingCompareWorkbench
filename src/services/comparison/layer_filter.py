# -*- coding: utf-8 -*-
"""ADR/RELIABILITY P0-4 — shared layer-pattern matcher for extent filtering.

Far-flung review markup (e.g. ``!검토``, ``*_OLD``) inflates a drawing's
extents so the real 도곽 renders as a speck. Both the normalizer (comparison
extents, ``drawing_normalizer``) and the raster renderer (viewer-frame extents,
``dxf_renderer``) need to exclude such layers when computing bounds. This is the
single fnmatch-style matcher both call — same semantics as
``LayerPriorityConfig.should_ignore`` (case-insensitive fnmatch).

Pure stdlib, no Qt/ezdxf — safe to import anywhere.
"""

from __future__ import annotations

import fnmatch
from typing import Iterable, Optional


def layer_matches_any(layer_name: Optional[str], patterns: Iterable[str]) -> bool:
    """True if ``layer_name`` matches any fnmatch pattern (case-insensitive).

    Empty/None name or empty patterns → ``False`` (i.e. "keep"), so the default
    (no patterns) is a no-op and never changes existing extents.
    """

    if not layer_name or not patterns:
        return False
    up = str(layer_name).upper()
    for pattern in patterns:
        if pattern and fnmatch.fnmatch(up, str(pattern).upper()):
            return True
    return False


__all__ = ["layer_matches_any"]
