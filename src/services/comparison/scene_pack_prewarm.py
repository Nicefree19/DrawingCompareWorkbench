# -*- coding: utf-8 -*-
"""Detached scene-pack prewarmer — fills the GLOBAL pack cache after a run.

Why (2026-06-12, "미리보기 실패/뷰어 무거움" root-cause session): the vector
skeleton (scene pack) is what makes the lightweight viewer instant, but it
was only built lazily INSIDE the GUI process on first pair-select — a 115 MB
DXF cold parse + flatten measured at 4-6 minutes per side. A first fix baked
packs inside the viewer-package step, but that step runs in an isolated
proxy process (no warm document cache) and added the same minutes to every
pipeline run.

This module is the resolution: the pipeline LAUNCHES it as a detached,
below-normal-priority process right after the viewer stage and does NOT
wait. It builds packs into the same global cache directory the GUI's
``viewer_session._try_load_cached_pack`` already reads
(``cache/preview/scene_packs/{stem}__{mtime}__{size}/``), so by the time the
reviewer clicks a pair the lazy lookup is a cache HIT. Pipeline wall time:
+0 s. If the prewarmer dies, nothing is lost — the GUI lazy build remains.

Opt-out: ``DRAWING_COMPARE_PREWARM_SCENE_PACKS=0``.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)

PREWARM_ENV = "DRAWING_COMPARE_PREWARM_SCENE_PACKS"


def prewarm_enabled() -> bool:
    return os.environ.get(PREWARM_ENV, "").strip() != "0"


def prewarm_scene_packs(sources: Iterable[str]) -> int:
    """Build packs for each DXF source into the global cache. Returns count.

    Runs inside the detached worker process. Each source is independent;
    one failure never blocks the next.
    """

    from src.services.comparison.scene_pack_builder import build_scene_pack
    from src.services.comparison.viewer_session import _scene_pack_cache_dir

    built = 0
    for raw in sources:
        try:
            source = Path(raw)
            if not source.exists() or source.suffix.lower() != ".dxf":
                continue
            cache_dir = _scene_pack_cache_dir(source)
            overview = cache_dir / "overview_lod0.json"
            if overview.exists():
                logger.info("prewarm: cache already warm for %s", source.name)
                built += 1
                continue
            result = build_scene_pack(source, cache_dir)
            ref = result.scene_pack_ref
            if ref and ref.overview_lod0_path:
                built += 1
                logger.info(
                    "prewarm: built %d primitives for %s (%.0f ms)",
                    ref.primitive_count, source.name, ref.elapsed_build_ms,
                )
            else:
                logger.warning(
                    "prewarm: no overview for %s: %s",
                    source.name, getattr(ref, "notes", ""),
                )
        except Exception:  # noqa: BLE001 - independent best-effort items
            logger.warning("prewarm: failed for %s", raw, exc_info=True)
    return built


def launch_detached_prewarm(sources: Iterable[str]) -> Optional[int]:
    """Fire-and-forget the prewarmer as a separate low-priority process.

    Returns the PID, or None when skipped (disabled / frozen build / no
    usable sources / spawn failure). Never raises — the pipeline must not
    care whether prewarming happens.
    """

    try:
        if not prewarm_enabled():
            return None
        if getattr(sys, "frozen", False):
            # Packaged exe: sys.executable is the app itself — relaunching
            # it with -m would boot the GUI. The lazy GUI build (with the
            # global cache) still covers packaged users; an exe entry flag
            # is a possible follow-up.
            logger.info("prewarm: skipped in frozen build (lazy path covers it)")
            return None
        targets: List[str] = []
        for raw in sources:
            text = str(raw or "").strip()
            if text and Path(text).suffix.lower() == ".dxf" and Path(text).exists():
                targets.append(text)
        if not targets:
            return None
        creationflags = 0
        if os.name == "nt":
            creationflags = (
                subprocess.BELOW_NORMAL_PRIORITY_CLASS
                | subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW
            )
        proc = subprocess.Popen(
            [sys.executable, "-m", "src.services.comparison.scene_pack_prewarm",
             *targets],
            cwd=str(Path(__file__).resolve().parents[3]),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
        logger.info(
            "prewarm: launched detached scene-pack builder pid=%s for %d source(s)",
            proc.pid, len(targets),
        )
        return proc.pid
    except Exception:  # noqa: BLE001 - prewarm is strictly best-effort
        logger.warning("prewarm: detached launch failed", exc_info=True)
        return None


def _main(argv: List[str]) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s prewarm %(levelname)s %(message)s")
    built = prewarm_scene_packs(argv)
    return 0 if built else 1


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    raise SystemExit(_main(sys.argv[1:]))


__all__ = [
    "PREWARM_ENV",
    "launch_detached_prewarm",
    "prewarm_enabled",
    "prewarm_scene_packs",
]
