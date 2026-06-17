"""Pure render-decision helpers for the workbench (MONO-4 #7 slice A).

The render-callback cluster is mostly worker-result + viewport-widget
orchestration (the dead-island territory the audit flagged); these three
functions are the only cleanly-pure decision pieces — source-path validity and
active zone-render request-id matching — so they live and are unit-tested here.
``DrawingCompareWorkbenchV2`` keeps thin delegators that thread its state in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Tuple

from src.gui.source_path_repair import has_lossy_path_text
from src.gui.workbench_viewer_source import _is_redacted_artifact_path
from src.services.comparison.drawing_batch import SUPPORTED_DRAWING_EXTENSIONS

#: ``(pair_id, zone_id, request_id)`` of the in-flight selected-zone render.
ActiveRequest = Optional[Tuple[str, str, str]]


def is_usable_zone_render_source(value: Any) -> bool:
    """True when ``value`` points at an existing, supported, non-redacted,
    non-lossy drawing file the zone renderer can actually read."""
    text = str(value or "").strip()
    if not text or _is_redacted_artifact_path(text) or has_lossy_path_text(text):
        return False
    try:
        path = Path(text)
        return path.is_file() and path.suffix.lower() in SUPPORTED_DRAWING_EXTENSIONS
    except (OSError, ValueError, RuntimeError):
        return False


def active_zone_render_request_id(
    active: ActiveRequest, pair_id: str, zone_id: str
) -> str:
    """The in-flight request-id for ``(pair_id, zone_id)``, or "" if the active
    request is for a different pair/zone (or none)."""
    if active and active[0] == pair_id and active[1] == zone_id:
        return str(active[2] or "")
    return ""


def is_current_zone_render_request(
    current_pair: str,
    current_zone: str,
    active: ActiveRequest,
    pair_id: str,
    zone_id: str,
    request_id: str = "",
) -> bool:
    """True when ``(pair_id, zone_id)`` is the user's current selection and —
    if a ``request_id`` is given — it matches the in-flight active request
    (stale generations return False)."""
    if pair_id != current_pair or zone_id != current_zone:
        return False
    if not request_id:
        return True
    return bool(active and active == (pair_id, zone_id, request_id))


#: Qt's ``QImageIOHandler`` rejects any single image whose RGBA backing buffer
#: would exceed 256 MB (~67 M px at 4 bytes/px). The SVG vector overlay is
#: rasterised by QML at ``sourceSize``; for a zone that fills a large fraction
#: of the 8000 px-wide render, the naive ``displayed × 4`` grid blew past that
#: limit and Qt SILENTLY dropped the overlay — the change cloud vanished from
#: the viewer (live-test 2026-06-17, POT BEARING zones). Cap the grid to a
#: budget safely under the limit, preserving aspect ratio.
SVG_SOURCE_BUDGET_PX = 40_000_000  # ~160 MB RGBA, comfortably under Qt's 256 MB
SVG_SOURCE_MULTIPLIER = 4  # 4× displayed size keeps text/thin lines sharp to ~4× zoom
SVG_SOURCE_FLOOR = 2048    # never request a grid coarser than this on either axis


def capped_svg_source_size(
    width_px: float,
    height_px: float,
    *,
    multiplier: int = SVG_SOURCE_MULTIPLIER,
    budget_px: int = SVG_SOURCE_BUDGET_PX,
    floor: int = SVG_SOURCE_FLOOR,
) -> Tuple[int, int]:
    """Safe ``(sourceSize.width, sourceSize.height)`` for the SVG overlay.

    Starts from ``displayed × multiplier`` (sharp through ~4× zoom), floored at
    ``floor`` per axis, then scales the whole grid down uniformly so the total
    pixel count never exceeds ``budget_px`` — keeping the RGBA buffer under
    Qt's 256 MB ``QImageIOHandler`` limit so the overlay always decodes instead
    of being silently rejected.
    """
    w = max(float(floor), float(width_px) * float(multiplier))
    h = max(float(floor), float(height_px) * float(multiplier))
    total = w * h
    if budget_px > 0 and total > budget_px:
        scale = (budget_px / total) ** 0.5
        w *= scale
        h *= scale
    return (max(1, int(w)), max(1, int(h)))
