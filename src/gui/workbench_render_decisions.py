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
