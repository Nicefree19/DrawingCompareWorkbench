# -*- coding: utf-8 -*-
"""FailureBadge widget — surface RenderFailureCode events in the GUI.

S1.4 of the silent-fallback visibility roadmap. The badge is a small
coloured chip placed above the viewport; it appears only when the
viewer pipeline emitted at least one non-OK ``RenderFailureCode``.
Clicking the chip opens a Korean-language details dialog explaining
what fallback ran and what the user can do about it.

The viewport / workbench is responsible for collecting codes from the
underlying pipeline (zone_vector_renderer, lightweight_viewport,
embedding_classifier, etc.) and feeding them to this badge through
``set_failure_codes()``. The badge itself owns no data lifecycle.

Public API:

    FailureBadge(parent)
    set_failure_codes(codes: tuple[RenderFailureCode, ...]) -> None
    clear() -> None
    failure_codes() -> tuple[RenderFailureCode, ...]

Colour is picked from ``highest_severity(codes)``:

  info  -> grey chip,  ℹ️
  warn  -> amber chip, ⚠️
  error -> red chip,   🔴

When the filtered code set is empty (only ``"ok"`` or no codes), the
chip is hidden — the GUI shows nothing.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QWidget,
)

from src.services.comparison.render_failure_codes import (
    HIDDEN_CODES,
    RenderFailureCode,
    Severity,
    highest_severity,
    info_for,
)

logger = logging.getLogger(__name__)

# Severity → CSS hex (background/text), Korean label, and emoji.
# Mirrors the palette used by ``render_modes.RenderModeStyle`` so the
# overall GUI stays visually consistent.
_SEVERITY_PALETTE: dict[Severity, dict[str, str]] = {
    "info": {
        "background": "#6B7280",   # neutral gray
        "text": "#FFFFFF",
        "icon": "ℹ️",
        "label_ko": "알림",
    },
    "warn": {
        "background": "#F59E0B",   # amber
        "text": "#111827",
        "icon": "⚠️",
        "label_ko": "경고",
    },
    "error": {
        "background": "#DC2626",   # red
        "text": "#FFFFFF",
        "icon": "🔴",
        "label_ko": "오류",
    },
}


class FailureBadge(QWidget):
    """Small severity-coloured chip surfacing accumulated failure codes.

    Tied to a viewport's ``render_failure_codes()`` output by the
    workbench. Click the chip to read full Korean messages and
    suggested actions.
    """

    # Emitted whenever the underlying code set transitions to a
    # different tuple. The workbench may listen for telemetry/logging.
    # Default behaviour: no listeners; the badge silently re-renders.
    codesChanged = Signal(tuple)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._codes: Tuple[RenderFailureCode, ...] = ()

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(4, 2, 4, 2)
        self._layout.setSpacing(4)

        self._chip = QLabel("", self)
        self._chip.setObjectName("FailureBadgeChip")
        self._chip.setCursor(Qt.PointingHandCursor)
        self._chip.setToolTip("")
        self._chip.installEventFilter(self)

        self._layout.addWidget(self._chip)
        self._layout.addStretch(1)

        self.setLayout(self._layout)
        self.setVisible(False)  # hidden until set_failure_codes() reveals

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_failure_codes(
        self,
        codes: Tuple[RenderFailureCode, ...],
    ) -> None:
        """Replace the badge's underlying code set and re-render.

        Empty input (or input containing only ``HIDDEN_CODES`` such as
        ``"ok"``) hides the badge. Otherwise the chip colour matches
        the highest severity of the input and the label shows
        ``"icon label_ko count건"``.
        """

        visible_codes = tuple(c for c in codes if c not in HIDDEN_CODES)
        previous = self._codes
        self._codes = visible_codes

        if not visible_codes:
            self._chip.setText("")
            self._chip.setStyleSheet("")
            self._chip.setToolTip("")
            self.setVisible(False)
        else:
            top_sev = highest_severity(*visible_codes)
            palette = _SEVERITY_PALETTE[top_sev]
            count = len(visible_codes)
            label = f"{palette['icon']} {palette['label_ko']} {count}건"
            self._chip.setText(label)
            self._chip.setStyleSheet(
                "QLabel#FailureBadgeChip {"
                f"  background-color: {palette['background']};"
                f"  color: {palette['text']};"
                "  padding: 2px 8px;"
                "  border-radius: 8px;"
                "  font-weight: 600;"
                "}"
            )
            self._chip.setToolTip(self._tooltip_for(visible_codes))
            self.setVisible(True)

        if previous != visible_codes:
            self.codesChanged.emit(visible_codes)

    def clear(self) -> None:
        """Hide the badge — equivalent to ``set_failure_codes(())``."""

        self.set_failure_codes(())

    def failure_codes(self) -> Tuple[RenderFailureCode, ...]:
        """Return the badge's current visible code set (empty if hidden)."""

        return self._codes

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _tooltip_for(self, codes: Tuple[RenderFailureCode, ...]) -> str:
        """Multi-line summary suitable for hover tooltip."""

        lines = []
        for code in codes:
            info = info_for(code)
            lines.append(f"[{info.severity}] {info.message_ko}")
        return "\n".join(lines)

    def _show_details(self) -> None:
        """Open a modal Korean-language dialog explaining each active code."""

        if not self._codes:
            return

        top_sev = highest_severity(*self._codes)
        palette = _SEVERITY_PALETTE[top_sev]
        title = f"{palette['icon']} 렌더링 상태 — {len(self._codes)}건"

        sections = []
        for code in self._codes:
            info = info_for(code)
            section = [
                f"[{info.severity.upper()}] {info.message_ko}",
            ]
            if info.suggested_action_ko:
                section.append(f"  → 조치: {info.suggested_action_ko}")
            section.append(f"  (코드: {info.code})")
            sections.append("\n".join(section))

        body = "\n\n".join(sections)

        icon = QMessageBox.Information
        if top_sev == "warn":
            icon = QMessageBox.Warning
        elif top_sev == "error":
            icon = QMessageBox.Critical

        msg = QMessageBox(self)
        msg.setIcon(icon)
        msg.setWindowTitle(title)
        msg.setText(body)
        msg.exec()

    # ------------------------------------------------------------------
    # Qt event handling
    # ------------------------------------------------------------------

    def eventFilter(self, watched, event) -> bool:
        """Forward chip clicks to the details dialog."""

        if watched is self._chip and event.type() == QEvent.MouseButtonRelease:
            if self._codes:
                self._show_details()
                return True
        return super().eventFilter(watched, event)


__all__ = ["FailureBadge"]
