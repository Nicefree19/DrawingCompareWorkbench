# -*- coding: utf-8 -*-
"""Cold + cache-hit p50/p95 benchmark for the selected-zone render path.

Plan §17 Phase B-1b (GPT Pro F3 HIGH follow-up). The legacy
``_render_pdf_image_crop`` opens a full-page PNG via PIL for every
zone; the new DisplayList path renders only the clip region. This
benchmark measures the actual cold + cache-hit latency split so the
gates from the recommendation (cold p95 ≤ 2000 ms, cache-hit p95 ≤
500 ms) can be enforced in CI.

Two fixture types
-----------------
- **PDF** fixtures are synthesised inline using PyMuPDF (the
  ``--fixture`` choice selects the page size and entity count).
  Synthetic content is enough to exercise the DisplayList cache
  because the cache key is built from the file signature, not from
  visual content.
- **DXF** fixtures reuse the existing
  ``tests/data/comparison/golden/dxf/02_single_modification/before.dxf``
  so the harness can be cross-checked against the production
  ``DrawingRenderIndex`` cache.

Reading the report
------------------
- ``cold_pXX`` = p50 / p95 across all ``runs * zones`` measurements
  after wiping the cache between every zone (worst case — every zone
  pays the full parse + clip cost).
- ``cache_hit_pXX`` = same measurements immediately re-run with the
  cache populated. The wire metric the recommendation gates on.

Usage
-----
::

    python -X utf8 scripts/benchmark_zone_render.py \\
        --fixture small --zones 10 --runs 5 \\
        --cold-p95-target-ms 2000 --cache-hit-p95-target-ms 500

Exit code is 0 on PASS (both gates met) and 1 on FAIL unless
``--no-fail-on-exceed`` is passed.

This script is NOT a production code path. The unit smoke tests at
``tests/unit/scripts/test_benchmark_zone_render.py`` cover the helpers
only — they intentionally do NOT execute the full benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# Reuse the existing DXF golden fixture (Plan §16 Phase C-3.2 uses the
# same `02_single_modification` shape for a different benchmark). This
# keeps fixture maintenance to one location.
DXF_GOLDEN_DIR = (
    _REPO_ROOT
    / "tests"
    / "data"
    / "comparison"
    / "golden"
    / "dxf"
    / "02_single_modification"
)

# PDF fixtures are synthesised on demand — see ``_build_pdf_fixture``.
# Different sizes exercise different DisplayList build costs.
PDF_FIXTURE_SPECS: Dict[str, Dict[str, Any]] = {
    "small": {"page_size": (612, 792), "rect_count": 36},
    "medium": {"page_size": (1190, 1684), "rect_count": 40},
    "large": {"page_size": (1684, 2384), "rect_count": 200},
}


DEFAULT_ZONES = 10
DEFAULT_RUNS = 5
DEFAULT_COLD_P95_MS = 2000.0
DEFAULT_CACHE_HIT_P95_MS = 500.0


@dataclass
class FixturePaths:
    """Resolved fixture artefacts for one benchmark fixture choice."""

    source_pdf: Path
    background_png: Path
    bg_w: int
    bg_h: int
    page_index: int = 0


@dataclass
class ZoneRenderMeasurement:
    """One selected-zone render attempt with diagnostics kept for audit."""

    phase: str
    run_index: int
    zone_id: str
    elapsed_ms: float
    result_elapsed_ms: float
    cache_hit: bool
    renderer_backend: str
    visual_fidelity: str
    render_lifecycle: str
    reason_code: str
    warning_count: int
    warnings: List[str]
    request_id: str
    result_request_id: str
    request_id_mismatch: bool
    rss_before_mb: float
    rss_after_mb: float
    rss_delta_mb: Optional[float]
    before_image: str
    after_image: str
    before_image_bytes: int
    after_image_bytes: int
    before_image_blank: Optional[bool]
    after_image_blank: Optional[bool]
    output_blank: Optional[bool]
    error: str = ""
    pdf_display_list_cache: Dict[str, Any] | None = None
    dxf_index_cache: Dict[str, Any] | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "run_index": self.run_index,
            "zone_id": self.zone_id,
            "elapsed_ms": round(float(self.elapsed_ms), 3),
            "result_elapsed_ms": round(float(self.result_elapsed_ms), 3),
            "cache_hit": bool(self.cache_hit),
            "renderer_backend": self.renderer_backend,
            "visual_fidelity": self.visual_fidelity,
            "render_lifecycle": self.render_lifecycle,
            "reason_code": self.reason_code,
            "warning_count": int(self.warning_count),
            "warnings": list(self.warnings),
            "request_id": self.request_id,
            "result_request_id": self.result_request_id,
            "request_id_mismatch": bool(self.request_id_mismatch),
            "rss_before_mb": round(float(self.rss_before_mb), 3),
            "rss_after_mb": round(float(self.rss_after_mb), 3),
            "rss_delta_mb": _round_optional(self.rss_delta_mb),
            "before_image": self.before_image,
            "after_image": self.after_image,
            "before_image_bytes": int(self.before_image_bytes),
            "after_image_bytes": int(self.after_image_bytes),
            "before_image_blank": self.before_image_blank,
            "after_image_blank": self.after_image_blank,
            "output_blank": self.output_blank,
            "nonblank_status": (
                None if self.output_blank is None else not self.output_blank
            ),
            "error": self.error,
            "pdf_display_list_cache": dict(self.pdf_display_list_cache or {}),
            "dxf_index_cache": dict(self.dxf_index_cache or {}),
        }


@dataclass
class BenchmarkPassResult:
    cold_samples_ms: List[float]
    cache_hit_samples_ms: List[float]
    measurements: List[ZoneRenderMeasurement]


def _build_pdf_fixture(spec_name: str, scratch_dir: Path) -> FixturePaths:
    """Synthesize a PDF + matching pre-rendered background PNG.

    Mirrors the production layout where ``before_background_image``
    is the full-page PNG previously produced by
    ``viewer_package._render_pdf_to_png`` and the cache key includes
    ``page_index`` from the background transform.
    """
    spec = PDF_FIXTURE_SPECS[spec_name]
    page_w, page_h = spec["page_size"]
    rect_count = int(spec["rect_count"])

    import fitz  # type: ignore[import-not-found]

    pdf_path = scratch_dir / f"benchmark_{spec_name}.pdf"
    bg_path = scratch_dir / f"benchmark_{spec_name}.png"

    doc = fitz.open()
    try:
        page = doc.new_page(width=float(page_w), height=float(page_h))
        # Distribute rects across the page so the DisplayList has real
        # content to parse — empty pages parse in microseconds.
        cols = max(1, int(rect_count ** 0.5))
        rows = (rect_count + cols - 1) // cols
        cell_w = page_w / max(cols + 1, 2)
        cell_h = page_h / max(rows + 1, 2)
        for i in range(rect_count):
            col = i % cols
            row = i // cols
            x0 = (col + 0.5) * cell_w
            y0 = (row + 0.5) * cell_h
            page.draw_rect(
                fitz.Rect(x0, y0, x0 + cell_w * 0.7, y0 + cell_h * 0.7),
                color=(0, 0, 0),
            )
            page.insert_text(
                (x0 + 5, y0 + 12),
                f"r-{i}",
                fontsize=8,
            )
        doc.save(str(pdf_path))

        # Pre-rendered background at 144 DPI (scale=2.0) — mirrors what
        # viewer_package would produce for the same PDF.
        page = doc[0]
        matrix = fitz.Matrix(2.0, 2.0)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        pixmap.save(str(bg_path))
        bg_w = int(pixmap.width)
        bg_h = int(pixmap.height)
    finally:
        doc.close()

    return FixturePaths(
        source_pdf=pdf_path,
        background_png=bg_path,
        bg_w=bg_w,
        bg_h=bg_h,
        page_index=0,
    )


def _build_zones(fixture: FixturePaths, zones: int) -> List[Dict[str, Any]]:
    """Produce ``zones`` non-overlapping (mostly) crop windows in
    image-pixel coordinates of the background PNG. Each zone covers
    roughly 1/zones of the page area so DisplayList re-uses are
    plausible but not 100% trivially cached.
    """
    out: List[Dict[str, Any]] = []
    cols = max(1, int(zones ** 0.5))
    rows = (zones + cols - 1) // cols
    cell_w = fixture.bg_w / cols
    cell_h = fixture.bg_h / rows
    for i in range(zones):
        col = i % cols
        row = i // cols
        x0 = col * cell_w
        y0 = row * cell_h
        out.append(
            {
                "zone_id": f"Z-{i:03d}",
                "xmin": float(x0),
                "ymin": float(y0),
                "xmax": float(x0 + cell_w),
                "ymax": float(y0 + cell_h),
            }
        )
    return out


def _percentile(samples: List[float], pct: float) -> float:
    """Linear-interpolation percentile. ``pct`` is in [0, 100]."""
    if not samples:
        return float("nan")
    if len(samples) == 1:
        return samples[0]
    ordered = sorted(samples)
    k = (len(ordered) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def _round_optional(value: Optional[float], digits: int = 3) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _process_rss_mb() -> float:
    try:
        from src.services.comparison.cache_budget import process_rss_mb

        return float(process_rss_mb())
    except Exception:
        return 0.0


def _image_size_bytes(path: str) -> int:
    if not path:
        return 0
    try:
        return int(Path(path).stat().st_size)
    except OSError:
        return 0


def _image_blank_status(path: str) -> Optional[bool]:
    """Return True when the PNG is effectively white/transparent.

    ``None`` means the check itself was unavailable, usually because
    Pillow is not installed or the file could not be decoded.
    """

    if not path:
        return None
    image_path = Path(path)
    if not image_path.exists():
        return None
    try:
        from PIL import Image, ImageStat  # type: ignore[import-not-found]

        with Image.open(image_path) as image:
            rgba = image.convert("RGBA")
            alpha = rgba.getchannel("A")
            alpha_extrema = alpha.getextrema()
            if alpha_extrema[1] == 0:
                return True
            rgb = rgba.convert("RGB")
            stat = ImageStat.Stat(rgb)
            # White backgrounds are expected; visible ink lowers at
            # least one channel meaningfully below white.
            return bool(min(stat.extrema[0][0], stat.extrema[1][0], stat.extrema[2][0]) >= 250)
    except Exception:
        return None


def _counter(values: List[str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for value in values:
        key = str(value or "").strip()
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def _contains_token(measurement: ZoneRenderMeasurement, token: str) -> bool:
    needle = token.lower()
    fields = [measurement.reason_code, measurement.render_lifecycle, measurement.error]
    fields.extend(measurement.warnings)
    return any(needle in str(item).lower() for item in fields if item)


def _is_fallback_measurement(item: ZoneRenderMeasurement) -> bool:
    return bool(
        item.reason_code
        or item.render_lifecycle not in ("ready", "")
        or item.visual_fidelity == "relative_overlay"
    )


def _summarize_measurements(
    measurements: List[ZoneRenderMeasurement],
) -> Dict[str, Any]:
    rss_values = [
        value
        for item in measurements
        for value in (item.rss_before_mb, item.rss_after_mb)
        if value > 0
    ]
    rss_deltas = [
        item.rss_delta_mb
        for item in measurements
        if item.rss_delta_mb is not None
    ]
    blank_values = [
        item.output_blank
        for item in measurements
        if item.output_blank is not None
    ]
    missing_output_count = sum(
        1
        for item in measurements
        if item.before_image_bytes <= 0 or item.after_image_bytes <= 0
    )
    fallback_count = sum(1 for item in measurements if _is_fallback_measurement(item))
    fallback_missing_reason_count = sum(
        1
        for item in measurements
        if _is_fallback_measurement(item) and not item.reason_code
    )
    render_failure_count = sum(
        1 for item in measurements if item.render_lifecycle != "ready"
    )
    error_count = sum(1 for item in measurements if item.error)
    stale_count = sum(1 for item in measurements if item.request_id_mismatch)
    blank_count = sum(1 for value in blank_values if value is True)
    total = len(measurements)
    phase_rss: Dict[str, float] = {}
    for phase in ("cold", "cache_hit"):
        phase_deltas = [
            float(item.rss_delta_mb)
            for item in measurements
            if item.phase == phase and item.rss_delta_mb is not None
        ]
        phase_rss[f"{phase}_rss_delta_mb_p95"] = (
            _round_optional(_percentile(phase_deltas, 95.0), 3)
            if phase_deltas
            else 0.0
        )
        phase_rss[f"{phase}_rss_delta_mb_max"] = (
            _round_optional(max(phase_deltas), 3) if phase_deltas else 0.0
        )
    render_health_pass = (
        render_failure_count == 0
        and error_count == 0
        and stale_count == 0
        and blank_count == 0
        and missing_output_count == 0
    )
    return {
        "render_attempt_count": total,
        "render_failure_count": render_failure_count,
        "render_exception_count": error_count,
        "render_health_pass": render_health_pass,
        "blank_check_count": len(blank_values),
        "blank_output_count": blank_count,
        "blank_output_ratio": round(blank_count / len(blank_values), 4) if blank_values else 0.0,
        "output_missing_count": missing_output_count,
        "stale_render_count": stale_count,
        "stale_result_visible_count": stale_count,
        "fallback_count": fallback_count,
        "fallback_missing_reason_count": fallback_missing_reason_count,
        "timeout_count": sum(1 for item in measurements if _contains_token(item, "timeout")),
        "cancel_count": sum(1 for item in measurements if _contains_token(item, "cancel")),
        "warning_count_total": sum(item.warning_count for item in measurements),
        "renderer_backend_counts": _counter([item.renderer_backend for item in measurements]),
        "visual_fidelity_counts": _counter([item.visual_fidelity for item in measurements]),
        "render_lifecycle_counts": _counter([item.render_lifecycle for item in measurements]),
        "reason_code_counts": _counter([item.reason_code for item in measurements]),
        "rss_measurement_available": bool(rss_values),
        "rss_peak_mb": _round_optional(max(rss_values), 3) if rss_values else 0.0,
        "rss_min_mb": _round_optional(min(rss_values), 3) if rss_values else 0.0,
        "rss_delta_mb_max": _round_optional(max(rss_deltas), 3) if rss_deltas else 0.0,
        "rss_delta_mb_p95": _round_optional(_percentile([float(v) for v in rss_deltas], 95.0), 3)
        if rss_deltas
        else 0.0,
        **phase_rss,
    }


def _run_one_zone(
    fixture: FixturePaths,
    zone: Dict[str, Any],
    cache_root: Path,
    *,
    clear_cache_first: bool,
    phase: str,
    run_index: int,
) -> ZoneRenderMeasurement:
    """Render one zone (before+after both point at the same fixture
    so the harness drives the DisplayList path twice per zone, which
    mirrors production where the before/after PDFs are usually the
    same shape). Returns wall time plus render-health evidence.
    """
    from src.services.comparison.zone_render_service import (
        RenderJob,
        WorldWindow,
        render_zone_pair,
    )
    from src.services.comparison import pdf_display_list_cache

    if clear_cache_first:
        # Wipe BOTH the DisplayList cache AND the on-disk zone cache
        # so the next render hits the cold path end-to-end.
        pdf_display_list_cache._clear_cache()
        if cache_root.exists():
            shutil.rmtree(cache_root, ignore_errors=True)
        cache_root.mkdir(parents=True, exist_ok=True)

    bg_transform = {
        "coordinate_space": "image_pixels",
        "min_x": 0.0,
        "min_y": 0.0,
        "max_x": float(fixture.bg_w),
        "max_y": float(fixture.bg_h),
        "img_width": fixture.bg_w,
        "img_height": fixture.bg_h,
        "scale_x": 1.0,
        "scale_y": 1.0,
        "page": fixture.page_index,
    }
    job = RenderJob(
        pair_uuid="benchmark-pair",
        zone_id=zone["zone_id"],
        source_before=fixture.source_pdf,
        source_after=fixture.source_pdf,
        world_window=WorldWindow(
            zone["xmin"], zone["ymin"], zone["xmax"], zone["ymax"]
        ),
        cache_root=cache_root,
        dxf_cache_dir=cache_root / "dxf",
        request_id=f"benchmark:{phase}:{run_index}:{zone['zone_id']}",
        before_background_image=str(fixture.background_png),
        after_background_image=str(fixture.background_png),
        before_background_transform=bg_transform,
        after_background_transform=bg_transform,
    )

    rss_before = _process_rss_mb()
    started = time.perf_counter()
    try:
        result = render_zone_pair(job)
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        rss_after = _process_rss_mb()
        return ZoneRenderMeasurement(
            phase=phase,
            run_index=run_index,
            zone_id=str(zone["zone_id"]),
            elapsed_ms=elapsed_ms,
            result_elapsed_ms=0.0,
            cache_hit=False,
            renderer_backend="",
            visual_fidelity="",
            render_lifecycle="exception",
            reason_code=type(exc).__name__,
            warning_count=0,
            warnings=[],
            request_id=job.request_id,
            result_request_id="",
            request_id_mismatch=False,
            rss_before_mb=rss_before,
            rss_after_mb=rss_after,
            rss_delta_mb=(
                round(rss_after - rss_before, 3)
                if rss_before > 0 and rss_after > 0
                else None
            ),
            before_image="",
            after_image="",
            before_image_bytes=0,
            after_image_bytes=0,
            before_image_blank=None,
            after_image_blank=None,
            output_blank=None,
            error=f"{type(exc).__name__}: {exc}",
        )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    rss_after = _process_rss_mb()
    before_blank = _image_blank_status(result.before_image)
    after_blank = _image_blank_status(result.after_image)
    output_blank = (
        before_blank and after_blank
        if before_blank is not None and after_blank is not None
        else None
    )
    return ZoneRenderMeasurement(
        phase=phase,
        run_index=run_index,
        zone_id=str(zone["zone_id"]),
        elapsed_ms=elapsed_ms,
        result_elapsed_ms=float(result.elapsed_ms or 0.0),
        cache_hit=bool(result.cache_hit),
        renderer_backend=result.renderer_backend,
        visual_fidelity=result.visual_fidelity,
        render_lifecycle=result.render_lifecycle,
        reason_code=result.reason_code,
        warning_count=len(result.warnings),
        warnings=list(result.warnings),
        request_id=job.request_id,
        result_request_id=result.request_id,
        request_id_mismatch=bool(result.request_id != job.request_id),
        rss_before_mb=rss_before,
        rss_after_mb=rss_after,
        rss_delta_mb=(
            round(rss_after - rss_before, 3)
            if rss_before > 0 and rss_after > 0
            else None
        ),
        before_image=result.before_image,
        after_image=result.after_image,
        before_image_bytes=_image_size_bytes(result.before_image),
        after_image_bytes=_image_size_bytes(result.after_image),
        before_image_blank=before_blank,
        after_image_blank=after_blank,
        output_blank=output_blank,
        pdf_display_list_cache=dict(result.pdf_display_list_cache or {}),
        dxf_index_cache=dict(result.dxf_index_cache or {}),
    )


def _run_pass(
    fixture: FixturePaths,
    zones: List[Dict[str, Any]],
    runs: int,
    cache_root: Path,
) -> BenchmarkPassResult:
    """Drive the cold + cache_hit measurement loop.

    Returns cold/cache-hit latency samples plus per-render evidence.

    Methodology
    -----------
    For each ``run`` 1..N:
      - For each ``zone``:
          1. Cold: clear cache, render, record wall_ms.
          2. Cache hit: render the same zone again immediately,
             record wall_ms.

    The cache_hit measurement is dominated by the on-disk zone-cache
    JSON read (``render_zone_pair`` early-returns when
    ``meta_path.exists()``). The DisplayList cache hit is a sub-step
    inside that.
    """
    cold_samples: List[float] = []
    hit_samples: List[float] = []
    measurements: List[ZoneRenderMeasurement] = []
    for run_idx in range(1, runs + 1):
        for zone in zones:
            cold = _run_one_zone(
                fixture,
                zone,
                cache_root,
                clear_cache_first=True,
                phase="cold",
                run_index=run_idx,
            )
            cold_samples.append(cold.elapsed_ms)
            measurements.append(cold)
            hit = _run_one_zone(
                fixture,
                zone,
                cache_root,
                clear_cache_first=False,
                phase="cache_hit",
                run_index=run_idx,
            )
            hit_samples.append(hit.elapsed_ms)
            measurements.append(hit)
        print(
            f"[bench] run={run_idx}/{runs} cold_count={len(cold_samples)} "
            f"hit_count={len(hit_samples)}",
            file=sys.stderr,
            flush=True,
        )
    return BenchmarkPassResult(cold_samples, hit_samples, measurements)


def _short_git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(_REPO_ROOT),
            encoding="utf-8",
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def _prefixed_cache_fields(prefix: str, stats: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for key in (
        "entries",
        "capacity_entries",
        "total_estimated_bytes",
        "byte_limit",
        "entry_estimated_bytes_max",
        "hit_count",
        "miss_count",
        "lookup_count",
        "hit_rate",
        "eviction_count",
        "evicted_estimated_bytes",
        "last_eviction_reason",
        "worker_rss_mb",
    ):
        if key in stats:
            payload[f"{prefix}_{key}"] = stats.get(key)
    return payload


def _repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_REPO_ROOT.resolve())).replace("\\", "/")
    except Exception:
        return str(path)


def _file_fingerprint(path: Path) -> Dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        return {"path": _repo_relative(path), "exists": False}
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        pass
    return {
        "path": _repo_relative(path),
        "exists": True,
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "sha256": digest.hexdigest(),
    }


def _source_signature(paths: List[Path]) -> str:
    payload = [_file_fingerprint(path) for path in paths]
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def _dirty_worktree() -> bool:
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(_REPO_ROOT),
            encoding="utf-8",
        )
        return bool(out.stdout.strip())
    except Exception:
        return True


def _module_available(module_name: str) -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec(module_name) is not None
    except Exception:
        return False


def _build_environment() -> Dict[str, Any]:
    import platform

    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "psutil_available": _module_available("psutil"),
        "pillow_available": _module_available("PIL"),
        "fitz_available": _module_available("fitz"),
    }


def _gate(name: str, observed: Any, threshold: Any, op: str) -> Dict[str, Any]:
    if op == "==":
        passed = observed == threshold
    elif op == "<=":
        try:
            passed = float(observed) <= float(threshold)
        except (TypeError, ValueError):
            passed = False
    else:
        passed = False
    return {
        "name": name,
        "passed": bool(passed),
        "observed": observed,
        "threshold": threshold,
        "op": op,
    }


def _build_gates(
    summary: Dict[str, Any],
    *,
    cold_p95_target_ms: float,
    cache_hit_p95_target_ms: float,
) -> List[Dict[str, Any]]:
    return [
        _gate("cold_p95_ms", summary.get("cold_p95_ms"), cold_p95_target_ms, "<="),
        _gate(
            "cache_hit_p95_ms",
            summary.get("cache_hit_p95_ms"),
            cache_hit_p95_target_ms,
            "<=",
        ),
        _gate("render_exception_count", summary.get("render_exception_count"), 0, "=="),
        _gate("render_failure_count", summary.get("render_failure_count"), 0, "=="),
        _gate("blank_output_count", summary.get("blank_output_count"), 0, "=="),
        _gate("output_missing_count", summary.get("output_missing_count"), 0, "=="),
        _gate(
            "stale_result_visible_count",
            summary.get("stale_result_visible_count"),
            0,
            "==",
        ),
        _gate("timeout_count", summary.get("timeout_count"), 0, "=="),
        _gate("cancel_count", summary.get("cancel_count"), 0, "=="),
        _gate(
            "fallback_missing_reason_count",
            summary.get("fallback_missing_reason_count"),
            0,
            "==",
        ),
        _gate(
            "rss_measurement_available",
            summary.get("rss_measurement_available"),
            True,
            "==",
        ),
        _gate(
            "pdf_display_list_cache_retained_bytes",
            summary.get("pdf_display_list_cache_total_estimated_bytes", 0),
            summary.get("pdf_display_list_cache_byte_limit", 0),
            "<=",
        ),
        _gate(
            "dxf_index_cache_retained_bytes",
            summary.get("dxf_index_cache_total_estimated_bytes", 0),
            summary.get("dxf_index_cache_byte_limit", 0),
            "<=",
        ),
    ]


def _build_summary(
    *,
    fixture_name: str,
    fixture: FixturePaths,
    zones: int,
    runs: int,
    cold_samples: List[float],
    hit_samples: List[float],
    cold_p95_target_ms: float,
    cache_hit_p95_target_ms: float,
    cold_pass: bool,
    hit_pass: bool,
    display_list_cache_stats: Dict[str, Any],
    dxf_index_cache_stats: Dict[str, Any],
    measurement_summary: Dict[str, Any],
    measurements: List[ZoneRenderMeasurement],
    report_path: Path,
    json_path: Path,
    scratch_dir: Path,
    cache_root: Path,
) -> Dict[str, Any]:
    legacy_summary: Dict[str, Any] = {
        "fixture": fixture_name,
        "zones": zones,
        "runs": runs,
        "cold_p50_ms": _percentile(cold_samples, 50.0),
        "cold_p95_ms": _percentile(cold_samples, 95.0),
        "cache_hit_p50_ms": _percentile(hit_samples, 50.0),
        "cache_hit_p95_ms": _percentile(hit_samples, 95.0),
        "cold_pass": cold_pass,
        "cache_hit_pass": hit_pass,
        "measurement_summary": dict(measurement_summary),
    }
    legacy_summary.update(measurement_summary)
    legacy_summary.update(_prefixed_cache_fields("pdf_display_list_cache", display_list_cache_stats))
    legacy_summary.update(_prefixed_cache_fields("dxf_index_cache", dxf_index_cache_stats))
    gates = _build_gates(
        legacy_summary,
        cold_p95_target_ms=cold_p95_target_ms,
        cache_hit_p95_target_ms=cache_hit_p95_target_ms,
    )
    summary: Dict[str, Any] = {
        "schema_version": 2,
        "benchmark_id": "p5_g15_zone_render_memory",
        "profile": "selected_zone_render_memory",
        "status": "passed" if all(gate["passed"] for gate in gates) else "failed",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": {
            "git_short_sha": _short_git_sha(),
            "dirty_worktree": _dirty_worktree(),
            "source_signature": _source_signature([Path(__file__), fixture.source_pdf]),
        },
        "args": {
            "fixture": fixture_name,
            "zones": zones,
            "runs": runs,
            "cold_p95_target_ms": cold_p95_target_ms,
            "cache_hit_p95_target_ms": cache_hit_p95_target_ms,
        },
        "environment": _build_environment(),
        "artifacts": {
            "report": _repo_relative(report_path),
            "json": _repo_relative(json_path),
            "scratch_dir": _repo_relative(scratch_dir),
            "cache_root": _repo_relative(cache_root),
            "source_pdf": _repo_relative(fixture.source_pdf),
            "background_png": _repo_relative(fixture.background_png),
        },
        "gates": gates,
        "summary": dict(legacy_summary),
        "measurements": [item.to_dict() for item in measurements],
    }
    summary.update(legacy_summary)
    return summary


def _format_report(
    *,
    fixture_name: str,
    fixture: FixturePaths,
    zones: int,
    runs: int,
    cold_samples_ms: List[float],
    hit_samples_ms: List[float],
    cold_p95_target_ms: float,
    cache_hit_p95_target_ms: float,
    cold_verdict: str,
    hit_verdict: str,
    display_list_cache_stats: Dict[str, Any] | None = None,
    dxf_index_cache_stats: Dict[str, Any] | None = None,
    measurement_summary: Dict[str, Any] | None = None,
) -> str:
    import platform

    cold_p50 = _percentile(cold_samples_ms, 50.0)
    cold_p95 = _percentile(cold_samples_ms, 95.0)
    hit_p50 = _percentile(hit_samples_ms, 50.0)
    hit_p95 = _percentile(hit_samples_ms, 95.0)
    cold_mean = statistics.fmean(cold_samples_ms) if cold_samples_ms else 0.0
    hit_mean = statistics.fmean(hit_samples_ms) if hit_samples_ms else 0.0

    buf = io.StringIO()
    buf.write("=" * 90 + "\n")
    buf.write("Selected-zone render benchmark (Plan §17 Phase B-1b)\n")
    buf.write("=" * 90 + "\n")
    buf.write(
        f"timestamp_utc:   {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
    )
    buf.write(f"git_short_sha:   {_short_git_sha()}\n")
    buf.write(f"python_version:  {platform.python_version()}\n")
    buf.write(f"platform:        {platform.platform()}\n")
    buf.write("\n")
    buf.write("INPUTS\n")
    buf.write("------\n")
    buf.write(f"fixture:                 {fixture_name}\n")
    buf.write(f"source_pdf:              {fixture.source_pdf}\n")
    buf.write(f"background_png:          {fixture.background_png}\n")
    buf.write(f"background_dimensions:   {fixture.bg_w} x {fixture.bg_h}\n")
    buf.write(f"zones_per_run:           {zones}\n")
    buf.write(f"runs:                    {runs}\n")
    buf.write(f"total_samples_per_phase: {zones * runs}\n")
    buf.write("\n")
    buf.write("RESULTS — COLD (cache cleared between every zone)\n")
    buf.write("--------------------------------------------------\n")
    buf.write(f"  samples:                 {len(cold_samples_ms)}\n")
    buf.write(f"  cold_mean_ms:            {cold_mean:.2f}\n")
    buf.write(f"  cold_p50_ms:             {cold_p50:.2f}\n")
    buf.write(f"  cold_p95_ms:             {cold_p95:.2f}\n")
    buf.write(f"  cold_p95_target_ms:      {cold_p95_target_ms:.2f}\n")
    buf.write(f"  cold_verdict:            {cold_verdict}\n")
    buf.write("\n")
    buf.write("RESULTS — CACHE HIT (same zone re-rendered immediately)\n")
    buf.write("--------------------------------------------------\n")
    buf.write(f"  samples:                 {len(hit_samples_ms)}\n")
    buf.write(f"  cache_hit_mean_ms:       {hit_mean:.2f}\n")
    buf.write(f"  cache_hit_p50_ms:        {hit_p50:.2f}\n")
    buf.write(f"  cache_hit_p95_ms:        {hit_p95:.2f}\n")
    buf.write(f"  cache_hit_p95_target_ms: {cache_hit_p95_target_ms:.2f}\n")
    buf.write(f"  cache_hit_verdict:       {hit_verdict}\n")
    if measurement_summary:
        buf.write("\n")
        buf.write("RENDER HEALTH EVIDENCE\n")
        buf.write("----------------------\n")
        buf.write(f"  render_attempt_count:    {measurement_summary.get('render_attempt_count', 0)}\n")
        buf.write(f"  render_failure_count:    {measurement_summary.get('render_failure_count', 0)}\n")
        buf.write(f"  render_exception_count:  {measurement_summary.get('render_exception_count', 0)}\n")
        buf.write(f"  render_health_pass:      {measurement_summary.get('render_health_pass', False)}\n")
        buf.write(f"  blank_check_count:       {measurement_summary.get('blank_check_count', 0)}\n")
        buf.write(f"  blank_output_count:      {measurement_summary.get('blank_output_count', 0)}\n")
        buf.write(f"  blank_output_ratio:      {measurement_summary.get('blank_output_ratio', 0.0)}\n")
        buf.write(f"  output_missing_count:    {measurement_summary.get('output_missing_count', 0)}\n")
        buf.write(f"  stale_render_count:      {measurement_summary.get('stale_render_count', 0)}\n")
        buf.write(f"  fallback_count:          {measurement_summary.get('fallback_count', 0)}\n")
        buf.write(f"  timeout_count:           {measurement_summary.get('timeout_count', 0)}\n")
        buf.write(f"  cancel_count:            {measurement_summary.get('cancel_count', 0)}\n")
        buf.write(f"  warning_count_total:     {measurement_summary.get('warning_count_total', 0)}\n")
        buf.write(f"  renderer_backend_counts: {measurement_summary.get('renderer_backend_counts', {})}\n")
        buf.write(f"  visual_fidelity_counts:  {measurement_summary.get('visual_fidelity_counts', {})}\n")
        buf.write(f"  render_lifecycle_counts: {measurement_summary.get('render_lifecycle_counts', {})}\n")
        buf.write(f"  reason_code_counts:      {measurement_summary.get('reason_code_counts', {})}\n")
        buf.write("\n")
        buf.write("RSS EVIDENCE\n")
        buf.write("------------\n")
        buf.write(f"  rss_measurement_available: {measurement_summary.get('rss_measurement_available', False)}\n")
        buf.write(f"  rss_min_mb:                {measurement_summary.get('rss_min_mb', 0.0)}\n")
        buf.write(f"  rss_peak_mb:               {measurement_summary.get('rss_peak_mb', 0.0)}\n")
        buf.write(f"  rss_delta_mb_max:          {measurement_summary.get('rss_delta_mb_max', 0.0)}\n")
        buf.write(f"  rss_delta_mb_p95:          {measurement_summary.get('rss_delta_mb_p95', 0.0)}\n")
        buf.write(f"  cold_rss_delta_mb_p95:     {measurement_summary.get('cold_rss_delta_mb_p95', 0.0)}\n")
        buf.write(f"  cache_hit_rss_delta_mb_p95: {measurement_summary.get('cache_hit_rss_delta_mb_p95', 0.0)}\n")
    if display_list_cache_stats:
        buf.write("\n")
        buf.write("PDF DISPLAYLIST CACHE\n")
        buf.write("---------------------\n")
        buf.write(f"  entries:                 {display_list_cache_stats.get('entries', 0)}\n")
        buf.write(f"  capacity_entries:        {display_list_cache_stats.get('capacity_entries', display_list_cache_stats.get('capacity', 0))}\n")
        buf.write(f"  total_estimated_bytes:   {display_list_cache_stats.get('total_estimated_bytes', 0)}\n")
        buf.write(f"  byte_limit:              {display_list_cache_stats.get('byte_limit', 0)}\n")
        buf.write(f"  hit_count:               {display_list_cache_stats.get('hit_count', 0)}\n")
        buf.write(f"  miss_count:              {display_list_cache_stats.get('miss_count', 0)}\n")
        buf.write(f"  eviction_count:          {display_list_cache_stats.get('eviction_count', 0)}\n")
    if dxf_index_cache_stats:
        buf.write("\n")
        buf.write("DXF RENDER INDEX CACHE\n")
        buf.write("----------------------\n")
        buf.write(f"  entries:                 {dxf_index_cache_stats.get('entries', 0)}\n")
        buf.write(f"  capacity_entries:        {dxf_index_cache_stats.get('capacity_entries', 0)}\n")
        buf.write(f"  total_estimated_bytes:   {dxf_index_cache_stats.get('total_estimated_bytes', 0)}\n")
        buf.write(f"  byte_limit:              {dxf_index_cache_stats.get('byte_limit', 0)}\n")
        buf.write(f"  hit_count:               {dxf_index_cache_stats.get('hit_count', 0)}\n")
        buf.write(f"  miss_count:              {dxf_index_cache_stats.get('miss_count', 0)}\n")
        buf.write(f"  eviction_count:          {dxf_index_cache_stats.get('eviction_count', 0)}\n")
    buf.write("\n")
    buf.write("NOTE: the cold phase wipes BOTH the in-process DisplayList\n")
    buf.write("cache AND the on-disk zone_crops cache before every zone,\n")
    buf.write("so each cold measurement reflects the worst case (no\n")
    buf.write("memoization at any level). The cache-hit phase exercises\n")
    buf.write("the on-disk zone-cache early-return (render_zone_pair sees\n")
    buf.write("render_result.json and returns without re-rendering).\n")
    return buf.getvalue()


def _resolve_fixture(name: str, scratch_dir: Path) -> FixturePaths:
    if name not in PDF_FIXTURE_SPECS:
        raise ValueError(
            f"unknown fixture: {name!r}; choose from {list(PDF_FIXTURE_SPECS)}"
        )
    scratch_dir.mkdir(parents=True, exist_ok=True)
    return _build_pdf_fixture(name, scratch_dir)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        choices=list(PDF_FIXTURE_SPECS),
        default="small",
        help="Fixture size (default: small).",
    )
    parser.add_argument(
        "--zones",
        type=int,
        default=DEFAULT_ZONES,
        help=f"Zones per run (default: {DEFAULT_ZONES}).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS,
        help=f"Runs of the full zone set (default: {DEFAULT_RUNS}).",
    )
    parser.add_argument(
        "--cold-p95-target-ms",
        type=float,
        default=DEFAULT_COLD_P95_MS,
        help=f"PASS threshold for cold p95 (default: {DEFAULT_COLD_P95_MS} ms).",
    )
    parser.add_argument(
        "--cache-hit-p95-target-ms",
        type=float,
        default=DEFAULT_CACHE_HIT_P95_MS,
        help=(
            f"PASS threshold for cache-hit p95 "
            f"(default: {DEFAULT_CACHE_HIT_P95_MS} ms)."
        ),
    )
    parser.add_argument(
        "--fail-on-exceed",
        dest="fail_on_exceed",
        action="store_true",
        default=True,
        help="Exit non-zero when either p95 target is exceeded.",
    )
    parser.add_argument(
        "--no-fail-on-exceed",
        dest="fail_on_exceed",
        action="store_false",
        help="Always exit 0 regardless of verdict (measurement-only runs).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path to write the human-readable summary (default: tmp/...).",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Path to write the full machine-readable summary (default: output path with .json suffix).",
    )
    parser.add_argument(
        "--scratch-dir",
        type=Path,
        default=None,
        help="Scratch dir for fixtures + cache (default: tmp/zone_render_bench).",
    )
    args = parser.parse_args(argv)

    scratch = args.scratch_dir or (_REPO_ROOT / "tmp" / "zone_render_bench")
    scratch.mkdir(parents=True, exist_ok=True)
    fixture_dir = scratch / "fixtures"
    cache_root = scratch / "cache"

    print(
        f"[bench] fixture={args.fixture} zones={args.zones} runs={args.runs} "
        f"cold_p95_target={args.cold_p95_target_ms}ms "
        f"cache_hit_p95_target={args.cache_hit_p95_target_ms}ms"
    )
    fixture = _resolve_fixture(args.fixture, fixture_dir)
    zones = _build_zones(fixture, args.zones)

    pass_result = _run_pass(fixture, zones, args.runs, cache_root)
    cold_samples = pass_result.cold_samples_ms
    hit_samples = pass_result.cache_hit_samples_ms
    measurement_summary = _summarize_measurements(pass_result.measurements)
    from src.services.comparison import pdf_display_list_cache
    from src.services.comparison.zone_render_service import render_index_cache_stats

    display_list_cache_stats = pdf_display_list_cache.cache_stats()
    dxf_index_cache_stats = render_index_cache_stats()

    cold_p95 = _percentile(cold_samples, 95.0)
    hit_p95 = _percentile(hit_samples, 95.0)
    cold_pass = cold_p95 <= args.cold_p95_target_ms
    hit_pass = hit_p95 <= args.cache_hit_p95_target_ms
    render_health_pass = bool(measurement_summary.get("render_health_pass"))
    cold_verdict = (
        f"cold_p95={cold_p95:.2f}ms — PASS (<= {args.cold_p95_target_ms:.2f}ms)"
        if cold_pass
        else f"cold_p95={cold_p95:.2f}ms — FAIL (> {args.cold_p95_target_ms:.2f}ms)"
    )
    hit_verdict = (
        f"cache_hit_p95={hit_p95:.2f}ms — PASS "
        f"(<= {args.cache_hit_p95_target_ms:.2f}ms)"
        if hit_pass
        else f"cache_hit_p95={hit_p95:.2f}ms — FAIL "
        f"(> {args.cache_hit_p95_target_ms:.2f}ms)"
    )

    report = _format_report(
        fixture_name=args.fixture,
        fixture=fixture,
        zones=args.zones,
        runs=args.runs,
        cold_samples_ms=cold_samples,
        hit_samples_ms=hit_samples,
        cold_p95_target_ms=args.cold_p95_target_ms,
        cache_hit_p95_target_ms=args.cache_hit_p95_target_ms,
        cold_verdict=cold_verdict,
        hit_verdict=hit_verdict,
        display_list_cache_stats=display_list_cache_stats,
        dxf_index_cache_stats=dxf_index_cache_stats,
        measurement_summary=measurement_summary,
    )

    out_path = args.output
    if out_path is None:
        ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
        out_path = _REPO_ROOT / "tmp" / f"zone_render_benchmark_{ts}.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write(report)
    print(f"[bench] report -> {out_path}")
    print(f"[bench] {cold_verdict}")
    print(f"[bench] {hit_verdict}")

    # Also emit a machine-readable JSON line on stdout's last line so
    # other tools can scrape the verdicts without parsing the report.
    json_output_path = args.output_json or out_path.with_suffix(".json")
    summary = _build_summary(
        fixture_name=args.fixture,
        fixture=fixture,
        zones=args.zones,
        runs=args.runs,
        cold_samples=cold_samples,
        hit_samples=hit_samples,
        cold_p95_target_ms=args.cold_p95_target_ms,
        cache_hit_p95_target_ms=args.cache_hit_p95_target_ms,
        cold_pass=cold_pass,
        hit_pass=hit_pass,
        display_list_cache_stats=display_list_cache_stats,
        dxf_index_cache_stats=dxf_index_cache_stats,
        measurement_summary=measurement_summary,
        measurements=pass_result.measurements,
        report_path=out_path,
        json_path=json_output_path,
        scratch_dir=scratch,
        cache_root=cache_root,
    )
    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[bench] json -> {json_output_path}")
    sys.stdout.write(json.dumps(summary) + "\n")

    if not (cold_pass and hit_pass and render_health_pass) and args.fail_on_exceed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
