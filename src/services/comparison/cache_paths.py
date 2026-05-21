# -*- coding: utf-8 -*-
"""Centralised AppData-rooted cache path helper for the Drawing Compare Workbench.

The Workbench used to scatter caches across the user's input folders and a
single ``%LOCALAPPDATA%\\DrawingCompareWorkbench`` directory. Phase F enforces
a structured layout so that every artifact has a stable, namespaced location:

    %LOCALAPPDATA%\\DrawingCompareWorkbench\\
        cache\\viewer\\        ← overview/zone tile artifacts (viewer_manifest v2)
        cache\\normalize\\     ← DWG→DXF results, font resolution
        cache\\preview\\       ← simplified preview geometry (recorder npz)
        state\\                ← review_state, report_settings, recent paths, tutorial flag
        runs\\                 ← per-run review_artifacts
        temp\\                 ← short-lived working files

On non-Windows hosts, ``~/.drawing_compare_workbench`` is used as the root.
The module is import-safe (no side effects on import) — directories are only
created when a caller explicitly invokes :func:`ensure_subdir`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

# Stable name; do NOT change without coordinating with installer + migration.
WORKBENCH_DIR_NAME: Final[str] = "DrawingCompareWorkbench"

# Subdir constants — one Source of Truth so callers don't hand-craft paths.
SUBDIR_CACHE_VIEWER: Final[str] = "cache/viewer"
SUBDIR_CACHE_NORMALIZE: Final[str] = "cache/normalize"
SUBDIR_CACHE_PREVIEW: Final[str] = "cache/preview"
SUBDIR_CACHE_FAILURE: Final[str] = "cache/failure"
SUBDIR_STATE: Final[str] = "state"
SUBDIR_RUNS: Final[str] = "runs"
SUBDIR_TEMP: Final[str] = "temp"

# Legacy paths the Workbench used before Phase F — kept here so the migration
# helper can locate them without duplicating string literals.
_LEGACY_FLAT_DIRS: Final[tuple[str, ...]] = (
    "dxf_cache",
    "compare_state",
    "review_artifacts",
    "preview",
    "viewer_cache",
)


def workbench_data_root() -> Path:
    """Return the absolute root for all Workbench-managed files.

    Honors ``LOCALAPPDATA`` on Windows and falls back to a hidden directory
    under the user's home on POSIX. The path is **not** created here — call
    :func:`ensure_subdir` for the specific subdirectory you need.
    """

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / WORKBENCH_DIR_NAME
    return Path.home() / ".drawing_compare_workbench"


def subdir(rel: str) -> Path:
    """Return ``workbench_data_root() / rel`` without touching the filesystem.

    ``rel`` should be one of the ``SUBDIR_*`` constants (or ``state``-relative
    filenames like ``"state/review_state.json"``). Forward slashes are accepted
    on all platforms — they are normalised by ``Path``.
    """

    if not rel:
        raise ValueError("subdir() requires a non-empty relative path")
    if Path(rel).is_absolute():
        raise ValueError(f"subdir() expects a relative path, got absolute: {rel!r}")
    return workbench_data_root() / rel


def ensure_subdir(rel: str) -> Path:
    """Materialise the requested subdirectory and return its path.

    Creates the directory (and any missing parents) with ``parents=True,
    exist_ok=True``. Safe to call repeatedly.
    """

    path = subdir(rel)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # pragma: no cover - depends on filesystem state
        logger.warning("Failed to create cache subdirectory %s: %s", path, exc)
    return path


def viewer_cache_dir() -> Path:
    """Convenience: ensure-and-return the viewer artifact cache root."""

    return ensure_subdir(SUBDIR_CACHE_VIEWER)


def normalize_cache_dir() -> Path:
    """Convenience: ensure-and-return the DWG→DXF / metadata cache root."""

    return ensure_subdir(SUBDIR_CACHE_NORMALIZE)


def preview_cache_dir() -> Path:
    """Convenience: ensure-and-return the simplified-preview cache root."""

    return ensure_subdir(SUBDIR_CACHE_PREVIEW)


def failure_cache_dir() -> Path:
    """Convenience: ensure-and-return the negative/failure cache root.

    Negative cache stores ``{artifact_key}.json`` markers for renders that
    timed out or failed, so the next request can short-circuit until backoff
    expiry. Backoff policy is owned by ``zone_renderer`` — this function only
    provides the directory.
    """

    return ensure_subdir(SUBDIR_CACHE_FAILURE)


def state_dir() -> Path:
    """Convenience: ensure-and-return the persistent state directory.

    Used by review_state.json, report_settings.json, recent_paths.json,
    tutorial flags, etc.
    """

    return ensure_subdir(SUBDIR_STATE)


def runs_dir() -> Path:
    """Convenience: ensure-and-return the per-run review_artifacts root."""

    return ensure_subdir(SUBDIR_RUNS)


def temp_dir() -> Path:
    """Convenience: ensure-and-return the short-lived workspace."""

    return ensure_subdir(SUBDIR_TEMP)


def is_inside_workbench_root(path: Path) -> bool:
    """Return True if ``path`` is under the Workbench data root.

    Used by validation tests to assert that no cache file leaks into the
    user's input folder. Resolves symlinks before comparing.
    """

    try:
        resolved = Path(path).resolve()
        root = workbench_data_root().resolve()
    except OSError:
        return False
    try:
        resolved.relative_to(root)
    except ValueError:
        return False
    return True


def discover_legacy_flat_dirs() -> list[Path]:
    """Return existing legacy flat directories that pre-date the cache split.

    Pre-Phase-F, the Workbench wrote ``dxf_cache``, ``compare_state``,
    ``review_artifacts``, ``preview``, and ``viewer_cache`` directly under the
    root. This helper enumerates them so a migration script (out of scope for
    this commit) can move their contents into the new ``cache/*`` / ``runs/``
    layout without losing data.
    """

    root = workbench_data_root()
    if not root.exists():
        return []
    found: list[Path] = []
    for name in _LEGACY_FLAT_DIRS:
        candidate = root / name
        if candidate.exists():
            found.append(candidate)
    return found


__all__ = [
    "WORKBENCH_DIR_NAME",
    "SUBDIR_CACHE_VIEWER",
    "SUBDIR_CACHE_NORMALIZE",
    "SUBDIR_CACHE_PREVIEW",
    "SUBDIR_CACHE_FAILURE",
    "SUBDIR_STATE",
    "SUBDIR_RUNS",
    "SUBDIR_TEMP",
    "workbench_data_root",
    "subdir",
    "ensure_subdir",
    "viewer_cache_dir",
    "normalize_cache_dir",
    "preview_cache_dir",
    "failure_cache_dir",
    "state_dir",
    "runs_dir",
    "temp_dir",
    "is_inside_workbench_root",
    "discover_legacy_flat_dirs",
]
