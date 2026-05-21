# -*- coding: utf-8 -*-
"""Single-action folder comparison pipeline for the Korean Workbench UX."""

from __future__ import annotations

import json
import logging
import os
import gc
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Union

logger = logging.getLogger(__name__)

from .change_zones import (
    CloudMarkOptions,
    ExecutiveReviewPackage,
    export_change_artifacts,
    export_executive_review_from_artifacts,
)
from .comparison_config import ComparisonConfig
from .change_zones import ChangeZoneOptions
from .noise_filter_io import (
    NoiseFilterSettings,
    load_noise_filter_settings,
)
from .drawing_batch import (
    BatchCompareJob,
    BatchCompareOptions,
    BatchCompareSummary,
    DescriptorBuildOptions,
    DrawingFileDescriptor,
    MatchCandidate,
    MatchStatus,
    MatchingOptions,
    are_compatible,
    match_drawing_sets,
    scan_drawing_inputs,
)
from .confirmed_cloud_export import export_selected_cloud_marks
from .export_profiles import (
    apply_export_profile_to_file,
    apply_export_profile_to_json,
    audit_sharable_paths,
    normalize_export_profile,
)
from .preflight import PreflightResult, run_preflight
from .review_project import (
    PreviewPackage,
    export_preview_artifacts,
    save_review_state,
    update_artifact_manifest,
    write_review_project,
)
from .run_contract import RunManifestWriter
from .viewer_package import ViewerPackage, export_viewer_package
from .viewer_package_proxy import (
    SubprocessRunReport,
    export_viewer_package_isolated,
)

ProgressCallback = Callable[[str, float, str], None]
# Callback receives ``(stage, percent, message)``. ``percent`` is typically an
# int (whole percent) but Plan C-1 added a fractional 97.5 emit for the
# MemoryBudgetExceeded auto-retry path so listeners that record raw values
# can distinguish the retry tick from the regular post-process emit.
CancelCallback = Callable[[], bool]
AUTO_STRUCTURAL_CLOUD_CATEGORIES = {"member", "dimension", "rebar", "grid", "mixed"}


@dataclass
class FolderCompareRunRequest:
    """User-facing request for comparing two drawing folders."""

    source_a: Union[str, Path]
    source_b: Union[str, Path]
    output_dir: Union[str, Path]
    recursive: bool = False
    use_ocr: bool = False
    enable_descriptor_cache: bool = True
    dxf_cache_dir: Optional[Union[str, Path]] = None
    compare_state_dir: Optional[Union[str, Path]] = None
    max_workers: Optional[int] = None
    preview_dpi: int = 80
    preview_max_edge_px: int = 2400
    # Phase G2.5 critical regression fix — PDF compare DPI is now decoupled
    # from preview_dpi. Previously, the GUI's "구조도면 정밀 (DPI 400)"
    # quality preset bumped preview_dpi to 400, which the pipeline then
    # forwarded to ``BatchCompareOptions.pdf_dpi`` (line 261). The PDF
    # compare algorithm then ran at 400 DPI instead of its tested 200,
    # producing 2x larger raster, more anti-alias noise (false positive
    # changes), and — counter-intuitively — *missing* small text changes
    # because their pixel diff fell below threshold relative to the 4x
    # area increase.
    #
    # Decoupling PDF compare DPI fixes both regressions the user reported:
    #   1. PDF compare accuracy returns to its prior tested baseline
    #   2. Preview DPI can stay high for sharp viewport rendering
    #
    # Default 200 was the value that produced 90 %+ accuracy in the prior
    # tested baseline (5/2 23:00 runs).
    pdf_compare_dpi: int = 200
    # Phase H2 — PDF page-level auto-matching. When True (default), multi-
    # page PDF compares use ``page_matcher.match_pdf_pages`` to recover
    # reordered/inserted/deleted pages instead of comparing 0vs0, 1vs1.
    # Set False to revert to legacy sequential comparison.
    pdf_page_auto_match: bool = True
    pdf_page_match_auto_threshold: float = 0.85
    pdf_page_match_review_threshold: float = 0.60
    max_preview_pairs: Optional[int] = 0
    top_review_issues: int = 100
    top_issues_per_drawing: int = 20
    fold_repetitive_layers: bool = True
    viewer_mode: str = "image-tiles"
    viewer_render_policy: str = "lazy"
    # Per-pair render timeout (seconds). When >0 each background render runs
    # in a killable subprocess via viewer_render_worker; when 0 it falls
    # through to a direct synchronous call with NO timeout protection (which
    # caused the pipeline to hang at 88% on large industrial DXFs — see
    # docs/collab/REVIEWS.md RV-20260502-001 §3.1).
    #
    # Default raised from 60s to 180s in Phase A: the renderer now tries
    # PyMuPDF first and falls back to Matplotlib in a single call. The
    # subprocess timeout has to bound the *combined* primary+fallback budget,
    # so 180s is required to keep matplotlib's slower path from being
    # cut off prematurely while still preventing indefinite freezes.
    render_timeout_seconds: int = 180
    viewer_engine: str = "auto"
    viewer_cache_dir: Optional[Union[str, Path]] = None
    tile_size: int = 512
    max_visible_overlays: int = 500
    viewer_memory_budget_mb: int = 512
    render_selected_on_open: bool = False
    prefetch_neighbor_tiles: bool = True
    tile_prefetch_radius: int = 1
    overview_max_edge: int = 2200
    focus_tile_max_edge: int = 1600
    viewer_perf_log: bool = False
    max_viewer_pages: int = 30
    max_zone_tiles: int = 300
    export_marked_pdf: bool = False
    marked_pdf_mode: str = "off"
    export_profile: str = "sharable"
    # Workbench-first speed path. When enabled, the pipeline prioritizes the
    # first usable review screen over completion of all heavy share/export
    # artifacts: top-issue backgrounds are still rendered, but zone tiles,
    # marked PDFs, and full cloud-mark DXFs are deferred.
    fast_first_review: bool = False
    auto_export_structural_clouds: bool = False
    allow_long_path_warning: bool = False
    # Phase O — optional override for noise filter settings. When None,
    # the pipeline reads ``noise_filter_config.json`` from the
    # AppData path (``default_noise_filter_config_path``) and falls
    # back to ``NoiseFilterSettings.default()`` when the file is
    # missing/corrupt. Tests / programmatic callers can supply a
    # specific instance to bypass the disk read.
    noise_filter_settings: Optional[Any] = None
    # Phase O Commit 3 [RV-20260508-009] — DXF/DWG INSERT block-internal
    # text fingerprint 활성. True (default) 면 블록 라이브러리 텍스트
    # 변경 (예: dowel callout 블록 내부 ``DOWEL @100`` → ``@200``) 가
    # 비교 결과에 surface. False 시 Phase O Commit 1 이전 동작으로
    # 회귀. Workbench V2 의 "정밀 텍스트 감지" 체크박스 와 1:1 매핑.
    block_text_detection: bool = True


@dataclass
class FolderCompareRunResult:
    """Outputs and summaries from one single-action folder comparison."""

    request: FolderCompareRunRequest
    output_dir: str
    artifact_dir: str
    preview_dir: str
    review_project_path: str
    review_state_path: str
    dxf_cache_dir: str
    compare_state_dir: str
    descriptors_a: list[DrawingFileDescriptor]
    descriptors_b: list[DrawingFileDescriptor]
    candidates: list[MatchCandidate]
    compare_summary: BatchCompareSummary
    artifact_package: Any
    preview_package: PreviewPackage
    executive_package: ExecutiveReviewPackage
    viewer_package: ViewerPackage
    run_manifest_path: str
    success_sentinel_path: str
    failed_sentinel_path: str
    preflight_report_path: str
    preflight_result: PreflightResult
    started_at: str
    finished_at: str

    @property
    def confirmed_pairs(self) -> int:
        return sum(1 for candidate in self.candidates if candidate.is_confirmed)

    @property
    def review_required_pairs(self) -> int:
        return sum(1 for candidate in self.candidates if candidate.status == MatchStatus.REVIEW_REQUIRED)

    @property
    def unmatched_a(self) -> int:
        return sum(1 for candidate in self.candidates if candidate.status == MatchStatus.UNMATCHED_A)

    @property
    def unmatched_b(self) -> int:
        return sum(1 for candidate in self.candidates if candidate.status == MatchStatus.UNMATCHED_B)


class FolderComparePipeline:
    """Run scan, match, compare, and human-readable artifact export in one call."""

    def __init__(self, request: FolderCompareRunRequest):
        self.request = request

    def run(
        self,
        *,
        progress_callback: Optional[ProgressCallback] = None,
        is_cancelled: Optional[CancelCallback] = None,
        runtime_sampler: Optional[Any] = None,
        viewer_memory_cap_mb: Optional[float] = None,
    ) -> FolderCompareRunResult:
        started_at = datetime.now().isoformat()
        output_dir = Path(self.request.output_dir).resolve()
        artifact_dir = output_dir / "artifacts"
        preview_dir = output_dir / "preview"
        viewer_dir = output_dir / "viewer"
        review_state_path = output_dir / "review_state.json"
        review_project_path = output_dir / "review_project.json"
        dxf_cache_dir = (
            Path(self.request.dxf_cache_dir).resolve()
            if self.request.dxf_cache_dir
            else output_dir / "dxf_cache"
        )
        compare_state_dir = (
            Path(self.request.compare_state_dir).resolve()
            if self.request.compare_state_dir
            else output_dir / "compare_state"
        )
        export_profile = normalize_export_profile(self.request.export_profile)
        fast_first_review = bool(self.request.fast_first_review)
        run_manifest = RunManifestWriter(output_dir)
        active_stage = "prepare"

        # Phase O — resolve noise filter settings (request override → disk
        # file → safe defaults). All three branches return a populated
        # NoiseFilterSettings, so downstream code can apply uniformly.
        if isinstance(self.request.noise_filter_settings, NoiseFilterSettings):
            noise_filter = self.request.noise_filter_settings
        else:
            try:
                noise_filter = load_noise_filter_settings()
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Could not load noise filter settings — using defaults",
                    exc_info=True,
                )
                noise_filter = NoiseFilterSettings.default()
        zone_options = ChangeZoneOptions(
            min_changes_per_zone=noise_filter.min_changes_per_zone,
            single_entity_noise_score_threshold=(
                noise_filter.single_entity_noise_score_threshold
            ),
        )

        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            dxf_cache_dir.mkdir(parents=True, exist_ok=True)
            compare_state_dir.mkdir(parents=True, exist_ok=True)
            preflight = run_preflight(
                source_a=self.request.source_a,
                source_b=self.request.source_b,
                output_dir=output_dir,
                dxf_cache_dir=dxf_cache_dir,
                compare_state_dir=compare_state_dir,
                allow_long_path_warning=self.request.allow_long_path_warning,
            )
            preflight_path = output_dir / "preflight_report.json"
            _write_json_atomic(preflight_path, preflight.to_dict())
            run_manifest.start(
                inputs={
                    "source_a": self.request.source_a,
                    "source_b": self.request.source_b,
                    "recursive": self.request.recursive,
                    "fast_first_review": fast_first_review,
                },
                paths={
                    "output_dir": output_dir,
                    "artifact_dir": artifact_dir,
                    "preview_dir": preview_dir,
                    "viewer_dir": viewer_dir,
                    "dxf_cache_dir": dxf_cache_dir,
                    "compare_state_dir": compare_state_dir,
                    "preflight_report_json": preflight_path,
                },
                preflight=preflight.to_dict(),
            )
            run_manifest.stage("preflight", "completed", preflight_status=preflight.status)
            if preflight.errors:
                raise RuntimeError(
                    "Preflight failed: "
                    + "; ".join(check.message for check in preflight.errors[:3])
                )
            save_review_state(review_state_path, [])

            active_stage = "scan"
            run_manifest.stage(active_stage, "running")
            self._emit(progress_callback, "scan", 5, "도면 찾는 중")
            self._check_cancelled(is_cancelled)
            scan_options = DescriptorBuildOptions(
                recursive=self.request.recursive,
                use_ocr_fallback=self.request.use_ocr,
                enable_cache=self.request.enable_descriptor_cache,
                dxf_cache_dir=dxf_cache_dir,
            )
            descriptors_a = scan_drawing_inputs(self.request.source_a, options=scan_options)
            self._emit(progress_callback, "scan", 18, "변경 전 도면 확인 완료")
            self._check_cancelled(is_cancelled)
            descriptors_b = scan_drawing_inputs(self.request.source_b, options=scan_options)
            run_manifest.stage(active_stage, "completed", a_count=len(descriptors_a), b_count=len(descriptors_b))

            active_stage = "match"
            run_manifest.stage(active_stage, "running")
            self._emit(progress_callback, "match", 28, "도면 번호로 자동 매칭 중")
            self._check_cancelled(is_cancelled)
            candidates = _explicit_file_pair_candidates(
                self.request.source_a,
                self.request.source_b,
                descriptors_a,
                descriptors_b,
            )
            if candidates is None:
                candidates = match_drawing_sets(descriptors_a, descriptors_b, options=MatchingOptions())
            run_manifest.stage(
                active_stage,
                "completed",
                confirmed=sum(1 for candidate in candidates if candidate.is_confirmed),
                review_required=sum(1 for candidate in candidates if candidate.status == MatchStatus.REVIEW_REQUIRED),
            )

            active_stage = "compare"
            run_manifest.stage(active_stage, "running")
            self._emit(progress_callback, "compare", 35, "도면 비교 중")

            # Phase H4 — load any pre-existing manual page overrides
            # from the run output dir. The GUI dialog writes them to
            # ``output_dir/manual_page_overrides.json``; on re-run we
            # apply them via the lookup callback so the user's edits
            # survive across compare cycles.
            override_lookup = None
            overrides_path = output_dir / "manual_page_overrides.json"
            if overrides_path.exists():
                try:
                    from .manual_page_overrides import load_overrides as _load_overrides
                    overrides_by_pair = _load_overrides(overrides_path)
                    if overrides_by_pair:
                        def override_lookup(pair_uuid: str) -> Sequence[Any]:  # noqa: E306
                            return overrides_by_pair.get(pair_uuid, ())
                        logger.info(
                            "Loaded %d pair(s) of manual page overrides from %s",
                            len(overrides_by_pair), overrides_path,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Failed to load manual page overrides from %s: %s",
                        overrides_path, exc,
                    )

            # Phase O — apply user-saved noise filter settings to
            # SensitivityConfig (O2/O3). Default-constructed
            # ComparisonConfig is mutable so we can patch in place
            # without breaking other callers.
            comparison_config = ComparisonConfig.get_default()
            comparison_config.sensitivity.global_alignment_enabled = (
                noise_filter.global_alignment_enabled
            )
            comparison_config.sensitivity.hungarian_max_subset = (
                noise_filter.hungarian_max_subset
            )
            comparison_config.sensitivity.cosmetic_detection_enabled = (
                noise_filter.cosmetic_detection_enabled
            )
            comparison_config.sensitivity.suppress_cosmetic_only = (
                noise_filter.suppress_cosmetic_only
            )
            comparison_config.sensitivity.cosmetic_attributes = tuple(
                noise_filter.cosmetic_attributes
            )

            compare_options = BatchCompareOptions(
                comparison_config=comparison_config,
                max_workers=self.request.max_workers,
                dxf_cache_dir=dxf_cache_dir,
                compare_state_dir=compare_state_dir,
                write_compare_state_json=not fast_first_review,
                # Phase G2.5 fix — use the decoupled PDF compare DPI (200
                # default), NOT preview_dpi which the GUI may have bumped
                # to 400 for sharper viewport rendering. Preview rendering
                # and pixel-diff comparison have very different DPI sweet
                # spots; tying them caused the 90 %+ → degraded accuracy
                # regression the user reported.
                pdf_dpi=self.request.pdf_compare_dpi,
                # Phase H2 — multi-page PDF auto-matching defaults
                pdf_page_auto_match=self.request.pdf_page_auto_match,
                pdf_page_match_auto_threshold=self.request.pdf_page_match_auto_threshold,
                pdf_page_match_review_threshold=self.request.pdf_page_match_review_threshold,
                # Phase H4 — manual page override lookup (None when no JSON exists)
                manual_page_overrides_lookup=override_lookup,
                # Phase O5 — Codex review RV-20260507-003 fix: thread
                # the dialog's "PDF 시각 비교 강도" preset into the
                # batch options so each PDF page's DrawingDiffer picks
                # up the matching noise profile. Without this the O5
                # combo box was a dead-end UX.
                pdf_noise_filter_strength=noise_filter.noise_filter_strength,
                use_ocr_fallback=self.request.use_ocr,
                # Phase O Commit 3 [RV-20260508-009] — INSERT block-
                # internal text fingerprint propagation. GUI 의 정밀
                # 텍스트 감지 체크박스 가 OFF 면 ``self.request``의
                # 필드가 False 로 들어와 InsertNormalizer 가 fingerprint
                # 계산 skip → INSERT hash 가 legacy 동작.
                block_text_detection=self.request.block_text_detection,
            )

            def compare_progress(done: int, total: int, _message: str) -> None:
                percent = 72 if total <= 0 else 35 + int((done / total) * 40)
                self._emit(progress_callback, "compare", min(percent, 75), "도면 비교 중")

            compare_summary = BatchCompareJob(candidates, compare_options).run(
                progress_callback=compare_progress,
                is_cancelled=is_cancelled,
            )
            self._check_cancelled(is_cancelled)
            run_manifest.stage(
                active_stage,
                "completed",
                requested_pairs=compare_summary.requested_pairs,
                completed_pairs=compare_summary.completed_pairs,
                failed_pairs=compare_summary.failed_pairs,
                cancelled_pairs=compare_summary.cancelled_pairs,
            )

            active_stage = "artifact"
            run_manifest.stage(active_stage, "running")
            self._emit(progress_callback, "artifact", 78, "결과 만드는 중")
            artifact_package = export_change_artifacts(
                compare_summary,
                artifact_dir,
                dxf_cache_dir=dxf_cache_dir,
                compare_state_dir=compare_state_dir,
                cloud_options=CloudMarkOptions(export_mode="selected"),
                export_cloud_marks=not fast_first_review,
                # Phase O — Codex review RV-20260507-003 fix:
                # without this, change_zones.csv / artifact_manifest.json /
                # cloud_marked DXFs / dashboard JSON are still built with
                # default ChangeZoneOptions (min=1) so noise-suppressed
                # single-entity zones leak into the user-visible review
                # surface even after the dialog "saves" min_changes_per_zone>=2.
                zone_options=zone_options,
            )
            run_manifest.stage(
                active_stage,
                "completed",
                raw_change_count=artifact_package.raw_change_count,
                zone_count=artifact_package.zone_count,
                cloud_region_count=artifact_package.cloud_region_count,
                cloud_omitted_zone_count=artifact_package.cloud_omitted_zone_count,
            )

            active_stage = "preview"
            run_manifest.stage(active_stage, "running")
            # Granular emits 88→98 break the previously-silent block where
            # an unbounded background render at the viewer-package step would
            # leave the GUI stuck on "88%" indefinitely (see RV-20260502-001).
            # Even when the underlying step still takes minutes, the user now
            # sees the active sub-stage label change.
            self._emit(progress_callback, "preview", 88, "미리보기 만드는 중")
            preview_package = export_preview_artifacts(
                compare_summary,
                preview_dir,
                dxf_cache_dir=dxf_cache_dir,
                review_state_path=review_state_path,
                dpi=self.request.preview_dpi,
                max_edge_px=self.request.preview_max_edge_px,
                max_preview_pairs=self.request.max_preview_pairs,
                # Phase O4 — zone-level noise filter (min_changes_per_zone
                # + single_entity_noise_score_threshold) sourced from
                # noise_filter_config.json via the dialog.
                zone_options=zone_options,
            )
            self._emit(progress_callback, "review_project", 90, "검토 프로젝트 저장 중")
            write_review_project(
                review_project_path,
                source_a=self.request.source_a,
                source_b=self.request.source_b,
                dxf_cache_dir=dxf_cache_dir,
                compare_state_dir=compare_state_dir,
                artifact_dir=artifact_dir,
                review_state_path=review_state_path,
                preview_manifest_path=preview_package.manifest_path,
                options={
                    "ux": "ko-simple-v2",
                    "cloud_export_mode": "selected",
                    "export_preview": True,
                    "max_preview_pairs": self.request.max_preview_pairs,
                },
                export_profile=export_profile,
            )
            update_artifact_manifest(
                artifact_package.output_paths.get("artifact_manifest_json"),
                preview_manifest_path=preview_package.manifest_path,
                review_state_path=review_state_path,
                review_project_path=review_project_path,
                export_profile=export_profile,
            )
            self._emit(progress_callback, "executive_review", 93, "검토 대시보드 만드는 중")
            executive_package = export_executive_review_from_artifacts(
                artifact_dir,
                top_review_issues=self.request.top_review_issues,
                top_issues_per_drawing=self.request.top_issues_per_drawing,
                fold_repetitive_layers=self.request.fold_repetitive_layers,
            )
            artifact_package.output_paths.update(executive_package.output_paths)
            # Heuristic ETA hint — large drawings (>1000 zones) may need
            # 5-15 minutes for tile cache + overlay rendering. Surfacing the
            # zone count here turns the silent 96% wait into a "I know what's
            # happening" wait.
            zone_hint = ""
            try:
                _zone_count = int(getattr(artifact_package, "zone_count", 0))
                if _zone_count > 1000:
                    zone_hint = f" ({_zone_count:,}개 변경구역, 수 분 소요)"
                elif _zone_count > 0:
                    zone_hint = f" ({_zone_count}개 변경구역)"
            except Exception:
                pass
            self._emit(progress_callback, "viewer", 96, f"뷰어 패키지 만드는 중{zone_hint}")
            # Audit-gates §11.4 — viewer_package now runs in an isolated
            # subprocess so a memory blow-up on a single S20-class drawing
            # only kills the worker, not the host GUI. The proxy returns a
            # ``(dict, SubprocessRunReport)`` tuple where the dict mirrors
            # ``ViewerPackage.to_dict()``. ``runtime_sampler`` is omitted
            # because the subprocess starts its own sampler internally.
            effective_viewer_render_policy = (
                "top-issues"
                if fast_first_review
                else self.request.viewer_render_policy
            )
            effective_max_zone_tiles = (
                0 if fast_first_review else self.request.max_zone_tiles
            )
            effective_prefetch_neighbor_tiles = (
                False
                if fast_first_review
                else self.request.prefetch_neighbor_tiles
            )
            effective_tile_prefetch_radius = (
                0 if fast_first_review else self.request.tile_prefetch_radius
            )
            effective_export_marked_pdf = (
                False if fast_first_review else self.request.export_marked_pdf
            )
            effective_marked_pdf_mode = (
                "off" if fast_first_review else self.request.marked_pdf_mode
            )
            effective_build_lod_tiles = not fast_first_review

            viewer_options: dict[str, Any] = {
                "viewer_dir": viewer_dir,
                "review_dashboard": executive_package.output_paths.get("review_dashboard_json"),
                "preview_manifest": preview_package.manifest_path,
                "viewer_mode": self.request.viewer_mode,
                "render_policy": effective_viewer_render_policy,
                "render_timeout_seconds": self.request.render_timeout_seconds,
                "viewer_engine": self.request.viewer_engine,
                "viewer_cache_dir": self.request.viewer_cache_dir,
                "tile_size": self.request.tile_size,
                "max_visible_overlays": self.request.max_visible_overlays,
                "viewer_memory_budget_mb": self.request.viewer_memory_budget_mb,
                "render_selected_on_open": self.request.render_selected_on_open,
                "prefetch_neighbor_tiles": effective_prefetch_neighbor_tiles,
                "tile_prefetch_radius": effective_tile_prefetch_radius,
                "overview_max_edge": self.request.overview_max_edge,
                "focus_tile_max_edge": self.request.focus_tile_max_edge,
                "viewer_perf_log": self.request.viewer_perf_log,
                "max_viewer_pages": self.request.max_viewer_pages,
                "max_zone_tiles": effective_max_zone_tiles,
                "dxf_cache_dir": dxf_cache_dir,
                "preview_dpi": self.request.preview_dpi,
                "preview_max_edge_px": self.request.preview_max_edge_px,
                "export_marked_pdf": effective_export_marked_pdf,
                "marked_pdf_mode": effective_marked_pdf_mode,
                "build_lod_tiles": effective_build_lod_tiles,
            }

            def _viewer_subprocess_progress(event: dict[str, Any]) -> None:
                # Forward subprocess heartbeat to the outer GUI bar so a
                # stuck worker is visible in real time. We map memory_sample
                # events to a 96% emit with current MB hint; result/error
                # are handled below by inspecting the report.
                if event.get("event") == "memory_sample":
                    peek_mb = event.get("peak_working_set_mb")
                    if isinstance(peek_mb, (int, float)) and peek_mb > 0:
                        self._emit(
                            progress_callback,
                            "viewer",
                            96,
                            f"뷰어 패키지 만드는 중{zone_hint} (메모리 {peek_mb:.0f}MB)",
                        )

            # Audit-gates §13.4 Phase B-2 — pass the parent's fault log dir so
            # subprocess crashes land alongside the parent's fault_*.log. The
            # child has its own fallback if this lookup fails so a missing
            # _FAULT_LOG_PATH (e.g. tests that did not arm the parent handler)
            # is still safe.
            subprocess_fault_dir: Optional[Path] = None
            try:
                from src.core import error_handler as _eh

                parent_fault_path = getattr(_eh, "_FAULT_LOG_PATH", None)
                if parent_fault_path is not None:
                    subprocess_fault_dir = Path(parent_fault_path).parent / "subprocess"
            except Exception:
                subprocess_fault_dir = None

            viewer_package_dict, viewer_report = export_viewer_package_isolated(
                artifact_dir,
                options=viewer_options,
                memory_cap_mb=viewer_memory_cap_mb,
                progress_callback=_viewer_subprocess_progress,
                allow_inprocess_fallback=False,  # isolation is mandatory for S20-class
                fault_log_dir=subprocess_fault_dir,
            )
            if viewer_package_dict is None:
                # Subprocess failed — translate to MemoryBudgetExceeded when
                # the report identifies the cap as the cause; otherwise raise
                # a generic RuntimeError so the GUI catch surfaces it.
                from .runtime_budget import MemoryBudgetExceeded

                if viewer_report.error_type == "MemoryBudgetExceeded":
                    # Plan C-1 (audit §1.3 finding #2) — auto-retry ONCE with a
                    # lower DPI tier before surfacing the failure to the GUI.
                    # Previously the user had to manually drop the quality
                    # combo and re-run, which is a poor UX for a
                    # programmatically-detectable condition.
                    #
                    # Strategy: synthesise a ``QualityDecision`` from the
                    # current ``viewer_options`` (we don't thread one through
                    # the pipeline ``run()`` signature — that would balloon
                    # this PR), call ``downgrade_one_step``, and retry once
                    # with the lowered tier. If we are already at the floor
                    # OR the second attempt also raises, propagate the
                    # original exception.
                    from .adaptive_quality import (
                        InputCharacteristics,
                        QUALITY_TIERS,
                        QualityDecision,
                        QualityTier,
                        SCHEMA_VERSION,
                        downgrade_one_step,
                    )

                    current_dpi = int(viewer_options.get("preview_dpi") or 80)
                    current_max_edge = int(
                        viewer_options.get("preview_max_edge_px") or 2400
                    )
                    # Find the matching tier; if no exact match, build an
                    # ad-hoc tier so downgrade_one_step still has a starting
                    # index to walk down from. Ad-hoc tiers (e.g. DPI 400)
                    # are treated as above the highest known tier and walk
                    # down to the highest known tier on first downgrade.
                    matching_tier = next(
                        (t for t in QUALITY_TIERS if t.dpi == current_dpi),
                        None,
                    )
                    if matching_tier is None:
                        # Ad-hoc starting tier — pretend we are at the
                        # highest known tier so downgrade_one_step will
                        # actually move us down (otherwise current_index
                        # is None and the function no-ops).
                        synthetic_tier = QUALITY_TIERS[-1]
                    else:
                        synthetic_tier = matching_tier
                    synthetic_inputs = InputCharacteristics(
                        file_count_a=0,
                        file_count_b=0,
                        total_bytes=0,
                        max_pair_bytes=0,
                        average_pair_bytes=0,
                    )
                    synthetic_decision = QualityDecision(
                        schema_version=SCHEMA_VERSION,
                        tier=synthetic_tier,
                        rationale="auto_retry_synthesised",
                        auto_selected=True,
                        inputs=synthetic_inputs,
                        memory_cap_mb=float(viewer_memory_cap_mb or 4096.0),
                        safety_margin_ratio=0.6,
                    )
                    downgraded = downgrade_one_step(
                        synthetic_decision,
                        reason="auto_retry_after_budget_exceeded",
                    )
                    if downgraded.dpi >= synthetic_tier.dpi:
                        # Already at the lowest tier (or no real downgrade
                        # available) — propagate without retrying.
                        raise MemoryBudgetExceeded(
                            stage=viewer_report.error_stage
                            or "viewer_package_subprocess",
                            current_mb=float(
                                viewer_report.error_current_mb or 0.0
                            ),
                            max_mb=float(viewer_report.error_max_mb or 0.0),
                        )
                    # Update viewer_options with downgraded tier and retry.
                    viewer_options["preview_dpi"] = downgraded.dpi
                    viewer_options["preview_max_edge_px"] = downgraded.max_edge_px
                    # Plan C-1 — 97.5 slots between viewer-96 (build start)
                    # and post-process-97 below so GUI listeners that record
                    # raw progress values get a unique marker for the
                    # auto-retry event in their telemetry. ProgressCallback
                    # was widened from int → float to accept this.
                    self._emit(
                        progress_callback,
                        "viewer",
                        97.5,
                        f"메모리 한계 — DPI {downgraded.dpi} 로 자동 하향 후 재시도 중",
                    )
                    viewer_package_dict, viewer_report = export_viewer_package_isolated(
                        artifact_dir,
                        options=viewer_options,
                        memory_cap_mb=viewer_memory_cap_mb,
                        progress_callback=_viewer_subprocess_progress,
                        allow_inprocess_fallback=False,
                        fault_log_dir=subprocess_fault_dir,
                    )
                    if viewer_package_dict is None:
                        # Second attempt also failed — give up. If the cause
                        # is *still* MemoryBudgetExceeded, surface that
                        # specifically; otherwise wrap as RuntimeError.
                        if viewer_report.error_type == "MemoryBudgetExceeded":
                            raise MemoryBudgetExceeded(
                                stage=viewer_report.error_stage
                                or "viewer_package_subprocess_retry",
                                current_mb=float(
                                    viewer_report.error_current_mb or 0.0
                                ),
                                max_mb=float(viewer_report.error_max_mb or 0.0),
                            )
                        raise RuntimeError(
                            "viewer_package subprocess failed after auto-retry: "
                            f"{viewer_report.error_type}: {viewer_report.error_message}"
                        )
                else:
                    raise RuntimeError(
                        "viewer_package subprocess failed: "
                        f"{viewer_report.error_type}: {viewer_report.error_message}"
                    )
            viewer_output_paths: dict[str, Any] = (
                viewer_package_dict.get("output_paths") or {}
            )
            viewer_overlay_count: int = int(viewer_package_dict.get("overlay_count", 0))
            viewer_manifest_path_value: str = str(
                viewer_output_paths.get("viewer_manifest_json") or ""
            )
            # Audit-gates §11.4 — reconstruct the ViewerPackage dataclass from
            # the subprocess dict so downstream consumers (FolderCompareRunResult,
            # GUI bindings) keep their attribute access pattern. The proxy
            # returns a payload that matches ViewerPackage.to_dict().
            viewer_package = ViewerPackage(
                viewer_dir=Path(
                    viewer_package_dict.get("viewer_dir") or viewer_dir
                ),
                manifest_path=Path(viewer_manifest_path_value)
                if viewer_manifest_path_value
                else (Path(viewer_dir) / "viewer_manifest.json"),
                index_html=Path(
                    viewer_output_paths.get("viewer_index_html")
                    or (Path(viewer_dir) / "index.html")
                ),
                pair_count=int(viewer_package_dict.get("pair_count", 0)),
                overlay_count=viewer_overlay_count,
                page_count=int(viewer_package_dict.get("page_count", 0)),
                tile_count=int(viewer_package_dict.get("tile_count", 0)),
                marked_pdf_count=int(viewer_package_dict.get("marked_pdf_count", 0)),
                marked_pdf_skipped_count=int(
                    viewer_package_dict.get("marked_pdf_skipped_count", 0)
                ),
                rendered_pair_count=int(
                    viewer_package_dict.get("rendered_pair_count", 0)
                ),
                lazy_pair_count=int(viewer_package_dict.get("lazy_pair_count", 0)),
                transform_complete=bool(
                    viewer_package_dict.get("transform_complete", True)
                ),
                warnings=[str(w) for w in (viewer_package_dict.get("warnings") or [])],
                output_paths={k: str(v) for k, v in viewer_output_paths.items()},
            )
            artifact_package.output_paths.update(viewer_output_paths)
            run_manifest.stage(
                "first_review_ready",
                "completed",
                fast_first_review=fast_first_review,
                review_dashboard_json=executive_package.output_paths.get("review_dashboard_json"),
                viewer_manifest_json=viewer_manifest_path_value,
                viewer_render_policy=effective_viewer_render_policy,
                max_zone_tiles=effective_max_zone_tiles,
                build_lod_tiles=effective_build_lod_tiles,
                cloud_marks_deferred=fast_first_review,
                marked_pdf_deferred=fast_first_review,
            )
            if runtime_sampler is not None and hasattr(
                runtime_sampler, "mark_first_review_ready"
            ):
                try:
                    runtime_sampler.mark_first_review_ready()
                except Exception:
                    logger.debug(
                        "Runtime sampler first-review marker failed",
                        exc_info=True,
                    )
            # Audit-gates §10 follow-up — emit sub-progress between 96% and
            # 100% so the GUI progress bar does not appear frozen for 1-12
            # minutes during S20-class viewer build + post-processing. The
            # earlier code emitted 96% then nothing until 100%, which users
            # reported as "stuck at 97%".
            self._emit(progress_callback, "viewer", 97, "뷰어 패키지 완료 - 후처리 중")
            auto_structural_cloud_count = 0
            if self.request.auto_export_structural_clouds and not fast_first_review:
                self._emit(
                    progress_callback,
                    "auto_clouds",
                    97,
                    "구조 핵심 자동 구름마크 추출 중",
                )
                auto_cloud_outputs = _export_auto_structural_clouds(
                    review_dashboard_path=executive_package.output_paths.get("review_dashboard_json"),
                    viewer_manifest_path=viewer_manifest_path_value,
                    output_dir=artifact_dir / "auto_structural_clouds",
                )
                artifact_package.output_paths.update(auto_cloud_outputs)
                auto_structural_cloud_count = int(auto_cloud_outputs.get("auto_structural_cloud_count") or 0)
            self._emit(progress_callback, "export_profile", 98, "결과 패키지 정리 중")
            run_manifest.stage(
                active_stage,
                "completed",
                zone_count=artifact_package.zone_count,
                preview_count=preview_package.preview_count,
                viewer_overlay_count=viewer_overlay_count,
                auto_structural_cloud_count=auto_structural_cloud_count,
            )

            fast_state_cleanup: dict[str, Any] = {}
            if fast_first_review:
                fast_state_cleanup = _cleanup_fast_compare_state(compare_state_dir, output_dir)
                fast_state_stage_status = fast_state_cleanup.pop("cleanup_status", "skipped")
                run_manifest.stage(
                    "fast_state_cleanup",
                    "completed" if fast_state_stage_status == "cleaned" else "skipped",
                    **fast_state_cleanup,
                )

            _apply_export_profile_outputs(
                export_profile,
                output_dir,
                {
                    **artifact_package.output_paths,
                    "preview_manifest_json": preview_package.manifest_path,
                    "review_state_json": review_state_path,
                    **(
                        {}
                        if fast_first_review
                        else {"compare_state_json": compare_state_dir / "compare_state.json"}
                    ),
                    "preflight_report_json": preflight_path,
                },
                review_project_path,
            )
            released_change_records = 0
            if fast_first_review:
                released_change_records = _release_compare_memory(compare_summary)
                if released_change_records:
                    gc.collect()
                run_manifest.stage(
                    "memory_release",
                    "completed",
                    released_change_records=released_change_records,
                )
            finished_at = datetime.now().isoformat()
            run_manifest.complete(
                counts={
                    "descriptors_a": len(descriptors_a),
                    "descriptors_b": len(descriptors_b),
                    "confirmed_pairs": sum(1 for candidate in candidates if candidate.is_confirmed),
                    "completed_pairs": compare_summary.completed_pairs,
                    "failed_pairs": compare_summary.failed_pairs,
                    "raw_change_count": artifact_package.raw_change_count,
                    "zone_count": artifact_package.zone_count,
                    "cloud_region_count": artifact_package.cloud_region_count,
                    "released_change_records": released_change_records,
                },
                outputs={
                    **artifact_package.output_paths,
                    "review_project_json": review_project_path,
                    "preview_manifest_json": preview_package.manifest_path,
                    "preflight_report_json": preflight_path,
                },
                warnings=[check.message for check in preflight.warnings],
            )
            _apply_export_profile_outputs(
                export_profile,
                output_dir,
                {
                    "run_manifest_json": str(run_manifest.path),
                    "success_sentinel_json": str(run_manifest.success_path),
                },
                review_project_path,
            )
            self._emit(progress_callback, "audit", 99, "공유 안전성 검사 중")
            _enforce_sharable_path_audit(export_profile, output_dir)
            self._emit(progress_callback, "done", 100, "완료 - 결과 적재 중")

            return FolderCompareRunResult(
                request=self.request,
                output_dir=str(output_dir),
                artifact_dir=str(artifact_dir),
                preview_dir=str(preview_dir),
                review_project_path=str(review_project_path),
                review_state_path=str(review_state_path),
                dxf_cache_dir=str(dxf_cache_dir),
                compare_state_dir=str(compare_state_dir),
                descriptors_a=descriptors_a,
                descriptors_b=descriptors_b,
                candidates=candidates,
                compare_summary=compare_summary,
                artifact_package=artifact_package,
                preview_package=preview_package,
                executive_package=executive_package,
                viewer_package=viewer_package,
                run_manifest_path=str(run_manifest.path),
                success_sentinel_path=str(run_manifest.success_path),
                failed_sentinel_path=str(run_manifest.failed_path),
                preflight_report_path=str(preflight_path),
                preflight_result=preflight,
                started_at=started_at,
                finished_at=finished_at,
            )
        except Exception as exc:
            run_manifest.fail(active_stage, exc)
            raise

    @staticmethod
    def _emit(
        callback: Optional[ProgressCallback],
        stage: str,
        percent: float,
        message: str,
    ) -> None:
        if callback:
            callback(stage, percent, message)

    @staticmethod
    def _check_cancelled(is_cancelled: Optional[CancelCallback]) -> None:
        if is_cancelled and is_cancelled():
            raise RuntimeError("사용자가 도면 비교 작업을 취소했습니다.")

def _release_compare_memory(compare_summary: BatchCompareSummary) -> int:
    """Drop retained raw change objects after artifacts have been written."""

    released = 0
    for item in getattr(compare_summary, "items", []) or []:
        result = getattr(item, "result", None)
        changes = getattr(result, "changes", None)
        if isinstance(changes, list):
            released += len(changes)
            changes.clear()
        metadata = getattr(result, "metadata", None)
        if isinstance(metadata, dict):
            zone_payload = metadata.get("change_zones")
            if isinstance(zone_payload, list):
                metadata["change_zones"] = []
    return released


def _cleanup_fast_compare_state(compare_state_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Remove bulky transient compare state after fast-review artifacts exist."""

    state_dir = Path(compare_state_dir).resolve()
    root = Path(output_dir).resolve()
    try:
        state_dir.relative_to(root)
    except ValueError:
        return {
            "cleanup_status": "skipped",
            "reason": "compare_state_dir_outside_output_dir",
            "removed_file_count": 0,
            "removed_bytes": 0,
        }

    removed_file_count = 0
    removed_bytes = 0
    for target in (state_dir / "streams", state_dir / "compare_state.json"):
        if not target.exists():
            continue
        if target.is_file():
            try:
                removed_bytes += target.stat().st_size
            except OSError:
                pass
            target.unlink(missing_ok=True)
            removed_file_count += 1
            continue
        file_count, byte_count = _count_tree_files(target)
        shutil.rmtree(target, ignore_errors=True)
        removed_file_count += file_count
        removed_bytes += byte_count

    return {
        "cleanup_status": "cleaned",
        "removed_file_count": removed_file_count,
        "removed_bytes": removed_bytes,
    }


def _count_tree_files(path: Path) -> tuple[int, int]:
    file_count = 0
    byte_count = 0
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        file_count += 1
        try:
            byte_count += item.stat().st_size
        except OSError:
            pass
    return file_count, byte_count


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _explicit_file_pair_candidates(
    source_a: Union[str, Path],
    source_b: Union[str, Path],
    descriptors_a: list[DrawingFileDescriptor],
    descriptors_b: list[DrawingFileDescriptor],
) -> Optional[list[MatchCandidate]]:
    """Return a confirmed pair when the user explicitly selected two files."""

    path_a = Path(source_a)
    path_b = Path(source_b)
    if not path_a.is_file() or not path_b.is_file():
        return None
    if len(descriptors_a) != 1 or len(descriptors_b) != 1:
        return None

    desc_a = descriptors_a[0]
    desc_b = descriptors_b[0]
    if not are_compatible(desc_a, desc_b):
        return [
            MatchCandidate(
                source_a=desc_a,
                source_b=None,
                score=0.0,
                status=MatchStatus.UNMATCHED_A,
                reasons=["explicit file selection blocked by incompatible formats"],
            ),
            MatchCandidate(
                source_a=None,
                source_b=desc_b,
                score=0.0,
                status=MatchStatus.UNMATCHED_B,
                reasons=["explicit file selection blocked by incompatible formats"],
            ),
        ]

    return [
        MatchCandidate(
            source_a=desc_a,
            source_b=desc_b,
            score=1.0,
            status=MatchStatus.MANUAL_CONFIRMED,
            reasons=["explicit file selection"],
            component_scores={"explicit_file_pair": 1.0},
        )
    ]


def _apply_export_profile_outputs(
    export_profile: str,
    output_dir: Path,
    output_paths: dict[str, Any],
    review_project_path: Path,
) -> None:
    if normalize_export_profile(export_profile) != "sharable":
        return
    applied: set[Path] = set()
    for value in output_paths.values():
        if isinstance(value, (str, Path)):
            target = Path(value)
            apply_export_profile_to_file(target, profile=export_profile, package_root=output_dir)
            try:
                applied.add(target.resolve())
            except Exception:
                pass
    for path in sorted(item for item in output_dir.rglob("*") if item.is_file()):
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        if resolved in applied:
            continue
        apply_export_profile_to_file(path, profile=export_profile, package_root=output_dir)
        applied.add(resolved)
    apply_export_profile_to_json(review_project_path, profile=export_profile, package_root=output_dir)


def _enforce_sharable_path_audit(export_profile: str, output_dir: Path) -> list[dict[str, str]]:
    if normalize_export_profile(export_profile) != "sharable":
        return []
    leaks = audit_sharable_paths(output_dir)
    audit_path = output_dir / "sharable_path_audit.json"
    _write_json_atomic(
        audit_path,
        {
            "schema_version": 1,
            "profile": "sharable",
            "passed": not leaks,
            "leak_count": len(leaks),
            "leaks": leaks,
        },
    )
    if leaks:
        first = leaks[0]
        raise RuntimeError(
            "Sharable path leakage audit failed: "
            f"{len(leaks)} leak(s), first={first.get('file')}:{first.get('key')}"
        )
    return leaks


def _export_auto_structural_clouds(
    *,
    review_dashboard_path: Any,
    viewer_manifest_path: Any,
    output_dir: Path,
) -> dict[str, Any]:
    dashboard = _read_json_if_exists(review_dashboard_path)
    viewer_manifest = _read_json_if_exists(viewer_manifest_path)
    queue = dashboard.get("review_queue") if isinstance(dashboard, dict) else {}
    items = queue.get("items") if isinstance(queue, dict) else []
    if not isinstance(items, list):
        items = []

    zone_ids_by_pair: dict[str, set[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "")
        if category not in AUTO_STRUCTURAL_CLOUD_CATEGORIES:
            continue
        pair_id = str(item.get("pair_id") or item.get("pair_uuid") or "")
        zone_id = str(item.get("zone_id") or "")
        if pair_id and zone_id:
            zone_ids_by_pair.setdefault(pair_id, set()).add(zone_id)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "auto_structural_clouds_manifest.json"
    viewer_base = Path(viewer_manifest_path).parent if viewer_manifest_path else output_dir
    pairs = viewer_manifest.get("pairs") if isinstance(viewer_manifest, dict) else []
    results: list[dict[str, Any]] = []
    exported_count = 0
    skipped_count = 0

    if isinstance(pairs, list):
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            pair_id = str(pair.get("pair_id") or pair.get("pair_uuid") or "")
            zone_ids = zone_ids_by_pair.get(pair_id)
            if not zone_ids:
                continue
            overlay_path = _resolve_package_path(pair.get("overlay_json"), viewer_base)
            overlay_payload = _read_json_if_exists(overlay_path)
            overlays = overlay_payload.get("overlays") if isinstance(overlay_payload, dict) else []
            if not isinstance(overlays, list):
                overlays = []
            after_image_path = _resolve_package_path(pair.get("after_image"), viewer_base)
            after_transform = pair.get("after_transform") if isinstance(pair.get("after_transform"), dict) else {}
            image_dpi = _float_value(after_transform.get("dpi")) if isinstance(after_transform, dict) else 0.0
            result = export_selected_cloud_marks(
                pair_id=pair_id,
                after_image_path=str(after_image_path) if after_image_path else "",
                overlays=overlays,
                zone_ids=zone_ids,
                output_dir=output_dir,
                is_pdf_pair=_viewer_pair_is_pdf(pair),
                label_prefix="구조",
                output_suffix="auto_structural",
                image_dpi=image_dpi,
            )
            row = result.to_dict()
            row["selected_zone_ids"] = sorted(zone_ids)
            results.append(row)
            if result.output_path:
                exported_count += 1
            else:
                skipped_count += 1

    _write_json_atomic(
        manifest_path,
        {
            "schema_version": 1,
            "mode": "auto_structural_clouds",
            "categories": sorted(AUTO_STRUCTURAL_CLOUD_CATEGORIES),
            "exported_pair_count": exported_count,
            "skipped_pair_count": skipped_count,
            "candidate_pair_count": len(zone_ids_by_pair),
            "results": results,
        },
    )
    return {
        "auto_structural_clouds_dir": str(output_dir),
        "auto_structural_cloud_manifest_json": str(manifest_path),
        "auto_structural_cloud_count": exported_count,
    }


def _read_json_if_exists(path_value: Any) -> dict[str, Any]:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_package_path(path_value: Any, base_dir: Path) -> Optional[Path]:
    text = str(path_value or "")
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute():
        path = Path(base_dir) / path
    return path


def _viewer_pair_is_pdf(pair: dict[str, Any]) -> bool:
    if str(pair.get("coordinate_source") or "") == "image_pixels":
        return True
    joined = " ".join(str(pair.get(key) or "") for key in ("source_a", "source_b")).lower()
    return ".pdf" in joined


def _float_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
