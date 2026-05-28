# -*- coding: utf-8 -*-
"""P5-G16 real-corpus artifact replay and RSS soak benchmark.

This benchmark consumes an existing ``validate_drawing_compare_realset.py``
summary and its viewer package artifacts. It does not synthesize drawings.
The goal is to prove that already-produced customer-corpus artifacts can be
revisited repeatedly without blank selected-zone crops, stale visible results,
unbounded retained caches, or RSS growth after warmup.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

BENCHMARK_ID = "p5_g16_real_corpus_replay"
PROFILE = "real_corpus_artifact_replay"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ReplayUnit:
    kind: str
    unit_id: str
    path: Path
    pair_id: str = ""
    zone_id: str = ""
    before_image: Optional[Path] = None
    after_image: Optional[Path] = None
    renderer_backend: str = ""
    visual_fidelity: str = ""
    render_lifecycle: str = ""
    reason_code: str = ""
    warnings: tuple[str, ...] = ()


def _load_json_dict(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _repo_relative(path: Path, *, base: Optional[Path] = None) -> str:
    resolved = Path(path)
    for root in (base, _REPO_ROOT):
        if root is None:
            continue
        try:
            return str(resolved.resolve().relative_to(Path(root).resolve())).replace("\\", "/")
        except Exception:
            continue
    return str(path)


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


def _file_fingerprint(path: Path, *, base: Optional[Path] = None) -> dict[str, Any]:
    try:
        stat = Path(path).stat()
    except OSError:
        return {"path": _repo_relative(path, base=base), "exists": False}
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        pass
    return {
        "path": _repo_relative(path, base=base),
        "exists": True,
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "sha256": digest.hexdigest(),
    }


def _source_signature(paths: Iterable[Path], *, base: Optional[Path] = None) -> str:
    payload = [_file_fingerprint(path, base=base) for path in paths]
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except Exception:
        return False


def _process_rss_mb() -> Optional[float]:
    try:
        from src.services.comparison.cache_budget import process_rss_mb

        value = float(process_rss_mb())
        return value if value > 0 else None
    except Exception:
        return None


def _percentile(samples: list[float], pct: float) -> float:
    if not samples:
        return 0.0
    if len(samples) == 1:
        return round(float(samples[0]), 3)
    ordered = sorted(float(item) for item in samples)
    k = (len(ordered) - 1) * (pct / 100.0)
    floor = int(k)
    ceil = min(floor + 1, len(ordered) - 1)
    if floor == ceil:
        return round(ordered[floor], 3)
    return round(ordered[floor] + (ordered[ceil] - ordered[floor]) * (k - floor), 3)


def _latency_summary(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {"p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0, "mean_ms": 0.0}
    return {
        "p50_ms": _percentile(samples, 50.0),
        "p95_ms": _percentile(samples, 95.0),
        "p99_ms": _percentile(samples, 99.0),
        "max_ms": round(max(samples), 3),
        "mean_ms": round(sum(samples) / len(samples), 3),
    }


def _resolve_path(value: Any, bases: Iterable[Path]) -> Optional[Path]:
    text = str(value or "").strip()
    if not text or text.startswith("<redacted>"):
        return None
    path = Path(text)
    if path.is_absolute():
        return path
    for base in bases:
        candidate = Path(base) / path
        if candidate.exists():
            return candidate
    return Path(next(iter(bases), Path("."))) / path


def _resolve_viewer_root(
    summary: dict[str, Any],
    validation_summary: Path,
    explicit_viewer_root: Optional[Path],
) -> Optional[Path]:
    if explicit_viewer_root is not None:
        return explicit_viewer_root
    output_dir = Path(str(summary.get("output_dir") or validation_summary.parent))
    package = summary.get("viewer_package") if isinstance(summary.get("viewer_package"), dict) else {}
    output_paths = package.get("output_paths") if isinstance(package.get("output_paths"), dict) else {}
    candidates = [
        output_paths.get("viewer_dir"),
        package.get("viewer_dir"),
        output_dir / "viewer",
    ]
    bases = [output_dir, validation_summary.parent, _REPO_ROOT]
    for candidate in candidates:
        path = candidate if isinstance(candidate, Path) else _resolve_path(candidate, bases)
        if path and Path(path).exists():
            return Path(path)
    return None


def _image_status(path: Optional[Path], *, base: Path) -> dict[str, Any]:
    if path is None:
        return {"path": "", "exists": False, "status": "missing", "nonblank": False, "bytes": 0}
    payload: dict[str, Any] = {
        "path": _repo_relative(path, base=base),
        "exists": Path(path).exists(),
        "status": "missing",
        "nonblank": False,
        "bytes": 0,
    }
    try:
        payload["bytes"] = int(Path(path).stat().st_size)
    except OSError:
        return payload
    try:
        from PIL import Image, ImageStat  # type: ignore[import-not-found]

        with Image.open(path) as image:
            rgb = image.convert("RGB")
            stat = ImageStat.Stat(rgb)
            extrema = rgb.getextrema()
        channel_ranges = [int(high) - int(low) for low, high in extrema]
        mean = sum(float(value) for value in stat.mean) / max(1, len(stat.mean))
        nonblank = any(value > 3 for value in channel_ranges) or mean < 250.0
        payload.update(
            {
                "status": "checked",
                "nonblank": bool(nonblank),
                "mean": round(mean, 3),
                "channel_ranges": channel_ranges,
            }
        )
    except Exception as exc:
        payload.update({"status": "unavailable", "reason": exc.__class__.__name__})
    return payload


def _read_perf_events(viewer_root: Path) -> list[dict[str, Any]]:
    path = viewer_root / "viewer_perf.jsonl"
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    events.append(payload)
    except OSError:
        return events
    return events


def _collect_zone_units(viewer_root: Path, *, limit: int) -> list[ReplayUnit]:
    limit = max(0, int(limit))
    if limit <= 0:
        return []
    roots = [viewer_root / "zone_crops"]
    units: list[ReplayUnit] = []
    for root in roots:
        if not root.exists():
            continue
        for meta_path in sorted(root.rglob("render_result.json")):
            payload = _load_json_dict(meta_path)
            bases = [meta_path.parent, viewer_root, viewer_root.parent]
            before = _resolve_path(payload.get("before_image"), bases)
            after = _resolve_path(payload.get("after_image"), bases)
            if before is None:
                candidate = next(iter(sorted(meta_path.parent.glob("*_before.png"))), None)
                before = candidate
            if after is None:
                candidate = next(iter(sorted(meta_path.parent.glob("*_after.png"))), None)
                after = candidate
            pair_id = meta_path.parent.parent.name if meta_path.parent.parent != root else ""
            zone_id = str(payload.get("zone_id") or meta_path.parent.name)
            warnings = tuple(str(item) for item in payload.get("warnings", []) if item is not None)
            units.append(
                ReplayUnit(
                    kind="zone",
                    unit_id=f"{pair_id}:{zone_id}:{meta_path.parent.name}",
                    pair_id=pair_id,
                    zone_id=zone_id,
                    path=meta_path,
                    before_image=before,
                    after_image=after,
                    renderer_backend=str(payload.get("renderer_backend") or ""),
                    visual_fidelity=str(payload.get("visual_fidelity") or ""),
                    render_lifecycle=str(payload.get("render_lifecycle") or ""),
                    reason_code=str(payload.get("reason_code") or payload.get("fallback_reason_code") or ""),
                    warnings=warnings,
                )
            )
            if len(units) >= limit:
                return units
    return units


def _collect_page_units(viewer_root: Path, *, limit: int) -> list[ReplayUnit]:
    limit = max(0, int(limit))
    if limit <= 0:
        return []
    units: list[ReplayUnit] = []
    for root_name in ("images", "tiles", "focus_tiles"):
        root = viewer_root / root_name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                continue
            units.append(
                ReplayUnit(
                    kind="page",
                    unit_id=f"{root_name}:{path.stem}",
                    path=path,
                    before_image=path,
                )
            )
            if len(units) >= limit:
                return units
    return units


def _contains_token(unit: ReplayUnit, token: str) -> bool:
    needle = token.lower()
    values = [unit.reason_code, unit.render_lifecycle, unit.visual_fidelity, unit.renderer_backend]
    values.extend(unit.warnings)
    return any(needle in str(value).lower() for value in values if value)


def _event_token_count(events: list[dict[str, Any]], token: str) -> int:
    needle = token.lower()
    count = 0
    for event in events:
        text = json.dumps(event, ensure_ascii=False).lower()
        if needle in text:
            count += 1
    return count


def _rss_slope_summary(samples: list[dict[str, Any]], *, warmup_visit: int) -> dict[str, Any]:
    tail = [
        sample
        for sample in samples
        if _safe_int(sample.get("visit_index"), -1) >= int(warmup_visit)
        and sample.get("rss_mb") is not None
    ]
    if len(tail) < 2:
        return {
            "available": False,
            "sample_count": len(tail),
            "slope_mb_per_100_visits": None,
            "positive_end_delta_mb": None,
            "tail_peak_delta_mb": None,
        }
    first = tail[0]
    last = tail[-1]
    first_visit = _safe_int(first.get("visit_index"))
    last_visit = _safe_int(last.get("visit_index"))
    span = max(1, last_visit - first_visit)
    first_rss = _safe_float(first.get("rss_mb"))
    last_rss = _safe_float(last.get("rss_mb"))
    values = [_safe_float(sample.get("rss_mb")) for sample in tail if sample.get("rss_mb") is not None]
    positive_end_delta = max(0.0, last_rss - first_rss)
    return {
        "available": True,
        "sample_count": len(tail),
        "warmup_visit": int(warmup_visit),
        "slope_mb_per_100_visits": round((positive_end_delta / span) * 100.0, 3),
        "positive_end_delta_mb": round(positive_end_delta, 3),
        "tail_peak_delta_mb": round(max(values) - min(values), 3) if values else 0.0,
    }


def _gate(
    name: str,
    observed: Any,
    threshold: Any,
    op: str,
    *,
    required: bool = True,
    domain: str = "",
    detail: str = "",
) -> dict[str, Any]:
    if not required:
        return {
            "name": name,
            "domain": domain,
            "passed": True,
            "observed": observed,
            "threshold": threshold,
            "actual": observed,
            "target": threshold,
            "op": op,
            "required": False,
            "detail": detail,
        }
    if op == "==":
        passed = observed == threshold
    elif op == ">=":
        passed = _safe_float(observed, -1.0) >= _safe_float(threshold)
    elif op == "<=":
        passed = _safe_float(observed, 10**18) <= _safe_float(threshold)
    else:
        passed = False
    return {
        "name": name,
        "domain": domain,
        "passed": bool(passed),
        "observed": observed,
        "threshold": threshold,
        "actual": observed,
        "target": threshold,
        "op": op,
        "required": True,
        "detail": detail,
    }


def _cache_bound_gate(summary: dict[str, Any], name: str, total_key: str, limit_key: str) -> dict[str, Any]:
    total = _safe_int(summary.get(total_key))
    limit = _safe_int(summary.get(limit_key))
    return _gate(
        name,
        total,
        limit,
        "<=",
        required=limit > 0 or total > 0,
        domain="cache",
    )


def _cache_summary(viewer_perf_summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "pdf_display_list_cache_total_estimated_bytes": _safe_int(
            viewer_perf_summary.get("pdf_display_list_cache_max_total_bytes")
        ),
        "pdf_display_list_cache_byte_limit": _safe_int(
            viewer_perf_summary.get("pdf_display_list_cache_byte_limit")
        ),
        "dxf_index_cache_total_estimated_bytes": _safe_int(
            viewer_perf_summary.get("dxf_index_cache_max_total_bytes")
        ),
        "dxf_index_cache_byte_limit": _safe_int(
            viewer_perf_summary.get("dxf_index_cache_byte_limit")
        ),
        "tile_cache_retained_estimated_bytes": _safe_int(
            viewer_perf_summary.get("tile_cache_retained_estimated_bytes")
        ),
        "tile_cache_byte_limit": _safe_int(viewer_perf_summary.get("tile_cache_byte_limit")),
        "overlay_cache_total_estimated_bytes": _safe_int(
            viewer_perf_summary.get("overlay_cache_max_total_bytes")
        ),
        "overlay_cache_byte_limit": _safe_int(viewer_perf_summary.get("overlay_cache_byte_limit")),
    }


def _resolve_customer_manifest(args: argparse.Namespace, validation_summary: Path) -> Optional[Path]:
    if args.customer_evidence_manifest is not None:
        return Path(args.customer_evidence_manifest)
    candidate = validation_summary.parent / "customer_evidence_manifest.json"
    return candidate if candidate.exists() else None


def _corpus_summary(
    manifest_path: Optional[Path],
    manifest: dict[str, Any],
    *,
    min_sheet_count: int,
    max_sheet_count: int,
) -> dict[str, Any]:
    provenance = manifest.get("dataset_provenance") if isinstance(manifest.get("dataset_provenance"), dict) else {}
    format_coverage = manifest.get("format_coverage") if isinstance(manifest.get("format_coverage"), dict) else {}
    sheet_count = _safe_int(manifest.get("sheet_count"))
    return {
        "manifest_present": bool(manifest),
        "manifest_path": _repo_relative(manifest_path, base=manifest_path.parent) if manifest_path else "",
        "manifest_sha256": _file_fingerprint(manifest_path, base=manifest_path.parent).get("sha256", "") if manifest_path else "",
        "evidence_level": str(manifest.get("evidence_level") or ""),
        "dataset_id": str(manifest.get("dataset_id") or ""),
        "source_kind": str(provenance.get("source_kind") or ""),
        "approval_status": str(provenance.get("approval_status") or ""),
        "sheet_count": sheet_count,
        "min_sheet_count": int(min_sheet_count),
        "max_sheet_count": int(max_sheet_count),
        "sheet_count_in_range": int(min_sheet_count) <= sheet_count <= int(max_sheet_count),
        "format_coverage": dict(format_coverage),
        "has_dwg_dxf": format_coverage.get("dwg_dxf") is True,
        "has_pdf_pdf": format_coverage.get("pdf_pdf") is True,
        "has_cad_pdf_blocked": format_coverage.get("cad_pdf_blocked") is True,
    }


def run_replay(args: argparse.Namespace) -> dict[str, Any]:
    validation_summary = Path(args.validation_summary)
    validation_payload = _load_json_dict(validation_summary)
    viewer_root = _resolve_viewer_root(validation_payload, validation_summary, args.viewer_root)
    customer_manifest_path = _resolve_customer_manifest(args, validation_summary)
    customer_manifest = _load_json_dict(customer_manifest_path) if customer_manifest_path else {}
    corpus = _corpus_summary(
        customer_manifest_path,
        customer_manifest,
        min_sheet_count=int(args.min_customer_sheet_count),
        max_sheet_count=int(args.max_customer_sheet_count),
    )
    events = _read_perf_events(viewer_root) if viewer_root else []

    from src.services.comparison.viewer_perf_summary import summarize_viewer_perf

    viewer_perf_summary = summarize_viewer_perf(viewer_root) if viewer_root else {}
    zone_units = _collect_zone_units(viewer_root, limit=args.max_zone_artifacts) if viewer_root else []
    page_units = _collect_page_units(viewer_root, limit=args.max_page_artifacts) if viewer_root else []
    replay_units = zone_units + page_units

    samples: list[dict[str, Any]] = []
    replay_ms: list[float] = []
    gaps_ms: list[float] = []
    blank_zone_count = 0
    missing_zone_image_count = 0
    stale_result_visible_count = _safe_int(viewer_perf_summary.get("selected_zone_stale_count"))
    stale_result_visible_count += sum(1 for event in events if bool(event.get("stale_result_visible")))
    timeout_count = _event_token_count(events, "timeout")
    cancel_count = _safe_int(viewer_perf_summary.get("selected_zone_cancel_count"))
    cancel_count += _event_token_count(events, "cancel")
    fallback_count = _safe_int(viewer_perf_summary.get("selected_zone_fallback_count"))
    fallback_missing_reason_count = 0

    for unit in zone_units:
        fallback_like = bool(
            unit.reason_code
            or unit.render_lifecycle not in ("ready", "")
            or unit.visual_fidelity == "relative_overlay"
        )
        if fallback_like:
            fallback_count += 1
            if not unit.reason_code:
                fallback_missing_reason_count += 1
        if _contains_token(unit, "timeout"):
            timeout_count += 1
        if _contains_token(unit, "cancel"):
            cancel_count += 1

    started = time.perf_counter()
    last_tick = started
    completed = bool(replay_units)
    visit_count = max(1, int(args.visits))
    for visit_idx in range(visit_count):
        if not replay_units:
            completed = False
            break
        if time.perf_counter() - started > max(1.0, float(args.timeout_s)):
            completed = False
            break
        unit = replay_units[visit_idx % len(replay_units)]
        unit_started = time.perf_counter()
        image_checks: list[dict[str, Any]] = []
        if unit.kind == "zone":
            _load_json_dict(unit.path)
            before = _image_status(unit.before_image, base=viewer_root or validation_summary.parent)
            after = _image_status(unit.after_image, base=viewer_root or validation_summary.parent)
            image_checks.extend([before, after])
            if not before.get("exists") or not after.get("exists"):
                missing_zone_image_count += 1
            if before.get("status") == "checked" and after.get("status") == "checked":
                if not before.get("nonblank") and not after.get("nonblank"):
                    blank_zone_count += 1
        else:
            image_checks.append(_image_status(unit.before_image, base=viewer_root or validation_summary.parent))
        elapsed_ms = round((time.perf_counter() - unit_started) * 1000.0, 3)
        now = time.perf_counter()
        gap_ms = round((now - last_tick) * 1000.0, 3)
        last_tick = now
        replay_ms.append(elapsed_ms)
        gaps_ms.append(gap_ms)
        rss = _process_rss_mb()
        samples.append(
            {
                "visit_index": visit_idx,
                "kind": unit.kind,
                "unit_id": unit.unit_id,
                "pair_id": unit.pair_id,
                "zone_id": unit.zone_id,
                "elapsed_ms": elapsed_ms,
                "gap_ms": gap_ms,
                "rss_mb": rss,
                "image_checks": image_checks,
            }
        )
        if args.settle_ms > 0:
            time.sleep(float(args.settle_ms) / 1000.0)

    rss_values = [float(sample["rss_mb"]) for sample in samples if sample.get("rss_mb") is not None]
    rss_slope = _rss_slope_summary(samples, warmup_visit=max(0, int(args.warmup_visits)))
    cache = _cache_summary(viewer_perf_summary if isinstance(viewer_perf_summary, dict) else {})
    summary: dict[str, Any] = {
        "validation_summary_present": bool(validation_payload),
        "viewer_root_present": viewer_root is not None and viewer_root.exists(),
        "viewer_root": _repo_relative(viewer_root, base=validation_summary.parent) if viewer_root else "",
        "viewer_perf_status": str(viewer_perf_summary.get("status") or ""),
        "viewer_perf_event_count": _safe_int(viewer_perf_summary.get("event_count")),
        "zone_render_artifact_count": len(zone_units),
        "page_artifact_count": len(page_units),
        "replay_artifact_count": len(replay_units),
        "visit_count": visit_count,
        "completed_visit_count": len(samples),
        "replay_completed": bool(completed and len(samples) == visit_count),
        "artifact_replay_ms": _latency_summary(replay_ms),
        "artifact_replay_gap_ms": _latency_summary(gaps_ms),
        "blank_zone_output_count": blank_zone_count,
        "missing_zone_image_count": missing_zone_image_count,
        "stale_result_visible_count": stale_result_visible_count,
        "fallback_count": fallback_count,
        "fallback_missing_reason_count": fallback_missing_reason_count,
        "timeout_count": timeout_count,
        "cancel_count": cancel_count,
        "rss_measurement_available": bool(rss_values),
        "rss_start_mb": round(rss_values[0], 3) if rss_values else None,
        "rss_peak_mb": round(max(rss_values), 3) if rss_values else None,
        "rss_end_mb": round(rss_values[-1], 3) if rss_values else None,
        "rss_slope": rss_slope,
        **cache,
    }
    gates = [
        _gate("validation_summary_present", summary["validation_summary_present"], True, "==", domain="source"),
        _gate("viewer_root_present", summary["viewer_root_present"], True, "==", domain="source"),
        _gate(
            "p5_g16_real_corpus_declared",
            corpus["evidence_level"],
            "customer_grade",
            "==",
            required=bool(args.require_customer_corpus),
            domain="corpus",
        ),
        _gate(
            "p5_g16_customer_manifest_present",
            corpus["manifest_present"],
            True,
            "==",
            required=bool(args.require_customer_corpus),
            domain="corpus",
        ),
        _gate(
            "p5_g16_customer_sheet_count_min",
            corpus["sheet_count"],
            int(args.min_customer_sheet_count),
            ">=",
            required=bool(args.require_customer_corpus),
            domain="corpus",
        ),
        _gate(
            "p5_g16_customer_sheet_count_max",
            corpus["sheet_count"],
            int(args.max_customer_sheet_count),
            "<=",
            required=bool(args.require_customer_corpus),
            domain="corpus",
        ),
        _gate(
            "p5_g16_customer_format_dwg_dxf",
            corpus["has_dwg_dxf"],
            True,
            "==",
            required=bool(args.require_customer_corpus),
            domain="corpus",
        ),
        _gate(
            "p5_g16_customer_format_pdf_pdf",
            corpus["has_pdf_pdf"],
            True,
            "==",
            required=bool(args.require_customer_corpus),
            domain="corpus",
        ),
        _gate("zone_render_artifact_count", summary["zone_render_artifact_count"], int(args.min_zone_artifacts), ">=", domain="artifacts"),
        _gate("page_artifact_count", summary["page_artifact_count"], int(args.min_page_artifacts), ">=", domain="artifacts"),
        _gate("replay_completed", summary["replay_completed"], True, "==", domain="replay"),
        _gate("artifact_replay_p95_ms", summary["artifact_replay_ms"]["p95_ms"], float(args.replay_p95_target_ms), "<=", domain="replay"),
        _gate("artifact_replay_gap_max_ms", summary["artifact_replay_gap_ms"]["max_ms"], float(args.gap_max_target_ms), "<=", domain="replay"),
        _gate("blank_zone_output_count", summary["blank_zone_output_count"], 0, "==", domain="selected_zone"),
        _gate("missing_zone_image_count", summary["missing_zone_image_count"], 0, "==", domain="selected_zone"),
        _gate("stale_result_visible_count", summary["stale_result_visible_count"], 0, "==", domain="selected_zone"),
        _gate("fallback_missing_reason_count", summary["fallback_missing_reason_count"], 0, "==", domain="selected_zone"),
        _gate("timeout_count", summary["timeout_count"], 0, "==", domain="worker"),
        _gate("cancel_count", summary["cancel_count"], 0, "==", domain="worker"),
        _gate("rss_measurement_available", summary["rss_measurement_available"], True, "==", required=not args.allow_rss_unavailable, domain="rss"),
        _gate("rss_slope_mb_per_100_visits", rss_slope.get("slope_mb_per_100_visits"), float(args.rss_slope_target_mb_per_100), "<=", required=rss_slope.get("available") is True, domain="rss"),
        _gate("rss_positive_end_delta_mb", rss_slope.get("positive_end_delta_mb"), float(args.rss_end_delta_mb), "<=", required=rss_slope.get("available") is True, domain="rss"),
        _gate("rss_tail_peak_delta_mb", rss_slope.get("tail_peak_delta_mb"), float(args.rss_tail_delta_mb), "<=", required=rss_slope.get("available") is True, domain="rss"),
        _cache_bound_gate(summary, "pdf_display_list_cache_retained_bytes", "pdf_display_list_cache_total_estimated_bytes", "pdf_display_list_cache_byte_limit"),
        _cache_bound_gate(summary, "dxf_index_cache_retained_bytes", "dxf_index_cache_total_estimated_bytes", "dxf_index_cache_byte_limit"),
        _cache_bound_gate(summary, "tile_cache_retained_bytes", "tile_cache_retained_estimated_bytes", "tile_cache_byte_limit"),
        _cache_bound_gate(summary, "overlay_cache_retained_bytes", "overlay_cache_total_estimated_bytes", "overlay_cache_byte_limit"),
    ]
    status = "passed" if all(gate["passed"] for gate in gates) else "failed"
    output_json = Path(args.output_json) if args.output_json else validation_summary.parent / "p5_g16_real_corpus_replay.json"
    return {
        "schema_version": SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "profile": PROFILE,
        "status": status,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": {
            "git_short_sha": _short_git_sha(),
            "dirty_worktree": _dirty_worktree(),
            "source_signature": _source_signature([validation_summary], base=validation_summary.parent),
            "validation_summary": _file_fingerprint(validation_summary, base=validation_summary.parent),
        },
        "args": {
            "validation_summary": _repo_relative(validation_summary, base=validation_summary.parent),
            "viewer_root": _repo_relative(viewer_root, base=validation_summary.parent) if viewer_root else "",
            "visits": visit_count,
            "warmup_visits": int(args.warmup_visits),
            "min_zone_artifacts": int(args.min_zone_artifacts),
            "min_page_artifacts": int(args.min_page_artifacts),
            "replay_p95_target_ms": float(args.replay_p95_target_ms),
            "gap_max_target_ms": float(args.gap_max_target_ms),
            "rss_slope_target_mb_per_100": float(args.rss_slope_target_mb_per_100),
            "rss_end_delta_mb": float(args.rss_end_delta_mb),
            "rss_tail_delta_mb": float(args.rss_tail_delta_mb),
            "allow_rss_unavailable": bool(args.allow_rss_unavailable),
            "require_customer_corpus": bool(args.require_customer_corpus),
            "customer_evidence_manifest": (
                _repo_relative(customer_manifest_path, base=validation_summary.parent)
                if customer_manifest_path
                else ""
            ),
        },
        "environment": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "psutil_available": _module_available("psutil"),
            "pillow_available": _module_available("PIL"),
            "allow_missing_psutil": bool(args.allow_rss_unavailable),
        },
        "artifacts": {
            "output_json": _repo_relative(output_json, base=validation_summary.parent),
            "viewer_root": _repo_relative(viewer_root, base=validation_summary.parent) if viewer_root else "",
        },
        "corpus": corpus,
        "gates": gates,
        "summary": summary,
        "samples": samples,
    }


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-summary", type=Path, required=True)
    parser.add_argument("--viewer-root", type=Path, default=None)
    parser.add_argument("--customer-evidence-manifest", type=Path, default=None)
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--visits", type=int, default=100)
    parser.add_argument("--warmup-visits", type=int, default=20)
    parser.add_argument("--max-zone-artifacts", type=int, default=50)
    parser.add_argument("--max-page-artifacts", type=int, default=50)
    parser.add_argument("--min-zone-artifacts", type=int, default=1)
    parser.add_argument("--min-page-artifacts", type=int, default=0)
    parser.add_argument("--replay-p95-target-ms", type=float, default=250.0)
    parser.add_argument("--gap-max-target-ms", type=float, default=500.0)
    parser.add_argument("--rss-slope-target-mb-per-100", type=float, default=5.0)
    parser.add_argument("--rss-end-delta-mb", type=float, default=64.0)
    parser.add_argument("--rss-tail-delta-mb", type=float, default=128.0)
    parser.add_argument("--settle-ms", type=float, default=0.0)
    parser.add_argument("--timeout-s", type=float, default=60.0)
    parser.add_argument("--allow-rss-unavailable", action="store_true")
    parser.add_argument("--require-customer-corpus", action="store_true")
    parser.add_argument("--min-customer-sheet-count", type=int, default=20)
    parser.add_argument("--max-customer-sheet-count", type=int, default=50)
    parser.add_argument("--fail-on-gate", dest="fail_on_gate", action="store_true", default=True)
    parser.add_argument("--no-fail-on-gate", dest="fail_on_gate", action="store_false")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    payload = run_replay(args)
    output_json = Path(args.output_json) if args.output_json else Path(args.validation_summary).parent / "p5_g16_real_corpus_replay.json"
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[p5-g16] json -> {output_json}")
    print(f"[p5-g16] status={payload['status']}")
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    if payload["status"] != "passed" and args.fail_on_gate:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
