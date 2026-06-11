# -*- coding: utf-8 -*-
"""Single-action folder comparison pipeline for the Korean Workbench UX."""

from __future__ import annotations

import json
import logging
import os
import gc
import shutil
import inspect
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence, Union

logger = logging.getLogger(__name__)


def _is_explicit_oda_converter_backend(value: Any) -> bool:
    if value is None:
        return False
    try:
        return normalize_dwg_backend_mode(str(value)) == DWG_BACKEND_ODA_CONVERTER
    except ValueError:
        return False


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
    _descriptor_uses_commercial_sdk,
    _descriptor_user_converter_dxf,
    are_compatible,
    match_drawing_sets,
    scan_drawing_inputs,
)
from .dxf_read import dxf_document_cache_scope
from .dwg_dxf_fallback import (
    auto_convert_unsupported_dwg,
    fallback_review_notice,
    resolve_dwg_dxf_fallback_pair,
)
from .dwg_backend import DWG_BACKEND_ODA_CONVERTER, normalize_dwg_backend_mode
from .confirmed_cloud_export import export_selected_cloud_marks
from .export_profiles import (
    apply_export_profile_to_file,
    apply_export_profile_to_json,
    audit_sharable_paths,
    normalize_export_profile,
)
from .preflight import PreflightResult, run_preflight
from .perf_events import (
    PERF_EVENTS_SUMMARY_FILENAME,
    PerfEventWriter,
    remove_raw_perf_events,
)
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
FirstReviewReadyCallback = Callable[["FolderCompareRunResult"], None]
AUTO_STRUCTURAL_CLOUD_CATEGORIES = {"member", "dimension", "rebar", "grid", "mixed"}
MULTI_FRAME_MODE_ENV = "DRAWING_COMPARE_MULTI_FRAME"
MULTI_FRAME_MODES = {"off", "sidecar_only", "review_gate", "auto"}
AUTO_REGION_COMPARE_ENV = "DRAWING_COMPARE_AUTO_REGION_COMPARE"
REGION_LOCAL_DEFAULT_ENV = "DRAWING_COMPARE_REGION_LOCAL_DEFAULT"
REGION_PILOT_SUMMARY_ENV = "DRAWING_COMPARE_REGION_PILOT_SUMMARY"
REGION_LOCAL_DEFAULT_MODES = {"off", "pilot_passed"}
REQUIRED_REGION_PILOT_ACCEPTANCE_KEYS = {
    "detected_region_rate",
    "whole_modelspace_fallback_rate",
    "user_approved_match_accuracy",
    "false_positive_reduction",
    "viewer_screenshot_count",
}


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
    cad_visual_backend: str = ""
    cad_visual_conversion_timeout_seconds: int = 180
    dwg_backend_mode: Optional[str] = None
    allowed_dwg_license_ids: Sequence[str] = ("MIT", "INTERNAL")
    user_converter_path: Optional[Union[str, Path]] = None
    user_conversion_args: Sequence[str] = tuple()
    user_conversion_timeout_seconds: Optional[float] = None
    dwg_conversion_cache_dir: Optional[Union[str, Path]] = None
    export_marked_pdf: bool = False
    marked_pdf_mode: str = "off"
    export_profile: str = "sharable"
    # Workbench-first speed path. When enabled, the pipeline prioritizes the
    # first usable review screen over completion of all heavy share/export
    # artifacts: top-issue backgrounds are still rendered, but zone tiles,
    # marked PDFs, and full cloud-mark DXFs are deferred.
    fast_first_review: bool = False
    auto_fast_first_review: bool = True
    fast_first_review_pair_threshold: int = 20
    fast_first_review_zone_threshold: int = 1000
    fast_first_review_max_viewer_pages: int = 1
    fast_first_review_render_timeout_seconds: int = 30
    fast_first_review_max_overlay_records_per_pair: int = 500
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
    result_state: str = "package_complete"
    package_complete: bool = True
    first_review_ready_at: str = ""
    package_completed_at: str = ""
    first_review_metadata: dict[str, Any] = field(default_factory=dict)

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
        first_review_ready_callback: Optional[FirstReviewReadyCallback] = None,
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
        # Hang self-diagnosis (2026-06-11 live incident: GUI compare sat in
        # one stage 65+ min with zero events while a headless rerun of the
        # same pair took 62.7 s; py-spy could not attach, so the cause died
        # with the process). If no stage transition happens for the timeout,
        # every thread's stack is dumped into the run dir — observation
        # only, the run is never interrupted.
        from .stage_hang_watchdog import StageHangWatchdog

        hang_watchdog = StageHangWatchdog(output_dir).start()
        run_manifest.on_stage = (
            lambda name, status: hang_watchdog.pet(f"{name}:{status}")
        )
        perf_writer = PerfEventWriter(
            output_dir,
            run_id=str(run_manifest.payload.get("run_id") or ""),
            runtime_sampler=runtime_sampler,
        )
        perf_summary_path = output_dir / PERF_EVENTS_SUMMARY_FILENAME
        active_stage = "prepare"
        auto_fast_first_review_triggered = False
        auto_fast_first_review_reason = ""

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

        # Issue-1 lever #2 (2026-06-11): share parsed DXF documents across the
        # scan/compare/region stages of THIS run — profiling showed the same
        # 71.9 MB converted DXF parsed 7x per run (>50% of wall time), all via
        # read_dxf_document_result. The cloud marker opts out (mutable=True)
        # because it mutates and saveas-es its copy. Scope closes in finally,
        # releasing the cached documents on success and failure alike.
        from contextlib import ExitStack

        _doc_cache_cm = ExitStack()
        try:
            _doc_cache_cm.enter_context(dxf_document_cache_scope())
            output_dir.mkdir(parents=True, exist_ok=True)
            try:
                perf_writer.path.unlink(missing_ok=True)
            except OSError:
                logger.debug("Could not reset perf_events log", exc_info=True)
            dxf_cache_dir.mkdir(parents=True, exist_ok=True)
            compare_state_dir.mkdir(parents=True, exist_ok=True)
            fallback_resolution = resolve_dwg_dxf_fallback_pair(
                self.request.source_a,
                self.request.source_b,
            )
            source_a_input = fallback_resolution.effective_source_a
            source_b_input = fallback_resolution.effective_source_b
            if fallback_resolution.used:
                logger.info(
                    "Using converted DXF fallback for unsupported DWG pair: %s -> %s, %s -> %s",
                    fallback_resolution.source_a,
                    source_a_input,
                    fallback_resolution.source_b,
                    source_b_input,
                )
            if _is_explicit_oda_converter_backend(self.request.dwg_backend_mode):
                source_a_input, _oda_conv_a, _oda_note_a = auto_convert_unsupported_dwg(
                    source_a_input, dxf_cache_dir
                )
                source_b_input, _oda_conv_b, _oda_note_b = auto_convert_unsupported_dwg(
                    source_b_input, dxf_cache_dir
                )
                if _oda_conv_a or _oda_conv_b:
                    logger.info(
                        "ODA auto-converted unsupported DWG -> DXF (a=%s, b=%s)",
                        _oda_note_a,
                        _oda_note_b,
                    )
                    run_manifest.stage(
                        "dwg_oda_autoconvert",
                        "completed",
                        source_a_note=_oda_note_a,
                        source_b_note=_oda_note_b,
                        effective_source_a=str(source_a_input),
                        effective_source_b=str(source_b_input),
                    )
            preflight_started = time.perf_counter()
            preflight = run_preflight(
                source_a=source_a_input,
                source_b=source_b_input,
                output_dir=output_dir,
                dxf_cache_dir=dxf_cache_dir,
                compare_state_dir=compare_state_dir,
                allow_long_path_warning=self.request.allow_long_path_warning,
                dwg_backend_mode=self.request.dwg_backend_mode,
                allowed_dwg_license_ids=tuple(self.request.allowed_dwg_license_ids or ()),
                user_converter_path=self.request.user_converter_path,
            )
            preflight_path = output_dir / "preflight_report.json"
            _write_json_atomic(preflight_path, preflight.to_dict())
            run_manifest.start(
                inputs={
                    "source_a": self.request.source_a,
                    "source_b": self.request.source_b,
                    "effective_source_a": source_a_input,
                    "effective_source_b": source_b_input,
                    "dwg_dxf_fallback": fallback_resolution.to_dict(),
                    "recursive": self.request.recursive,
                    "fast_first_review": fast_first_review,
                    "auto_fast_first_review": self.request.auto_fast_first_review,
                    "fast_first_review_pair_threshold": self.request.fast_first_review_pair_threshold,
                    "fast_first_review_zone_threshold": self.request.fast_first_review_zone_threshold,
                    "dwg_backend_mode": self.request.dwg_backend_mode,
                    "allowed_dwg_license_ids": list(self.request.allowed_dwg_license_ids or ()),
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
            if fallback_resolution.used:
                run_manifest.stage(
                    "dwg_dxf_fallback",
                    "completed",
                    **fallback_resolution.to_dict(),
                )
            run_manifest.stage("preflight", "completed", preflight_status=preflight.status)
            perf_writer.stage_event(
                "preflight",
                "completed",
                preflight_started,
                warning_count=len(preflight.warnings),
                error_code="preflight_errors" if preflight.errors else "",
            )
            if preflight.errors:
                raise RuntimeError(
                    "Preflight failed: "
                    + "; ".join(check.message for check in preflight.errors[:3])
                )
            save_review_state(review_state_path, [])

            active_stage = "scan"
            run_manifest.stage(active_stage, "running")
            stage_started = time.perf_counter()
            self._emit(progress_callback, "scan", 5, "도면 찾는 중")
            self._check_cancelled(is_cancelled)
            scan_options = DescriptorBuildOptions(
                recursive=self.request.recursive,
                use_ocr_fallback=self.request.use_ocr,
                enable_cache=self.request.enable_descriptor_cache,
                dxf_cache_dir=dxf_cache_dir,
                dwg_backend_mode=self.request.dwg_backend_mode,
                allowed_dwg_license_ids=tuple(self.request.allowed_dwg_license_ids or ()),
                user_converter_path=self.request.user_converter_path,
                user_conversion_args=tuple(self.request.user_conversion_args or ()),
                user_conversion_timeout_seconds=self.request.user_conversion_timeout_seconds,
                dwg_conversion_cache_dir=self.request.dwg_conversion_cache_dir,
            )
            descriptors_a = scan_drawing_inputs(source_a_input, options=scan_options)
            self._emit(progress_callback, "scan", 18, "변경 전 도면 확인 완료")
            self._check_cancelled(is_cancelled)
            descriptors_b = scan_drawing_inputs(source_b_input, options=scan_options)
            run_manifest.stage(active_stage, "completed", a_count=len(descriptors_a), b_count=len(descriptors_b))
            perf_writer.stage_event(
                active_stage,
                "completed",
                stage_started,
                input_bytes=_descriptor_total_bytes(descriptors_a)
                + _descriptor_total_bytes(descriptors_b),
                entity_count=_descriptor_entity_count(descriptors_a)
                + _descriptor_entity_count(descriptors_b),
                a_count=len(descriptors_a),
                b_count=len(descriptors_b),
            )

            active_stage = "match"
            run_manifest.stage(active_stage, "running")
            stage_started = time.perf_counter()
            self._emit(progress_callback, "match", 28, "도면 번호로 자동 매칭 중")
            self._check_cancelled(is_cancelled)
            candidates = _explicit_file_pair_candidates(
                source_a_input,
                source_b_input,
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
            perf_writer.stage_event(
                active_stage,
                "completed",
                stage_started,
                pair_count=len(candidates),
                confirmed=sum(1 for candidate in candidates if candidate.is_confirmed),
                review_required=sum(
                    1
                    for candidate in candidates
                    if candidate.status == MatchStatus.REVIEW_REQUIRED
                ),
            )
            if (
                not fast_first_review
                and self.request.auto_fast_first_review
                and _should_auto_fast_first_review(
                    pair_count=len(candidates),
                    source_a_count=len(descriptors_a),
                    source_b_count=len(descriptors_b),
                    threshold=self.request.fast_first_review_pair_threshold,
                )
            ):
                fast_first_review = True
                auto_fast_first_review_triggered = True
                auto_fast_first_review_reason = "large_run_pair_or_input_count"
                run_manifest.stage(
                    "fast_first_review_auto",
                    "completed",
                    reason=auto_fast_first_review_reason,
                    pair_count=len(candidates),
                    source_a_count=len(descriptors_a),
                    source_b_count=len(descriptors_b),
                    threshold=max(1, int(self.request.fast_first_review_pair_threshold or 1)),
                )
                perf_writer.append(
                    stage="fast_first_review_auto",
                    event="completed",
                    pair_count=len(candidates),
                    source_a_count=len(descriptors_a),
                    source_b_count=len(descriptors_b),
                    threshold=max(1, int(self.request.fast_first_review_pair_threshold or 1)),
                    reason=auto_fast_first_review_reason,
                )

            active_stage = "compare"
            run_manifest.stage(active_stage, "running")
            stage_started = time.perf_counter()
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
                dwg_backend_mode=self.request.dwg_backend_mode,
                allowed_dwg_license_ids=tuple(self.request.allowed_dwg_license_ids or ()),
                user_converter_path=self.request.user_converter_path,
                user_conversion_args=tuple(self.request.user_conversion_args or ()),
                user_conversion_timeout_seconds=self.request.user_conversion_timeout_seconds,
                dwg_conversion_cache_dir=self.request.dwg_conversion_cache_dir,
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
            compare_failures = _compare_failure_records(compare_summary)
            compare_failure_path = _write_compare_failures(
                compare_failures,
                artifact_dir / "compare_failures.json",
            )
            run_manifest.stage(
                active_stage,
                "completed",
                requested_pairs=compare_summary.requested_pairs,
                completed_pairs=compare_summary.completed_pairs,
                failed_pairs=compare_summary.failed_pairs,
                cancelled_pairs=compare_summary.cancelled_pairs,
                failures=compare_failures,
            )
            perf_writer.stage_event(
                active_stage,
                "completed",
                stage_started,
                pair_count=compare_summary.requested_pairs,
                completed_pairs=compare_summary.completed_pairs,
                failed_pairs=compare_summary.failed_pairs,
                warning_count=len(compare_failures),
                error_code="compare_failed" if compare_failures else "",
            )

            active_stage = "artifact"
            run_manifest.stage(active_stage, "running")
            stage_started = time.perf_counter()
            self._emit(progress_callback, "artifact", 78, "결과 만드는 중")
            effective_export_cloud_marks = not fast_first_review
            artifact_package = export_change_artifacts(
                compare_summary,
                artifact_dir,
                dxf_cache_dir=dxf_cache_dir,
                compare_state_dir=compare_state_dir,
                cloud_options=CloudMarkOptions(export_mode="selected"),
                export_cloud_marks=effective_export_cloud_marks,
                # Phase O — Codex review RV-20260507-003 fix:
                # without this, change_zones.csv / artifact_manifest.json /
                # cloud_marked DXFs / dashboard JSON are still built with
                # default ChangeZoneOptions (min=1) so noise-suppressed
                # single-entity zones leak into the user-visible review
                # surface even after the dialog "saves" min_changes_per_zone>=2.
                zone_options=zone_options,
            )
            if compare_failure_path is not None:
                artifact_package.output_paths["compare_failures_json"] = str(
                    compare_failure_path
                )
            region_paths = _export_region_aware_artifacts(
                compare_summary=compare_summary,
                artifact_package=artifact_package,
                artifact_dir=artifact_dir,
                dxf_cache_dir=dxf_cache_dir,
                dwg_backend_mode=self.request.dwg_backend_mode,
                allowed_dwg_license_ids=tuple(self.request.allowed_dwg_license_ids or ()),
                user_converter_path=self.request.user_converter_path,
                user_conversion_args=tuple(self.request.user_conversion_args or ()),
                user_conversion_timeout_seconds=self.request.user_conversion_timeout_seconds,
                dwg_conversion_cache_dir=self.request.dwg_conversion_cache_dir,
            )
            artifact_package.output_paths.update(region_paths)
            run_manifest.stage(
                active_stage,
                "completed",
                raw_change_count=artifact_package.raw_change_count,
                zone_count=artifact_package.zone_count,
                cloud_region_count=artifact_package.cloud_region_count,
                cloud_omitted_zone_count=artifact_package.cloud_omitted_zone_count,
                region_aware_output_count=len(region_paths),
            )
            perf_writer.stage_event(
                active_stage,
                "completed",
                stage_started,
                entity_count=artifact_package.raw_change_count,
                zone_count=artifact_package.zone_count,
                cloud_region_count=artifact_package.cloud_region_count,
            )
            if (
                not fast_first_review
                and self.request.auto_fast_first_review
                and _should_auto_fast_first_review_for_zone_count(
                    zone_count=getattr(artifact_package, "zone_count", 0),
                    threshold=self.request.fast_first_review_zone_threshold,
                )
            ):
                fast_first_review = True
                auto_fast_first_review_triggered = True
                auto_fast_first_review_reason = "large_run_zone_count"
                run_manifest.stage(
                    "fast_first_review_auto",
                    "completed",
                    reason=auto_fast_first_review_reason,
                    zone_count=int(getattr(artifact_package, "zone_count", 0) or 0),
                    threshold=max(1, int(self.request.fast_first_review_zone_threshold or 1)),
                    scope="viewer_only",
                )
                perf_writer.append(
                    stage="fast_first_review_auto",
                    event="completed",
                    zone_count=int(getattr(artifact_package, "zone_count", 0) or 0),
                    threshold=max(1, int(self.request.fast_first_review_zone_threshold or 1)),
                    reason=auto_fast_first_review_reason,
                    scope="viewer_only",
                )

            active_stage = "preview"
            run_manifest.stage(active_stage, "running")
            stage_started = time.perf_counter()
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
            review_project_options = {
                "ux": "ko-simple-v2",
                "cloud_export_mode": "selected",
                "export_preview": True,
                "max_preview_pairs": self.request.max_preview_pairs,
            }
            fallback_notice = fallback_review_notice(fallback_resolution)
            if fallback_notice:
                review_project_options["input_resolution"] = fallback_notice
            self._emit(progress_callback, "review_project", 90, "검토 프로젝트 저장 중")
            write_review_project(
                review_project_path,
                source_a=source_a_input,
                source_b=source_b_input,
                dxf_cache_dir=dxf_cache_dir,
                compare_state_dir=compare_state_dir,
                artifact_dir=artifact_dir,
                review_state_path=review_state_path,
                preview_manifest_path=preview_package.manifest_path,
                options=review_project_options,
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
            perf_writer.stage_event(
                active_stage,
                "completed",
                stage_started,
                zone_count=artifact_package.zone_count,
                preview_count=preview_package.preview_count,
            )
            run_manifest.stage(
                active_stage,
                "completed",
                preview_count=preview_package.preview_count,
                review_dashboard_json=executive_package.output_paths.get("review_dashboard_json"),
            )
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
            active_stage = "viewer"
            run_manifest.stage(active_stage, "running")
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
            effective_max_viewer_pages = self.request.max_viewer_pages
            effective_render_timeout_seconds = self.request.render_timeout_seconds
            effective_max_overlay_records_per_pair: Optional[int] = None
            if fast_first_review:
                fast_page_cap = max(
                    0,
                    int(self.request.fast_first_review_max_viewer_pages or 0),
                )
                effective_max_viewer_pages = min(
                    max(0, int(self.request.max_viewer_pages or 0)),
                    fast_page_cap,
                )
                fast_timeout_cap = max(
                    1,
                    int(self.request.fast_first_review_render_timeout_seconds or 30),
                )
                requested_timeout = int(self.request.render_timeout_seconds or 0)
                effective_render_timeout_seconds = (
                    min(requested_timeout, fast_timeout_cap)
                    if requested_timeout > 0
                    else fast_timeout_cap
                )
                effective_max_overlay_records_per_pair = max(
                    1,
                    int(self.request.fast_first_review_max_overlay_records_per_pair or 500),
                )

            viewer_options: dict[str, Any] = {
                "viewer_dir": viewer_dir,
                "review_dashboard": executive_package.output_paths.get("review_dashboard_json"),
                "preview_manifest": preview_package.manifest_path,
                "viewer_mode": self.request.viewer_mode,
                "render_policy": effective_viewer_render_policy,
                "render_timeout_seconds": effective_render_timeout_seconds,
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
                "max_viewer_pages": effective_max_viewer_pages,
                "max_zone_tiles": effective_max_zone_tiles,
                "max_overlay_records_per_pair": effective_max_overlay_records_per_pair,
                "cad_visual_backend": self.request.cad_visual_backend,
                "cad_visual_conversion_timeout_seconds": self.request.cad_visual_conversion_timeout_seconds,
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

            viewer_stage_started = time.perf_counter()
            viewer_package_dict, viewer_report = _export_viewer_package_isolated_compat(
                artifact_dir,
                options=viewer_options,
                memory_cap_mb=viewer_memory_cap_mb,
                progress_callback=_viewer_subprocess_progress,
                cancel_callback=is_cancelled,
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
                    viewer_package_dict, viewer_report = _export_viewer_package_isolated_compat(
                        artifact_dir,
                        options=viewer_options,
                        memory_cap_mb=viewer_memory_cap_mb,
                        progress_callback=_viewer_subprocess_progress,
                        cancel_callback=is_cancelled,
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
            perf_writer.stage_event(
                "viewer",
                "completed",
                viewer_stage_started,
                pair_count=viewer_package.pair_count,
                entity_count=viewer_overlay_count,
                cache_namespace="viewer_package",
                cache_hit=None,
                warning_count=len(viewer_package.warnings),
                render_mode=effective_viewer_render_policy,
                fidelity="pdf_first" if viewer_package.page_count else "cad_or_lazy",
                tile_count=viewer_package.tile_count,
                rendered_pair_count=viewer_package.rendered_pair_count,
                lazy_pair_count=viewer_package.lazy_pair_count,
            )
            first_review_ready_at = datetime.now().isoformat()
            ready_artifacts = {
                key: value
                for key, value in {
                    "review_dashboard_json": executive_package.output_paths.get("review_dashboard_json"),
                    "viewer_manifest_json": viewer_manifest_path_value,
                    "viewer_index_html": viewer_package.output_paths.get("viewer_index_html"),
                    "preview_manifest_json": preview_package.manifest_path,
                    "review_project_json": str(review_project_path),
                }.items()
                if value
            }
            marked_pdf_requested = bool(self.request.export_marked_pdf)
            lod_tiles_requested = (
                bool(self.request.max_zone_tiles)
                or self.request.viewer_render_policy == "all"
                or bool(self.request.prefetch_neighbor_tiles)
            )
            deferred_outputs = {
                "cloud_marks": "completed" if effective_export_cloud_marks else "deferred",
                "marked_pdf": (
                    "not_requested"
                    if not marked_pdf_requested
                    else "completed"
                    if effective_export_marked_pdf
                    else "deferred"
                ),
                "lod_tiles": (
                    "not_requested"
                    if not lod_tiles_requested
                    else "completed"
                    if effective_build_lod_tiles
                    else "deferred"
                ),
                "compare_state_json": "deferred" if fast_first_review else "ready",
                "export_profile": "pending",
                "package_success_sentinel": "pending",
            }
            if self.request.auto_export_structural_clouds:
                deferred_outputs["auto_structural_clouds"] = (
                    "pending" if not fast_first_review else "deferred"
                )
            first_review_metadata = {
                "fast_first_review": fast_first_review,
                "fast_first_review_auto": auto_fast_first_review_triggered,
                "fast_first_review_auto_reason": auto_fast_first_review_reason,
                "review_dashboard_json": executive_package.output_paths.get("review_dashboard_json"),
                "viewer_manifest_json": viewer_manifest_path_value,
                "viewer_render_policy": effective_viewer_render_policy,
                "max_zone_tiles": effective_max_zone_tiles,
                "max_viewer_pages": effective_max_viewer_pages,
                "render_timeout_seconds": effective_render_timeout_seconds,
                "max_overlay_records_per_pair": effective_max_overlay_records_per_pair,
                "build_lod_tiles": effective_build_lod_tiles,
                "cloud_marks_deferred": not effective_export_cloud_marks,
                "marked_pdf_deferred": fast_first_review,
                "review_ready": True,
                "package_complete": False,
                "ready_at": first_review_ready_at,
                "ready_artifacts": ready_artifacts,
                "deferred_outputs": deferred_outputs,
            }
            run_manifest.stage("first_review_ready", "completed", **first_review_metadata)
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
            review_ready_result = FolderCompareRunResult(
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
                finished_at=first_review_ready_at,
                result_state="review_ready",
                package_complete=False,
                first_review_ready_at=first_review_ready_at,
                package_completed_at="",
                first_review_metadata=dict(first_review_metadata),
            )
            self._emit(
                progress_callback,
                "first_review_ready",
                97,
                "검토 가능 - 최종 패키지 정리 중",
            )
            if first_review_ready_callback is not None:
                try:
                    first_review_ready_callback(review_ready_result)
                except Exception:
                    logger.debug("first_review_ready callback failed", exc_info=True)
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
            # Detached scene-pack prewarm (2026-06-12) — fills the GLOBAL
            # pack cache in a separate below-normal-priority process so the
            # GUI's first pair-select is a cache HIT instead of a 4-6 min
            # in-GUI cold parse of a 115 MB DXF. Fire-and-forget: adds 0 s
            # to the pipeline; on any failure the lazy GUI build remains.
            try:
                from .scene_pack_prewarm import launch_detached_prewarm

                launch_detached_prewarm(
                    [str(source_a_input), str(source_b_input)]
                )
            except Exception:  # noqa: BLE001 - prewarm is best-effort only
                logger.debug("scene-pack prewarm launch failed", exc_info=True)

            fast_state_cleanup: dict[str, Any] = {}
            if fast_first_review:
                fast_state_cleanup = _cleanup_fast_compare_state(compare_state_dir, output_dir)
                fast_state_stage_status = fast_state_cleanup.pop("cleanup_status", "skipped")
                run_manifest.stage(
                    "fast_state_cleanup",
                    "completed" if fast_state_stage_status == "cleaned" else "skipped",
                    **fast_state_cleanup,
                )

            active_stage = "export_profile"
            run_manifest.stage(active_stage, "running")
            export_profile_started = time.perf_counter()
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
            perf_writer.stage_event(
                "export_profile",
                "completed",
                export_profile_started,
                export_profile=export_profile,
                raw_perf_will_be_removed=export_profile == "sharable",
            )
            perf_summary = perf_writer.summarize(write=True)
            raw_perf_removed = False
            if export_profile == "sharable":
                raw_perf_removed = remove_raw_perf_events(output_dir)
            perf_writer_path_for_outputs = (
                {}
                if raw_perf_removed or export_profile == "sharable"
                else {"perf_events_jsonl": str(perf_writer.path)}
            )
            run_manifest.stage(
                active_stage,
                "completed",
                export_profile=export_profile,
                raw_perf_removed=raw_perf_removed,
                output_path_count=len(artifact_package.output_paths),
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
            package_completed_at = finished_at
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
                    "perf_events_summary_json": perf_summary_path,
                    **perf_writer_path_for_outputs,
                },
                warnings=[check.message for check in preflight.warnings],
                failures=compare_failures,
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
                result_state="package_complete",
                package_complete=True,
                first_review_ready_at=first_review_ready_at,
                package_completed_at=package_completed_at,
                first_review_metadata=dict(first_review_metadata),
            )
        except Exception as exc:
            try:
                perf_writer.append(
                    stage=active_stage,
                    event="failed",
                    error_code=type(exc).__name__,
                )
                perf_writer.summarize(write=True)
            except Exception:
                logger.debug("Failed to record perf failure event", exc_info=True)
            run_manifest.fail(active_stage, exc)
            raise
        finally:
            hang_watchdog.stop()
            _doc_cache_cm.close()

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

def _compare_failure_records(compare_summary: BatchCompareSummary) -> list[dict[str, Any]]:
    """Return compact per-pair failure diagnostics for run artifacts."""

    records: list[dict[str, Any]] = []
    for item in getattr(compare_summary, "items", []) or []:
        if getattr(item, "status", "") != "failed":
            continue
        candidate_payload = (
            item.candidate.to_dict()
            if getattr(item, "candidate", None) is not None
            else {}
        )
        result = getattr(item, "result", None)
        metadata = getattr(result, "metadata", None)
        if not isinstance(metadata, dict):
            metadata = {}
        warnings = getattr(result, "warnings", None)
        records.append(
            {
                "pair_id": candidate_payload.get("pair_id")
                or candidate_payload.get("pair_uuid"),
                "display_label": candidate_payload.get("display_label"),
                "status": getattr(item, "status", ""),
                "error": getattr(item, "error", "") or "",
                "source_a": (candidate_payload.get("source_a") or {}).get("path", ""),
                "source_b": (candidate_payload.get("source_b") or {}).get("path", ""),
                "error_code": metadata.get("error_code"),
                "pipeline_status": metadata.get("pipeline_status"),
                "message": metadata.get("message"),
                "canonical_fallback_used": metadata.get("canonical_fallback_used"),
                "canonical_fallback_reason": metadata.get("canonical_fallback_reason"),
                "canonical_error_code": metadata.get("canonical_error_code"),
                "dxf_cache_resolution_notes": metadata.get(
                    "dxf_cache_resolution_notes",
                    [],
                ),
                "warnings": list(warnings or [])[:20],
            }
        )
    return records


def _descriptor_total_bytes(descriptors: Sequence[DrawingFileDescriptor]) -> int:
    total = 0
    for descriptor in descriptors:
        try:
            total += Path(descriptor.path).stat().st_size
        except OSError:
            continue
    return total


def _should_auto_fast_first_review(
    *,
    pair_count: int,
    source_a_count: int,
    source_b_count: int,
    threshold: int,
) -> bool:
    """Return True when a run is large enough to prioritize first review."""

    try:
        safe_threshold = max(1, int(threshold or 1))
    except (TypeError, ValueError):
        safe_threshold = 20
    try:
        largest_count = max(int(pair_count or 0), int(source_a_count or 0), int(source_b_count or 0))
    except (TypeError, ValueError):
        largest_count = 0
    return largest_count >= safe_threshold


def _should_auto_fast_first_review_for_zone_count(
    *,
    zone_count: Any,
    threshold: int,
) -> bool:
    try:
        safe_threshold = max(1, int(threshold or 1))
        safe_zone_count = int(zone_count or 0)
    except (TypeError, ValueError):
        return False
    return safe_zone_count >= safe_threshold


def _descriptor_entity_count(descriptors: Sequence[DrawingFileDescriptor]) -> int:
    total = 0
    for descriptor in descriptors:
        counts = getattr(descriptor, "entity_counts", {}) or {}
        if not isinstance(counts, Mapping):
            continue
        for value in counts.values():
            try:
                total += int(value)
            except (TypeError, ValueError):
                continue
    return total


def _write_compare_failures(
    records: Sequence[Mapping[str, Any]],
    output_path: Path,
) -> Optional[Path]:
    if not records:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": datetime.now().isoformat(),
                "failed_pair_count": len(records),
                "failures": list(records),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return output_path


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


def _export_viewer_package_isolated_compat(*args: Any, **kwargs: Any) -> tuple[Any, SubprocessRunReport]:
    """Call viewer export while tolerating older test doubles.

    Production export accepts cancel_callback. Some tests monkeypatch it with
    small doubles written before that parameter existed.
    """

    try:
        params = inspect.signature(export_viewer_package_isolated).parameters
    except (TypeError, ValueError):
        params = {}
    if "cancel_callback" not in params:
        kwargs.pop("cancel_callback", None)
    return export_viewer_package_isolated(*args, **kwargs)


def _resolve_multi_frame_mode() -> tuple[str, str]:
    raw = os.getenv(MULTI_FRAME_MODE_ENV, "review_gate").strip().lower()
    if raw not in MULTI_FRAME_MODES:
        logger.warning(
            "Unknown %s=%r; using review_gate",
            MULTI_FRAME_MODE_ENV,
            raw,
        )
        return "review_gate", f"invalid {MULTI_FRAME_MODE_ENV} value; defaulted to review_gate"
    if raw == "auto":
        return (
            "review_gate",
            "auto region-local compare is not enabled in this build; downgraded to review_gate sidecars",
        )
    if raw == "sidecar_only":
        return "sidecar_only", "region-aware output is diagnostic sidecar only"
    if raw == "review_gate":
        return "review_gate", "region-aware output requires review before automatic localized compare"
    return "off", "region-aware multi-frame output disabled"


def _auto_region_compare_requested() -> bool:
    return os.getenv(AUTO_REGION_COMPARE_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _resolve_region_local_default_enablement(
    detection_results: Sequence[Any],
    match_summaries: Sequence[Any],
) -> dict[str, Any]:
    """Return the guarded R10 default-enable decision.

    R10 must not silently turn region-local primary compare on until a real
    pilot summary has passed. Operators can roll back by setting
    ``DRAWING_COMPARE_REGION_LOCAL_DEFAULT=off``; explicit opt-in via
    ``DRAWING_COMPARE_AUTO_REGION_COMPARE=1`` remains unchanged.
    """

    raw_mode = os.getenv(REGION_LOCAL_DEFAULT_ENV, "pilot_passed").strip().lower()
    if raw_mode in {"0", "false", "no"}:
        raw_mode = "off"
    if raw_mode not in REGION_LOCAL_DEFAULT_MODES:
        return {
            "mode": raw_mode,
            "status": "disabled",
            "automatic_localized_compare_requested": False,
            "gate_reasons": [
                f"invalid {REGION_LOCAL_DEFAULT_ENV} value; use off or pilot_passed"
            ],
        }
    if raw_mode == "off":
        return {
            "mode": raw_mode,
            "status": "disabled",
            "automatic_localized_compare_requested": False,
            "gate_reasons": ["region-local default enablement disabled by feature flag"],
        }

    pilot_path_raw = os.getenv(REGION_PILOT_SUMMARY_ENV, "").strip()
    if not pilot_path_raw:
        return {
            "mode": raw_mode,
            "status": "waiting_for_pilot_summary",
            "automatic_localized_compare_requested": False,
            "gate_reasons": [f"{REGION_PILOT_SUMMARY_ENV} is not configured"],
        }
    pilot_path = Path(pilot_path_raw)
    pilot_status, pilot_reasons = _pilot_summary_acceptance_status(pilot_path)
    if pilot_status != "passed":
        return {
            "mode": raw_mode,
            "status": "pilot_not_passed",
            "pilot_summary_json": str(pilot_path),
            "automatic_localized_compare_requested": False,
            "gate_reasons": pilot_reasons,
        }

    high_confidence, gate_reasons = _high_confidence_region_local_default_gate(
        detection_results,
        match_summaries,
    )
    return {
        "mode": raw_mode,
        "status": "enabled" if high_confidence else "review_required",
        "pilot_summary_json": str(pilot_path),
        "automatic_localized_compare_requested": high_confidence,
        "gate_reasons": gate_reasons,
    }


def _pilot_summary_acceptance_status(path: Path) -> tuple[str, list[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "missing", [f"pilot summary not found: {path}"]
    except Exception as exc:  # noqa: BLE001
        return "invalid", [f"pilot summary cannot be read: {exc}"]
    if not isinstance(payload, Mapping):
        return "invalid", ["pilot summary must be a JSON object"]
    if str(payload.get("mode") or "") != "multi_detail_region_compare_pilot":
        return "invalid", ["pilot summary mode is not multi_detail_region_compare_pilot"]
    try:
        case_count = int(payload.get("case_count") or 0)
    except (TypeError, ValueError):
        case_count = 0
    if case_count <= 0:
        return "invalid", ["pilot summary has no pilot cases"]
    if str(payload.get("overall_status") or "") != "passed":
        return "failed", ["pilot summary overall_status is not passed"]
    acceptance = payload.get("acceptance")
    if not isinstance(acceptance, Mapping):
        return "invalid", ["pilot summary acceptance block is missing"]
    missing = sorted(REQUIRED_REGION_PILOT_ACCEPTANCE_KEYS - set(acceptance.keys()))
    if missing:
        return "failed", [f"pilot acceptance missing: {', '.join(missing)}"]
    failed = [
        str(name)
        for name, item in acceptance.items()
        if not isinstance(item, Mapping) or str(item.get("status") or "") != "passed"
    ]
    if failed:
        return "failed", [f"pilot acceptance not passed: {', '.join(failed)}"]
    return "passed", []


def _high_confidence_region_local_default_gate(
    detection_results: Sequence[Any],
    match_summaries: Sequence[Any],
) -> tuple[bool, list[str]]:
    gate_reasons: list[str] = []
    if not detection_results or not match_summaries:
        return False, ["region artifacts are missing"]

    region_counts: list[int] = []
    whole_modelspace_count = 0
    for result in detection_results:
        regions = list(getattr(result, "regions", []) or [])
        non_whole = [
            region
            for region in regions
            if str(getattr(region, "detection_method", "")) != "whole_modelspace"
        ]
        region_counts.append(len(non_whole))
        whole_modelspace_count += len(regions) - len(non_whole)
    if not region_counts or any(count < 2 for count in region_counts):
        gate_reasons.append("single-detail or incomplete multi-detail detection; kept global compare")
    if whole_modelspace_count:
        gate_reasons.append("whole-modelspace fallback requires manual review")

    approved_count = 0
    review_required_count = 0
    unmatched_count = 0
    for summary in match_summaries:
        approved_count += int(getattr(summary, "auto_matched_count", 0) or 0)
        approved_count += int(getattr(summary, "manual_matched_count", 0) or 0)
        review_required_count += int(getattr(summary, "review_required_count", 0) or 0)
        unmatched_count += int(getattr(summary, "unmatched_before_count", 0) or 0)
        unmatched_count += int(getattr(summary, "unmatched_after_count", 0) or 0)
    if not approved_count:
        gate_reasons.append("no approved region matches")
    if review_required_count or unmatched_count:
        gate_reasons.append("ambiguous or unmatched regions require manual review")
    return not gate_reasons, gate_reasons


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


def _export_region_aware_artifacts(
    *,
    compare_summary: BatchCompareSummary,
    artifact_package: Any,
    artifact_dir: Path,
    dxf_cache_dir: Path,
    dwg_backend_mode: Optional[str] = None,
    allowed_dwg_license_ids: Sequence[str] = ("MIT", "INTERNAL"),
    user_converter_path: Optional[Union[str, Path]] = None,
    user_conversion_args: Sequence[str] = tuple(),
    user_conversion_timeout_seconds: Optional[float] = None,
    dwg_conversion_cache_dir: Optional[Union[str, Path]] = None,
) -> dict[str, str]:
    """Write region-aware side-car summaries for multi-detail drawings.

    These summaries are diagnostic/UX aids for now. They deliberately do not
    mutate the raw comparison result so existing review/export behaviour stays
    stable while the workbench gains enough context to explain added/deleted
    detail regions and future localized comparison decisions.
    """

    paths: dict[str, str] = {}
    feature_mode, fallback_reason = _resolve_multi_frame_mode()
    if feature_mode == "off":
        return {}
    try:
        from .detail_region_matcher import (
            RegionMatchSummary,
            match_sheet_regions,
            write_region_match_summary,
        )
        from .region_match_overrides import load_region_match_overrides
        from .localized_compare import (
            LocalizedCompareSummary,
            localize_change_zones,
            read_change_zones,
            serialize_localized_region_result,
            write_localized_region_compare_results,
            write_localized_compare_summary,
            compare_localized_region_entities,
        )
        from .region_compare_pipeline import (
            build_region_local_primary_change_zones,
            write_region_local_primary_change_zones,
        )
        from .region_viewer_package import export_region_viewer_package
        from .dxf_entity_extractor import DxfEntityExtractor
        from .pair_identity import candidate_pair_uuid
        from .sheet_region_detector import (
            RegionDetectionResult,
            detect_sheet_regions,
            write_region_detection_summary,
        )

        change_zones_value = artifact_package.output_paths.get("change_zones_json", "")
        change_zones_path = Path(change_zones_value) if change_zones_value else None
        zones = (
            read_change_zones(change_zones_path)
            if change_zones_path is not None and change_zones_path.is_file()
            else []
        )
        detection_results: list[RegionDetectionResult] = []
        match_summaries: list[RegionMatchSummary] = []
        localized_summaries: list[LocalizedCompareSummary] = []
        pair_contexts: list[dict[str, Any]] = []

        for item in getattr(compare_summary, "items", []) or []:
            candidate = getattr(item, "candidate", None)
            if not candidate or not getattr(candidate, "source_a", None) or not getattr(candidate, "source_b", None):
                continue
            if str(getattr(item, "status", "")).lower() not in {"completed", "success", "passed"}:
                continue
            pair_id = candidate_pair_uuid(candidate)
            before_result = detect_sheet_regions(
                candidate.source_a.path_obj,
                side="before",
                dxf_cache_dir=dxf_cache_dir,
            )
            after_result = detect_sheet_regions(
                candidate.source_b.path_obj,
                side="after",
                dxf_cache_dir=dxf_cache_dir,
            )
            detection_results.extend([before_result, after_result])
            region_overrides_path = artifact_dir.parent / "manual_region_matches.json"
            region_overrides = tuple()
            if region_overrides_path.exists():
                try:
                    region_overrides = load_region_match_overrides(
                        region_overrides_path,
                        pair_id=pair_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Failed to load manual region overrides from %s: %s",
                        region_overrides_path,
                        exc,
                    )
            match_summary = match_sheet_regions(
                before_result.regions,
                after_result.regions,
                pair_id=pair_id,
                overrides=region_overrides,
            )
            match_summaries.append(match_summary)
            localized_summary = localize_change_zones(
                zones,
                before_regions=before_result.regions,
                after_regions=after_result.regions,
                match_summary=match_summary,
                pair_id=pair_id,
            )
            localized_summaries.append(localized_summary)
            pair_contexts.append(
                {
                    "pair_id": pair_id,
                    "source_a": candidate.source_a.path_obj,
                    "source_b": candidate.source_b.path_obj,
                    "before_result": before_result,
                    "after_result": after_result,
                    "match_summary": match_summary,
                    "localized_summary": localized_summary,
                }
            )

        if not detection_results and not match_summaries and not localized_summaries:
            return {}
        status_path = artifact_dir / "region_aware_status.json"
        localized_total_zones = sum(summary.total_zones for summary in localized_summaries)
        localized_assigned_zones = sum(summary.assigned_zones for summary in localized_summaries)
        unassigned_zone_count = sum(
            summary.unassigned_zone_count for summary in localized_summaries
        )
        cross_region_zone_count = sum(
            summary.cross_region_zone_count for summary in localized_summaries
        )
        review_required_zone_count = sum(
            summary.review_required_zone_count for summary in localized_summaries
        )
        localized_gate_status = (
            "review_required"
            if any(summary.gate_status != "passed" for summary in localized_summaries)
            else "passed"
        )
        explicit_auto_region_requested = _auto_region_compare_requested()
        default_enablement = _resolve_region_local_default_enablement(
            detection_results,
            match_summaries,
        )
        auto_region_requested = explicit_auto_region_requested or bool(
            default_enablement.get("automatic_localized_compare_requested")
        )
        auto_region_request_source = (
            "explicit_feature_flag"
            if explicit_auto_region_requested
            else "default_pilot_passed"
            if auto_region_requested
            else "not_requested"
        )
        auto_region_payload: dict[str, Any] = {
            "automatic_localized_compare_requested": auto_region_requested,
            "automatic_localized_compare_enabled": False,
            "automatic_localized_compare_request_source": auto_region_request_source,
            "default_enablement": default_enablement,
            "status": "not_requested",
            "gate_reasons": [],
        }
        primary_region_payload: dict[str, Any] = {
            "primary_enabled": False,
            "status": "not_requested",
            "zone_count": 0,
            "gate_reasons": [],
        }
        if auto_region_requested:
            region_compare_contexts = _attach_region_compare_sources(
                pair_contexts,
                dxf_cache_dir,
                dwg_backend_mode=dwg_backend_mode,
                allowed_dwg_license_ids=tuple(allowed_dwg_license_ids or ()),
                user_converter_path=user_converter_path,
                user_conversion_args=tuple(user_conversion_args or ()),
                user_conversion_timeout_seconds=user_conversion_timeout_seconds,
                dwg_conversion_cache_dir=dwg_conversion_cache_dir,
            )
            dxf_extractor = DxfEntityExtractor()
            auto_region_payload = _build_auto_region_compare_payload(
                region_compare_contexts,
                extractor=dxf_extractor,
                compare_localized_region_entities=compare_localized_region_entities,
                serialize_localized_region_result=serialize_localized_region_result,
            )
            auto_region_payload["automatic_localized_compare_request_source"] = (
                auto_region_request_source
            )
            auto_region_payload["default_enablement"] = default_enablement
            auto_region_path = write_localized_region_compare_results(
                auto_region_payload,
                artifact_dir / "localized_region_compare_results.json",
            )
            paths["localized_region_compare_results_json"] = str(auto_region_path)
            primary_region_payload = build_region_local_primary_change_zones(
                region_compare_contexts,
                extractor=dxf_extractor,
            )
            primary_region_path = write_region_local_primary_change_zones(
                primary_region_payload,
                artifact_dir / "localized_change_zones_v2.json",
            )
            paths["localized_change_zones_v2_json"] = str(primary_region_path)
        _write_json_atomic(
            status_path,
            {
                "schema_version": 1,
                "feature_mode": feature_mode,
                "fallback_reason": fallback_reason,
                "automatic_localized_compare_requested": auto_region_requested,
                "automatic_localized_compare_enabled": bool(
                    auto_region_payload.get("automatic_localized_compare_enabled")
                ),
                "automatic_localized_compare_request_source": str(
                    auto_region_payload.get("automatic_localized_compare_request_source")
                    or "not_requested"
                ),
                "automatic_localized_compare_status": str(
                    auto_region_payload.get("status") or "not_requested"
                ),
                "region_default_enablement_status": str(
                    default_enablement.get("status") or "unknown"
                ),
                "region_default_enablement_gate_reasons": list(
                    default_enablement.get("gate_reasons") or []
                ),
                "region_default_enablement_pilot_summary_json": str(
                    default_enablement.get("pilot_summary_json") or ""
                ),
                "region_local_primary_enabled": bool(
                    primary_region_payload.get("primary_enabled")
                ),
                "region_local_primary_status": str(
                    primary_region_payload.get("status") or "not_requested"
                ),
                "region_local_primary_zone_count": int(
                    primary_region_payload.get("zone_count") or 0
                ),
                "pair_count": len(match_summaries),
                "region_detection_source_count": len(detection_results),
                "auto_matched_count": sum(
                    summary.auto_matched_count for summary in match_summaries
                ),
                "review_required_count": sum(
                    summary.review_required_count for summary in match_summaries
                ),
                "unmatched_before_count": sum(
                    summary.unmatched_before_count for summary in match_summaries
                ),
                "unmatched_after_count": sum(
                    summary.unmatched_after_count for summary in match_summaries
                ),
                "localized_total_zones": localized_total_zones,
                "localized_assigned_zones": localized_assigned_zones,
                "localized_assignment_rate": (
                    localized_assigned_zones / localized_total_zones
                    if localized_total_zones
                    else 0.0
                ),
                "unassigned_zone_count": unassigned_zone_count,
                "cross_region_zone_count": cross_region_zone_count,
                "review_required_zone_count": review_required_zone_count,
                "localized_gate_status": localized_gate_status,
            },
        )
        region_detection_path = write_region_detection_summary(
            detection_results,
            artifact_dir / "region_detection_summary.json",
        )
        region_match_path = write_region_match_summary(
            match_summaries,
            artifact_dir / "region_match_summary.json",
        )
        localized_path = write_localized_compare_summary(
            localized_summaries,
            artifact_dir / "localized_compare_summary.json",
        )
        validation_path = artifact_dir / "multi_frame_validation.json"
        _write_json_atomic(
            validation_path,
            _build_multi_frame_validation_payload(
                feature_mode=feature_mode,
                fallback_reason=fallback_reason,
                detection_results=detection_results,
                match_summaries=match_summaries,
                localized_summaries=localized_summaries,
                auto_region_payload=auto_region_payload,
            ),
        )
        paths.update(
            {
                "region_detection_summary_json": str(region_detection_path),
                "region_match_summary_json": str(region_match_path),
                "localized_compare_summary_json": str(localized_path),
                "region_aware_status_json": str(status_path),
                "multi_frame_validation_json": str(validation_path),
            }
        )
        if auto_region_requested and (artifact_dir / "localized_change_zones_v2.json").exists():
            region_viewer_path = export_region_viewer_package(artifact_dir)
            paths["region_viewer_manifest_json"] = str(region_viewer_path)
        _merge_artifact_manifest_paths(
            artifact_package.output_paths.get("artifact_manifest_json"),
            paths,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Region-aware artifact export failed: %s", exc, exc_info=True)
        try:
            artifact_package.warnings.append(f"region-aware export failed: {exc}")
        except Exception:
            pass
        warning_path = artifact_dir / "region_aware_warning.json"
        _write_json_atomic(
            warning_path,
            {
                "schema_version": 1,
                "status": "failed",
                "message": str(exc),
            },
        )
        paths["region_aware_warning_json"] = str(warning_path)
    return paths


def _build_auto_region_compare_payload(
    pair_contexts: Sequence[Mapping[str, Any]],
    *,
    extractor: Any,
    compare_localized_region_entities: Callable[..., Any],
    serialize_localized_region_result: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Build opt-in region-local DXF compare results without mutating main output."""

    pairs: list[dict[str, Any]] = []
    gate_reasons: list[str] = []
    compared_region_count = 0
    total_changes = 0
    unsupported_pair_count = 0
    skipped_pair_count = 0

    for context in pair_contexts:
        pair_id = str(context.get("pair_id") or "")
        original_source_a = Path(context.get("source_a") or "")
        original_source_b = Path(context.get("source_b") or "")
        source_a = Path(context.get("region_compare_source_a") or original_source_a)
        source_b = Path(context.get("region_compare_source_b") or original_source_b)
        match_summary = context.get("match_summary")
        localized_summary = context.get("localized_summary")
        before_result = context.get("before_result")
        after_result = context.get("after_result")
        pair_reasons: list[str] = []
        pair_results: list[dict[str, Any]] = []

        if source_a.suffix.lower() != ".dxf" or source_b.suffix.lower() != ".dxf":
            unsupported_pair_count += 1
            pair_reasons.append(
                "automatic region-local compare requires resolved DXF sources "
                f"(before={original_source_a.suffix.lower() or '<none>'}:"
                f"{context.get('region_compare_source_a_reason') or 'unresolved'}, "
                f"after={original_source_b.suffix.lower() or '<none>'}:"
                f"{context.get('region_compare_source_b_reason') or 'unresolved'})"
            )
        if getattr(match_summary, "review_required_count", 0):
            pair_reasons.append("one or more region matches require manual review")
        if getattr(match_summary, "unmatched_before_count", 0) or getattr(match_summary, "unmatched_after_count", 0):
            pair_reasons.append("one or more detected regions are unmatched")
        if getattr(localized_summary, "gate_status", "passed") != "passed":
            pair_reasons.extend(str(reason) for reason in getattr(localized_summary, "gate_reasons", ()) or ())
        if pair_reasons:
            skipped_pair_count += 1
            gate_reasons.extend(f"{pair_id}: {reason}" for reason in pair_reasons)
            pairs.append(
                {
                    "pair_id": pair_id,
                    "status": "skipped",
                    "source_a": str(original_source_a),
                    "source_b": str(original_source_b),
                    "region_compare_source_a": str(source_a),
                    "region_compare_source_b": str(source_b),
                    "gate_reasons": pair_reasons,
                    "region_result_count": 0,
                    "total_changes": 0,
                    "region_results": [],
                }
            )
            continue

        before_regions = {
            region.region_id: region
            for region in getattr(before_result, "regions", ()) or ()
        }
        after_regions = {
            region.region_id: region
            for region in getattr(after_result, "regions", ()) or ()
        }
        try:
            entities_before = extractor.extract_from_file(source_a)
            entities_after = extractor.extract_from_file(source_b)
        except Exception as exc:  # noqa: BLE001
            skipped_pair_count += 1
            reason = f"DXF entity extraction failed: {exc}"
            gate_reasons.append(f"{pair_id}: {reason}")
            pairs.append(
                {
                    "pair_id": pair_id,
                    "status": "skipped",
                    "source_a": str(original_source_a),
                    "source_b": str(original_source_b),
                    "region_compare_source_a": str(source_a),
                    "region_compare_source_b": str(source_b),
                    "gate_reasons": [reason],
                    "region_result_count": 0,
                    "total_changes": 0,
                    "region_results": [],
                }
            )
            continue

        for match in getattr(match_summary, "matches", ()) or ():
            if getattr(match, "status", "") not in {"auto_matched", "manual_matched"}:
                continue
            before_region = before_regions.get(getattr(match, "before_region_id", ""))
            after_region = after_regions.get(getattr(match, "after_region_id", ""))
            if before_region is None or after_region is None:
                continue
            region_result = compare_localized_region_entities(
                entities_before,
                entities_after,
                before_region=before_region,
                after_region=after_region,
                match_id=str(getattr(match, "match_id", "")),
            )
            serialized = serialize_localized_region_result(
                region_result,
                match_id=str(getattr(match, "match_id", "")),
                before_region=before_region,
                after_region=after_region,
            )
            pair_results.append(serialized)
            compared_region_count += 1
            total_changes += int(serialized.get("total_changes") or 0)

        pair_status = "passed" if pair_results else "skipped"
        if not pair_results:
            skipped_pair_count += 1
            reason = "no approved region matches were eligible for localized compare"
            gate_reasons.append(f"{pair_id}: {reason}")
            pair_reasons.append(reason)
        pairs.append(
            {
                "pair_id": pair_id,
                "status": pair_status,
                "source_a": str(original_source_a),
                "source_b": str(original_source_b),
                "region_compare_source_a": str(source_a),
                "region_compare_source_b": str(source_b),
                "gate_reasons": pair_reasons,
                "region_result_count": len(pair_results),
                "total_changes": sum(int(item.get("total_changes") or 0) for item in pair_results),
                "region_results": pair_results,
            }
        )

    enabled = compared_region_count > 0 and not gate_reasons
    return {
        "schema_version": 1,
        "automatic_localized_compare_requested": True,
        "automatic_localized_compare_enabled": enabled,
        "status": "passed" if enabled else "review_required",
        "gate_reasons": gate_reasons,
        "pair_count": len(pair_contexts),
        "compared_region_count": compared_region_count,
        "unsupported_pair_count": unsupported_pair_count,
        "skipped_pair_count": skipped_pair_count,
        "total_changes": total_changes,
        "pairs": pairs,
    }


def _attach_region_compare_sources(
    pair_contexts: Sequence[Mapping[str, Any]],
    dxf_cache_dir: Path,
    *,
    dwg_backend_mode: Optional[str] = None,
    allowed_dwg_license_ids: Sequence[str] = ("MIT", "INTERNAL"),
    user_converter_path: Optional[Union[str, Path]] = None,
    user_conversion_args: Sequence[str] = tuple(),
    user_conversion_timeout_seconds: Optional[float] = None,
    dwg_conversion_cache_dir: Optional[Union[str, Path]] = None,
) -> list[dict[str, Any]]:
    """Resolve DWG inputs to cached DXFs only after region-local compare is gated on."""

    resolved_contexts: list[dict[str, Any]] = []
    for context in pair_contexts:
        source_a = Path(context.get("source_a") or "")
        source_b = Path(context.get("source_b") or "")
        region_compare_source_a, region_compare_source_a_reason = _resolve_region_compare_source(
            source_a,
            dxf_cache_dir,
            dwg_backend_mode=dwg_backend_mode,
            allowed_dwg_license_ids=tuple(allowed_dwg_license_ids or ()),
            user_converter_path=user_converter_path,
            user_conversion_args=tuple(user_conversion_args or ()),
            user_conversion_timeout_seconds=user_conversion_timeout_seconds,
            dwg_conversion_cache_dir=dwg_conversion_cache_dir,
        )
        region_compare_source_b, region_compare_source_b_reason = _resolve_region_compare_source(
            source_b,
            dxf_cache_dir,
            dwg_backend_mode=dwg_backend_mode,
            allowed_dwg_license_ids=tuple(allowed_dwg_license_ids or ()),
            user_converter_path=user_converter_path,
            user_conversion_args=tuple(user_conversion_args or ()),
            user_conversion_timeout_seconds=user_conversion_timeout_seconds,
            dwg_conversion_cache_dir=dwg_conversion_cache_dir,
        )
        enriched = dict(context)
        enriched.update(
            {
                "region_compare_source_a": region_compare_source_a,
                "region_compare_source_b": region_compare_source_b,
                "region_compare_source_a_reason": region_compare_source_a_reason,
                "region_compare_source_b_reason": region_compare_source_b_reason,
            }
        )
        resolved_contexts.append(enriched)
    return resolved_contexts


def _resolve_region_compare_source(
    source: Path,
    dxf_cache_dir: Path,
    *,
    dwg_backend_mode: Optional[str] = None,
    allowed_dwg_license_ids: Sequence[str] = ("MIT", "INTERNAL"),
    user_converter_path: Optional[Union[str, Path]] = None,
    user_conversion_args: Sequence[str] = tuple(),
    user_conversion_timeout_seconds: Optional[float] = None,
    dwg_conversion_cache_dir: Optional[Union[str, Path]] = None,
) -> tuple[Path, str]:
    """Return a DXF source path suitable for region-local entity extraction."""

    source = Path(source)
    suffix = source.suffix.lower()
    if suffix == ".dxf":
        return source, "direct_dxf"
    if suffix != ".dwg":
        return source, "unsupported_source_format"
    user_converter_dxf = _descriptor_user_converter_dxf(
        source,
        DescriptorBuildOptions(
            dwg_backend_mode=dwg_backend_mode,
            allowed_dwg_license_ids=tuple(allowed_dwg_license_ids or ()),
            user_converter_path=user_converter_path,
            user_conversion_args=tuple(user_conversion_args or ()),
            user_conversion_timeout_seconds=user_conversion_timeout_seconds,
            dwg_conversion_cache_dir=dwg_conversion_cache_dir or dxf_cache_dir,
        ),
    )
    if user_converter_dxf is not None:
        return user_converter_dxf, "user_converter_cached_dxf"
    descriptor_options = DescriptorBuildOptions(
        dwg_backend_mode=dwg_backend_mode,
        allowed_dwg_license_ids=tuple(allowed_dwg_license_ids or ()),
    )
    if _descriptor_uses_commercial_sdk(descriptor_options):
        return source, "commercial_sdk_canonical_dwg"
    try:
        from .dwg_differ import DwgDiffer

        resolved = DwgDiffer(dxf_cache_dir=dxf_cache_dir)._ensure_dxf(source)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to resolve DWG cached DXF for region compare %s: %s", source, exc)
        return source, f"dwg_to_dxf_failed:{exc.__class__.__name__}"
    return Path(resolved), "cached_dxf"


def _build_multi_frame_validation_payload(
    *,
    feature_mode: str,
    fallback_reason: str,
    detection_results: Sequence[Any],
    match_summaries: Sequence[Any],
    localized_summaries: Sequence[Any],
    auto_region_payload: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Return a hard-gate oriented summary for multi-frame review decisions."""

    total_zones = sum(int(getattr(summary, "total_zones", 0)) for summary in localized_summaries)
    assigned_zones = sum(int(getattr(summary, "assigned_zones", 0)) for summary in localized_summaries)
    unassigned_zones = sum(
        int(getattr(summary, "unassigned_zone_count", 0)) for summary in localized_summaries
    )
    cross_region_zones = sum(
        int(getattr(summary, "cross_region_zone_count", 0)) for summary in localized_summaries
    )
    review_required_zones = sum(
        int(getattr(summary, "review_required_zone_count", 0)) for summary in localized_summaries
    )
    match_review_required = sum(
        int(getattr(summary, "review_required_count", 0)) for summary in match_summaries
    )
    unmatched_before = sum(
        int(getattr(summary, "unmatched_before_count", 0)) for summary in match_summaries
    )
    unmatched_after = sum(
        int(getattr(summary, "unmatched_after_count", 0)) for summary in match_summaries
    )

    gate_reasons: list[str] = []
    if feature_mode != "review_gate":
        gate_reasons.append(f"feature mode is {feature_mode}")
    if unassigned_zones:
        gate_reasons.append("one or more change bboxes are outside detected detail regions")
    if cross_region_zones:
        gate_reasons.append("one or more changes span unmatched before/after regions")
    if review_required_zones:
        gate_reasons.append("one or more changed regions require manual review")
    if match_review_required:
        gate_reasons.append("one or more region matches require manual review")
    if unmatched_before or unmatched_after:
        gate_reasons.append("one or more detected regions are unmatched")
    auto_requested = bool(
        auto_region_payload
        and auto_region_payload.get("automatic_localized_compare_requested")
    )
    auto_enabled = bool(
        auto_region_payload
        and auto_region_payload.get("automatic_localized_compare_enabled")
    )
    auto_status = str(
        (auto_region_payload or {}).get("status") or "not_requested"
    )
    if auto_requested and auto_status != "passed":
        reasons = list((auto_region_payload or {}).get("gate_reasons") or [])
        gate_reasons.extend(reasons or ["automatic region-local compare did not pass"])

    pair_summaries = []
    for summary in localized_summaries:
        pair_summaries.append(
            {
                "pair_id": str(getattr(summary, "pair_id", "")),
                "total_zones": int(getattr(summary, "total_zones", 0)),
                "assigned_zones": int(getattr(summary, "assigned_zones", 0)),
                "assignment_rate": (
                    float(getattr(summary, "assigned_zones", 0))
                    / float(getattr(summary, "total_zones", 0))
                    if int(getattr(summary, "total_zones", 0))
                    else 0.0
                ),
                "unassigned_zone_count": int(getattr(summary, "unassigned_zone_count", 0)),
                "cross_region_zone_count": int(getattr(summary, "cross_region_zone_count", 0)),
                "review_required_zone_count": int(getattr(summary, "review_required_zone_count", 0)),
                "gate_status": str(getattr(summary, "gate_status", "passed")),
                "gate_reasons": list(getattr(summary, "gate_reasons", ())),
            }
        )

    return {
        "schema_version": 1,
        "feature_mode": feature_mode,
        "fallback_reason": fallback_reason,
        "automatic_localized_compare_requested": auto_requested,
        "automatic_localized_compare_enabled": auto_enabled,
        "automatic_localized_compare_status": auto_status,
        "automatic_localized_compare_compared_region_count": int(
            (auto_region_payload or {}).get("compared_region_count") or 0
        ),
        "gate_status": "review_required" if gate_reasons else "passed",
        "gate_reasons": gate_reasons,
        "detection_source_count": len(detection_results),
        "pair_count": len(match_summaries),
        "total_zones": total_zones,
        "assigned_zones": assigned_zones,
        "assignment_rate": assigned_zones / total_zones if total_zones else 0.0,
        "unassigned_zone_count": unassigned_zones,
        "cross_region_zone_count": cross_region_zones,
        "review_required_zone_count": review_required_zones,
        "match_review_required_count": match_review_required,
        "unmatched_before_count": unmatched_before,
        "unmatched_after_count": unmatched_after,
        "pairs": pair_summaries,
    }


def _merge_artifact_manifest_paths(
    manifest_path: Any,
    output_paths: dict[str, str],
) -> None:
    if not manifest_path or not output_paths:
        return
    path = Path(manifest_path)
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    current = payload.setdefault("output_paths", {})
    current.update(output_paths)
    _write_json_atomic(path, payload)


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
