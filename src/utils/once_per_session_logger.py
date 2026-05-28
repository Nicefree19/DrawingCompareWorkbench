# -*- coding: utf-8 -*-
"""Once-per-session log throttle helper.

S1.5 of the silent-fallback visibility roadmap. Some module-level
warnings — for example ``QSGLineItem unavailable``, the embedding
backend "falling back to heuristic" notice, or repeated DWG cache reuse
— fire once per pair-load and accumulate to 100+ identical lines per
session, polluting the log stream and drowning out genuinely new
failures.

This module exposes a single helper plus a test-only reset:

    log_once(logger, level, key, message, *args, **kwargs) -> bool

    reset_once_per_session_state() -> None

The first call with a given ``key`` writes at ``level``. Every
subsequent call with the same key is demoted to ``logging.DEBUG`` so
the record still exists in the file (for debugging recurrence
patterns) but stops being part of the user-visible INFO/WARNING
stream.

The helper is generic — each caller is responsible for choosing a key
that's stable across the session (typically a constant or a
combination of module + symbolic event name, NOT a per-call string
that varies on every invocation).

Thread-safe via a module-level lock.

Reference: the inline "Phase N hotfix" cooldown in
``src/services/comparison/ai_classifier/embedding_classifier.py``
(``self._last_error`` check) is the same pattern this helper
generalises.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

_LOCK = threading.Lock()
_SEEN_KEYS: set[str] = set()


def log_once(
    logger: logging.Logger,
    level: int,
    key: str,
    message: str,
    *args: Any,
    **kwargs: Any,
) -> bool:
    """Emit ``message`` at ``level`` only on the first call per ``key``.

    Subsequent calls with the same key are demoted to ``logging.DEBUG``
    with a ``"[throttled:<key>] "`` prefix so the recurrence is
    recorded but does not clutter the user-visible log stream.

    Returns:
        ``True`` if the first-call (full-level) branch ran,
        ``False`` if the call was throttled.

    Thread-safe.

    Notes:
        A degenerate ``key`` (empty string or non-string) is treated
        as "no throttling" and the record is emitted at the requested
        level. This is intentional defensive behaviour so a misuse of
        the helper does not silently swallow log records.
    """

    if not isinstance(key, str) or not key:
        logger.log(level, message, *args, **kwargs)
        return True

    with _LOCK:
        first = key not in _SEEN_KEYS
        if first:
            _SEEN_KEYS.add(key)

    if first:
        logger.log(level, message, *args, **kwargs)
        return True
    logger.debug("[throttled:%s] " + message, key, *args, **kwargs)
    return False


def reset_once_per_session_state() -> None:
    """Drop the recorded set of seen keys.

    Tests call this in setup/teardown so case-to-case throttle state
    does not leak between tests. Production code should not need it —
    the set lives for the entire process lifetime by design.
    """

    with _LOCK:
        _SEEN_KEYS.clear()


__all__ = ["log_once", "reset_once_per_session_state"]
