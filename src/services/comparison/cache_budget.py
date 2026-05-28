# -*- coding: utf-8 -*-
"""Shared helpers for byte-aware render/viewer cache budgets."""

from __future__ import annotations

import os

RENDER_CACHE_MB_ENV_VAR = "DRAWING_COMPARE_RENDER_CACHE_MB"


def resolve_cache_byte_limit(
    *,
    specific_env_var: str = "",
    default_mb: float = 256.0,
    min_bytes: int = 1,
) -> int:
    """Resolve a cache byte limit from a specific env var or shared fallback."""

    for env_name in (specific_env_var, RENDER_CACHE_MB_ENV_VAR):
        if not env_name:
            continue
        raw = os.environ.get(env_name)
        if not raw:
            continue
        try:
            mb_value = float(raw)
        except (TypeError, ValueError):
            continue
        if mb_value > 0:
            return max(int(min_bytes), int(mb_value * 1024 * 1024))
    return max(int(min_bytes), int(float(default_mb) * 1024 * 1024))


def process_rss_mb() -> float:
    """Return current process RSS in MiB, or 0 when unavailable."""

    try:
        import psutil  # type: ignore[import-not-found]

        return round(float(psutil.Process().memory_info().rss) / (1024.0 * 1024.0), 3)
    except Exception:
        return 0.0


__all__ = [
    "RENDER_CACHE_MB_ENV_VAR",
    "process_rss_mb",
    "resolve_cache_byte_limit",
]
