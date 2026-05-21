# -*- coding: utf-8 -*-
"""Phase O — Noise filter user-config persistence.

Mirrors ``ai_classifier.config_io`` for the SensitivityConfig +
ChangeZoneOptions + DrawingDiffer noise-filter knobs introduced in
Phase O2/O3/O4/O5. The dialog at
``[설정] → [🧹 노이즈 필터...]`` (Ctrl+Shift+N) reads/writes
``noise_filter_config.json`` under the same AppData directory used
by the AI classifier (``%LOCALAPPDATA%/DrawingCompareWorkbench/``).

The atomic-write contract matches ``save_ai_config``:
  1. Write to a same-directory temp file
  2. ``Path.replace()`` to the target (POSIX rename — never leaves a
     partial JSON if the process crashes mid-write)
  3. Best-effort cleanup of stale tmp on exception path

Forward/backward compat: ``load`` always returns a usable
``NoiseFilterSettings`` — schema mismatches/parse errors degrade to
``NoiseFilterSettings.default()`` with a warning, and corrupt files
are renamed to ``.bak`` so the next save doesn't trip over them.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_SCHEMA_VERSION = "noise_filter.v1"
CONFIG_FILENAME = "noise_filter_config.json"

_VALID_PDF_STRENGTHS = {"low", "medium", "high"}
_VALID_COSMETIC_ATTRS = {"color", "lineweight", "linetype"}
# RV-20260508-001 #6 — JSON bomb defence: cap cosmetic_attributes
# length so a malicious config can't materialise a multi-MB tuple.
# 32 is generous (we only have 3 valid values today) but allows
# room for a future Phase P/Q extension before the cap matters.
_MAX_COSMETIC_ATTRS_LEN = 32


# ---------------------------------------------------------------------------
# Settings dataclass
# ---------------------------------------------------------------------------


@dataclass
class NoiseFilterSettings:
    """User-facing noise-filter knobs persisted to noise_filter_config.json.

    Field map (Phase O step → field):
      O2 → global_alignment_enabled, hungarian_max_subset
      O3 → cosmetic_detection_enabled, suppress_cosmetic_only,
           cosmetic_attributes
      O4 → min_changes_per_zone, single_entity_noise_score_threshold
      O5 → noise_filter_strength

    All defaults preserve **legacy behaviour** so loading a missing
    file produces no behavioural change. The dialog's "추천 설정"
    button is what flips ``suppress_cosmetic_only=True`` and
    ``min_changes_per_zone=2``.
    """

    # O2 — coordinate noise absorption
    global_alignment_enabled: bool = True
    hungarian_max_subset: int = 200

    # O3 — cosmetic separation
    cosmetic_detection_enabled: bool = True
    suppress_cosmetic_only: bool = False
    cosmetic_attributes: tuple[str, ...] = ("color", "lineweight", "linetype")

    # O4 — zone-level noise filter
    min_changes_per_zone: int = 1
    single_entity_noise_score_threshold: float = 0.7

    # O5 — PDF visual diff strength
    noise_filter_strength: str = "medium"

    @classmethod
    def default(cls) -> "NoiseFilterSettings":
        """Constructor that preserves all current defaults."""
        return cls()

    @classmethod
    def recommended(cls) -> "NoiseFilterSettings":
        """The "추천 설정 적용" preset.

        Suppresses cosmetic-only changes and blocks single-entity
        promotes for high-noise (low-priority layer / sub-mm shift)
        clusters. This is the preset that maps directly to the user
        feedback that drove Phase O.
        """
        return cls(
            suppress_cosmetic_only=True,
            min_changes_per_zone=2,
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # tuple → list for JSON serialisation
        d["cosmetic_attributes"] = list(self.cosmetic_attributes)
        return d


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def default_noise_filter_config_path() -> Path:
    """Resolve user-config path mirroring ``default_ai_config_path``.

    RV-20260508-001 #9 — ``.resolve()`` canonicalises the path so a
    relative ``LOCALAPPDATA`` env var (rare in production, common in
    CI) doesn't produce a relative config path that breaks Path.replace.
    """
    appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if appdata:
        return (Path(appdata) / "DrawingCompareWorkbench" / CONFIG_FILENAME).resolve()
    return (Path.home() / ".config" / "DrawingCompareWorkbench" / CONFIG_FILENAME).resolve()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_payload(payload: dict[str, Any]) -> Optional[str]:
    """Return None when the payload is acceptable, else a reason string.

    Codex review RV-20260507-003 fix: every membership check must be
    guarded with a scalar/type assertion FIRST. Without this guard a
    malformed JSON like ``{"noise_filter_strength": []}`` or
    ``{"cosmetic_attributes": [["nested"]]}`` would raise ``TypeError``
    on the ``in`` operator (lists / dicts aren't hashable, so they
    can't probe a ``set``). That violates the load contract that
    promises to never raise — callers (the Workbench boot path)
    rely on it returning ``NoiseFilterSettings.default()`` quietly.
    """
    sv = payload.get("schema_version")
    if sv != CONFIG_SCHEMA_VERSION:
        return f"schema_version={sv!r} (expected {CONFIG_SCHEMA_VERSION!r})"
    # RV-20260508-001 #4 — bool fields (introduced in v1) must reject
    # non-bool truthy values so a careless ``"global_alignment_enabled":
    # "yes"`` doesn't silently coerce via ``bool()`` at load time.
    for bool_field in (
        "global_alignment_enabled",
        "cosmetic_detection_enabled",
        "suppress_cosmetic_only",
    ):
        if bool_field in payload and not isinstance(payload[bool_field], bool):
            return (
                f"{bool_field} must be bool, got "
                f"{type(payload[bool_field]).__name__}"
            )
    strength = payload.get("noise_filter_strength", "medium")
    if not isinstance(strength, str):
        return f"noise_filter_strength must be str, got {type(strength).__name__}"
    if strength not in _VALID_PDF_STRENGTHS:
        return f"noise_filter_strength={strength!r} not in {_VALID_PDF_STRENGTHS}"
    attrs = payload.get("cosmetic_attributes", [])
    if not isinstance(attrs, list):
        return "cosmetic_attributes must be a list"
    # RV-20260508-001 #6 — JSON bomb cap. Apply BEFORE iterating so a
    # 10K-element list doesn't run the per-entry isinstance loop at all.
    if len(attrs) > _MAX_COSMETIC_ATTRS_LEN:
        return (
            f"cosmetic_attributes length {len(attrs)} exceeds cap "
            f"{_MAX_COSMETIC_ATTRS_LEN}"
        )
    # RV-20260508-001 #5 — empty list silently disables detection while
    # ``cosmetic_detection_enabled=True`` reports it as on. Reject so
    # callers that hand-edit JSON get an explicit error instead of a
    # confusing no-op.
    if "cosmetic_attributes" in payload and len(attrs) == 0:
        return "cosmetic_attributes must not be empty"
    for a in attrs:
        if not isinstance(a, str):
            return (
                f"cosmetic_attributes entries must be str, got "
                f"{type(a).__name__}"
            )
        if a not in _VALID_COSMETIC_ATTRS:
            return f"cosmetic_attributes contains unknown {a!r}"
    mcz = payload.get("min_changes_per_zone", 1)
    # bool is a subclass of int — reject explicitly so a careless
    # ``true`` in JSON doesn't pass through as the int 1.
    if not isinstance(mcz, int) or isinstance(mcz, bool):
        return f"min_changes_per_zone must be int, got {type(mcz).__name__}"
    if mcz < 1 or mcz > 10:
        return f"min_changes_per_zone={mcz!r} out of [1, 10]"
    th = payload.get("single_entity_noise_score_threshold", 0.7)
    if isinstance(th, bool) or not isinstance(th, (int, float)):
        return (
            f"single_entity_noise_score_threshold must be number, got "
            f"{type(th).__name__}"
        )
    if not (0.0 <= float(th) <= 1.0):
        return f"single_entity_noise_score_threshold={th!r} out of [0, 1]"
    hms = payload.get("hungarian_max_subset", 200)
    if not isinstance(hms, int) or isinstance(hms, bool):
        return f"hungarian_max_subset must be int, got {type(hms).__name__}"
    if hms < 10 or hms > 5000:
        return f"hungarian_max_subset={hms!r} out of [10, 5000]"
    return None


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


def load_noise_filter_settings(
    path: Optional[Path] = None,
) -> NoiseFilterSettings:
    """Read noise_filter_config.json or return ``NoiseFilterSettings.default()``.

    Behaviour by file state:
      * File doesn't exist → default() (silent — first launch)
      * JSON parse fails → log warning, move file to .bak, default()
      * Schema version unknown / validation fails → log warning, default()
      * All checks pass → settings populated from JSON

    Always returns a usable settings object — never raises.
    """
    if path is None:
        path = default_noise_filter_config_path()
    path = Path(path)

    if not path.exists():
        return NoiseFilterSettings.default()

    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except OSError as exc:
        logger.warning(
            "noise_filter_config.json read failed (%s) — using defaults", exc,
        )
        return NoiseFilterSettings.default()
    except json.JSONDecodeError as exc:
        logger.warning(
            "noise_filter_config.json parse failed (%s) — moving to .bak, "
            "using defaults", exc,
        )
        try:
            backup = path.with_suffix(path.suffix + ".bak")
            path.replace(backup)
            logger.info(
                "Corrupt noise_filter_config.json moved to %s", backup,
            )
        except OSError:
            logger.exception(
                "Could not move corrupt noise_filter_config.json to .bak",
            )
        return NoiseFilterSettings.default()

    if not isinstance(payload, dict):
        logger.warning(
            "noise_filter_config.json root is %s, not dict — using defaults",
            type(payload).__name__,
        )
        return NoiseFilterSettings.default()

    try:
        err = _validate_payload(payload)
    except Exception as exc:  # noqa: BLE001 — load contract says never raise
        logger.warning(
            "noise_filter_config.json validation crashed (%s) — using defaults",
            exc,
        )
        return NoiseFilterSettings.default()
    if err is not None:
        logger.warning(
            "noise_filter_config.json validation failed: %s — using defaults",
            err,
        )
        return NoiseFilterSettings.default()

    base = NoiseFilterSettings.default()
    return NoiseFilterSettings(
        global_alignment_enabled=bool(
            payload.get("global_alignment_enabled", base.global_alignment_enabled)
        ),
        hungarian_max_subset=int(
            payload.get("hungarian_max_subset", base.hungarian_max_subset)
        ),
        cosmetic_detection_enabled=bool(
            payload.get(
                "cosmetic_detection_enabled", base.cosmetic_detection_enabled,
            )
        ),
        suppress_cosmetic_only=bool(
            payload.get("suppress_cosmetic_only", base.suppress_cosmetic_only)
        ),
        cosmetic_attributes=tuple(
            payload.get("cosmetic_attributes", list(base.cosmetic_attributes))
        ),
        min_changes_per_zone=int(
            payload.get("min_changes_per_zone", base.min_changes_per_zone)
        ),
        single_entity_noise_score_threshold=float(
            payload.get(
                "single_entity_noise_score_threshold",
                base.single_entity_noise_score_threshold,
            )
        ),
        noise_filter_strength=str(
            payload.get("noise_filter_strength", base.noise_filter_strength)
        ),
    )


def save_noise_filter_settings(
    settings: NoiseFilterSettings,
    path: Optional[Path] = None,
) -> Path:
    """Atomically persist ``settings``.

    Returns the resolved target path. Raises on filesystem errors so
    the dialog can surface them via QMessageBox.critical (mirrors
    ``save_ai_config``).
    """
    if path is None:
        path = default_noise_filter_config_path()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "computed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    payload.update(settings.to_dict())

    serialised = json.dumps(payload, indent=2, ensure_ascii=False)

    tmp = path.parent / f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        tmp.write_text(serialised, encoding="utf-8")
        tmp.replace(path)
    finally:
        # RV-20260508-001 #10 — ``unlink(missing_ok=True)`` collapses
        # the prior 4-line exists/try/unlink/OSError pattern and
        # eliminates the narrow TOCTOU window between exists() and
        # unlink(). The successful path leaves no tmp (replace already
        # consumed it); the failure path may leave a partial tmp that
        # this best-effort unlink reaps.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass

    return path


__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "CONFIG_FILENAME",
    "NoiseFilterSettings",
    "default_noise_filter_config_path",
    "load_noise_filter_settings",
    "save_noise_filter_settings",
]
