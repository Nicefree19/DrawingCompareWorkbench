"""Pure overlay-model helpers for the drawing-compare viewports.

Extracted verbatim from the ``drawing_compare_workbench`` monolith (tech-debt
audit MONO-4: satellite-seam decomposition). These functions are pure — no Qt,
no widget state — so they live and are unit-tested outside the 13k-line GUI
file. ``drawing_compare_workbench`` re-imports every public name below so the
existing import surface (``from src.gui.drawing_compare_workbench import
build_overlay_entries`` etc.) and all in-file call sites keep working unchanged.

This is the first extraction of the V2 god-object decomposition; subsequent
cohesive clusters (overlay cache, review-state controller, render callbacks)
follow the same re-export pattern so each move is net-negative monolith lines.
"""

from __future__ import annotations

#: Keep the full analysis/zone list, but switch the QML viewport into
#: focus-only mode once the overlay source set is this large — each cloud
#: marker owns a Canvas + label and thousands of them starve the Qt event loop.
GPU_VIEWER_FOCUS_ONLY_OVERLAY_SOURCE_THRESHOLD = 300


def resolve_overlay_match_side(change_type: str) -> str:
    """Classify a change type into A-only / B-only / matched / mixed buckets.

    deleted → ``a_only`` (변경 전 A에만 존재), added → ``b_only`` (변경 후 B에만 존재),
    modified/moved → ``matched`` (양쪽 매칭), mixed → ``mixed``. Used by the GPU viewport
    to dim or highlight cloud overlays based on which viewport side is showing them.
    """

    normalized = str(change_type or "").lower()
    if "delete" in normalized or "remove" in normalized:
        return "a_only"
    if "add" in normalized:
        return "b_only"
    if "mixed" in normalized:
        return "mixed"
    return "matched"


def overlay_cloud_should_dim(match_side: str, *, before: bool, selected: bool) -> bool:
    """Decide whether a cloud overlay should render dimmed.

    Dim when (1) it belongs to the selected zone so the focus marker stands out,
    or (2) the change is one-sided and we are showing the wrong viewport side
    (b_only changes on the before viewport, a_only on the after viewport).
    """

    if selected:
        return True
    if before and match_side == "b_only":
        return True
    if (not before) and match_side == "a_only":
        return True
    return False


def build_overlay_entries(
    *,
    zone_id: str,
    rect: tuple[float, float, float, float],
    change_type: str,
    label: str,
    raw_change_count: int = 0,
    cluster_count: int = 0,
    selected: bool = False,
    before: bool = False,
    pin_only: bool = False,
) -> list[dict]:
    """Build cloud + focus overlay entries for the QML viewport.

    For non-selected zones returns a single ``cloud`` entry. For the selected zone
    returns a dimmed cloud entry plus a ``focus`` entry carrying pin coordinates,
    a crosshair flag and a compact label so the QML side can render a small marker
    on top of the larger cloud area. ``pin_only`` (used for PDF page-level fallback
    when bbox is unknown) skips the cloud and emits only the focus pin.
    """

    match_side = resolve_overlay_match_side(change_type)
    dim_cloud = overlay_cloud_should_dim(match_side, before=before, selected=selected)
    width = max(1.0, float(rect[2]))
    height = max(1.0, float(rect[3]))
    x = float(rect[0])
    y = float(rect[1])
    entries: list[dict] = []

    if not pin_only:
        entries.append(
            {
                "role": "cloud",
                "zoneId": zone_id,
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "changeType": str(change_type or "mixed"),
                "matchSide": match_side,
                "label": label,
                "labelMode": "area",
                "dimmed": dim_cloud,
                "rawChangeCount": int(raw_change_count or 0),
                "clusterCount": int(cluster_count or 0),
            }
        )

    if selected:
        entries.append(
            {
                "role": "focus",
                "zoneId": zone_id,
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "pinX": x + width / 2.0,
                "pinY": y + height / 2.0,
                "crosshair": True,
                "changeType": str(change_type or "mixed"),
                "matchSide": match_side,
                "label": label,
                "labelMode": "compact",
                "rawChangeCount": int(raw_change_count or 0),
                "clusterCount": int(cluster_count or 0),
                "pinOnly": bool(pin_only),
            }
        )

    return entries


def split_overlay_entries(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """Partition a flat overlay model into ``(cloud, focus)`` lists for QML."""

    cloud: list[dict] = []
    focus: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("role") == "focus":
            focus.append(entry)
        else:
            cloud.append(entry)
    return cloud, focus


def should_use_focus_only_overlay_mode(overlay_source_count: int) -> bool:
    """Return True when cloud Canvas rendering should be skipped.

    The analysis/result model can hold thousands of zones, but QML renders each
    cloud marker as a Canvas plus label. Once the source set is this large the
    viewport should keep the full zone list in Python and only send the selected
    zone's focus marker to QML.
    """

    try:
        count = int(overlay_source_count)
    except (TypeError, ValueError):
        count = 0
    return count > GPU_VIEWER_FOCUS_ONLY_OVERLAY_SOURCE_THRESHOLD


def match_side_ko(change_type: str) -> str:
    """Render the A-only / B-only / matched / mixed bucket as a Korean phrase."""

    side = resolve_overlay_match_side(change_type)
    if side == "a_only":
        return "변경 전(A)에만 존재"
    if side == "b_only":
        return "변경 후(B)에만 존재"
    if side == "mixed":
        return "혼합 (A/B 모두에 일부)"
    return "양쪽 매칭됨"
