# -*- coding: utf-8 -*-
"""Persistent opt-in/out for DWG auto-conversion via a locally installed ODA.

Why this exists (live pilot failure, 2026-06-11): auto-conversion of
natively-unsupported DWG versions (AC1018+) was enabled ONLY by the
``DRAWING_COMPARE_DWG_BACKEND`` env var, which the packaged launcher .bat
sets — but any launch that bypassed the bat (repo run, direct exe shortcut)
silently lost DWG support and failed with a version error even though ODA
File Converter was installed on the machine.

Owner decision (internal pilot, 2026-06-11): a locally installed ODA File
Converter is used automatically by default. The env var and the settings
file written here remain explicit overrides in both directions. Bundling or
redistributing ODA binaries stays forbidden — see
``docs/CAD_FORMAT_SUPPORT_POLICY.md``.

Mirrors the ``report_settings`` persistence pattern: one JSON file under the
per-user Workbench data dir.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional, Tuple

DWG_AUTOCONVERT_SETTINGS_FILENAME = "dwg_autoconvert_settings.json"
ODA_DOWNLOAD_URL = "https://www.opendesign.com/guestfiles/oda_file_converter"

#: Setting keys persisted (possibly together) in the shared settings JSON.
DWG_AUTOCONVERT_KEY = "dwg_auto_convert"
#: Opt-in for the experimental clean-room AC1032 native reader (default off).
AC1032_NATIVE_OPT_IN_KEY = "ac1032_native_opt_in"


def default_dwg_autoconvert_settings_path() -> Path:
    """Per-user settings location (same data dir the Workbench GUI uses)."""

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data) / "DrawingCompareWorkbench"
    else:
        base = Path.home() / ".drawing_compare_workbench"
    return base / DWG_AUTOCONVERT_SETTINGS_FILENAME


def _read_settings_dict(target: Path) -> dict:
    """The settings JSON as a dict, or {} on a missing/corrupt/non-dict file."""

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_bool_setting(key: str, path: Optional[Path]) -> Optional[bool]:
    target = Path(path) if path is not None else default_dwg_autoconvert_settings_path()
    value = _read_settings_dict(target).get(key)
    return value if isinstance(value, bool) else None


def _save_bool_setting(key: str, enabled: bool, path: Optional[Path]) -> Path:
    # Read-modify-write so the shared file's other keys survive (e.g. saving the
    # AC1032 opt-in must not wipe the dwg_auto_convert choice, and vice versa).
    target = Path(path) if path is not None else default_dwg_autoconvert_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    data = _read_settings_dict(target)
    data[key] = bool(enabled)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_dwg_autoconvert_enabled(path: Optional[Path] = None) -> Optional[bool]:
    """Saved user decision, or None when the user never chose.

    None means "no explicit decision" — callers fall back to auto-detecting
    an installed converter. Missing/corrupt files read as None, never raise.
    """

    return _load_bool_setting(DWG_AUTOCONVERT_KEY, path)


def save_dwg_autoconvert_enabled(enabled: bool, path: Optional[Path] = None) -> Path:
    """Persist the user's explicit on/off choice. Creates parent dirs."""

    return _save_bool_setting(DWG_AUTOCONVERT_KEY, enabled, path)


def load_ac1032_native_enabled(path: Optional[Path] = None) -> Optional[bool]:
    """Saved AC1032 native-reader opt-in, or None when the user never chose.

    None means "no explicit decision" — the resolver then falls back to the
    default (off). Missing/corrupt files read as None, never raise.
    """

    return _load_bool_setting(AC1032_NATIVE_OPT_IN_KEY, path)


def save_ac1032_native_enabled(enabled: bool, path: Optional[Path] = None) -> Path:
    """Persist the AC1032 native-reader opt-in. Preserves the file's other keys."""

    return _save_bool_setting(AC1032_NATIVE_OPT_IN_KEY, enabled, path)


def detect_oda_installation() -> Tuple[bool, Optional[str]]:
    """(installed, exe path) for a locally installed ODA File Converter.

    Never raises — detection failure reads as "not installed" so compare
    requests degrade to the native-only path instead of crashing.
    """

    try:
        # Token-free shim: cad_policy_gate quarantines the converter class
        # name to dwg_converter.py; this module only asks the yes/no question.
        from .dwg_converter import converter_installation_status

        status = converter_installation_status()
        if isinstance(status, dict) and status.get("installed"):
            return True, status.get("path")
        return False, None
    except Exception:  # noqa: BLE001 - availability probe must stay non-fatal
        return False, None


__all__ = [
    "DWG_AUTOCONVERT_SETTINGS_FILENAME",
    "DWG_AUTOCONVERT_KEY",
    "AC1032_NATIVE_OPT_IN_KEY",
    "ODA_DOWNLOAD_URL",
    "default_dwg_autoconvert_settings_path",
    "detect_oda_installation",
    "load_dwg_autoconvert_enabled",
    "save_dwg_autoconvert_enabled",
    "load_ac1032_native_enabled",
    "save_ac1032_native_enabled",
]
