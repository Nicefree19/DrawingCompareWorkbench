"""Lightweight viewer package export for drawing comparison review.

The viewer package is a presentation layer.  Change detection stays based on
CAD/PDF comparison results and change zones; this module turns those zones into
overlay JSON plus optional low-resolution PNG backgrounds and crop tiles.
"""

from __future__ import annotations

import csv
import html
import json
import logging
import math
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from .review_project import _bbox_to_pixel_bbox, _ensure_preview_dxf, _render_dxf_to_png
from .viewer_manifest_v2 import (
    IDENTITY_AFFINE as V2_IDENTITY_AFFINE,
    MANIFEST_FILENAME as V2_MANIFEST_FILENAME,
    ArtifactRef as V2ArtifactRef,
    ManifestValidationError as V2ManifestValidationError,
    PairEntry as V2PairEntry,
    ViewerManifestV2,
    write_manifest_v2,
)
from .viewer_manifest_v3 import (
    MANIFEST_FILENAME as V3_MANIFEST_FILENAME,
    ManifestV3ValidationError,
    ScenePackRef as V3ScenePackRef,
    SourceSignature as V3SourceSignature,
    ViewerManifestV3,
    write_manifest_v3,
)
from .viewer_tile_cache import (
    ViewerTileCacheOptions,
    append_pair_to_tiles_manifest_jsonl,
    append_viewer_perf_event,
    materialise_tiles_manifest_from_jsonl,
    merge_tiles_manifest,
    tiles_manifest_is_current,
    viewer_cache_key,
    write_pair_tile_cache,
)
from .workbench_subprocess import (
    VIEWER_RENDER_WORKER_MODULE,
    worker_command_for_module,
    worker_working_directory,
)

logger = logging.getLogger(__name__)

VIEWER_PACKAGE_SCHEMA_VERSION = 2
OVERLAY_SCHEMA_VERSION = 2

# Phase F: name of the v2 manifest written *alongside* the v1 file. The v1
# manifest stays untouched so existing GUI code keeps working — the v2 is read
# by the new fidelity badge + state-machine layer in drawing_compare_workbench.
V2_MANIFEST_OUTPUT_NAME = V2_MANIFEST_FILENAME.replace(".json", "_v2.json")

# Phase G: name of the v3 manifest written alongside v1/v2. The v3 manifest
# carries ScenePackRef + 7-state RenderMode and drives the lightweight
# viewport. v1/v2 stay untouched so the existing GUI path is unaffected.
V3_MANIFEST_OUTPUT_NAME = V3_MANIFEST_FILENAME

# Phase G: per-pair scene pack subdirectory under the viewer artifact root.
# Each pair gets ``scene_packs/{pair_id}/{side}/scene_pack.json`` etc.
SCENE_PACKS_SUBDIR = "scene_packs"

CAD_EXTENSIONS = {".dwg", ".dxf"}
PDF_EXTENSIONS = {".pdf"}


@dataclass
class ViewerPackageOptions:
    """Options for lightweight viewer package generation."""

    viewer_mode: str = "image-tiles"
    render_policy: str = "lazy"
    max_viewer_pages: int = 30
    max_zone_tiles: int = 300
    export_marked_pdf: bool = False
    marked_pdf_mode: str = "selected"
    max_overlay_records_per_pair: Optional[int] = None
    preview_dpi: int = 80
    preview_max_edge_px: int = 2400
    tile_size: int = 512
    max_visible_overlays: int = 500
    viewer_memory_budget_mb: int = 512
    viewer_engine: str = "auto"
    tile_prefetch_radius: int = 1
    overview_max_edge: int = 2200
    focus_tile_max_edge: int = 1600
    viewer_perf_log: bool = False
    render_timeout_seconds: int = 0
    build_lod_tiles: bool = True


@dataclass
class ViewerPackage:
    """Generated viewer package summary."""

    viewer_dir: Path
    manifest_path: Path
    index_html: Path
    pair_count: int
    overlay_count: int
    page_count: int
    tile_count: int
    marked_pdf_count: int
    marked_pdf_skipped_count: int
    rendered_pair_count: int
    lazy_pair_count: int
    transform_complete: bool
    warnings: List[str] = field(default_factory=list)
    output_paths: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": VIEWER_PACKAGE_SCHEMA_VERSION,
            "viewer_dir": str(self.viewer_dir),
            "viewer_manifest": str(self.manifest_path),
            "viewer_index_html": str(self.index_html),
            "pair_count": self.pair_count,
            "overlay_count": self.overlay_count,
            "page_count": self.page_count,
            "tile_count": self.tile_count,
            "marked_pdf_count": self.marked_pdf_count,
            "marked_pdf_skipped_count": self.marked_pdf_skipped_count,
            "rendered_pair_count": self.rendered_pair_count,
            "lazy_pair_count": self.lazy_pair_count,
            "transform_complete": self.transform_complete,
            "warnings": list(self.warnings),
            "output_paths": dict(self.output_paths),
        }


def export_viewer_package(
    artifact_dir: Union[str, Path],
    viewer_dir: Optional[Union[str, Path]] = None,
    *,
    viewer_mode: str = "image-tiles",
    render_policy: str = "lazy",
    max_viewer_pages: int = 30,
    max_zone_tiles: int = 300,
    export_marked_pdf: bool = False,
    marked_pdf_mode: str = "selected",
    review_dashboard: Optional[Union[str, Path, Dict[str, Any]]] = None,
    preview_manifest: Optional[Union[str, Path, Dict[str, Any]]] = None,
    review_dashboard_path: Optional[Union[str, Path]] = None,
    preview_manifest_path: Optional[Union[str, Path]] = None,
    marked_pdf_selection_csv: Optional[Union[str, Path]] = None,
    dxf_cache_dir: Optional[Union[str, Path]] = None,
    preview_dpi: int = 80,
    preview_max_edge_px: int = 2400,
    viewer_engine: str = "auto",
    viewer_cache_dir: Optional[Union[str, Path]] = None,
    tile_size: int = 512,
    max_visible_overlays: int = 500,
    viewer_memory_budget_mb: int = 512,
    render_selected_on_open: bool = False,
    prefetch_neighbor_tiles: bool = True,
    tile_prefetch_radius: int = 1,
    overview_max_edge: int = 2200,
    focus_tile_max_edge: int = 1600,
    viewer_perf_log: bool = False,
    render_timeout_seconds: int = 0,
    build_lod_tiles: bool = True,
    runtime_sampler: Optional[Any] = None,
    memory_cap_mb: Optional[float] = None,
) -> ViewerPackage:
    """Create viewer manifest, overlays, optional PNG backgrounds/tiles/PDFs.

    ``lazy`` writes full overlay data only.  ``top-issues`` renders pairs that
    are part of the review dashboard selection.  ``all`` renders every pair up
    to ``max_viewer_pages``.  Marked PDF export is only created from a rendered
    PNG background and pixel bbox data; inaccurate extent-normalized PDF output
    is intentionally not produced.
    """

    artifact_root = Path(artifact_dir)
    viewer_root = Path(viewer_dir) if viewer_dir else artifact_root / "viewer"
    overlay_dir = viewer_root / "overlays"
    page_dir = viewer_root / "pages"
    image_dir = viewer_root / "images"
    focus_tile_dir = viewer_root / "focus_tiles"
    marked_pdf_dir = viewer_root / "marked_pdf"
    viewer_cache_root = Path(viewer_cache_dir) if viewer_cache_dir else viewer_root
    tile_dir = viewer_cache_root / "tiles"
    overlay_tile_dir = viewer_cache_root / "overlay_tiles"
    for directory in (viewer_root, overlay_dir, page_dir, image_dir, tile_dir, focus_tile_dir, overlay_tile_dir, marked_pdf_dir):
        directory.mkdir(parents=True, exist_ok=True)

    options = ViewerPackageOptions(
        viewer_mode=viewer_mode,
        render_policy=render_policy,
        max_viewer_pages=max(0, int(max_viewer_pages)),
        max_zone_tiles=max(0, int(max_zone_tiles)),
        export_marked_pdf=bool(export_marked_pdf),
        marked_pdf_mode=marked_pdf_mode,
        preview_dpi=max(20, int(preview_dpi)),
        preview_max_edge_px=max(800, int(preview_max_edge_px)),
        tile_size=max(128, int(tile_size)),
        max_visible_overlays=max(25, int(max_visible_overlays)),
        viewer_memory_budget_mb=max(128, int(viewer_memory_budget_mb)),
        viewer_engine=viewer_engine if viewer_engine in {"auto", "qtquick", "qtquick-widget", "qtquick-window", "widgets"} else "auto",
        tile_prefetch_radius=max(0, int(tile_prefetch_radius)),
        overview_max_edge=max(800, int(overview_max_edge)),
        focus_tile_max_edge=max(512, int(focus_tile_max_edge)),
        viewer_perf_log=bool(viewer_perf_log),
        render_timeout_seconds=max(0, int(render_timeout_seconds)),
        build_lod_tiles=bool(build_lod_tiles),
    )
    tile_options = ViewerTileCacheOptions(
        tile_size=options.tile_size,
        max_edge_overview=options.overview_max_edge,
        max_visible_overlays=options.max_visible_overlays,
        viewer_memory_budget_mb=options.viewer_memory_budget_mb,
    ).normalized()

    zones_path = artifact_root / "change_zones.csv"
    manifest_path = artifact_root / "artifact_manifest.json"
    if not zones_path.exists():
        raise FileNotFoundError(f"change_zones.csv not found: {zones_path}")

    zones = _read_csv(zones_path)
    artifact_manifest = _read_json(manifest_path) if manifest_path.exists() else {}
    if review_dashboard is None and review_dashboard_path is not None:
        review_dashboard = review_dashboard_path
    if preview_manifest is None and preview_manifest_path is not None:
        preview_manifest = preview_manifest_path
    review_data = _load_optional_json(review_dashboard or artifact_manifest.get("review_dashboard_json"))
    preview_data = _load_optional_json(preview_manifest or artifact_manifest.get("preview_manifest_json"))
    preview_by_pair = _preview_by_pair(preview_data)
    selected_keys = _selected_zone_keys(review_data)
    priority_by_key = _priority_by_key(review_data)
    pair_artifacts = _pair_artifacts(artifact_manifest)
    zones_by_pair = _group_zones(zones)
    pair_extents = {pair_id: _pair_extents(rows) for pair_id, rows in zones_by_pair.items()}

    warnings: List[str] = []
    pair_entries: List[Dict[str, Any]] = []
    total_overlay_count = 0
    total_tile_count = 0
    marked_pdf_count = 0
    marked_pdf_skipped_count = 0
    rendered_pairs = 0
    lazy_pairs = 0
    page_count = 0
    render_slots_used = 0

    dxf_cache_root = Path(dxf_cache_dir) if dxf_cache_dir else artifact_root / "dxf_cache"
    dxf_cache_root.mkdir(parents=True, exist_ok=True)
    viewer_cache_root.mkdir(parents=True, exist_ok=True)
    cache_tile_dir = viewer_cache_root / "tiles"
    cache_overlay_tile_dir = viewer_cache_root / "overlay_tiles"
    cache_tiles_manifest = viewer_cache_root / "tiles_manifest.json"

    # Audit-gates §10.4 — enforce viewer memory budget per pair so a single
    # large drawing (e.g. S20 with 350K change-zone records at DPI 400) cannot
    # exhaust the host machine. Default cap derives from
    # ``options.viewer_memory_budget_mb`` (existing field) but a stricter
    # ``memory_cap_mb`` argument may be supplied by the GUI.
    effective_memory_cap = (
        float(memory_cap_mb)
        if memory_cap_mb is not None
        else float(max(options.viewer_memory_budget_mb * 8, 4096))
    )

    # Audit-gates §11.6 — track adaptive degradation warnings so each tier
    # transition is reported exactly once instead of once-per-pair.
    _degradation_warned = {"value": False}

    for pair_id in sorted(zones_by_pair):
        if runtime_sampler is not None:
            runtime_sampler.assert_within_memory_budget(
                effective_memory_cap, stage="viewer_package.pair_loop"
            )
            # Adaptive degradation warning: when working set crosses 70% of
            # the cap mid-loop, surface a single warning into the per-pair
            # ``warnings`` list so the GUI/audit can advise the user to
            # downgrade DPI on subsequent runs. Real per-pair DPI override
            # is left to a follow-up commit; this hook is the first step.
            if not _degradation_warned["value"]:
                try:
                    peek_mb = runtime_sampler.peek_working_set_mb()
                except Exception:
                    peek_mb = None
                if (
                    peek_mb is not None
                    and effective_memory_cap > 0
                    and peek_mb > effective_memory_cap * 0.7
                ):
                    _degradation_warned["value"] = True
                    warnings.append(
                        "memory_pressure_at_pair_loop_start: "
                        f"peak_working_set={peek_mb:.0f}MB > "
                        f"{effective_memory_cap * 0.7:.0f}MB (70% of cap "
                        f"{effective_memory_cap:.0f}MB) — consider lowering "
                        "preview_dpi or running with viewer_render_policy='lazy'"
                    )
        rows = zones_by_pair[pair_id]
        safe_pair = _safe_name(pair_id)
        pair_warning: List[str] = []
        pair_artifact = pair_artifacts.get(pair_id, {})
        source_a = _source_path_for_pair(rows, pair_artifact, "a")
        source_b = _source_path_for_pair(rows, pair_artifact, "b")
        preview_entry = preview_by_pair.get(pair_id, {})

        # Phase H integration — pick the per-side PDF page indices for
        # background rendering. For multi-page PDFs each ``row`` is a
        # zone tagged with the matched (page_a, page_b) from the page
        # matcher; we render the FIRST matched page pair so that page's
        # cloud markers line up with its background image. Single-page
        # PDFs and DXF/DWG default to (0, 0).
        primary_page_a, primary_page_b = _primary_page_pair_for_pair(rows)
        # Phase H + multi-page navigation — surface every matched page
        # pair so the workbench can render a navigator when N > 1.
        all_page_pairs = _all_page_pairs_for_pair(rows)

        render_decision = _render_decision(
            pair_id=pair_id,
            policy=options.render_policy,
            selected_keys=selected_keys,
            render_slots_used=render_slots_used,
            max_viewer_pages=options.max_viewer_pages,
        )

        background = _reuse_preview_background(preview_entry)
        render_status = background["render_status"]
        if render_decision == "render":
            render_slots_used += 1
            if background["after_image"] and background["after_transform"]:
                render_status = "preview_reused"
            else:
                rendered = _render_pair_backgrounds_with_timeout(
                    pair_id=pair_id,
                    source_a=source_a,
                    source_b=source_b,
                    image_dir=image_dir,
                    dxf_cache_dir=dxf_cache_root,
                    dpi=options.preview_dpi,
                    max_edge_px=options.preview_max_edge_px,
                    timeout_seconds=options.render_timeout_seconds,
                    page_a=primary_page_a,
                    page_b=primary_page_b,
                )
                background.update(rendered)
                render_status = rendered["render_status"]
                pair_warning.extend(rendered["warnings"])
        elif render_decision == "skipped_by_page_cap":
            render_status = "skipped_by_page_cap"
        elif background["after_image"] and background["after_transform"]:
            render_status = "preview_reused"
        else:
            render_status = "lazy_not_rendered"

        before_transform = background.get("before_transform") or None
        after_transform = background.get("after_transform") or None
        before_image = background.get("before_image") or ""
        after_image = background.get("after_image") or ""
        background_type = "png" if after_image else "none"
        is_pdf_pair = bool(
            source_a
            and source_b
            and source_a.suffix.lower() in PDF_EXTENSIONS
            and source_b.suffix.lower() in PDF_EXTENSIONS
        )
        pdf_page_size = _pdf_page_size_from_transforms(after_transform, before_transform) if is_pdf_pair else None
        coordinate_source = "image_pixels" if is_pdf_pair else "cad_world"
        visual_fidelity = "pdf_render" if is_pdf_pair else ("cad_render" if background_type == "png" else "relative_overlay")
        render_lifecycle = "ready" if background_type == "png" else ("idle" if is_pdf_pair else "queued")
        if background_type == "png":
            rendered_pairs += 1
        else:
            lazy_pairs += 1

        # G2.7-COORDFIX — for PDF pairs, propagate the coordinate-space
        # marker + effective DPI down into each overlay so the lightweight
        # viewport (which renders the page in PDF points, not pixels) can
        # convert px → pt before plotting cloud markers. Without these
        # fields, ``convert_bbox_to_world_space`` skips the conversion and
        # the markers float at world coords ~6× outside the page bounds.
        pair_pdf_dpi = _pdf_dpi_from_zone_rows(rows) if is_pdf_pair else 0.0
        overlays = [
            _overlay_from_zone_row(
                row,
                pair_extents[pair_id],
                priority_by_key.get((pair_id, str(row.get("zone_id", "")))),
                (pair_id, str(row.get("zone_id", ""))) in selected_keys,
                before_transform=before_transform,
                after_transform=after_transform,
                bbox_coordinate_space=(coordinate_source if is_pdf_pair else ""),
                pdf_dpi=pair_pdf_dpi,
            )
            for row in rows
        ]
        overlays = _sort_overlays(overlays)
        if options.max_overlay_records_per_pair and options.max_overlay_records_per_pair > 0:
            overlays = overlays[: options.max_overlay_records_per_pair]

        pair_tile_count = 0
        if options.render_policy in {"top-issues", "all"} and after_image and total_tile_count < options.max_zone_tiles:
            tile_budget = options.max_zone_tiles - total_tile_count
            pair_tile_count, tile_warnings = _write_zone_tiles(
                pair_id=pair_id,
                overlays=overlays,
                after_image=after_image,
                tile_dir=focus_tile_dir,
                max_tiles=tile_budget,
                max_edge=options.focus_tile_max_edge,
            )
            pair_warning.extend(tile_warnings)
            total_tile_count += pair_tile_count

        pair_lod_tile_count = 0
        pair_overlay_tile_count = 0
        tile_manifest_path = ""
        tile_cache_key = viewer_cache_key(
            pair_uuid=pair_id,
            source_a=source_a,
            source_b=source_b,
            options=tile_options,
        )
        if (
            options.build_lod_tiles
            and after_image
            and options.render_policy in {"top-issues", "all"}
        ):
            pair_tile_manifest = {}
            if tiles_manifest_is_current(cache_tiles_manifest, pair_id, tile_cache_key):
                pair_tile_manifest = ((_read_json(cache_tiles_manifest).get("pairs") or {}).get(pair_id) or {})
            if not isinstance(pair_tile_manifest, dict) or not pair_tile_manifest:
                pair_tile_manifest = write_pair_tile_cache(
                    pair_uuid=pair_id,
                    before_image=before_image,
                    after_image=after_image,
                    overlays=overlays,
                    tile_root=cache_tile_dir,
                    overlay_tile_root=cache_overlay_tile_dir,
                    options=tile_options,
                    cache_key=tile_cache_key,
                )
                # Audit-gates §11.5 — streaming JSONL append instead of the
                # legacy O(N²) read-mutate-rewrite. Both sinks (cache + viewer)
                # accumulate per-pair records cheaply; a single materialise
                # call after the pair loop produces the consolidated JSON.
                append_pair_to_tiles_manifest_jsonl(viewer_cache_root, pair_tile_manifest)
            append_pair_to_tiles_manifest_jsonl(viewer_root, pair_tile_manifest)
            tile_manifest_path = str(viewer_root / "tiles_manifest.json")
            pair_lod_tile_count = int(pair_tile_manifest.get("tile_count", 0))
            pair_overlay_tile_count = int(pair_tile_manifest.get("overlay_tile_count", 0))
            if options.viewer_perf_log:
                append_viewer_perf_event(
                    viewer_root,
                    "package_tile_write",
                    pair_uuid=pair_id,
                    tile_count=pair_lod_tile_count,
                    overlay_tile_count=pair_overlay_tile_count,
                    render_policy=options.render_policy,
                )

        overlay_payload = {
            "schema_version": OVERLAY_SCHEMA_VERSION,
            "pair_id": pair_id,
            "coordinate_source": coordinate_source,
            "visual_fidelity": visual_fidelity,
            "render_lifecycle": render_lifecycle,
            "pdf_page": primary_page_a if is_pdf_pair else None,
            "page_a": primary_page_a if is_pdf_pair else None,
            "page_b": primary_page_b if is_pdf_pair else None,
            "pdf_page_size": pdf_page_size,
            "compare_pdf_dpi": pair_pdf_dpi if is_pdf_pair else None,
            "viewer_coordinate_space": "pixel" if after_transform else "unit_page",
            "before_transform": before_transform,
            "after_transform": after_transform,
            "before_image": before_image,
            "after_image": after_image,
            "overlay_count": len(overlays),
            "zone_count": len(overlays),
            "overlays": overlays,
        }
        overlay_path = overlay_dir / f"{safe_pair}.json"
        _write_json(overlay_path, overlay_payload)
        total_overlay_count += len(overlays)

        marked_pdf_path = ""
        marked_pdf_status = "off"
        if options.export_marked_pdf:
            marked_pdf_path, marked_pdf_status, pdf_warning = _export_marked_pdf_for_pair(
                pair_id=pair_id,
                source_b=source_b,
                after_image=after_image,
                overlays=_marked_pdf_overlays(overlays, options.marked_pdf_mode, selected_keys),
                marked_pdf_dir=marked_pdf_dir,
            )
            if pdf_warning:
                pair_warning.append(pdf_warning)
            if marked_pdf_path:
                marked_pdf_count += 1
            else:
                marked_pdf_skipped_count += 1

        before_page_path = _copy_reference_pdf(pair_id, source_a, page_dir, side="before")
        after_page_path = _copy_reference_pdf(pair_id, source_b, page_dir, side="after")
        page_path = after_page_path or before_page_path
        if before_page_path or after_page_path:
            page_count += 1

        pair_entry = {
            "pair_id": pair_id,
            "pair_uuid": pair_id,
            "display_label": _first_nonempty(rows, "display_label", default=_first_nonempty(rows, "drawing_number", default=pair_id)),
            "drawing_number": _first_nonempty(rows, "drawing_number", default=pair_id),
            "source_a": str(source_a) if source_a else "",
            "source_b": str(source_b) if source_b else "",
            "coordinate_source": coordinate_source,
            "background_type": background_type,
            "visual_fidelity": visual_fidelity,
            "render_lifecycle": render_lifecycle,
            # Phase H integration — primary page indices (per side) used
            # for background rendering. Same value as `pdf_page` for
            # back-compat; new consumers (G2.7 lightweight viewer)
            # prefer page_a/page_b explicitly.
            "pdf_page": primary_page_a if is_pdf_pair else None,
            "page_a": primary_page_a if is_pdf_pair else None,
            "page_b": primary_page_b if is_pdf_pair else None,
            # Multi-page navigation — empty list when N <= 1 so the GUI
            # decides cheaply whether to show the navigator widget.
            "page_match_pairs": all_page_pairs if is_pdf_pair else [],
            "pdf_page_size": pdf_page_size,
            "compare_pdf_dpi": pair_pdf_dpi if is_pdf_pair else None,
            "before_transform": before_transform,
            "after_transform": after_transform,
            "before_image": before_image,
            "after_image": after_image,
            "before_page_pdf": str(before_page_path) if before_page_path else "",
            "after_page_pdf": str(after_page_path) if after_page_path else "",
            "page_pdf": str(page_path) if page_path else "",
            "overlay_json": str(overlay_path),
            "render_status": render_status,
            "render_warning": "; ".join(pair_warning),
            "overlay_count": len(overlays),
            "tile_count": pair_tile_count,
            "lod_tile_count": pair_lod_tile_count,
            "overlay_tile_count": pair_overlay_tile_count,
            "tile_manifest": tile_manifest_path,
            "tile_cache_key": tile_cache_key,
            "marked_pdf": marked_pdf_path,
            "marked_pdf_status": marked_pdf_status,
        }
        pair_entries.append(pair_entry)
        warnings.extend([f"{pair_id}: {warning}" for warning in pair_warning if warning])

    # Audit-gates §11.5 — pair loop is done; consolidate the streaming JSONL
    # records into the canonical ``tiles_manifest.json`` consumed by GUI /
    # viewer packaging downstream. Both sinks (cache + viewer) are materialised
    # so existing readers (tiles_manifest_is_current, viewer manifest export)
    # see the same dict shape they expect from the legacy merge code path.
    try:
        materialise_tiles_manifest_from_jsonl(viewer_cache_root, keep_jsonl=False)
    except Exception:
        # Best-effort: a missing JSONL on the cache side just means no pairs
        # produced tile records this run; downstream readers tolerate empty
        # manifests, so we do not fail the whole viewer build over this.
        pass
    try:
        materialise_tiles_manifest_from_jsonl(viewer_root, keep_jsonl=False)
    except Exception:
        pass

    transform_complete = bool(pair_entries) and all(entry.get("after_transform") for entry in pair_entries)
    viewer_manifest = {
        "schema_version": VIEWER_PACKAGE_SCHEMA_VERSION,
        "viewer_mode": options.viewer_mode,
        "viewer_engine": options.viewer_engine,
        "viewer_render_policy": options.render_policy,
        "viewer_cache_dir": str(viewer_cache_root),
        "tile_size": options.tile_size,
        "max_visible_overlays": options.max_visible_overlays,
        "viewer_memory_budget_mb": options.viewer_memory_budget_mb,
        "render_selected_on_open": bool(render_selected_on_open),
        "prefetch_neighbor_tiles": bool(prefetch_neighbor_tiles),
        "tile_prefetch_radius": options.tile_prefetch_radius,
        "overview_max_edge": options.overview_max_edge,
        "focus_tile_max_edge": options.focus_tile_max_edge,
        "viewer_perf_log": bool(options.viewer_perf_log),
        "build_lod_tiles": bool(options.build_lod_tiles),
        "coordinate_source": "cad_world",
        "rendered_pair_count": rendered_pairs,
        "lazy_pair_count": lazy_pairs,
        "page_count": page_count,
        "tile_count": total_tile_count,
        "tiles_manifest": str(viewer_root / "tiles_manifest.json"),
        "marked_pdf_count": marked_pdf_count,
        "marked_pdf_skipped_count": marked_pdf_skipped_count,
        "transform_complete": transform_complete,
        "pair_count": len(pair_entries),
        "overlay_count": total_overlay_count,
        "warnings": warnings,
        "directories": {
            "overlays": str(overlay_dir),
            "pages": str(page_dir),
            "images": str(image_dir),
            "tiles": str(tile_dir),
            "focus_tiles": str(focus_tile_dir),
            "overlay_tiles": str(overlay_tile_dir),
            "marked_pdf": str(marked_pdf_dir),
        },
        "viewer_perf_json": str(viewer_root / "viewer_perf.json"),
        "pairs": pair_entries,
    }

    manifest_out = viewer_root / "viewer_manifest.json"
    tiles_manifest_out = viewer_root / "tiles_manifest.json"
    if not tiles_manifest_out.exists():
        _write_json(
            tiles_manifest_out,
            {
                "schema_version": 1,
                "tile_size": options.tile_size,
                "pairs": {},
                "pair_count": 0,
                "tile_count": 0,
                "overlay_tile_count": 0,
            },
        )
    _write_json(manifest_out, viewer_manifest)
    # Phase F P0 — emit a v2 manifest sidecar with explicit fidelity + per-artifact
    # transform_quality. The v2 file is read by the new badge / state-machine
    # layer in the GUI; absence of this file means the GUI falls back to v1
    # behaviour. Failures here are non-fatal — the v1 manifest is the source of
    # truth for the legacy code path.
    try:
        v2_path = viewer_root / V2_MANIFEST_OUTPUT_NAME
        v2_manifest = _build_v2_manifest_from_v1(
            v1_manifest=viewer_manifest,
            options=options,
        )
        write_manifest_v2(v2_path, v2_manifest)
    except (V2ManifestValidationError, OSError, ValueError) as exc:
        logger.warning("Skipped v2 manifest emission: %s", exc)
    # Phase G — emit a v3 manifest sidecar with scene-pack references. Scene
    # pack BUILD is intentionally lazy: G1 ships the schema + manifest path
    # plumbing only. The actual scene_pack.json + primitive_index.* artifacts
    # are produced on first viewport open by the viewer_session (G2). Until
    # then, the manifest carries empty ScenePackRef entries and the GUI
    # falls back to the v1/v2 raster path. Failures are non-fatal.
    try:
        v3_path = viewer_root / V3_MANIFEST_OUTPUT_NAME
        v3_manifest = _build_v3_manifest_from_v1(
            v1_manifest=viewer_manifest,
            options=options,
            viewer_root=viewer_root,
        )
        write_manifest_v3(v3_path, v3_manifest)
    except (ManifestV3ValidationError, OSError, ValueError) as exc:
        logger.warning("Skipped v3 manifest emission: %s", exc)
    index_html = viewer_root / "index.html"
    _write_index_html(index_html, viewer_manifest)
    _update_artifact_manifest(artifact_root, manifest_out, index_html, viewer_manifest)

    output_paths = {
        "viewer_dir": str(viewer_root),
        "viewer_manifest_json": str(manifest_out),
        "viewer_index_html": str(index_html),
        "viewer_overlays_dir": str(overlay_dir),
        "viewer_pages_dir": str(page_dir),
        "viewer_images_dir": str(image_dir),
        "viewer_tiles_dir": str(tile_dir),
        "viewer_focus_tiles_dir": str(focus_tile_dir),
        "viewer_overlay_tiles_dir": str(overlay_tile_dir),
        "viewer_tiles_manifest_json": str(viewer_root / "tiles_manifest.json"),
        "viewer_perf_json": str(viewer_root / "viewer_perf.json"),
        "marked_pdf_dir": str(marked_pdf_dir),
    }
    return ViewerPackage(
        viewer_dir=viewer_root,
        manifest_path=manifest_out,
        index_html=index_html,
        pair_count=len(pair_entries),
        overlay_count=total_overlay_count,
        page_count=page_count,
        tile_count=total_tile_count,
        marked_pdf_count=marked_pdf_count,
        marked_pdf_skipped_count=marked_pdf_skipped_count,
        rendered_pair_count=rendered_pairs,
        lazy_pair_count=lazy_pairs,
        transform_complete=transform_complete,
        warnings=warnings,
        output_paths=output_paths,
    )


def _read_json(path: Union[str, Path]) -> Dict[str, Any]:
    try:
        with Path(path).open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: Union[str, Path], data: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    # Sanitize lone surrogate codepoints before serializing — Korean Windows
    # paths sometimes carry CP949↔UTF-16 leftovers that explode here as
    # "'utf-8' codec can't encode character ... surrogates not allowed".
    # See safe_unicode module for full background.
    from .safe_unicode import safe_unicode

    safe_data = safe_unicode(data)
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(safe_data, f, ensure_ascii=False, indent=2)
    tmp.replace(target)


def _read_csv(path: Union[str, Path]) -> List[Dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _load_optional_json(source: Optional[Union[str, Path, Dict[str, Any]]]) -> Dict[str, Any]:
    if isinstance(source, dict):
        return source
    if not source:
        return {}
    path = Path(source)
    return _read_json(path) if path.exists() else {}


def _preview_by_pair(preview_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    previews = preview_data.get("previews") or preview_data.get("pairs") or []
    if not isinstance(previews, list):
        return {}
    result: Dict[str, Dict[str, Any]] = {}
    for preview in previews:
        if isinstance(preview, dict) and preview.get("pair_id"):
            result[str(preview["pair_id"])] = preview
    return result


def _selected_zone_keys(review_data: Dict[str, Any]) -> set[Tuple[str, str]]:
    keys: set[Tuple[str, str]] = set()
    for issue in _review_issues(review_data):
        pair_id = str(issue.get("pair_id") or issue.get("drawing_number") or "")
        zone_id = str(issue.get("zone_id") or "")
        if pair_id and zone_id:
            keys.add((pair_id, zone_id))
    return keys


def _priority_by_key(review_data: Dict[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    result: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for idx, issue in enumerate(_review_issues(review_data), start=1):
        pair_id = str(issue.get("pair_id") or issue.get("drawing_number") or "")
        zone_id = str(issue.get("zone_id") or "")
        if pair_id and zone_id:
            result[(pair_id, zone_id)] = {
                "rank": issue.get("rank") or issue.get("priority_rank") or idx,
                "score": issue.get("priority_score"),
                "reason": issue.get("priority_reason") or issue.get("why") or "",
            }
    return result


def _review_issues(review_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    issues = review_data.get("top_issues")
    if not isinstance(issues, list):
        issues = review_data.get("top_project_issues")
    if not isinstance(issues, list):
        issues = review_data.get("review_issues")
    if not isinstance(issues, list):
        issues = review_data.get("issues")
    return [issue for issue in issues if isinstance(issue, dict)] if isinstance(issues, list) else []


def _pair_artifacts(artifact_manifest: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for item in (
        artifact_manifest.get("items", [])
        or artifact_manifest.get("pairs", [])
        or artifact_manifest.get("artifacts", [])
        or []
    ):
        if isinstance(item, dict) and item.get("pair_id"):
            result[str(item["pair_id"])] = item
    return result


def _group_zones(zones: Iterable[Dict[str, str]]) -> Dict[str, List[Dict[str, str]]]:
    grouped: Dict[str, List[Dict[str, str]]] = {}
    for row in zones:
        pair_id = str(row.get("pair_id") or row.get("drawing_number") or row.get("file_id") or "unknown")
        grouped.setdefault(pair_id, []).append(row)
    return grouped


def _source_path_for_pair(rows: Sequence[Dict[str, str]], artifact: Dict[str, Any], side: str) -> Optional[Path]:
    keys = (
        ("source_a", "a_path", "before_path", "old_path")
        if side == "a"
        else ("source_b", "b_path", "after_path", "new_path")
    )
    for key in keys:
        value = artifact.get(key)
        if value:
            return Path(str(value))
    for row in rows:
        for key in keys:
            value = row.get(key)
            if value:
                return Path(str(value))
    return None


def _first_nonempty(rows: Sequence[Dict[str, str]], key: str, default: str = "") -> str:
    for row in rows:
        value = row.get(key)
        if value:
            return str(value)
    return default


def _pdf_dpi_from_zone_rows(rows: Sequence[Dict[str, Any]]) -> float:
    for row in rows:
        if not isinstance(row, dict):
            continue
        for source in (row, row.get("metadata") if isinstance(row.get("metadata"), dict) else None):
            if not isinstance(source, dict):
                continue
            for key in ("pdf_dpi", "compare_pdf_dpi", "effective_dpi"):
                try:
                    value = float(source.get(key) or 0.0)
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    return value
    return 0.0


def _row_metadata_sources(row: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    yield row
    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        yield metadata


def _row_page_pair(row: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    if not isinstance(row, dict):
        return None
    for source in _row_metadata_sources(row):
        if "page_a" not in source and "page_b" not in source:
            continue
        try:
            return (
                int(source.get("page_a", 0) or 0),
                int(source.get("page_b", 0) or 0),
            )
        except (TypeError, ValueError):
            continue
    return None


def _row_value(row: Dict[str, Any], key: str, default: Any = "") -> Any:
    if key in row and row.get(key) not in (None, ""):
        return row.get(key)
    metadata = row.get("metadata")
    if isinstance(metadata, dict) and metadata.get(key) not in (None, ""):
        return metadata.get(key)
    return default


def _primary_page_pair_for_pair(rows: Sequence[Dict[str, Any]]) -> Tuple[int, int]:
    """Phase H integration — return the matched (page_a, page_b) for the
    page that the viewer should render as the background.

    Walks the per-zone rows looking for ``page_a``/``page_b`` keys
    (populated by ``compare_pdf_documents`` after Phase H matching).
    Returns the FIRST encountered pair so the viewer's background image
    matches the first batch of zones — singletons / DXF / single-page
    PDF runs all default to ``(0, 0)``.

    The helper is intentionally tolerant: missing keys and non-int values
    are skipped. Negative sentinels are preserved because ``-1`` is the
    contract for a missing before/after side in one-sided PDF page matches.
    """

    for row in rows:
        pair = _row_page_pair(row)
        if pair is not None:
            return pair
    return (0, 0)


def _all_page_pairs_for_pair(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, int]]:
    """Phase H integration — collect EVERY unique (page_a, page_b) tuple
    referenced by the change-zone rows, deduped and sorted.

    Drives the workbench's multi-page navigation widget: when a PDF
    file pair has more than one matched page pair the viewer surfaces
    a "1/N pages" navigator so the user can step between them.

    Returns a list of ``{"page_a": int, "page_b": int}`` dicts. Empty
    list when no rows have page metadata (DXF / single-page PDF —
    navigation is unnecessary in those cases).
    """

    seen: set[Tuple[int, int]] = set()
    for row in rows:
        pair = _row_page_pair(row)
        if pair is not None:
            seen.add(pair)
    return [{"page_a": pa, "page_b": pb} for pa, pb in sorted(seen)]

            # Keep negative sentinels out — they mean "unmatched on this side"


def _pair_extents(rows: Sequence[Dict[str, str]]) -> Tuple[float, float, float, float]:
    boxes: List[Tuple[float, float, float, float]] = []
    for row in rows:
        bbox = _bbox_from_zone_row(row)
        if bbox:
            boxes.append(bbox)
        old_bbox = _bbox_from_zone_row(row, old=True)
        if old_bbox:
            boxes.append(old_bbox)
    if not boxes:
        return (0.0, 0.0, 1.0, 1.0)
    min_x = min(box[0] for box in boxes)
    min_y = min(box[1] for box in boxes)
    max_x = max(box[2] for box in boxes)
    max_y = max(box[3] for box in boxes)
    if math.isclose(min_x, max_x):
        max_x = min_x + 1.0
    if math.isclose(min_y, max_y):
        max_y = min_y + 1.0
    return (min_x, min_y, max_x, max_y)


def _parse_bbox(value: Any) -> Optional[Tuple[float, float, float, float]]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        try:
            return tuple(float(value[i]) for i in range(4))  # type: ignore[return-value]
        except (TypeError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, (list, tuple)) and len(data) >= 4:
            return tuple(float(data[i]) for i in range(4))  # type: ignore[return-value]
    except Exception:
        pass
    parts = [part.strip() for part in re.split(r"[,;| ]+", text) if part.strip()]
    if len(parts) >= 4:
        try:
            return (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))
        except ValueError:
            return None
    return None


def _bbox_from_zone_row(row: Dict[str, Any], *, old: bool = False) -> Optional[Tuple[float, float, float, float]]:
    direct_keys = ("old_bbox", "before_bbox") if old else ("bbox", "after_bbox")
    for key in direct_keys:
        box = _parse_bbox(row.get(key))
        if box:
            return box

    prefix = "old_bbox" if old else "bbox"
    try:
        return (
            float(row.get(f"{prefix}_min_x")),
            float(row.get(f"{prefix}_min_y")),
            float(row.get(f"{prefix}_max_x")),
            float(row.get(f"{prefix}_max_y")),
        )
    except (TypeError, ValueError):
        return None


def _first_list_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return str(data[0]) if data else ""
    except Exception:
        pass
    return next((part.strip() for part in re.split(r"[|;,]+", text) if part.strip()), text)


def _bbox_dict(box: Optional[Tuple[float, float, float, float]]) -> Optional[Dict[str, float]]:
    if not box:
        return None
    return {"min_x": box[0], "min_y": box[1], "max_x": box[2], "max_y": box[3]}


def _normalize_bbox(
    box: Optional[Tuple[float, float, float, float]],
    extents: Tuple[float, float, float, float],
) -> Optional[Dict[str, float]]:
    if not box:
        return None
    min_x, min_y, max_x, max_y = extents
    width = max(max_x - min_x, 1e-9)
    height = max(max_y - min_y, 1e-9)
    x1 = (box[0] - min_x) / width
    y1 = 1.0 - ((box[3] - min_y) / height)
    x2 = (box[2] - min_x) / width
    y2 = 1.0 - ((box[1] - min_y) / height)
    return {
        "x": _clamp01(x1),
        "y": _clamp01(y1),
        "width": _clamp01(x2 - x1),
        "height": _clamp01(y2 - y1),
    }


def _pixel_bbox(
    box: Optional[Tuple[float, float, float, float]],
    transform: Optional[Dict[str, Any]],
) -> Optional[Dict[str, float]]:
    if not box or not transform:
        return None
    try:
        pixel = _bbox_to_pixel_bbox(box, transform)
    except Exception:
        return None
    if isinstance(pixel, (list, tuple)) and len(pixel) >= 4:
        left = float(pixel[0])
        top = float(pixel[1])
        right = float(pixel[2])
        bottom = float(pixel[3])
        return {
            "x": left,
            "y": top,
            "width": max(1.0, right - left),
            "height": max(1.0, bottom - top),
        }
    return {
        "x": float(pixel["x"]),
        "y": float(pixel["y"]),
        "width": float(pixel["width"]),
        "height": float(pixel["height"]),
    }


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _overlay_from_zone_row(
    row: Dict[str, str],
    extents: Tuple[float, float, float, float],
    priority: Optional[Dict[str, Any]],
    selected: bool,
    *,
    before_transform: Optional[Dict[str, Any]],
    after_transform: Optional[Dict[str, Any]],
    bbox_coordinate_space: str = "",
    pdf_dpi: float = 0.0,
) -> Dict[str, Any]:
    bbox = _bbox_from_zone_row(row)
    old_bbox = _bbox_from_zone_row(row, old=True)
    if not old_bbox:
        old_bbox = bbox
    zone_id = str(row.get("zone_id") or "")
    change_type = str(row.get("change_type") or row.get("dominant_change_type") or "mixed")
    layer = str(row.get("primary_layer") or row.get("layer") or _first_list_value(row.get("layers")) or "")
    entity_type = str(row.get("entity_type") or row.get("primary_entity_type") or _first_list_value(row.get("entity_types")) or "")
    raw_count = _safe_int(row.get("raw_change_count") or row.get("raw_count") or row.get("change_count"))
    severity = str(row.get("severity") or _severity_from_count(raw_count))
    priority_rank = priority.get("rank") if priority else None
    priority_score = priority.get("score") if priority else None
    page_pair = _row_page_pair(row)
    overlay = {
        "schema_version": OVERLAY_SCHEMA_VERSION,
        "pair_id": str(row.get("pair_id") or row.get("drawing_number") or ""),
        "pair_uuid": str(row.get("pair_uuid") or row.get("pair_id") or row.get("drawing_number") or ""),
        "display_label": str(row.get("display_label") or row.get("drawing_number") or row.get("pair_id") or ""),
        "drawing_number": str(row.get("drawing_number") or row.get("pair_id") or ""),
        "zone_id": zone_id,
        "label": zone_id or "C-000",
        "change_type": change_type,
        "change_label": _change_label(change_type),
        "severity": severity,
        "layer": layer,
        "entity_type": entity_type,
        "raw_change_count": raw_count,
        "bbox": _bbox_dict(bbox),
        "old_bbox": _bbox_dict(old_bbox),
        "normalized_bbox": _normalize_bbox(bbox, extents),
        "before_bbox_px": _pixel_bbox(old_bbox, before_transform),
        "after_bbox_px": _pixel_bbox(bbox, after_transform),
        "selected_for_review": bool(selected),
        "priority_rank": priority_rank,
        "priority_score": priority_score,
        "priority_reason": priority.get("reason") if priority else "",
        # G2.7-COORDFIX — PDF metadata for lightweight viewer coord conversion
        "bbox_coordinate_space": bbox_coordinate_space,
        "pdf_dpi": pdf_dpi,
    }
    if page_pair is not None:
        page_a, page_b = page_pair
        overlay["page_a"] = page_a
        overlay["page_b"] = page_b
        overlay["pdf_page"] = page_a if page_a >= 0 else None
        overlay["page_match_status"] = _row_value(row, "page_match_status", "")
        overlay["page_match_score"] = _row_value(row, "page_match_score", "")
    return overlay


def _sort_overlays(overlays: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def key(overlay: Dict[str, Any]) -> Tuple[int, int, int, str]:
        selected = 0 if overlay.get("selected_for_review") else 1
        rank = overlay.get("priority_rank")
        rank_value = int(rank) if isinstance(rank, (int, float)) or str(rank).isdigit() else 999999
        raw = -_safe_int(overlay.get("raw_change_count"))
        zone = str(overlay.get("zone_id") or "")
        return (selected, rank_value, raw, zone)

    return sorted(overlays, key=key)


def _change_label(change_type: str) -> str:
    normalized = (change_type or "").lower()
    if "add" in normalized:
        return "+ 추가"
    if "delete" in normalized or "remove" in normalized:
        return "- 삭제"
    if "move" in normalized:
        return "이동"
    if "mod" in normalized:
        return "~ 수정"
    if "mix" in normalized:
        return "혼합"
    return "변경"


def _severity_from_count(raw_count: int) -> str:
    if raw_count >= 100:
        return "high"
    if raw_count >= 20:
        return "medium"
    return "low"


def _safe_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _safe_name(value: Any) -> str:
    text = str(value or "item").strip()
    text = re.sub(r"[^0-9A-Za-z가-힣_.-]+", "_", text)
    return text[:120] or "item"


def _render_decision(
    *,
    pair_id: str,
    policy: str,
    selected_keys: set[Tuple[str, str]],
    render_slots_used: int,
    max_viewer_pages: int,
) -> str:
    policy = (policy or "lazy").lower()
    should_render = False
    if policy == "all":
        should_render = True
    elif policy == "top-issues":
        should_render = any(key_pair == pair_id for key_pair, _ in selected_keys)
    elif policy == "lazy":
        should_render = False
    else:
        logger.warning("Unsupported viewer_render_policy=%s; using lazy", policy)
        should_render = False
    if should_render and render_slots_used >= max_viewer_pages:
        return "skipped_by_page_cap"
    return "render" if should_render else "lazy"


def _reuse_preview_background(preview: Dict[str, Any]) -> Dict[str, Any]:
    before_image = str(preview.get("before_image") or "")
    after_image = str(preview.get("after_image") or "")
    before_transform = preview.get("before_transform") if isinstance(preview.get("before_transform"), dict) else None
    after_transform = preview.get("after_transform") if isinstance(preview.get("after_transform"), dict) else None
    if before_image and not Path(before_image).exists():
        before_image = ""
    if after_image and not Path(after_image).exists():
        after_image = ""
    return {
        "before_image": before_image,
        "after_image": after_image,
        "before_transform": before_transform,
        "after_transform": after_transform,
        "render_status": "preview_reused" if after_image and after_transform else "lazy_not_rendered",
        "warnings": [],
    }


def _render_pair_backgrounds(
    *,
    pair_id: str,
    source_a: Optional[Path],
    source_b: Optional[Path],
    image_dir: Path,
    dxf_cache_dir: Path,
    dpi: int,
    max_edge_px: int,
    page_a: int = 0,
    page_b: int = 0,
) -> Dict[str, Any]:
    """Render the before/after backgrounds for one viewer pair.

    Phase H integration — ``page_a`` and ``page_b`` are the per-side
    PDF page indices the caller wants rendered (typically the first
    matched page pair from the page-level matcher). Defaults are 0/0
    so single-page PDFs and DXF/DWG keep working unchanged.
    """

    warnings: List[str] = []
    if not source_a or not source_b:
        return {
            "before_image": "",
            "after_image": "",
            "before_transform": None,
            "after_transform": None,
            "render_status": "render_failed",
            "warnings": ["원본 도면 경로를 찾을 수 없어 미리보기를 만들지 못했습니다."],
        }
    if source_a.suffix.lower() in PDF_EXTENSIONS and source_b.suffix.lower() in PDF_EXTENSIONS:
        try:
            safe = _safe_name(pair_id)
            before_image = image_dir / f"{safe}_before.png"
            after_image = image_dir / f"{safe}_after.png"
            before_transform = None
            after_transform = None
            before_image_text = ""
            after_image_text = ""
            if int(page_a) >= 0:
                before_transform = _render_pdf_to_png(
                    source_a, before_image,
                    dpi=dpi, max_edge_px=max_edge_px, page_index=int(page_a),
                )
                before_image_text = str(before_image)
            else:
                warnings.append("before PDF side is unmatched for this page pair")
            if int(page_b) >= 0:
                after_transform = _render_pdf_to_png(
                    source_b, after_image,
                    dpi=dpi, max_edge_px=max_edge_px, page_index=int(page_b),
                )
                after_image_text = str(after_image)
            else:
                warnings.append("after PDF side is unmatched for this page pair")
            return {
                "before_image": before_image_text,
                "after_image": after_image_text,
                "before_transform": before_transform,
                "after_transform": after_transform,
                "render_status": "rendered" if (before_transform or after_transform) else "render_failed",
                "warnings": warnings,
            }
        except Exception as exc:
            logger.warning("Failed to render PDF viewer background for %s: %s", pair_id, exc)
            return {
                "before_image": "",
                "after_image": "",
                "before_transform": None,
                "after_transform": None,
                "render_status": "render_failed",
                "warnings": [f"PDF viewer render failed: {exc}"],
            }

    if source_a.suffix.lower() not in CAD_EXTENSIONS or source_b.suffix.lower() not in CAD_EXTENSIONS:
        return {
            "before_image": "",
            "after_image": "",
            "before_transform": None,
            "after_transform": None,
            "render_status": "render_failed",
            "warnings": ["CAD 형식이 아니어서 PNG 미리보기 렌더를 건너뜁니다."],
        }
    try:
        before_dxf = _ensure_preview_dxf(source_a, dxf_cache_dir)
        after_dxf = _ensure_preview_dxf(source_b, dxf_cache_dir)
        safe = _safe_name(pair_id)
        before_image = image_dir / f"{safe}_before.png"
        after_image = image_dir / f"{safe}_after.png"
        before_transform = _render_dxf_to_png(before_dxf, before_image, dpi=dpi, max_edge_px=max_edge_px)
        after_transform = _render_dxf_to_png(after_dxf, after_image, dpi=dpi, max_edge_px=max_edge_px)
        return {
            "before_image": str(before_image),
            "after_image": str(after_image),
            "before_transform": before_transform,
            "after_transform": after_transform,
            "render_status": "rendered",
            "warnings": warnings,
        }
    except Exception as exc:
        logger.warning("Failed to render viewer background for %s: %s", pair_id, exc)
        return {
            "before_image": "",
            "after_image": "",
            "before_transform": None,
            "after_transform": None,
            "render_status": "render_failed",
            "warnings": [f"미리보기 렌더 실패: {exc}"],
        }


def _pdf_page_size_from_transforms(
    after_transform: Optional[Dict[str, Any]],
    before_transform: Optional[Dict[str, Any]],
) -> Optional[Dict[str, float]]:
    """Derive ``{"width": px, "height": px}`` from a PDF render transform.

    PDF backgrounds are rendered with origin (0, 0); ``max_x``/``max_y`` are the
    page pixel dimensions. ``img_width``/``img_height`` are also written by the
    renderer and preferred when present. Returns ``None`` when neither side has
    been rendered yet so downstream consumers can fall back to relative layout.
    """

    for transform in (after_transform, before_transform):
        if not isinstance(transform, dict):
            continue
        width = transform.get("img_width") or transform.get("max_x")
        height = transform.get("img_height") or transform.get("max_y")
        try:
            width_f = float(width) if width is not None else 0.0
            height_f = float(height) if height is not None else 0.0
        except (TypeError, ValueError):
            continue
        if width_f > 0 and height_f > 0:
            return {"width": width_f, "height": height_f}
    return None


def _render_pdf_to_png(
    pdf_path: Path,
    output_path: Path,
    *,
    dpi: int,
    max_edge_px: int,
    page_index: int = 0,
) -> Dict[str, Any]:
    """Render one PDF page to PNG.

    Phase H integration: ``page_index`` lets the caller render the
    matched page (A.page_a, B.page_b) instead of always page 0. The
    legacy single-page path keeps working because the default is 0.
    """

    import fitz  # type: ignore

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    try:
        if len(doc) == 0:
            raise ValueError(f"PDF has no pages: {pdf_path}")
        # Clamp out-of-range page indices to 0 with a warning rather
        # than raising — this keeps the comparison run from blowing up
        # if Phase H emits an inconsistent page index.
        safe_index = int(page_index)
        if safe_index < 0 or safe_index >= len(doc):
            logger.warning(
                "PDF render: page_index %d out of range [0, %d) for %s; "
                "falling back to page 0",
                safe_index, len(doc), pdf_path,
            )
            safe_index = 0
        page = doc[safe_index]
        requested_scale = max(float(dpi), 1.0) / 72.0
        max_page_edge = max(float(page.rect.width), float(page.rect.height), 1.0)
        edge_scale = float(max_edge_px) / max_page_edge if max_edge_px and max_edge_px > 0 else requested_scale
        scale = min(requested_scale, edge_scale)
        effective_dpi = scale * 72.0
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        pixmap.save(str(output_path))
        return {
            "min_x": 0.0,
            "min_y": 0.0,
            "max_x": float(pixmap.width),
            "max_y": float(pixmap.height),
            "img_width": int(pixmap.width),
            "img_height": int(pixmap.height),
            "scale_x": 1.0,
            "scale_y": 1.0,
            "coordinate_space": "image_pixels",
            "page": safe_index,
            "dpi": effective_dpi,
            "pdf_dpi": effective_dpi,
            "effective_dpi": effective_dpi,
            "requested_dpi": float(dpi),
            "render_scale": scale,
        }
    finally:
        doc.close()


def _render_pair_backgrounds_with_timeout(
    *,
    pair_id: str,
    source_a: Optional[Path],
    source_b: Optional[Path],
    image_dir: Path,
    dxf_cache_dir: Path,
    dpi: int,
    max_edge_px: int,
    timeout_seconds: int = 0,
    page_a: int = 0,
    page_b: int = 0,
) -> Dict[str, Any]:
    """Render backgrounds directly or in a killable subprocess when timeboxed.

    Phase H integration — ``page_a``/``page_b`` carry the matched-page
    indices to render. The subprocess fallback receives them via CLI
    flags so the rendered PNGs reflect the correct pages.
    """
    if timeout_seconds <= 0 or not source_a or not source_b:
        return _render_pair_backgrounds(
            pair_id=pair_id,
            source_a=source_a,
            source_b=source_b,
            image_dir=image_dir,
            dxf_cache_dir=dxf_cache_dir,
            dpi=dpi,
            max_edge_px=max_edge_px,
            page_a=page_a,
            page_b=page_b,
        )

    image_dir.mkdir(parents=True, exist_ok=True)
    result_json = image_dir / f"{_safe_name(pair_id)}.render_result.json"
    program, worker_args = worker_command_for_module(VIEWER_RENDER_WORKER_MODULE)
    command = [
        program,
        *worker_args,
        "--pair-id",
        pair_id,
        "--source-a",
        str(source_a),
        "--source-b",
        str(source_b),
        "--image-dir",
        str(image_dir),
        "--dxf-cache-dir",
        str(dxf_cache_dir),
        "--dpi",
        str(int(dpi)),
        "--max-edge-px",
        str(int(max_edge_px)),
        "--result-json",
        str(result_json),
        # Phase H — pass per-side page indices to the subprocess. The
        # worker reads these and forwards to _render_pdf_to_png.
        "--page-a",
        str(int(page_a)),
        "--page-b",
        str(int(page_b)),
    ]
    try:
        worker_cwd = worker_working_directory(project_root=Path(__file__).resolve().parents[3])
        completed = subprocess.run(
            command,
            cwd=str(worker_cwd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Viewer render timed out for %s after %ss", pair_id, timeout_seconds)
        return {
            "before_image": "",
            "after_image": "",
            "before_transform": None,
            "after_transform": None,
            "render_status": "render_timeout",
            "warnings": [f"viewer render timed out after {timeout_seconds}s; kept overlay-only lazy view"],
        }
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        logger.warning("Viewer render subprocess failed for %s: %s", pair_id, detail)
        return {
            "before_image": "",
            "after_image": "",
            "before_transform": None,
            "after_transform": None,
            "render_status": "render_failed",
            "warnings": [f"viewer render subprocess failed: {detail[:500]}"],
        }
    payload = _read_json(result_json) if result_json.exists() else {}
    if not payload:
        return {
            "before_image": "",
            "after_image": "",
            "before_transform": None,
            "after_transform": None,
            "render_status": "render_failed",
            "warnings": ["viewer render subprocess did not write a result"],
        }
    return payload


def _write_zone_tiles(
    *,
    pair_id: str,
    overlays: List[Dict[str, Any]],
    after_image: str,
    tile_dir: Path,
    max_tiles: int,
    max_edge: int = 1600,
) -> Tuple[int, List[str]]:
    if max_tiles <= 0:
        return 0, []
    try:
        from PIL import Image
    except Exception as exc:
        return 0, [f"타일 생성에 필요한 Pillow를 사용할 수 없습니다: {exc}"]

    warnings: List[str] = []
    count = 0
    pair_dir = tile_dir / _safe_name(pair_id)
    pair_dir.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(after_image) as image:
            width, height = image.size
            for overlay in overlays:
                if count >= max_tiles:
                    break
                bbox = overlay.get("after_bbox_px")
                if not isinstance(bbox, dict):
                    continue
                crop = _expanded_crop_box(bbox, width, height)
                if not crop:
                    continue
                tile_path = pair_dir / f"{_safe_name(overlay.get('zone_id'))}.png"
                tile = image.crop(crop)
                tile.thumbnail((max_edge, max_edge))
                tile.save(tile_path)
                overlay["tile_image"] = str(tile_path)
                overlay["tile_crop_px"] = {
                    "x": crop[0],
                    "y": crop[1],
                    "width": crop[2] - crop[0],
                    "height": crop[3] - crop[1],
                }
                count += 1
    except Exception as exc:
        warnings.append(f"타일 생성 실패: {exc}")
    return count, warnings


def _expanded_crop_box(bbox: Dict[str, Any], image_width: int, image_height: int) -> Optional[Tuple[int, int, int, int]]:
    try:
        x = float(bbox.get("x", 0))
        y = float(bbox.get("y", 0))
        w = max(1.0, float(bbox.get("width", 0)))
        h = max(1.0, float(bbox.get("height", 0)))
    except (TypeError, ValueError):
        return None
    margin = max(80.0, min(400.0, max(w, h) * 0.35))
    left = max(0, int(math.floor(x - margin)))
    top = max(0, int(math.floor(y - margin)))
    right = min(image_width, int(math.ceil(x + w + margin)))
    bottom = min(image_height, int(math.ceil(y + h + margin)))
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def _marked_pdf_overlays(
    overlays: List[Dict[str, Any]],
    mode: str,
    selected_keys: set[Tuple[str, str]],
) -> List[Dict[str, Any]]:
    normalized = (mode or "selected").lower()
    if normalized == "off":
        return []
    if normalized == "all":
        return list(overlays)
    if normalized == "selected":
        result: List[Dict[str, Any]] = []
        for overlay in overlays:
            pair_id = str(overlay.get("pair_id") or "")
            zone_id = str(overlay.get("zone_id") or "")
            if (pair_id, zone_id) in selected_keys or overlay.get("selected_for_review"):
                result.append(overlay)
        return result
    # CSV mode is resolved upstream by selected flags.  Keep the same behavior.
    return [
        overlay
        for overlay in overlays
        if overlay.get("selected_for_review")
    ]


def _export_marked_pdf_for_pair(
    *,
    pair_id: str,
    source_b: Optional[Path],
    after_image: str,
    overlays: List[Dict[str, Any]],
    marked_pdf_dir: Path,
) -> Tuple[str, str, str]:
    if not overlays:
        return "", "skipped_no_selected_zones", "선택된 PDF 구름마크 대상이 없습니다."
    if after_image:
        try:
            output = marked_pdf_dir / f"{_safe_name(pair_id)}_marked.pdf"
            _write_raster_marked_pdf(after_image, overlays, output)
            return str(output), "created_raster_review_pdf", ""
        except Exception as exc:
            logger.warning("Failed to create raster marked PDF for %s: %s", pair_id, exc)
            return "", "failed", f"raster marked PDF 생성 실패: {exc}"
    if source_b and source_b.suffix.lower() in PDF_EXTENSIONS:
        return "", "skipped_missing_transform", "PDF 원본 좌표 transform이 없어 부정확한 구름마크 PDF를 만들지 않았습니다."
    return "", "skipped_missing_transform", "렌더 transform이 없어 PDF 구름마크를 만들지 않았습니다."


def _write_raster_marked_pdf(after_image: str, overlays: List[Dict[str, Any]], output: Path) -> None:
    try:
        import fitz  # PyMuPDF
    except Exception as exc:  # pragma: no cover - depends on optional package
        raise RuntimeError(f"PyMuPDF unavailable: {exc}") from exc
    try:
        from PIL import Image
    except Exception as exc:  # pragma: no cover - depends on optional package
        raise RuntimeError(f"Pillow unavailable: {exc}") from exc

    with Image.open(after_image) as image:
        width, height = image.size
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    try:
        page = doc.new_page(width=width, height=height)
        page.insert_image(page.rect, filename=str(after_image))
        for overlay in overlays:
            bbox = overlay.get("after_bbox_px")
            if not isinstance(bbox, dict):
                continue
            rect = _fitz_rect_from_bbox(fitz, bbox, width, height)
            color = _pdf_color(overlay.get("change_type"))
            page.draw_rect(rect, color=color, width=2.0, dashes="[5 3]" if "delete" in str(overlay.get("change_type", "")).lower() else None)
            label = str(overlay.get("label") or overlay.get("zone_id") or "")
            if label:
                page.insert_text(
                    fitz.Point(rect.x0, max(12, rect.y0 - 4)),
                    label,
                    fontsize=9,
                    color=color,
                )
        doc.save(str(output))
    finally:
        doc.close()


def _fitz_rect_from_bbox(fitz_module: Any, bbox: Dict[str, Any], width: int, height: int) -> Any:
    x = _bounded_float(bbox.get("x"), 0, width)
    y = _bounded_float(bbox.get("y"), 0, height)
    w = max(1.0, float(bbox.get("width", 1) or 1))
    h = max(1.0, float(bbox.get("height", 1) or 1))
    x2 = min(width, x + w)
    y2 = min(height, y + h)
    return fitz_module.Rect(x, y, x2, y2)


def _bounded_float(value: Any, lower: float, upper: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = lower
    return max(lower, min(upper, number))


def _pdf_color(change_type: Any) -> Tuple[float, float, float]:
    text = str(change_type or "").lower()
    if "add" in text:
        return (0.0, 0.45, 0.15)
    if "delete" in text or "remove" in text:
        return (0.75, 0.05, 0.05)
    if "mod" in text:
        return (0.0, 0.25, 0.75)
    return (0.45, 0.1, 0.65)


def _copy_reference_pdf(
    pair_id: str,
    source_pdf: Optional[Path],
    page_dir: Path,
    *,
    side: str = "",
) -> Optional[Path]:
    if not source_pdf or source_pdf.suffix.lower() not in PDF_EXTENSIONS or not source_pdf.exists():
        return None
    side_suffix = f"_{_safe_name(side)}" if side else ""
    target = page_dir / f"{_safe_name(pair_id)}{side_suffix}.pdf"
    try:
        if source_pdf.resolve() != target.resolve():
            target.write_bytes(source_pdf.read_bytes())
        return target
    except Exception as exc:
        logger.warning("Failed to copy reference PDF for viewer package: %s", exc)
        return None


def _write_index_html(path: Path, manifest: Dict[str, Any]) -> None:
    rows = []
    for pair in manifest.get("pairs", []):
        pair_tile_count = _display_tile_count(pair) if isinstance(pair, dict) else 0
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(pair.get('drawing_number') or pair.get('pair_id')))}</td>"
            f"<td>{html.escape(str(pair.get('overlay_count', 0)))}</td>"
            f"<td>{html.escape(str(pair.get('render_status', '')))}</td>"
            f"<td>{html.escape(str(pair_tile_count))}</td>"
            f"<td>{_link(pair.get('overlay_json'), 'overlay')}</td>"
            f"<td>{_link(pair.get('after_image'), 'PNG')}</td>"
            f"<td>{_link(pair.get('marked_pdf'), 'PDF')}</td>"
            "</tr>"
        )
    display_tile_count = sum(
        _display_tile_count(pair)
        for pair in manifest.get("pairs", [])
        if isinstance(pair, dict)
    )
    manifest = dict(manifest)
    manifest["tile_count"] = display_tile_count
    html_text = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>경량 도면 뷰어 패키지</title>
  <style>
    body {{ font-family: "Segoe UI", "Malgun Gothic", sans-serif; background:#f7f8fa; color:#111827; margin:24px; }}
    h1 {{ font-size:24px; margin-bottom:8px; }}
    .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin:16px 0; }}
    .card {{ background:#fff; border:1px solid #9ca3af; border-radius:8px; padding:12px; }}
    .label {{ color:#374151; font-size:12px; }}
    .value {{ font-weight:700; font-size:22px; }}
    table {{ width:100%; border-collapse:collapse; background:#fff; }}
    th,td {{ border:1px solid #9ca3af; padding:8px; text-align:left; }}
    th {{ background:#e5e7eb; }}
    a {{ color:#005fcc; font-weight:600; }}
  </style>
</head>
<body>
  <h1>경량 도면 뷰어 패키지</h1>
  <p>좌표 기준: CAD world 좌표. PNG 배경이 있는 도면은 transform 기반 픽셀 좌표로 overlay를 표시합니다.</p>
  <div class="cards">
    <div class="card"><div class="label">도면 쌍</div><div class="value">{manifest.get('pair_count', 0)}</div></div>
    <div class="card"><div class="label">Overlay</div><div class="value">{manifest.get('overlay_count', 0)}</div></div>
    <div class="card"><div class="label">PNG 렌더 도면</div><div class="value">{manifest.get('rendered_pair_count', 0)}</div></div>
    <div class="card"><div class="label">타일</div><div class="value">{manifest.get('tile_count', 0)}</div></div>
    <div class="card"><div class="label">Marked PDF</div><div class="value">{manifest.get('marked_pdf_count', 0)}</div></div>
  </div>
  <table>
    <thead><tr><th>도면</th><th>Overlay</th><th>렌더 상태</th><th>타일</th><th>Overlay JSON</th><th>PNG</th><th>Marked PDF</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</body>
</html>
"""
    path.write_text(html_text, encoding="utf-8")


def _display_tile_count(pair: Dict[str, Any]) -> int:
    try:
        return int(pair.get("tile_count") or 0) + int(pair.get("lod_tile_count") or 0)
    except (TypeError, ValueError):
        return 0


def _link(path_value: Any, label: str) -> str:
    if not path_value:
        return ""
    path = str(path_value)
    return f'<a href="{html.escape(path)}">{html.escape(label)}</a>'


def _update_artifact_manifest(artifact_dir: Path, viewer_manifest_path: Path, index_html: Path, manifest: Dict[str, Any]) -> None:
    manifest_path = artifact_dir / "artifact_manifest.json"
    data = _read_json(manifest_path) if manifest_path.exists() else {}
    output_paths = data.get("output_paths")
    if not isinstance(output_paths, dict):
        output_paths = {}
    output_paths.update(
        {
            "viewer_manifest_json": str(viewer_manifest_path),
            "viewer_index_html": str(index_html),
            "viewer_tiles_manifest_json": str(Path(viewer_manifest_path).parent / "tiles_manifest.json"),
            "viewer_perf_json": str(Path(viewer_manifest_path).parent / "viewer_perf.json"),
        }
    )
    data["output_paths"] = output_paths
    data["viewer_package"] = {
        "schema_version": VIEWER_PACKAGE_SCHEMA_VERSION,
        "overlay_count": manifest.get("overlay_count", 0),
        "pair_count": manifest.get("pair_count", 0),
        "page_count": manifest.get("page_count", 0),
        "tile_count": manifest.get("tile_count", 0),
        "tiles_manifest": manifest.get("tiles_manifest", ""),
        "viewer_perf_json": manifest.get("viewer_perf_json", ""),
        "viewer_engine": manifest.get("viewer_engine", "auto"),
        "max_visible_overlays": manifest.get("max_visible_overlays", 500),
        "marked_pdf_count": manifest.get("marked_pdf_count", 0),
        "marked_pdf_skipped_count": manifest.get("marked_pdf_skipped_count", 0),
        "rendered_pair_count": manifest.get("rendered_pair_count", 0),
        "lazy_pair_count": manifest.get("lazy_pair_count", 0),
        "transform_complete": manifest.get("transform_complete", False),
    }
    data.update(
        {
            "viewer_schema_version": VIEWER_PACKAGE_SCHEMA_VERSION,
            "viewer_manifest_json": str(viewer_manifest_path),
            "viewer_index_html": str(index_html),
            "viewer_tiles_manifest_json": str(Path(viewer_manifest_path).parent / "tiles_manifest.json"),
            "viewer_perf_json": str(Path(viewer_manifest_path).parent / "viewer_perf.json"),
            "viewer_overlay_count": manifest.get("overlay_count", 0),
            "viewer_pair_count": manifest.get("pair_count", 0),
            "viewer_page_count": manifest.get("page_count", 0),
            "viewer_tile_count": manifest.get("tile_count", 0),
            "rendered_pair_count": manifest.get("rendered_pair_count", 0),
            "lazy_pair_count": manifest.get("lazy_pair_count", 0),
            "tile_count": manifest.get("tile_count", 0),
            "viewer_engine": manifest.get("viewer_engine", "auto"),
            "max_visible_overlays": manifest.get("max_visible_overlays", 500),
            "marked_pdf_count": manifest.get("marked_pdf_count", 0),
            "marked_pdf_skipped_count": manifest.get("marked_pdf_skipped_count", 0),
            "transform_complete": manifest.get("transform_complete", False),
            "viewer_warnings": manifest.get("warnings", []),
        }
    )
    _write_json(manifest_path, data)


# ---------------------------------------------------------------------------
# Phase F P0 — v1 → v2 manifest mapping
# ---------------------------------------------------------------------------


# Mapping from v1 ``visual_fidelity`` to v2 ``background_fidelity``. The v2
# enum is intentionally more conservative — when in doubt we downgrade to
# ``relative_only`` so the GUI shows the orange watermark and disables
# measurement tools rather than overstating accuracy.
_V1_TO_V2_FIDELITY: Dict[str, str] = {
    "cad_render": "exact_world_render",
    "pdf_render": "exact_world_tile_sparse",
    "relative_overlay": "relative_only",
}

# Mapping from v1 ``render_status`` (per-pair) to v2 ``render_job_status``.
_V1_RENDER_STATUS_TO_V2_JOB: Dict[str, str] = {
    "rendered": "idle",
    "preview_reused": "idle",
    "skipped_by_page_cap": "idle",
    "lazy_not_rendered": "idle",
    "render_failed": "failed",
    "render_timeout": "timed_out",
    "rendering": "rendering",
    "queued": "queued",
}


def _v2_fidelity_for_pair(pair: Dict[str, Any]) -> str:
    """Pick the v2 ``background_fidelity`` enum for a v1 pair entry.

    Conservatism rule: if ``after_transform`` is missing, force
    ``relative_only`` regardless of what ``visual_fidelity`` says — without a
    transform we cannot honestly claim the background is exact.
    """

    if not pair.get("after_transform"):
        return "relative_only"
    visual = str(pair.get("visual_fidelity") or "").strip()
    return _V1_TO_V2_FIDELITY.get(visual, "relative_only")


def _v2_job_status_for_pair(pair: Dict[str, Any]) -> str:
    """Pick the v2 ``render_job_status`` for a v1 pair entry."""

    raw = str(pair.get("render_status") or "").strip()
    return _V1_RENDER_STATUS_TO_V2_JOB.get(raw, "idle")


def _v2_world_bbox_from_transform(transform: Optional[Dict[str, Any]]) -> Tuple[float, float, float, float]:
    """Best-effort extraction of a world bbox from a v1 transform dict.

    The v1 transform schema varies. Supported shapes:

    1. ``{"world_bbox": [x0, y0, x1, y1], ...}`` — explicit list form.
    2. ``{"min_x", "min_y", "max_x", "max_y", ...}`` — flat dict form
       (this is what the DWG/DXF compare pipeline actually emits per
       ``viewer_package._compute_pdf_image_pixel_transform`` and the
       ``MatplotlibBackend.render_to_png`` writer; was missed before
       Phase G3.1).
    3. ``{"pdf_page_size": {"width", "height"}}`` — PDF-only path,
       represents page in pixel coords.

    Returns ``(0,0,0,0)`` when no bbox is recoverable so the caller can
    downgrade fidelity safely.

    Phase G3.1 fix — added shape #2 (min_x/min_y/max_x/max_y). Without
    this branch the v2 + v3 manifests recorded ``shared_world_bbox:
    [0,0,0,0]`` for every DWG/DXF run, which broke lightweight viewer
    fit-to-view computation. Confirmed with the user's S20-0002 run:
    transform actually carries ``{"min_x": -1218202.0, "min_y":
    -163278.6, "max_x": 448256.4, "max_y": 45952.0, ...}``.
    """

    if not isinstance(transform, dict):
        return (0.0, 0.0, 0.0, 0.0)
    # Shape 1 — explicit list/tuple
    bbox = transform.get("world_bbox") or transform.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        try:
            return (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        except (TypeError, ValueError):
            pass
    # Shape 2 — flat min_x/min_y/max_x/max_y dict (DWG/DXF compare path)
    if all(k in transform for k in ("min_x", "min_y", "max_x", "max_y")):
        try:
            x0 = float(transform["min_x"])
            y0 = float(transform["min_y"])
            x1 = float(transform["max_x"])
            y1 = float(transform["max_y"])
            if x1 > x0 and y1 > y0:
                return (x0, y0, x1, y1)
        except (TypeError, ValueError):
            pass
    # Shape 3 — PDF page_size pixel coords
    page_size = transform.get("pdf_page_size") or transform.get("page_size")
    if isinstance(page_size, dict):
        try:
            w = float(page_size.get("width") or 0.0)
            h = float(page_size.get("height") or 0.0)
            if w > 0 and h > 0:
                return (0.0, 0.0, w, h)
        except (TypeError, ValueError):
            pass
    return (0.0, 0.0, 0.0, 0.0)


def _v2_pixel_size_from_transform(transform: Optional[Dict[str, Any]]) -> Tuple[int, int]:
    if not isinstance(transform, dict):
        return (0, 0)
    px = transform.get("pixel_size") or transform.get("image_size")
    if isinstance(px, (list, tuple)) and len(px) >= 2:
        try:
            return (int(px[0]), int(px[1]))
        except (TypeError, ValueError):
            pass
    return (0, 0)


def _v2_artifact_ref(
    image_path: str,
    transform: Optional[Dict[str, Any]],
    *,
    fidelity: str,
    renderer_id: str,
) -> Optional[V2ArtifactRef]:
    """Build a v2 ArtifactRef from the v1 background image + transform.

    Returns ``None`` when the image path is empty (lazy-not-rendered pair).
    """

    if not image_path:
        return None
    bbox = _v2_world_bbox_from_transform(transform)
    pixel_size = _v2_pixel_size_from_transform(transform)
    if pixel_size == (0, 0):
        # Without a pixel size we can't even fall back — refuse the artifact.
        return None
    quality = "exact" if fidelity in {"exact_world_render", "exact_world_tile_sparse"} else "relative_only"
    if bbox == (0.0, 0.0, 0.0, 0.0):
        quality = "relative_only"
    return V2ArtifactRef(
        image_uri=image_path,
        world_bbox=bbox,
        pixel_size=pixel_size,
        world_to_pixel=V2_IDENTITY_AFFINE,
        pixel_to_world=V2_IDENTITY_AFFINE,
        transform_quality=quality,
        renderer_id=renderer_id,
        renderer_version="",
        notes="auto-translated from viewer_manifest.v1",
    )


def _build_v2_manifest_from_v1(
    *,
    v1_manifest: Dict[str, Any],
    options: ViewerPackageOptions,
) -> ViewerManifestV2:
    """Translate the existing v1 manifest into a Phase-F v2 manifest.

    This is an additive translation — every pair in v1 produces one
    :class:`PairEntry` in v2 with explicit ``background_fidelity`` and
    ``render_job_status``. The shared world bbox is computed as the union of
    available pair bboxes (or zeros when none is recoverable), which lets the
    GUI render a ``relative_only`` watermark for the whole package when no
    pair has an exact transform.
    """

    pairs_v1 = v1_manifest.get("pairs") or []
    is_pdf_only = bool(pairs_v1) and all(
        (p.get("coordinate_source") == "image_pixels") for p in pairs_v1
    )
    is_cad_only = bool(pairs_v1) and all(
        (p.get("coordinate_source") == "cad_world") for p in pairs_v1
    )
    if is_pdf_only:
        source_kind = "pdf"
    elif is_cad_only:
        source_kind = "normalized_dxf"
    else:
        source_kind = "mixed"

    pair_entries: List[V2PairEntry] = []
    bbox_union: Optional[Tuple[float, float, float, float]] = None
    for pair in pairs_v1:
        fidelity = _v2_fidelity_for_pair(pair)
        job_status = _v2_job_status_for_pair(pair)
        before_ref = _v2_artifact_ref(
            image_path=str(pair.get("before_image") or ""),
            transform=pair.get("before_transform"),
            fidelity=fidelity,
            renderer_id="viewer_package_v1",
        )
        after_ref = _v2_artifact_ref(
            image_path=str(pair.get("after_image") or ""),
            transform=pair.get("after_transform"),
            fidelity=fidelity,
            renderer_id="viewer_package_v1",
        )
        for ref in (before_ref, after_ref):
            if ref is None:
                continue
            if ref.world_bbox == (0.0, 0.0, 0.0, 0.0):
                continue
            if bbox_union is None:
                bbox_union = ref.world_bbox
            else:
                bbox_union = (
                    min(bbox_union[0], ref.world_bbox[0]),
                    min(bbox_union[1], ref.world_bbox[1]),
                    max(bbox_union[2], ref.world_bbox[2]),
                    max(bbox_union[3], ref.world_bbox[3]),
                )
        pair_entries.append(
            V2PairEntry(
                pair_id=str(pair.get("pair_id") or pair.get("pair_uuid") or ""),
                background_fidelity=fidelity,  # type: ignore[arg-type]
                render_job_status=job_status,  # type: ignore[arg-type]
                before=before_ref,
                after=after_ref,
                notes=str(pair.get("render_warning") or ""),
            )
        )

    shared_bbox = bbox_union if bbox_union is not None else (0.0, 0.0, 0.0, 0.0)

    capabilities = {
        "viewer_engine": v1_manifest.get("viewer_engine", "auto"),
        "viewer_render_policy": v1_manifest.get("viewer_render_policy", "lazy"),
        "tile_size": v1_manifest.get("tile_size", options.tile_size),
        "max_visible_overlays": v1_manifest.get(
            "max_visible_overlays", options.max_visible_overlays
        ),
        "viewer_memory_budget_mb": v1_manifest.get(
            "viewer_memory_budget_mb", options.viewer_memory_budget_mb
        ),
    }

    overlay_space = "world" if (
        is_cad_only and any(p.get("after_transform") for p in pairs_v1)
    ) else "relative_only"

    return ViewerManifestV2(
        pair_uuid=str(v1_manifest.get("pair_uuid") or "viewer-package"),
        package_version=str(v1_manifest.get("schema_version") or "v1"),
        source_kind=source_kind,  # type: ignore[arg-type]
        renderer_capabilities=capabilities,
        before_world_bbox=shared_bbox,
        after_world_bbox=shared_bbox,
        shared_world_bbox=shared_bbox,
        overlay_space=overlay_space,  # type: ignore[arg-type]
        pairs=pair_entries,
    )


# ---------------------------------------------------------------------------
# Phase G — v1 → v3 manifest mapping (scene-pack-driven)
# ---------------------------------------------------------------------------


def _v3_source_kind(pairs_v1: list) -> str:
    """Same dispatch rule as v2 — kept duplicated so the helpers are
    independently maintainable."""

    if not pairs_v1:
        return "normalized_dxf"
    if all((p.get("coordinate_source") == "image_pixels") for p in pairs_v1):
        return "pdf"
    if all((p.get("coordinate_source") == "cad_world") for p in pairs_v1):
        return "normalized_dxf"
    return "mixed"


def _v3_initial_render_mode(pairs_v1: list) -> str:
    """Phase G1 ships *manifest plumbing only* — no scene pack is built
    yet by viewer_package itself (that's G2's viewer_session). So every
    pair starts at ``relative_only`` until the GUI loads the pack on
    demand. The GUI re-saves the manifest as it transitions states.
    """

    return "relative_only"


def _build_v3_manifest_from_v1(
    *,
    v1_manifest: Dict[str, Any],
    options: ViewerPackageOptions,
    viewer_root: Path,
) -> ViewerManifestV3:
    """Translate the v1 manifest into a Phase-G v3 manifest skeleton.

    G1 deliverable scope: the v3 manifest carries
      - source_kind / signatures
      - shared world bbox (computed from v1 transforms)
      - empty ScenePackRef placeholders (built on demand by viewer_session)
      - empty zone_requests / evidence (populated by viewer_session)
      - current_render_mode = ``relative_only`` (escalated by GUI)

    G2 will extend this to actually build scene packs at compare time
    (hot path) or on first GUI open (cold path).
    """

    pairs_v1 = v1_manifest.get("pairs") or []
    source_kind = _v3_source_kind(pairs_v1)

    # Compute shared bbox from any pair that has a usable transform.
    shared: Optional[Tuple[float, float, float, float]] = None
    for pair in pairs_v1:
        for tk in ("after_transform", "before_transform"):
            t = pair.get(tk)
            bbox = _v2_world_bbox_from_transform(t)
            if bbox != (0.0, 0.0, 0.0, 0.0):
                if shared is None:
                    shared = bbox
                else:
                    shared = (
                        min(shared[0], bbox[0]),
                        min(shared[1], bbox[1]),
                        max(shared[2], bbox[2]),
                        max(shared[3], bbox[3]),
                    )
    shared_bbox = shared if shared is not None else (0.0, 0.0, 0.0, 0.0)

    # Source signatures — best effort. file_hash etc. left empty in G1; the
    # viewer_session computes + persists them on first open. Source paths
    # come from the first available pair.
    before_path = ""
    after_path = ""
    for pair in pairs_v1:
        if not before_path:
            before_path = str(pair.get("source_a") or "")
        if not after_path:
            after_path = str(pair.get("source_b") or "")
        if before_path and after_path:
            break

    backend_sig = (
        f"ezdxf|qt-pyside6|viewer_package_v{v1_manifest.get('schema_version', 'unknown')}"
    )
    before_sig = V3SourceSignature(
        source_path=before_path,
        backend_sig=backend_sig,
    )
    after_sig = V3SourceSignature(
        source_path=after_path,
        backend_sig=backend_sig,
    )

    capabilities = {
        "viewer_engine": v1_manifest.get("viewer_engine", "auto"),
        "viewer_render_policy": v1_manifest.get("viewer_render_policy", "lazy"),
        "tile_size": v1_manifest.get("tile_size", options.tile_size),
        "scene_pack_root": str(viewer_root / SCENE_PACKS_SUBDIR),
        "scene_pack_built": False,  # G1: lazy build by viewer_session
    }

    overlay_space = "world" if (
        source_kind == "normalized_dxf"
        and any(p.get("after_transform") for p in pairs_v1)
    ) else "relative_only"

    return ViewerManifestV3(
        pair_uuid=str(v1_manifest.get("pair_uuid") or "viewer-package"),
        package_version=str(v1_manifest.get("schema_version") or "v1"),
        source_kind=source_kind,  # type: ignore[arg-type]
        before_source_signature=before_sig,
        after_source_signature=after_sig,
        renderer_capabilities=capabilities,
        before_world_bbox=shared_bbox,
        after_world_bbox=shared_bbox,
        shared_world_bbox=shared_bbox,
        overlay_space=overlay_space,  # type: ignore[arg-type]
        before_scene_pack=None,  # built on demand by viewer_session
        after_scene_pack=None,
        zone_requests=[],
        evidence=[],
        current_render_mode=_v3_initial_render_mode(pairs_v1),  # type: ignore[arg-type]
    )
