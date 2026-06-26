"""Full-zone-tree rebuild failure surfacing (extracted from the V2 monolith).

When a background full-zone-tree rebuild worker fails (overlay load or plan
build), the change-zone list would otherwise stay empty/stale with no
explanation — a silent failure. This records the rebuild-failure perf event AND
returns the user-facing status text, so the monolith handlers can surface it to
``lbl_status_v2`` instead of only logging telemetry. Extracted as a satellite so
the wiring stays net-negative against the monolith line-ceiling freeze.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.services.comparison.viewer_tile_cache import append_viewer_perf_event

#: Shown to the user when the change-zone list fails to (re)build.
ZONE_TREE_REBUILD_FAILED_STATUS = (
    "변경구역 목록을 불러오지 못했습니다 — 다시 시도하거나 다른 도면 쌍을 선택하세요."
)


def append_zone_tree_rebuild_failure(
    viewer_root: Optional[Path],
    pair_id: str,
    message: str,
    *,
    plan_worker: bool,
) -> str:
    """Record the rebuild-failure perf event (when a viewer root exists) and
    return the user-facing status string.

    ``plan_worker`` selects the chunked plan-build worker telemetry shape;
    otherwise the overlay-load worker shape (preserves the prior per-handler
    perf payloads). The returned string is set on the GUI status label so the
    failure is never silent.
    """

    if viewer_root:
        worker_kwargs = (
            {"plan_build_worker": True} if plan_worker else {"overlay_load_worker": True}
        )
        default_message = "plan_build_failed" if plan_worker else "overlay_load_failed"
        append_viewer_perf_event(
            viewer_root,
            "full_zone_tree_rebuild",
            pair_uuid=pair_id,
            elapsed_ms=0.0,
            overlay_count=0,
            visible_overlay_count=0,
            chunked=plan_worker,
            chunk_count=0,
            max_chunk_elapsed_ms=0.0,
            error_message=str(message or default_message),
            **worker_kwargs,
        )
    return ZONE_TREE_REBUILD_FAILED_STATUS
