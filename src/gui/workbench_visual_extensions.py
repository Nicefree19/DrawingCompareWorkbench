# -*- coding: utf-8 -*-
"""Small GUI extension hooks kept outside the workbench monolith."""

from __future__ import annotations

import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)


def attach_visual_extensions(workbench: Any, menu_bar: Any) -> None:
    """Attach optional visual-review menu actions without breaking menu build."""

    try:
        from src.gui.hybrid_reference_pdf import attach_reference_pdf_action

        attach_reference_pdf_action(workbench, menu_bar)
    except Exception:  # noqa: BLE001
        logger.debug("Optional visual extension attach failed", exc_info=True)


def apply_shared_lightweight_camera_frame(
    workbench: Any,
    viewer_pair: Mapping[str, Any],
) -> None:
    """Synchronize before/after lightweight camera frames when transforms exist."""

    try:
        from src.services.comparison.viewer_frame import apply_shared_camera_frame

        apply_shared_camera_frame(
            viewer_pair.get("before_transform"),
            viewer_pair.get("after_transform"),
            getattr(workbench, "preview_before_lightweight_v2", None),
            getattr(workbench, "preview_after_lightweight_v2", None),
        )
    except Exception:  # noqa: BLE001
        logger.debug("Shared lightweight camera frame hook failed", exc_info=True)


__all__ = [
    "attach_visual_extensions",
    "apply_shared_lightweight_camera_frame",
]
