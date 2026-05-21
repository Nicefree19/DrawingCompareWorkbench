# -*- coding: utf-8 -*-
"""Stable pair identity helpers for drawing comparison artifacts."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


def candidate_pair_uuid(candidate: Any) -> str:
    """Return the canonical storage key for a matched A/B drawing pair."""

    existing = str(getattr(candidate, "pair_uuid", "") or "")
    if existing:
        return existing
    digest = hashlib.sha256()
    for descriptor in (getattr(candidate, "source_a", None), getattr(candidate, "source_b", None)):
        digest.update(_descriptor_identity_token(descriptor).encode("utf-8", errors="ignore"))
        digest.update(b"\0")
    value = f"pair_{digest.hexdigest()[:16]}"
    try:
        candidate.pair_uuid = value
    except Exception:
        pass
    return value


def candidate_display_label(candidate: Any, fallback: str = "pair") -> str:
    """Return the human-readable drawing label for a matched pair."""

    existing = str(getattr(candidate, "display_label", "") or "")
    if existing:
        return existing
    for descriptor in (getattr(candidate, "source_b", None), getattr(candidate, "source_a", None)):
        identity = getattr(descriptor, "identity", None)
        drawing_number = str(getattr(identity, "drawing_number", "") or "")
        if drawing_number:
            return drawing_number
    descriptor = getattr(candidate, "source_b", None) or getattr(candidate, "source_a", None)
    if descriptor and getattr(descriptor, "path", ""):
        return Path(str(descriptor.path)).stem
    return fallback


def safe_display_label(value: str, fallback: str = "drawing") -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return safe.strip("._") or fallback


def _descriptor_identity_token(descriptor: Any) -> str:
    if descriptor is None:
        return "<missing>"
    path_text = str(getattr(descriptor, "path", "") or "")
    try:
        path = Path(path_text)
        resolved = str(path.resolve()).casefold()
        stat = path.stat()
        size = str(stat.st_size)
        mtime = str(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)))
    except Exception:
        resolved = path_text.casefold()
        size = ""
        mtime = ""
    identity = getattr(descriptor, "identity", None)
    drawing_number = str(getattr(identity, "drawing_number", "") or "")
    sheet = str(getattr(identity, "sheet", "") or "")
    return "|".join([resolved, size, mtime, drawing_number, sheet])
