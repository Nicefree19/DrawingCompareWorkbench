# -*- coding: utf-8 -*-
"""Phase O — Noise filter settings dialog.

Exposes the SensitivityConfig + ChangeZoneOptions + DrawingDiffer
noise-filter knobs introduced in Phase O2/O3/O4/O5 to end users via
``[설정] → [🧹 노이즈 필터...]`` (``Ctrl+Shift+N``).

UX policy (mirrors AiSettingsDialog from Phase L4):
  * Modal QDialog
  * On ``OK`` (저장): atomically writes ``noise_filter_config.json``
    via ``save_noise_filter_settings``. Workbench reloads the
    settings the next time it builds a comparison job — no restart
    required.
  * On ``Cancel``: no changes persisted.
  * ``[추천 설정 적용]`` button → flips ``suppress_cosmetic_only=True``
    and ``min_changes_per_zone=2`` (the Phase O recommended preset
    that maps directly to the user-feedback that drove Phase O).

Three panels match the Phase O step grouping:
  Panel 1 — 좌표 정밀도 (O2): global alignment toggle + Hungarian cap
  Panel 2 — Cosmetic 변경 (O3): suppress + per-attribute checkboxes
  Panel 3 — Zone Promote (O4): min_changes_per_zone + noise score
  Panel 4 — PDF 시각 비교 (O5): noise_filter_strength preset picker
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.services.comparison.noise_filter_io import (
    NoiseFilterSettings,
    save_noise_filter_settings,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# (combo_value, display_label, tooltip)
_STRENGTH_OPTIONS: list[tuple[str, str, str]] = [
    (
        "low",
        "약함 — 노이즈 보존 (변경 누락 최소)",
        "PDF 비교에서 morphology kernel 3px, 1-pass만 적용. "
        "thin line / 미세 변경은 모두 살려두지만 anti-aliasing 노이즈도 남음.",
    ),
    (
        "medium",
        "중간 (권장) — 균형",
        "Phase O 기본값. morphology kernel 5px + anti-aliasing 보정 2-pass. "
        "anti-aliasing은 SSIM ≥ 0.98 가드 통과 시에만 추가 erosion 적용.",
    ),
    (
        "high",
        "강함 — 노이즈 제거 우선 (누락 위험 ↑)",
        "morphology kernel 7px + 2-pass + blob area 50px² 미만 제거. "
        "노이즈 깨끗하지만 dimension tick 같은 미세 표시 누락 가능.",
    ),
]


# ---------------------------------------------------------------------------
# Dialog widget
# ---------------------------------------------------------------------------


class NoiseFilterDialog(QDialog):
    """Phase O noise filter settings — modal QDialog.

    Constructor is given a ``NoiseFilterSettings`` instance representing
    the currently-saved state; the dialog populates its widgets from
    those values. On Accept the dialog persists a fresh settings object
    via ``save_noise_filter_settings``. The Workbench is responsible
    for reloading + applying.
    """

    def __init__(
        self,
        current: NoiseFilterSettings,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._current = current

        self.setWindowTitle("노이즈 필터 설정 — Phase O")
        self.setModal(True)
        self.resize(640, 620)

        self._build_ui()
        self._populate_from_settings(current)

    # ------------------------------------------------------------------
    # Layout construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 12)
        outer.setSpacing(12)

        intro = QLabel(
            "도면 비교 결과의 노이즈를 줄이기 위한 5가지 필터를 조정합니다. "
            "변경하면 다음 비교 실행부터 적용됩니다 (재시작 불필요)."
        )
        intro.setProperty("role", "muted")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        outer.addWidget(self._build_panel_alignment())     # O2
        outer.addWidget(self._build_panel_cosmetic())      # O3
        outer.addWidget(self._build_panel_zone_promote())  # O4
        outer.addWidget(self._build_panel_pdf_strength())  # O5

        outer.addWidget(_hr())

        # 추천 설정 + 버튼 영역
        action_row = QHBoxLayout()
        self.btn_recommended = QPushButton("✨ 추천 설정 적용")
        self.btn_recommended.setToolTip(
            "Phase O 권장 프리셋: cosmetic-only 변경 숨기고, "
            "단일 entity의 노이즈성 zone promote 차단."
        )
        self.btn_recommended.clicked.connect(self._apply_recommended)
        action_row.addWidget(self.btn_recommended)
        action_row.addStretch(1)

        outer.addLayout(action_row)

        # Save / Cancel 버튼
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.button(QDialogButtonBox.StandardButton.Save).setText("저장")
        button_box.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        outer.addWidget(button_box)

    # --- Panel 1: O2 좌표 정밀도 (global alignment + Hungarian) -----------

    def _build_panel_alignment(self) -> QWidget:
        group = QGroupBox("좌표 정밀도 (O2)")
        layout = QFormLayout()
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        layout.setHorizontalSpacing(16)
        layout.setVerticalSpacing(8)

        self.chk_global_alignment = QCheckBox(
            "전역 rigid 정렬 (global alignment) 활성"
        )
        self.chk_global_alignment.setToolTip(
            "도면 전체 시프트/회전을 RANSAC 으로 추정해 B → A 좌표계로 백투영. "
            "미세 시프트로 인한 false positive 폭증을 차단합니다."
        )
        layout.addRow(self.chk_global_alignment)

        self.spn_hungarian_max = QSpinBox()
        self.spn_hungarian_max.setRange(10, 5000)
        self.spn_hungarian_max.setSingleStep(10)
        self.spn_hungarian_max.setSuffix(" entity")
        self.spn_hungarian_max.setToolTip(
            "Hungarian assignment cluster 당 최대 크기. 초과 시 greedy fallback. "
            "기본 200 (실측 5만 entity 도면에서도 안전)."
        )
        layout.addRow("Hungarian cluster 상한", self.spn_hungarian_max)

        group.setLayout(layout)
        return group

    # --- Panel 2: O3 Cosmetic 변경 ----------------------------------------

    def _build_panel_cosmetic(self) -> QWidget:
        group = QGroupBox("Cosmetic 변경 (O3)")
        layout = QFormLayout()
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        layout.setHorizontalSpacing(16)
        layout.setVerticalSpacing(8)

        self.chk_cosmetic_detect = QCheckBox(
            "cosmetic 변경 탐지 (color / lineweight / linetype)"
        )
        self.chk_cosmetic_detect.setToolTip(
            "좌표가 동일한 entity 페어의 색·선두께·선종류 차이를 별도 분류합니다. "
            "끄면 cosmetic 차이는 비교 결과에 전혀 등장하지 않습니다."
        )
        layout.addRow(self.chk_cosmetic_detect)

        self.chk_suppress_cosmetic = QCheckBox(
            "cosmetic-only 변경 숨기기 (변경 결과에서 제외)"
        )
        self.chk_suppress_cosmetic.setToolTip(
            "탐지는 하되 비교 결과에서 cosmetic-only 변경을 제외하고 통계만 남깁니다. "
            "구조 변경에만 집중하고 싶을 때 켭니다."
        )
        layout.addRow(self.chk_suppress_cosmetic)

        attr_label = QLabel("탐지/비교할 cosmetic 속성")
        attr_label.setProperty("role", "muted")
        layout.addRow(attr_label)

        attr_row = QHBoxLayout()
        self.chk_attr_color = QCheckBox("색상 (color)")
        self.chk_attr_lineweight = QCheckBox("선두께 (lineweight)")
        self.chk_attr_linetype = QCheckBox("선종류 (linetype)")
        attr_row.addWidget(self.chk_attr_color)
        attr_row.addWidget(self.chk_attr_lineweight)
        attr_row.addWidget(self.chk_attr_linetype)
        attr_row.addStretch(1)
        attr_widget = QWidget()
        attr_widget.setLayout(attr_row)
        layout.addRow(attr_widget)

        # cosmetic_detection_enabled OFF → suppress + checkbox grey-out
        self.chk_cosmetic_detect.toggled.connect(self._sync_cosmetic_subwidgets)

        group.setLayout(layout)
        return group

    def _sync_cosmetic_subwidgets(self, enabled: bool) -> None:
        self.chk_suppress_cosmetic.setEnabled(enabled)
        self.chk_attr_color.setEnabled(enabled)
        self.chk_attr_lineweight.setEnabled(enabled)
        self.chk_attr_linetype.setEnabled(enabled)

    # --- Panel 3: O4 Zone Promote -----------------------------------------

    def _build_panel_zone_promote(self) -> QWidget:
        group = QGroupBox("Zone Promote (O4)")
        layout = QFormLayout()
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        layout.setHorizontalSpacing(16)
        layout.setVerticalSpacing(8)

        self.spn_min_changes = QSpinBox()
        self.spn_min_changes.setRange(1, 10)
        self.spn_min_changes.setSingleStep(1)
        self.spn_min_changes.setToolTip(
            "Zone 으로 promote 할 최소 변경 수. 1 = 단일 entity 변경도 zone 생성 (기존 동작). "
            "2 이상 = 단일 entity 변경 중 noise score 임계값 이상인 경우 zone 생성 차단."
        )
        layout.addRow("Zone promote 최소 변경 수", self.spn_min_changes)

        self.spn_noise_threshold = QDoubleSpinBox()
        self.spn_noise_threshold.setRange(0.0, 1.0)
        self.spn_noise_threshold.setSingleStep(0.05)
        self.spn_noise_threshold.setDecimals(2)
        self.spn_noise_threshold.setToolTip(
            "단일 entity 변경의 노이즈 점수가 이 값 이상이면 zone promote 차단. "
            "0.0 = 모두 차단, 1.0 = 차단 안 함, 0.7 = 권장 (cosmetic + 미세 + 비구조 layer 결합 시)."
        )
        layout.addRow(
            "단일 entity noise score 임계", self.spn_noise_threshold,
        )

        # Disable noise threshold when min_changes_per_zone == 1 (no effect)
        self.spn_min_changes.valueChanged.connect(self._sync_zone_subwidgets)

        group.setLayout(layout)
        return group

    def _sync_zone_subwidgets(self, value: int) -> None:
        self.spn_noise_threshold.setEnabled(value >= 2)

    # --- Panel 4: O5 PDF 시각 비교 ----------------------------------------

    def _build_panel_pdf_strength(self) -> QWidget:
        group = QGroupBox("PDF 시각 비교 강도 (O5)")
        layout = QVBoxLayout()
        layout.setSpacing(6)

        info = QLabel(
            "PDF 비교의 노이즈 필터 강도 프리셋입니다. DXF/DWG 비교에는 영향이 없습니다."
        )
        info.setProperty("role", "muted")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.cmb_strength = QComboBox()
        for value, label, tooltip in _STRENGTH_OPTIONS:
            self.cmb_strength.addItem(label, userData=value)
            idx = self.cmb_strength.count() - 1
            self.cmb_strength.setItemData(
                idx, tooltip, role=Qt.ItemDataRole.ToolTipRole,
            )
        layout.addWidget(self.cmb_strength)

        group.setLayout(layout)
        return group

    # ------------------------------------------------------------------
    # State plumbing
    # ------------------------------------------------------------------

    def _populate_from_settings(self, s: NoiseFilterSettings) -> None:
        # Panel 1 — O2
        self.chk_global_alignment.setChecked(s.global_alignment_enabled)
        self.spn_hungarian_max.setValue(int(s.hungarian_max_subset))

        # Panel 2 — O3
        self.chk_cosmetic_detect.setChecked(s.cosmetic_detection_enabled)
        self.chk_suppress_cosmetic.setChecked(s.suppress_cosmetic_only)
        attrs = set(s.cosmetic_attributes)
        self.chk_attr_color.setChecked("color" in attrs)
        self.chk_attr_lineweight.setChecked("lineweight" in attrs)
        self.chk_attr_linetype.setChecked("linetype" in attrs)
        self._sync_cosmetic_subwidgets(s.cosmetic_detection_enabled)

        # Panel 3 — O4
        self.spn_min_changes.setValue(int(s.min_changes_per_zone))
        self.spn_noise_threshold.setValue(
            float(s.single_entity_noise_score_threshold)
        )
        self._sync_zone_subwidgets(int(s.min_changes_per_zone))

        # Panel 4 — O5
        for i in range(self.cmb_strength.count()):
            if self.cmb_strength.itemData(i) == s.noise_filter_strength:
                self.cmb_strength.setCurrentIndex(i)
                break
        else:
            # Unknown strength → fall back to medium
            for i in range(self.cmb_strength.count()):
                if self.cmb_strength.itemData(i) == "medium":
                    self.cmb_strength.setCurrentIndex(i)
                    break

    def _build_settings_from_ui(self) -> NoiseFilterSettings:
        attrs: list[str] = []
        if self.chk_attr_color.isChecked():
            attrs.append("color")
        if self.chk_attr_lineweight.isChecked():
            attrs.append("lineweight")
        if self.chk_attr_linetype.isChecked():
            attrs.append("linetype")
        if not attrs:
            # Empty list = nothing to compare → revert to default (all on)
            attrs = ["color", "lineweight", "linetype"]

        strength = self.cmb_strength.currentData() or "medium"

        return NoiseFilterSettings(
            global_alignment_enabled=self.chk_global_alignment.isChecked(),
            hungarian_max_subset=int(self.spn_hungarian_max.value()),
            cosmetic_detection_enabled=self.chk_cosmetic_detect.isChecked(),
            suppress_cosmetic_only=self.chk_suppress_cosmetic.isChecked(),
            cosmetic_attributes=tuple(attrs),
            min_changes_per_zone=int(self.spn_min_changes.value()),
            single_entity_noise_score_threshold=float(
                self.spn_noise_threshold.value()
            ),
            noise_filter_strength=str(strength),
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _apply_recommended(self) -> None:
        """Flip ONLY the two fields that the recommended preset
        actually changes — ``suppress_cosmetic_only=True`` and
        ``min_changes_per_zone=2``. Every other widget retains the
        user's current edit.

        RV-20260508-001 #2 — the previous implementation called
        ``_populate_from_settings(recommended())`` which re-seeded all
        9 widgets, silently overwriting any in-progress edits the
        user had made (e.g. a custom hungarian_max_subset value).
        That contradicted both the docstring of
        ``NoiseFilterSettings.recommended`` ("Suppresses cosmetic-only
        changes and blocks single-entity promotes — 2 fields") AND
        the button tooltip. Now the code matches the contract.
        """
        # Only touch the two widgets that ``recommended()`` actually
        # differs from ``default()`` on. Sub-widget enable-state
        # callbacks fire automatically through Qt signals.
        self.chk_suppress_cosmetic.setChecked(True)
        self.spn_min_changes.setValue(2)

    def _on_accept(self) -> None:
        try:
            settings = self._build_settings_from_ui()
            save_noise_filter_settings(settings)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Saving noise_filter_config.json failed")
            try:
                QMessageBox.critical(
                    self,
                    "노이즈 필터 저장 실패",
                    f"noise_filter_config.json 저장에 실패했습니다.\n\n"
                    f"원인: {exc}\n\n"
                    f"%LOCALAPPDATA% 의 쓰기 권한과 디스크 공간을 확인하세요.",
                )
            except Exception:
                logger.exception("Could not show save-failure dialog")
            return  # don't accept on save failure
        self.accept()

    def get_saved_settings(self) -> NoiseFilterSettings:
        """Helper for callers that want to read the value the dialog saved.

        Only meaningful after ``exec()`` returns ``Accepted``. Before
        that, returns the original ``current`` settings unchanged.
        """
        if self.result() == QDialog.DialogCode.Accepted:
            return self._build_settings_from_ui()
        return self._current


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hr() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


__all__ = ["NoiseFilterDialog"]
