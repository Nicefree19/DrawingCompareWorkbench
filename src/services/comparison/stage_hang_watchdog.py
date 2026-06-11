# -*- coding: utf-8 -*-
"""Self-diagnosing watchdog for silent pipeline-stage hangs.

Why (2026-06-11 live incident): a GUI compare of a real 65.7 MB DWG pair
sat in the ``compare`` stage for 65+ minutes burning ~1.5 cores with no
events, no errors, no artifacts — while a headless rerun of the SAME
pair finished in 62.7 s. py-spy could not attach to the GUI process
(os error 299), so the hang died undiagnosed when the app was killed.

This watchdog turns the NEXT such hang into its own diagnosis: if no
stage transition is recorded for ``timeout_s``, it writes every Python
thread's stack (``faulthandler.dump_traceback``) into the run directory
and logs an ERROR naming the file. It never interrupts or cancels the
run — observation only.

Tunables: ``DRAWING_COMPARE_HANG_DUMP_S`` (seconds, default 600;
``0`` disables).
"""

from __future__ import annotations

import faulthandler
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

HANG_DUMP_ENV = "DRAWING_COMPARE_HANG_DUMP_S"
DEFAULT_TIMEOUT_S = 600.0
_POLL_S = 5.0


def resolve_hang_dump_timeout_s() -> float:
    """Env-tunable timeout; 0 (or invalid negative) disables the watchdog."""

    raw = os.environ.get(HANG_DUMP_ENV, "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_S
    return max(0.0, value)


class StageHangWatchdog:
    """Dump all thread stacks when a pipeline stage stops making progress.

    ``pet(label)`` on every stage transition; one dump fires per stall
    (re-armed by the next pet so a later, different stall still reports).
    """

    def __init__(
        self,
        output_dir: Path | str,
        *,
        timeout_s: Optional[float] = None,
    ) -> None:
        self._output_dir = Path(output_dir)
        self._timeout_s = (
            resolve_hang_dump_timeout_s() if timeout_s is None else float(timeout_s)
        )
        self._last_progress = time.monotonic()
        self._label = "(start)"
        self._fired = False
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.dump_path: Optional[Path] = None  # set when a dump fires

    # -- lifecycle -------------------------------------------------------

    def start(self) -> "StageHangWatchdog":
        if self._timeout_s <= 0:
            return self
        self._thread = threading.Thread(
            target=self._loop, name="stage-hang-watchdog", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=_POLL_S * 2)

    def __enter__(self) -> "StageHangWatchdog":
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()

    # -- progress --------------------------------------------------------

    def pet(self, label: str) -> None:
        """Record progress; re-arms the one-shot dump."""

        with self._lock:
            self._last_progress = time.monotonic()
            self._label = str(label)
            self._fired = False

    # -- internals -------------------------------------------------------

    def _loop(self) -> None:
        poll = min(_POLL_S, max(0.05, self._timeout_s / 4.0))
        while not self._stop.wait(poll):
            with self._lock:
                stalled = (
                    not self._fired
                    and (time.monotonic() - self._last_progress) > self._timeout_s
                )
                label = self._label
                if stalled:
                    self._fired = True
            if stalled:
                self._dump(label)

    def _dump(self, label: str) -> None:
        try:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = self._output_dir / f"hang_stacks_{stamp}.log"
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    f"Stage hang watchdog: no stage transition for "
                    f"{self._timeout_s:.0f}s after {label!r} "
                    f"(dumped {datetime.now().isoformat()})\n\n"
                )
                fh.flush()
                faulthandler.dump_traceback(file=fh, all_threads=True)
            self.dump_path = path
            logger.error(
                "Pipeline stage made no progress for %.0fs after %r — "
                "all thread stacks dumped to %s (run continues; attach this "
                "file to the report if it never finishes)",
                self._timeout_s, label, path,
            )
        except Exception:  # noqa: BLE001 - diagnosis must never break the run
            logger.warning("Stage hang watchdog dump failed", exc_info=True)


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "HANG_DUMP_ENV",
    "StageHangWatchdog",
    "resolve_hang_dump_timeout_s",
]
