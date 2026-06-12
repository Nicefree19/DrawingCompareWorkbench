# -*- coding: utf-8 -*-
"""Strip the dead-weight OBJECTS section from ODA-converted DXF files.

User insight that led here (2026-06-12): "실제로 대용량 파일이 아닌데 ODA를
통해서 DXF로 변환되면서 대형 캐드화 되는 거 같은데 비효율적인 방식인 거
같아." Measured on the real failing pairs:

* SPLICE detail: 1.0 MB DWG → 65.7 MB ASCII DXF, of which the OBJECTS
  section (third-party proxy dictionaries, scale lists, etc.) is
  **54.5 MB = 94%**. The actual drawing (ENTITIES+BLOCKS) is 3.1 MB.
* Rebar-interference sheet: 114.8 MB DXF → OBJECTS 62.6 MB (62%).

The comparison pipeline reads ENTITIES/BLOCKS/TABLES only — the OBJECTS
payload buys nothing and costs everything downstream: it pushed small
drawings over the 25 MB legacy-comparator threshold, multiplied parse
times, and starved the viewer render inside its timeout ("미리보기 실패").

Measured after stripping (SPLICE): 65.7 → 3.7 MB, ezdxf read 4.3 s →
0.44 s, extraction signatures IDENTICAL, scene-pack build 5.1 s → 1.2 s
with identical primitive counts. ezdxf replaces dangling style handles
(e.g. MLEADERSTYLE) with 'Standard' — the resilient render path already
tolerates that.

Safety: the slimmed file is verified by re-reading it with ezdxf and
comparing modelspace/blocks entity counts against the original; any
mismatch or error keeps the original file. Opt-out:
``DRAWING_COMPARE_SLIM_CONVERTED_DXF=0``.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

SLIM_ENV = "DRAWING_COMPARE_SLIM_CONVERTED_DXF"
# Below this size the legacy threshold / parse costs don't bite — skip.
SLIM_MIN_BYTES = 8 * 1024 * 1024


def slimming_enabled() -> bool:
    return os.environ.get(SLIM_ENV, "").strip() != "0"


def strip_objects_section(src: Path, dst: Path) -> dict:
    """Write ``src`` minus its OBJECTS section to ``dst``. Returns stats."""

    lines = src.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    out: list[str] = []
    i = 0
    n = len(lines)
    dropped_lines = 0
    while i < n:
        if (
            lines[i].strip() == "0"
            and i + 3 < n
            and lines[i + 1].strip() == "SECTION"
            and lines[i + 2].strip() == "2"
            and lines[i + 3].strip() == "OBJECTS"
        ):
            j = i + 4
            while j + 1 < n and not (
                lines[j].strip() == "0" and lines[j + 1].strip() == "ENDSEC"
            ):
                j += 1
            dropped_lines += (j + 2) - i
            i = j + 2
            continue
        out.append(lines[i])
        i += 1
    dst.write_text("".join(out), encoding="utf-8")
    return {
        "src_bytes": src.stat().st_size,
        "dst_bytes": dst.stat().st_size,
        "dropped_lines": dropped_lines,
    }


def _doc_shape(path: Path) -> Tuple[int, int, int]:
    """(modelspace entities, block layouts, total block entities) for parity checks."""

    import ezdxf

    doc = ezdxf.readfile(str(path))
    return (
        len(doc.modelspace()),
        len(doc.blocks),
        sum(len(b) for b in doc.blocks),
    )


def slim_converted_dxf(path: Path) -> Tuple[Path, str]:
    """Slim an ODA-converted DXF IN PLACE when safe. Returns (path, note).

    Notes: ``slimmed`` (replaced with the verified slim file),
    ``skipped_small`` / ``skipped_disabled`` / ``skipped_no_gain``,
    or ``kept_original:<reason>`` when verification refused the slim copy.
    Never raises; never leaves a partially-written file behind.
    """

    target = Path(path)
    if not slimming_enabled():
        return target, "skipped_disabled"
    try:
        if target.stat().st_size < SLIM_MIN_BYTES:
            return target, "skipped_small"
    except OSError:
        return target, "kept_original:stat_failed"

    tmp = target.with_suffix(".slim.tmp.dxf")
    try:
        t0 = time.perf_counter()
        stats = strip_objects_section(target, tmp)
        if stats["dst_bytes"] >= stats["src_bytes"] * 0.95 or stats["dropped_lines"] == 0:
            tmp.unlink(missing_ok=True)
            return target, "skipped_no_gain"
        # Parity gate: the slim file must carry the SAME drawing content.
        original_shape = _doc_shape(target)
        slim_shape = _doc_shape(tmp)
        if original_shape != slim_shape:
            tmp.unlink(missing_ok=True)
            logger.warning(
                "DXF slimming refused for %s: shape %s != %s",
                target.name, original_shape, slim_shape,
            )
            return target, "kept_original:shape_mismatch"
        os.replace(tmp, target)
        logger.info(
            "Slimmed converted DXF %s: %.1f MB -> %.1f MB (-%d%%, %d lines) "
            "verified identical shape %s in %.1f s",
            target.name,
            stats["src_bytes"] / 1e6,
            stats["dst_bytes"] / 1e6,
            100 - stats["dst_bytes"] * 100 // max(1, stats["src_bytes"]),
            stats["dropped_lines"],
            original_shape,
            time.perf_counter() - t0,
        )
        return target, "slimmed"
    except Exception as exc:  # noqa: BLE001 - slimming must never break conversion
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        logger.warning("DXF slimming failed for %s: %s", target.name, exc)
        return target, f"kept_original:{type(exc).__name__}"


__all__ = [
    "SLIM_ENV",
    "SLIM_MIN_BYTES",
    "slim_converted_dxf",
    "slimming_enabled",
    "strip_objects_section",
]
