"""Region-level match review dialog for multi-detail drawing comparisons."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.services.comparison.region_match_overrides import (
    RegionMatchOverride,
    load_region_match_overrides,
    write_region_match_overrides,
)


REGION_DETECTION_SUMMARY_NAME = "region_detection_summary.json"
REGION_MATCH_SUMMARY_NAME = "region_match_summary.json"
MANUAL_REGION_MATCHES_NAME = "manual_region_matches.json"


def load_region_review_data(artifact_dir: str | Path) -> dict[str, Any]:
    """Load detector and matcher summaries into rows that the dialog can show."""

    artifact_root = Path(artifact_dir)
    detection_path = artifact_root / REGION_DETECTION_SUMMARY_NAME
    match_path = artifact_root / REGION_MATCH_SUMMARY_NAME
    detection_payload = _read_json(detection_path)
    match_payload = _read_json(match_path)

    regions_by_id: dict[str, dict[str, Any]] = {}
    whole_modelspace_regions: list[dict[str, Any]] = []
    for result in detection_payload.get("results") or []:
        if not isinstance(result, dict):
            continue
        for region in result.get("regions") or []:
            if not isinstance(region, dict):
                continue
            region_id = str(region.get("region_id") or "")
            if not region_id:
                continue
            regions_by_id[region_id] = region
            if str(region.get("detection_method") or "") == "whole_modelspace":
                whole_modelspace_regions.append(region)

    rows: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    warnings: list[str] = []
    for summary in match_payload.get("summaries") or []:
        if not isinstance(summary, dict):
            continue
        pair_id = str(summary.get("pair_id") or "")
        for warning in summary.get("warnings") or []:
            warnings.append(str(warning))
        for match in summary.get("matches") or []:
            if not isinstance(match, dict):
                continue
            status = str(match.get("status") or "")
            status_counts[status] = status_counts.get(status, 0) + 1
            before_id = str(match.get("before_region_id") or "")
            after_id = str(match.get("after_region_id") or "")
            rows.append(
                {
                    "pair_id": pair_id,
                    "match_id": str(match.get("match_id") or ""),
                    "before_region_id": before_id,
                    "after_region_id": after_id,
                    "before_label": _format_region_label(regions_by_id.get(before_id)),
                    "after_label": _format_region_label(regions_by_id.get(after_id)),
                    "status": status,
                    "score": float(match.get("score") or 0.0),
                    "reasons": "; ".join(str(item) for item in match.get("reasons") or []),
                }
            )

    return {
        "artifact_dir": str(artifact_root),
        "region_detection_summary_path": str(detection_path),
        "region_match_summary_path": str(match_path),
        "regions_by_id": regions_by_id,
        "rows": rows,
        "status_counts": status_counts,
        "whole_modelspace_region_count": len(whole_modelspace_regions),
        "whole_modelspace_regions": whole_modelspace_regions,
        "warnings": warnings,
        "has_ambiguous_matches": bool(status_counts.get("review_required")),
    }


class RegionMatchReviewDialog(QDialog):
    """Review and save manual overrides for detected detail-region matches."""

    def __init__(
        self,
        *,
        artifact_dir: str | Path,
        overrides_path: str | Path,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._artifact_dir = Path(artifact_dir)
        self._overrides_path = Path(overrides_path)
        self._review_data = load_region_review_data(self._artifact_dir)
        self._override_combos: list[tuple[dict[str, Any], QComboBox]] = []

        self.setWindowTitle("Detail Region Matching")
        self.resize(1080, 640)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(self._build_summary_label())

        warning_text = self._warning_text()
        if warning_text:
            warning_label = QLabel(warning_text)
            warning_label.setWordWrap(True)
            warning_label.setStyleSheet("color: #B45309; font-weight: 600;")
            layout.addWidget(warning_label)

        rows = list(self._review_data.get("rows") or [])
        if not rows:
            empty = QLabel(
                "No detail region match rows were exported. Run comparison with region detection enabled first."
            )
            empty.setWordWrap(True)
            layout.addWidget(empty)
        else:
            table = QTableWidget(len(rows), 7)
            table.setHorizontalHeaderLabels(
                ["Pair", "Before region", "After region", "Status", "Score", "Reasons", "Override"]
            )
            table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            table.verticalHeader().setVisible(False)
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.setSelectionBehavior(QTableWidget.SelectRows)
            self._populate_table(table, rows)
            layout.addWidget(table, stretch=1)

        button_row = QHBoxLayout()
        save_button = QPushButton("Save Overrides")
        save_button.setToolTip(f"Write {MANUAL_REGION_MATCHES_NAME} for the next comparison run.")
        save_button.clicked.connect(self._save_overrides)
        button_row.addWidget(save_button)
        button_row.addStretch()
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

    def _build_summary_label(self) -> QLabel:
        counts = dict(self._review_data.get("status_counts") or {})
        total = sum(counts.values())
        text = (
            f"Detail Region Matching: {total} match rows. "
            f"auto={counts.get('auto_matched', 0)}, "
            f"manual={counts.get('manual_matched', 0)}, "
            f"review_required={counts.get('review_required', 0)}, "
            f"unmatched_before={counts.get('unmatched_before', 0)}, "
            f"unmatched_after={counts.get('unmatched_after', 0)}. "
            f"Overrides are saved to {self._overrides_path}."
        )
        label = QLabel(text)
        label.setWordWrap(True)
        return label

    def _warning_text(self) -> str:
        warnings = list(self._review_data.get("warnings") or [])
        whole_modelspace_count = int(self._review_data.get("whole_modelspace_region_count") or 0)
        if whole_modelspace_count:
            warnings.append(
                f"{whole_modelspace_count} whole_modelspace region(s) detected. "
                "Frame extraction failed for those sources; precision review is required."
            )
        if self._review_data.get("has_ambiguous_matches"):
            warnings.append(
                "Some region matches are review_required. Region-local primary compare should stay gated until approved."
            )
        return "\n".join(warnings)

    def _populate_table(self, table: QTableWidget, rows: list[dict[str, Any]]) -> None:
        existing = _load_existing_override_index(self._overrides_path)
        for row_index, row in enumerate(rows):
            table.setItem(row_index, 0, QTableWidgetItem(str(row.get("pair_id") or "")))
            table.setItem(row_index, 1, QTableWidgetItem(str(row.get("before_label") or "")))
            table.setItem(row_index, 2, QTableWidgetItem(str(row.get("after_label") or "")))
            table.setItem(row_index, 3, QTableWidgetItem(str(row.get("status") or "")))
            table.setItem(row_index, 4, QTableWidgetItem(f"{float(row.get('score') or 0.0):.3f}"))
            table.setItem(row_index, 5, QTableWidgetItem(str(row.get("reasons") or "")))
            combo = QComboBox()
            combo.addItem("Keep auto decision", "")
            combo.addItem("Manual match", "manual_match")
            combo.addItem("Unmatched before", "unmatched_before")
            combo.addItem("Unmatched after", "unmatched_after")
            selected = existing.get(
                _override_key(row),
                existing.get(
                    ("", str(row.get("before_region_id") or ""), str(row.get("after_region_id") or "")),
                    "",
                ),
            )
            selected_index = combo.findData(selected)
            if selected_index >= 0:
                combo.setCurrentIndex(selected_index)
            table.setCellWidget(row_index, 6, combo)
            self._override_combos.append((row, combo))
        table.resizeRowsToContents()

    def _save_overrides(self) -> None:
        overrides: list[RegionMatchOverride] = []
        for row, combo in self._override_combos:
            status = str(combo.currentData() or "")
            if not status:
                continue
            before_id = str(row.get("before_region_id") or "")
            after_id = str(row.get("after_region_id") or "")
            if status == "manual_match" and not (before_id and after_id):
                continue
            if status == "unmatched_before" and not before_id:
                continue
            if status == "unmatched_after" and not after_id:
                continue
            overrides.append(
                RegionMatchOverride(
                    pair_id=str(row.get("pair_id") or ""),
                    before_region_id=before_id,
                    after_region_id=after_id,
                    status=status,
                    reason="GUI region review",
                )
            )
        write_region_match_overrides(overrides, self._overrides_path)
        QMessageBox.information(
            self,
            "Overrides Saved",
            f"Saved {len(overrides)} manual region override(s) to {self._overrides_path}.",
        )
        self.accept()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _format_region_label(region: dict[str, Any] | None) -> str:
    if not region:
        return ""
    parts = [str(region.get("region_id") or "")]
    drawing_number = str(region.get("drawing_number") or "")
    if drawing_number:
        parts.append(drawing_number)
    title = str(region.get("title_text") or "").strip()
    if title:
        parts.append(title[:80])
    detection_method = str(region.get("detection_method") or "")
    if detection_method:
        parts.append(detection_method)
    return " | ".join(part for part in parts if part)


def _load_existing_override_index(path: Path) -> dict[tuple[str, str, str], str]:
    if not path.exists():
        return {}
    out: dict[tuple[str, str, str], str] = {}
    try:
        for override in load_region_match_overrides(path):
            out[(override.pair_id, override.before_region_id, override.after_region_id)] = (
                override.normalized_status()
            )
    except Exception:
        return {}
    return out


def _override_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("pair_id") or ""),
        str(row.get("before_region_id") or ""),
        str(row.get("after_region_id") or ""),
    )


__all__ = [
    "MANUAL_REGION_MATCHES_NAME",
    "REGION_DETECTION_SUMMARY_NAME",
    "REGION_MATCH_SUMMARY_NAME",
    "RegionMatchReviewDialog",
    "load_region_review_data",
]
