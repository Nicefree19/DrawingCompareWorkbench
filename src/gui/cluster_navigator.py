# -*- coding: utf-8 -*-
"""Cluster navigator — a spatial map of the drawing's change clusters.

A multi-detail 상세도's changes sit in a few far-apart detail clusters, but the
flat zone list groups by change *semantics* (type/severity/layer), not position,
so it gives no "which detail changed" map. This adds a thin strip of buttons (one
per detected spatial cluster) that pan BOTH lightweight viewports to that cluster.

It reuses ``content_frame.cluster_zone_bboxes`` (the verified, collapse-proof
clustering) and the viewport's ``set_camera_to_world_bbox`` — so it needs no new
render and no DXF reparse. Helper-module widget: the workbench creates it once via
``attach_cluster_navigator`` and refreshes it via ``update_cluster_navigator`` from
the overlay-set path, keeping the monolith change to two thin hooks.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional, Sequence

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

logger = logging.getLogger(__name__)

Bbox = Sequence[float]
OnPan = Callable[[Bbox], None]

# Above this many clusters the strip is noise, not a navigable detail map (a pair
# with outlier/corrupted zone coordinates scatters into dozens of singletons).
MAX_NAV_CLUSTERS = 8


class ClusterNavigator(QWidget):
    """A thin horizontal strip of per-cluster jump buttons (hidden when <2)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(4, 2, 4, 2)
        self._row.setSpacing(4)
        self._title = QLabel("디테일 이동:")
        self._title.setProperty("role", "muted")
        self._row.addWidget(self._title)
        self._buttons: List[QPushButton] = []
        self.setVisible(False)

    def _clear_buttons(self) -> None:
        while self._buttons:
            btn = self._buttons.pop()
            self._row.removeWidget(btn)
            btn.deleteLater()

    def set_clusters(self, clusters: Sequence[dict], on_pan: OnPan) -> None:
        """Rebuild the strip. Shows one button per cluster; hides when < 2 or when
        excessive (> MAX): dozens of clusters mean outlier/corrupted zone coords
        (validation 2026-06-18: a 36.7 km-spread pair fragmented into 67), not a
        navigable multi-detail map — a 67-button strip is worse than none."""

        self._clear_buttons()
        if not clusters or not (2 <= len(clusters) <= MAX_NAV_CLUSTERS):
            self.setVisible(False)
            return
        for index, cluster in enumerate(clusters):
            bbox = cluster.get("bbox")
            if bbox is None:
                continue
            count = int(cluster.get("count") or 0)
            btn = QPushButton(f"디테일 {index + 1} · 변경 {count}")
            btn.setToolTip("이 디테일 영역으로 두 도면을 함께 이동합니다.")
            btn.clicked.connect(lambda _checked=False, b=tuple(bbox): on_pan(b))
            self._row.addWidget(btn)
            self._buttons.append(btn)
        self.setVisible(bool(self._buttons))

    def clear(self) -> None:
        self._clear_buttons()
        self.setVisible(False)


def attach_cluster_navigator(workbench: Any, layout: Any) -> None:
    """Create the navigator once and add it to ``layout``. Thin monolith hook."""

    try:
        navigator = ClusterNavigator()
        workbench.cluster_navigator_v2 = navigator
        layout.addWidget(navigator)
    except Exception:  # noqa: BLE001
        logger.debug("cluster navigator attach failed", exc_info=True)


def _pan_both_viewports(workbench: Any, bbox: Bbox) -> None:
    """Pan/zoom both lightweight viewports to ``bbox`` (one shared frame)."""

    setattr(workbench, "_lightweight_camera_sync_in_progress", True)
    try:
        for attr in ("preview_before_lightweight_v2", "preview_after_lightweight_v2"):
            viewport = getattr(workbench, attr, None)
            setter = getattr(viewport, "set_camera_to_world_bbox", None) if viewport is not None else None
            if callable(setter):
                try:
                    setter(tuple(bbox))
                except Exception:  # noqa: BLE001
                    continue
    finally:
        setattr(workbench, "_lightweight_camera_sync_in_progress", False)


def update_cluster_navigator(workbench: Any) -> None:
    """Recompute spatial clusters from the active overlays and refresh the strip.

    Called from the overlay-set path. Safe no-op when the navigator/overlays are
    absent or clustering yields fewer than two clusters.
    """

    navigator = getattr(workbench, "cluster_navigator_v2", None)
    if navigator is None:
        return
    try:
        overlays = list((getattr(workbench, "_active_overlays_by_zone", {}) or {}).values())
        if not overlays:
            navigator.clear()
            return
        # The navigator is a DWG-modelspace multi-detail aid; PDF pairs are
        # page-scale (no sub-pixel problem) and their bboxes are image_pixels,
        # whose Y conversion needs a page height not yet available at overlay-set
        # time (the viewport's world_bbox is still 0). Skip PDF pairs to avoid
        # mis-clustered / wrong-Y pan targets.
        if any(
            str((o or {}).get("bbox_coordinate_space") or "") == "image_pixels"
            for o in overlays
            if isinstance(o, dict)
        ):
            navigator.clear()
            return
        from src.services.comparison.content_frame import cluster_zone_bboxes
        from src.gui.lightweight_viewport import (
            _page_height_points_from_world_bbox,
            convert_bbox_to_world_space,
        )

        before_vp = getattr(workbench, "preview_before_lightweight_v2", None)
        page_height = _page_height_points_from_world_bbox(
            getattr(before_vp, "world_bbox", (0.0, 0.0, 0.0, 0.0))
        )

        def _to_world(raw: Any, space: str, dpi: float):
            return convert_bbox_to_world_space(
                raw, coordinate_space=space, pdf_dpi=dpi, page_height_points=page_height
            )

        clusters = cluster_zone_bboxes(overlays, _to_world)
        navigator.set_clusters(clusters, lambda bbox: _pan_both_viewports(workbench, bbox))
    except Exception:  # noqa: BLE001
        logger.debug("cluster navigator update failed", exc_info=True)


__all__ = [
    "ClusterNavigator",
    "attach_cluster_navigator",
    "update_cluster_navigator",
]
