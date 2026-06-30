# -*- coding: utf-8 -*-
"""Standard AutoCAD Color Index (ACI) -> sRGB hex.

Clean-room: the ACI palette is the publicly published AutoCAD Color Index, not
derived from the ODA .dwg specification or any GPL/ODA-SDK source. This module
is intentionally standalone (no ezdxf/ODA import) so the native scene-pack
producer keeps its ODA/ezdxf-free property.

Mapped exactly: the 9 named base colors (1-9) and the standard gray ramp
(250-255). The special indices resolve to the viewer's theme ink (``None``):
``0`` BYBLOCK, ``7`` (black on light / white on dark), ``256`` BYLAYER.

The 10-249 colour cube is a documented follow-up; those indices return ``None``
(default ink) so the render is honest-partial — never a wrong fixed colour.
"""
from __future__ import annotations

from typing import Optional

BYBLOCK = 0
BYLAYER = 256

#: ACI 1-9 named base colors (sRGB hex). 7 is omitted on purpose: it is the
#: theme ink (black-on-light / white-on-dark) and resolves to ``None``.
_ACI_BASE = {
    1: "#FF0000",  # red
    2: "#FFFF00",  # yellow
    3: "#00FF00",  # green
    4: "#00FFFF",  # cyan
    5: "#0000FF",  # blue
    6: "#FF00FF",  # magenta
    8: "#808080",  # dark gray
    9: "#C0C0C0",  # light gray
}

#: Standard ACI gray ramp (250-255).
_ACI_GRAYS = {
    250: "#333333",
    251: "#5B5B5B",
    252: "#848484",
    253: "#ADADAD",
    254: "#D6D6D6",
    255: "#FFFFFF",
}

#: Indices that mean "use the layer/block/theme color" -> default viewer ink.
_DEFAULT_INK_INDICES = frozenset({BYBLOCK, 7, BYLAYER})


def aci_to_hex(index: object) -> Optional[str]:
    """Return the sRGB hex for an ACI index, or ``None`` to use the theme ink.

    ``None`` is returned for BYBLOCK/BYLAYER/black-white and for the not-yet-
    mapped 10-249 colour cube, so the caller renders with the default ink rather
    than an incorrect color.
    """

    try:
        idx = int(index)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if idx in _DEFAULT_INK_INDICES:
        return None
    if idx in _ACI_BASE:
        return _ACI_BASE[idx]
    if idx in _ACI_GRAYS:
        return _ACI_GRAYS[idx]
    return None


__all__ = ["aci_to_hex", "BYBLOCK", "BYLAYER"]
