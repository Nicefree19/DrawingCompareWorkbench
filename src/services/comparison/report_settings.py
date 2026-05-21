# -*- coding: utf-8 -*-
"""Persistent reviewer + company settings for the PDF review report.

Keeps the QW3 report module pure-data while letting the Workbench remember the
user's company logo, signature image, reviewer name/title/department, and
preferred accent colour across sessions. The settings file lives under the
Workbench data dir so each Windows account has its own profile.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

REPORT_SETTINGS_FILENAME = "report_settings.json"
DEFAULT_ACCENT_COLOR_HEX = "#DC2626"  # red — matches confirmed cloud markers
DEFAULT_COMPANY_NAME = "센엔지니어링 그룹"


@dataclass
class ReportSettings:
    """Reviewer + company profile applied to generated PDF reports."""

    company_name: str = DEFAULT_COMPANY_NAME
    company_logo_path: str = ""  # absolute path to logo image (PNG/JPG), optional
    reviewer_name: str = ""
    reviewer_title: str = ""
    reviewer_department: str = ""
    reviewer_contact: str = ""
    reviewer_signature_path: str = ""  # absolute path to signature/stamp PNG, optional
    accent_color_hex: str = DEFAULT_ACCENT_COLOR_HEX
    footer_note: str = ""  # extra footer line (e.g. project ID, contract number)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReportSettings":
        if not isinstance(data, dict):
            return cls()
        # Tolerate unknown keys for forward compat
        valid_keys = {f for f in cls.__dataclass_fields__}
        kwargs = {k: v for k, v in data.items() if k in valid_keys}
        try:
            return cls(**kwargs)
        except TypeError:
            return cls()

    @property
    def accent_color_rgb(self) -> tuple[float, float, float]:
        """Convert the configured hex accent into normalised RGB for PyMuPDF."""

        hex_color = self.accent_color_hex.lstrip("#")
        if len(hex_color) != 6:
            hex_color = DEFAULT_ACCENT_COLOR_HEX.lstrip("#")
        try:
            r = int(hex_color[0:2], 16) / 255.0
            g = int(hex_color[2:4], 16) / 255.0
            b = int(hex_color[4:6], 16) / 255.0
        except ValueError:
            r, g, b = 220 / 255.0, 38 / 255.0, 38 / 255.0
        return (r, g, b)

    def reviewer_one_line(self) -> str:
        """Compose a single 'name · title · department' string for the cover."""

        parts = [self.reviewer_name, self.reviewer_title, self.reviewer_department]
        joined = " · ".join(p.strip() for p in parts if p and p.strip())
        return joined or "(검토자 정보 미입력 — 보고서 설정에서 입력 가능)"


def load_report_settings(path: Path) -> ReportSettings:
    """Read settings from disk, returning defaults when missing/corrupt."""

    if not path.exists():
        return ReportSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ReportSettings()
    return ReportSettings.from_dict(data)


def save_report_settings(path: Path, settings: ReportSettings) -> None:
    """Persist settings as pretty-printed JSON. Creates parent dirs as needed."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(settings.to_dict(), ensure_ascii=False, indent=2)
    path.write_text(payload, encoding="utf-8")
