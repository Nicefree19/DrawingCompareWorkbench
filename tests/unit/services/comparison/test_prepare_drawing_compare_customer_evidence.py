"""Tests for customer-grade Drawing Compare evidence manifest preparation."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

from scripts import prepare_drawing_compare_customer_evidence as prepare
from src.services.comparison.visual_asset import build_visual_asset_cache_key


def _write_validation_summary(
    path: Path,
    *,
    kind: str,
    completed_pairs: int,
    queue_summary: str = "",
    cad_pdf_blocked_pairs: int = 0,
    review_truth_rows: int = 0,
    leak_count: int = 0,
    total_s: float = 10.0,
    include_sharable_audit: bool = True,
    include_ai_policy: bool = True,
    review_queue_mode: str = "structural_core",
    top_per_drawing: int = 5,
    zone_crop_count: int = 2,
    zone_cold_p95_ms: float = 500.0,
    zone_hit_p95_ms: float = 25.0,
    source_extensions: tuple[str, str] | None = None,
    include_workbench_acceptance_summary: bool = True,
    workbench_item9b: bool = True,
    workbench_item9c: bool = True,
    cad_block_text_no_expand: bool = True,
    forced_tile_eviction: bool = False,
    tile_cache_mb: float = 0.25,
    include_visual_asset_manifest: bool = True,
    visual_asset_nonblank_status: str = "passed",
) -> None:
    if source_extensions is None:
        source_extensions = ("dwg", "dxf") if kind == "cad" else (kind, kind)
    path.mkdir(parents=True, exist_ok=True)
    summary = {
        "input": {},
        "files": {
            "a_kind_counts": {kind: 1},
            "b_kind_counts": {kind: 1},
        },
        "matching": {"cad_pdf_blocked_pairs": cad_pdf_blocked_pairs},
        "comparison": {"completed_pairs": completed_pairs},
        "outputs": {
            "review_dashboard_json": "change_artifacts/review_dashboard.json",
            "viewer_manifest_json": "viewer/viewer_manifest.json",
        },
        "timings": {"total_s": total_s},
        "change_artifacts": {
            "artifacts": [
                {
                    "source_a": f"<redacted>/before.{source_extensions[0]}",
                    "source_b": f"<redacted>/after.{source_extensions[1]}",
                }
            ]
            if completed_pairs > 0
            else []
        },
        "review_dashboard": {
            "review_queue": {
                "mode": review_queue_mode,
                "top_per_drawing": top_per_drawing,
                "items": [
                    {
                        "category": "mixed",
                        "reason_ko": "구조 핵심 변경입니다.",
                        "change_summary_ko": queue_summary,
                        "detection_source": f"{kind}_entity",
                        "bbox_status": "exact",
                        "priority_rank": 1,
                    }
                ]
                if queue_summary
                else []
            }
        },
    }
    if kind == "cad" and completed_pairs > 0 and cad_block_text_no_expand:
        summary["input"]["cad_policy"] = {
            "expand_blocks": False,
            "block_text_detection": True,
        }
        if summary["review_dashboard"]["review_queue"]["items"]:
            summary["review_dashboard"]["review_queue"]["items"][0].update(
                {
                    "source_format": "cad",
                    "detection_source": "cad_entity",
                    "entity_types": ["ATTRIB"],
                    "change_summary_ko": f"{queue_summary} D13@100 -> D13@200",
                    "reason_ko": "블록 속성 텍스트의 @100 -> @200 변경입니다.",
                    "modified_count": 1,
                    "added_count": 0,
                    "deleted_count": 0,
                }
            )
    if completed_pairs > 0 and zone_crop_count >= 0:
        summary["viewer_perf_summary"] = {
            "zone_crop_count": zone_crop_count,
            "zone_crop_cold_ms": {"p95": zone_cold_p95_ms},
            "zone_crop_cache_hit_ms": {"p95": zone_hit_p95_ms},
        }
        summary["selected_zone_evidence"] = {
            "renders": [{"bbox_status": "exact"} for _ in range(max(1, zone_crop_count))]
        }
    if completed_pairs > 0:
        artifact_s = max(0.001, total_s - 3.0)
        summary["first_interactive_ready"] = {
            "schema_version": 1,
            "status": "passed" if total_s <= 600 else "failed",
            "profile": "standard",
            "speed_profile": False,
            "review_dashboard_ready_s": artifact_s,
            "first_top_issue_ready_s": artifact_s,
            "viewer_metadata_ready_s": min(total_s, 900.0),
            "thresholds": {
                "review_dashboard_ready_s": 600.0,
                "first_top_issue_ready_s": 600.0,
                "viewer_metadata_ready_s": 900.0,
            },
            "issues": [] if total_s <= 600 else ["review_dashboard_ready_s exceeds 600"],
        }
    if include_sharable_audit:
        summary["sharable_audit"] = {"leak_count": leak_count, "audited_at": "2026-05-11T00:00:00"}
    if include_ai_policy:
        summary["ai_policy"] = _ai_policy()
    if review_truth_rows:
        summary["review_ground_truth"] = {
            "rows": review_truth_rows,
            "details": [
                {
                    "category": "mixed",
                    "summary_contains": (
                        "member beam moved section dimension D13@100 D13@200 "
                        "SHD13@100 SHD13@200 grid structural text"
                    ),
                }
            ],
        }
    if forced_tile_eviction:
        byte_limit = int(tile_cache_mb * 1024 * 1024)
        summary["p5_g3_realset_gate"] = {
            "requested": True,
            "status": "passed",
            "failures": [],
            "evidence": {
                "tile_manifest": {
                    "status": "passed",
                    "require_eviction": True,
                    "evicted_pair_count": 2,
                    "min_evicted_pair_count": 1,
                    "evicted_estimated_bytes": 4096,
                    "min_evicted_estimated_bytes": 1,
                    "configured_tile_cache_mb": tile_cache_mb,
                    "tile_cache_env_mb": str(tile_cache_mb),
                    "byte_limit": byte_limit,
                    "retained_estimated_bytes": max(1, byte_limit // 2),
                    "stale_manifest_count": 0,
                    "missing_pair_payload_count": 0,
                    "orphan_payload_bytes": 0,
                    "max_orphan_payload_bytes": 0,
                }
            },
        }
    (path / "validation_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    if completed_pairs > 0:
        _write_visual_asset_manifest_fixture(
            path,
            include_manifest=include_visual_asset_manifest,
            nonblank_status=visual_asset_nonblank_status,
        )
    if cad_pdf_blocked_pairs:
        (path / "blocked_pairs.csv").write_text(
            "a_path,b_path,a_kind,b_kind,reason\n"
            "old.dwg,new.pdf,cad,pdf,CAD/PDF cross-family comparison is blocked\n",
            encoding="utf-8",
        )
    if completed_pairs > 0 and include_workbench_acceptance_summary:
        _write_workbench_acceptance_summary(
            path / "workbench_acceptance_summary.json",
            item9b=workbench_item9b,
            item9c=workbench_item9c,
        )


def _write_visual_asset_manifest_fixture(
    path: Path,
    *,
    include_manifest: bool = True,
    nonblank_status: str = "passed",
) -> None:
    viewer_dir = path / "viewer"
    viewer_dir.mkdir(parents=True, exist_ok=True)
    if not include_manifest:
        (viewer_dir / "viewer_manifest.json").write_text(
            json.dumps({"schema_version": 2, "visual_asset_manifest_paths": [], "pairs": []}),
            encoding="utf-8",
        )
        return

    manifest_rel = Path("viewer/visual_assets/S21-0001/after/source_pdf/visual_asset_manifest.json")
    manifest_ref = str(manifest_rel).replace("\\", "/")
    probe_rel = Path("viewer/visual_assets/S21-0001/after/source_pdf/nonblank_probe.json")
    probe_ref = str(probe_rel).replace("\\", "/")
    target_rel = Path("viewer/images/S21-0001_after.png")
    target_ref = str(target_rel).replace("\\", "/")
    target_path = path / target_rel
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(b"stable nonblank visual target")
    target_hash = hashlib.sha256(target_path.read_bytes()).hexdigest()
    source_hash = "test-source-hash"
    source_signature = {"schema_version": 1, "source_hash": source_hash}
    plot_profile_hash = "test-plot-profile"
    cache_key_hash = build_visual_asset_cache_key(
        source_hash=source_hash,
        source_signature=source_signature,
        backend_id="source_pdf",
        backend_version="1",
        license_id="customer_provided",
        plot_profile_hash=plot_profile_hash,
        page_index=0,
        dpi=80,
        page_size_pt=[612.0, 792.0],
        pixel_size=[680, 880],
        transform_quality="estimated",
    )
    probe_payload = {
        "schema_version": 2,
        "status": nonblank_status,
        "method": "pixel_nonblank_probe",
        "asset_path": "viewer/pages/S21-0001_after.pdf",
        "asset_hash": "test-asset-hash",
        "asset_size": 10,
        "probe_target_path": target_ref,
        "probe_target_hash": target_hash,
        "probe_target_size": target_path.stat().st_size,
        "source_hash": source_hash,
        "cache_key_hash": cache_key_hash,
        "page_index": 0,
        "dpi": 80,
        "pixel_width": 10,
        "pixel_height": 10,
        "mean": 200.0 if nonblank_status == "passed" else 255.0,
        "channel_ranges": [10, 10, 10] if nonblank_status == "passed" else [0, 0, 0],
        "extrema": [[0, 10], [0, 10], [0, 10]] if nonblank_status == "passed" else [[255, 255], [255, 255], [255, 255]],
        "nonblank": nonblank_status == "passed",
    }
    probe_payload["probe_hash"] = hashlib.sha256(
        json.dumps(probe_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    probe_path = path / probe_rel
    probe_path.parent.mkdir(parents=True, exist_ok=True)
    probe_path.write_text(json.dumps(probe_payload), encoding="utf-8")
    manifest_path = path / manifest_rel
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "visual_asset_id": "S21-0001:after:source_pdf",
                "source_path": "new.pdf",
                "asset_path": "viewer/pages/S21-0001_after.pdf",
                "asset_kind": "source_pdf",
                "status": "ready",
                "source_hash": source_hash,
                "source_signature": source_signature,
                "cache_key_hash": cache_key_hash,
                "plot_profile_hash": plot_profile_hash,
                "page_index": 0,
                "dpi": 80,
                "page_size_pt": [612.0, 792.0],
                "pixel_size": [680, 880],
                "visual_backend_id": "source_pdf",
                "visual_backend_version": "1",
                "visual_backend_license_id": "customer_provided",
                "visual_fidelity": "pdf_visual_background",
                "render_lifecycle": "ready",
                "transform_quality": "estimated",
                "nonblank_probe_status": nonblank_status,
                "metadata": {
                    "nonblank_probe": probe_ref,
                    "nonblank_probe_hash": probe_payload["probe_hash"],
                    "probe_target_path": target_ref,
                    "probe_target_hash": target_hash,
                    "probe_method": "pixel_nonblank_probe",
                },
            }
        ),
        encoding="utf-8",
    )
    (viewer_dir / "viewer_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "visual_asset_manifest_count": 1,
                "visual_asset_manifest_paths": [manifest_ref],
                "pairs": [{"pair_id": "S21-0001", "visual_asset_manifest_paths": [manifest_ref]}],
            }
        ),
        encoding="utf-8",
    )


def _ai_policy() -> dict:
    return {
        "schema_version": 1,
        "status": "passed",
        "ai_required": False,
        "embedding_optional": True,
        "llm_optional": True,
        "model_missing_handling": "warning",
        "warning_ko": "모델 없음 - 휴리스틱 분류만 사용",
        "heuristic_fallback_available": True,
        "heuristic_only": {"result_count": 1, "classifier_used": "heuristic"},
        "fallback_without_model": {
            "result_count": 1,
            "configured_embedding": True,
            "classifier_used": "heuristic",
        },
    }


def _write_truth_csv(path: Path) -> None:
    path.write_text(
        "drawing_label,category,summary_contains,source_format,detection_source,bbox_status,notes\n"
        "S1,mixed,member,cad,cad_entity,exact,member change\n"
        "S2,mixed,section dimension,pdf,pdf_text|pdf_ocr,exact|page_fallback,dimension change\n"
        "S3,mixed,D13@100 D13@200,pdf,pdf_visual|hybrid,exact|page_fallback,D13 spacing\n"
        "S4,mixed,SHD13@100 SHD13@200,cad,cad_entity,exact,SHD13 spacing\n"
        "S5,grid,grid,pdf,pdf_text|pdf_visual,exact|page_fallback,grid change\n"
        "S6,mixed,structural text,pdf,pdf_text|hybrid,exact|page_fallback,text change\n",
        encoding="utf-8",
    )
    _write_review_decision_truth_csv(path.parent / "review_decision_truth.csv")
    _write_dataset_strata_csv(path.parent / "dataset_strata.csv")
    _write_large_dwg_probe(path.parent / "large_dwg_probe.json")


def _write_review_decision_truth_csv(path: Path) -> None:
    buckets = list(prepare.STRUCTURAL_COVERAGE_TERMS)
    lines = [
        "pair_uuid,zone_id,drawing_label,structural_bucket,human_label,source_format,detection_source,bbox_status,notes"
    ]
    for index in range(24):
        bucket = buckets[index % len(buckets)]
        source = "cad" if index % 2 == 0 else "pdf"
        detection = "cad_entity" if source == "cad" else "pdf_text"
        lines.append(
            f"pair-{index:03d},zone-{index:03d},S{index:03d},{bucket},true_positive,{source},{detection},exact,reviewed"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_dataset_strata_csv(path: Path, *, rows: int = 21) -> None:
    sheet_types = ["plan", "section", "detail", "schedule_like"]
    lines = [
        "pair_uuid,drawing_label,format_pair,sheet_type,risk_class,large_dwg,block_text_case,negative_control,notes"
    ]
    for index in range(rows):
        if index < 8:
            format_pair = "dwg_dxf"
        elif index < 16:
            format_pair = "pdf_pdf"
        else:
            format_pair = "cad_pdf_blocked"
        risk = "raster_pdf" if index in {8, 9} else "standard"
        large = "true" if index in {0, 1} else "false"
        block = "true" if index in {0, 1} else "false"
        negative = "true" if index in {2, 3} else "false"
        lines.append(
            f"pair-{index:03d},S{index:03d},{format_pair},{sheet_types[index % len(sheet_types)]},"
            f"{risk},{large},{block},{negative},stratified"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_large_dwg_probe(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "elapsed_s": 55.0,
                "total": 100_000,
                "metadata": {
                    "change_zone_record_count": 100_000,
                    "change_records_in_memory": 10_000,
                    "large_drawing_mode": "active",
                    "change_zone_stream_complete": True,
                },
                "change_records_in_memory": 10_000,
                "stream_exists": True,
                "stream_bytes": 4096,
                "progress_event_count": 5,
                "progress_events_tail": [{"message": "DXF compare progress"}],
                "peak_rss_mb": 1024.0,
                "progress_max_gap_s": 2.0,
                "cancel_probe": {
                    "status": "passed",
                    "cancel_to_idle_s": 2.0,
                    "partial_outputs_cleaned": True,
                    "worker_processes_left": 0,
                },
            }
        ),
        encoding="utf-8",
    )


def _write_p5_g16_replay(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "benchmark_id": "p5_g16_real_corpus_replay",
                "profile": "real_corpus_artifact_replay",
                "status": "passed",
            }
        ),
        encoding="utf-8",
    )


def _write_p5_g22_gui_soak(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "benchmark_id": "p5_g22_actual_gui_soak",
                "profile": "actual_gui_customer_corpus_soak",
                "status": "passed",
                "summary": {
                    "native_resource_summary": {"measurement_available": True},
                    "worker_tree_summary": {"cleanup_ok": True, "orphan_worker_count": 0},
                },
            }
        ),
        encoding="utf-8",
    )


def _write_p5_g26_selection_latency(path: Path, *, failed_gate: str | None = None) -> None:
    gate_names = sorted(prepare.P5_G26_REQUIRED_GATES)
    path.write_text(
        json.dumps(
            {
                "schema_version": "workbench-gui-hotpath-benchmark/v1",
                "benchmark_id": "p5_g26_selection_latency_soak",
                "profile": "selection_latency_hard_gate",
                "status": "passed",
                "p5_g26_required_gate_names": gate_names,
                "p5_g26_contract": {
                    "wp_a_passed": failed_gate is None,
                    "wp_b_passed": failed_gate is None,
                    "has_zone_selection_evidence": True,
                    "zone_selection_background_work_count": 0,
                    "cad_to_pdf_hot_path_count": 0,
                },
                "gates": [
                    {
                        "name": name,
                        "passed": name != failed_gate,
                        "required": True,
                        "actual": 0,
                        "target": 0,
                        "detail": "",
                    }
                    for name in gate_names
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_p5_g27_selected_zone_crop(path: Path, *, failed_gate: str | None = None) -> None:
    gate_names = sorted(prepare.P5_G27_REQUIRED_GATES)
    path.write_text(
        json.dumps(
            {
                "schema_version": "workbench-gui-hotpath-benchmark/v1",
                "benchmark_id": "p5_g27_selected_zone_crop_soak",
                "profile": "selected_zone_crop_first_lifecycle",
                "status": "passed",
                "p5_g27_required_gate_names": gate_names,
                "p5_g27_contract": {
                    "crop_first_result_visible": failed_gate is None,
                    "crop_visible_before_vector_focus": failed_gate is None,
                    "vector_failure_does_not_clear_background": failed_gate is None,
                    "has_selected_zone_crop_first_evidence": True,
                    "worker_cleanup_ok": True,
                    "blank_selected_zone_count": 0,
                    "stale_result_visible_count": 0,
                    "cancel_without_visible_regression_count": 0,
                    "timeout_count": 0,
                    "fallback_missing_reason_count": 0,
                    "orphan_worker_count": 0,
                },
                "gates": [
                    {
                        "name": name,
                        "passed": name != failed_gate,
                        "required": True,
                        "actual": 0,
                        "target": 0,
                        "detail": "",
                    }
                    for name in gate_names
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_p5_g28_cache_plateau(
    path: Path,
    *,
    failed_gate: str | None = None,
    include_reason: bool = True,
    required_false_gate: str | None = None,
) -> None:
    gate_names = sorted(prepare.P5_G28_REQUIRED_GATES)
    reason_counts = {"byte_limit": 2} if include_reason else {}
    category_breakdown = {
        name: {
            "retained_bytes": 900,
            "byte_limit": 1000,
            "retained_entry_count": 3,
            "evicted_entry_count": 2,
            "evicted_estimated_bytes": 500,
            "orphan_bytes": 0,
            "orphan_entry_count": 0,
            "stale_entry_count": 0,
            "tail_slope_bytes_per_run": 0,
            "tail_slope_target_bytes_per_run": 0,
            "plateau_ok": True,
        }
        for name in ("display_list", "dxf_index", "visual_asset", "overlay", "spool")
    }
    path.write_text(
        json.dumps(
            {
                "schema_version": "workbench-gui-hotpath-benchmark/v1",
                "benchmark_id": "p5_g28_cache_plateau_soak",
                "profile": "tile_cache_plateau_lifecycle_seed",
                "status": "passed",
                "p5_g28_required_gate_names": gate_names,
                "p5_g28_contract": {
                    "passed": failed_gate is None and include_reason,
                    "tile_retention_completed": True,
                    "tile_retained_bytes": 900,
                    "tile_byte_limit": 1000,
                    "tile_byte_plateau_ok": True,
                    "tile_eviction_count": 2,
                    "tile_evicted_estimated_bytes": 500,
                    "tile_eviction_observed": True,
                    "tile_byte_limit_eviction_reason_present": include_reason,
                    "tile_orphan_bytes": 0,
                    "tile_orphan_pair_count": 0,
                    "tile_orphan_payloads_zero": True,
                    "tile_stale_manifest_count": 0,
                    "tile_stale_manifest_zero": True,
                    "tile_hot_pair_retained": True,
                    "tile_evicted_pair_cache_miss": True,
                    "single_entry_over_cap_count": 0,
                    "single_entry_over_cap_zero": True,
                    "prune_p95_ms": 20.0,
                    "prune_p95_target_ms": 500.0,
                    "event_loop_gap_p95_ms": 10.0,
                    "event_loop_gap_p95_target_ms": 150.0,
                    "event_loop_over_500ms_count": 0,
                    "event_loop_over_500ms_zero": True,
                    "eviction_reason_counts": reason_counts,
                    "cache_category_names": [
                        "display_list",
                        "dxf_index",
                        "visual_asset",
                        "overlay",
                        "spool",
                    ],
                    "cache_category_breakdown": category_breakdown,
                    "cache_category_breakdown_present": True,
                    "display_list_cache_plateau": True,
                    "dxf_index_cache_plateau": True,
                    "visual_asset_cache_plateau": True,
                    "overlay_cache_plateau": True,
                    "spool_namespace_plateau": True,
                    "cache_category_orphans_zero": True,
                    "cache_category_stale_entries_zero": True,
                    "cache_plateau_tail_slope_ok": True,
                    "cache_category_retained_bytes_total": 4500,
                    "cache_category_byte_limit_total": 5000,
                    "cache_category_evicted_entry_count": 10,
                    "cache_category_orphan_bytes_total": 0,
                    "cache_category_stale_entry_count": 0,
                    "cache_category_tail_slope_max_bytes_per_run": 0,
                },
                "gates": [
                    {
                        "name": name,
                        "passed": name != failed_gate
                        and (
                            include_reason
                            or name != "p5_g28_tile_cache_eviction_reason_present"
                        ),
                        "required": name != required_false_gate,
                        "actual": 0,
                        "target": 0,
                        "detail": "",
                    }
                    for name in gate_names
                ],
            }
        ),
        encoding="utf-8",
    )


def _run_prepare_ready_fixture(
    tmp_path: Path,
    *,
    review_decision_truth: Path | None = None,
    dataset_strata: Path | None = None,
    large_dwg_probe: Path | None = None,
    p5_g7_tile_eviction_proof_dirs: list[Path] | None = None,
    p5_g7_tile_eviction_release_manifests: list[Path] | None = None,
    require_p5_g7_tile_eviction_proof: bool = False,
    p5_g6_tile_cache_mb: float | None = None,
    p5_g16_benchmark_json: list[Path] | None = None,
    p5_g22_gui_soak_json: list[Path] | None = None,
    p5_g26_selection_latency_json: list[Path] | None = None,
    p5_g27_selected_zone_crop_json: list[Path] | None = None,
    p5_g28_cache_plateau_json: list[Path] | None = None,
    include_visual_asset_manifest: bool = True,
    visual_asset_nonblank_status: str = "passed",
) -> dict:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_summary="member beam moved structural text",
        include_visual_asset_manifest=include_visual_asset_manifest,
        visual_asset_nonblank_status=visual_asset_nonblank_status,
    )
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
        include_visual_asset_manifest=include_visual_asset_manifest,
        visual_asset_nonblank_status=visual_asset_nonblank_status,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")
    return prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
        review_decision_truth=review_decision_truth,
        dataset_strata=dataset_strata,
        large_dwg_probe=large_dwg_probe,
        p5_g7_tile_eviction_proof_dirs=p5_g7_tile_eviction_proof_dirs,
        p5_g7_tile_eviction_release_manifests=p5_g7_tile_eviction_release_manifests,
        require_p5_g7_tile_eviction_proof=require_p5_g7_tile_eviction_proof,
        p5_g6_tile_cache_mb=p5_g6_tile_cache_mb,
        p5_g16_benchmark_json=p5_g16_benchmark_json,
        p5_g22_gui_soak_json=p5_g22_gui_soak_json,
        p5_g26_selection_latency_json=p5_g26_selection_latency_json,
        p5_g27_selected_zone_crop_json=p5_g27_selected_zone_crop_json,
        p5_g28_cache_plateau_json=p5_g28_cache_plateau_json,
    )


def _write_operator_notes(
    path: Path,
    *,
    reviewer_role: str = "structural_review_lead",
    encoding: str = "utf-8",
) -> None:
    path.write_text(
        "Operator dry run passed.\n"
        f"reviewer_role: {reviewer_role}\n"
        "Operator notes:\n"
        "Reviewed S2401 zone Z-001 with synchronized Before/After zoom, Korean summary, "
        "confirmed/hold decisions, confirmed-only export, and path audit leak_count=0.\n"
        + "\n".join(f"- [x] {check_id}" for check_id in prepare.REQUIRED_OPERATOR_WORKFLOW_CHECKS),
        encoding=encoding,
    )


def _write_workbench_acceptance_summary(
    path: Path,
    *,
    item8b: bool = True,
    item9b: bool = True,
    item9c: bool = True,
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "passed" if item8b and item9b and item9c else "failed",
                "checks": [
                    {"name": "5. review_queue first-screen Top 이슈 + 점프/필터", "passed": True},
                    {"name": "8. Workbench confirmed 판정 → confirmed-only 구름마크 export", "passed": True},
                    {"name": "8b. Workbench 보류/오탐 판정 → confirmed-only export 제외", "passed": item8b},
                    {
                        "name": "9b. selected-zone Before/After synchronized focus/window",
                        "passed": item9b,
                    },
                    {
                        "name": "9c. selected-zone render subprocess timeout + responsive UI loop",
                        "passed": item9c,
                    },
                    {"name": "10. confirmed-only 검토 보고서 PDF 생성 + path leakage audit", "passed": True},
                ],
            }
        ),
        encoding="utf-8",
    )


def test_prepare_customer_evidence_manifest_ready(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_summary="member beam moved structural text",
    )
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    assert result["status"] == "ready"
    assert result["issues"] == []
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest_text.encode("ascii")
    (tmp_path / "sharable_path_audit_summary.json").read_text(encoding="utf-8").encode("ascii")
    manifest = json.loads(manifest_text)
    assert manifest["dataset_provenance"] == {
        "source_kind": "customer_grade",
        "source_description": "Approved customer-grade fixture for MVP exit.",
        "approval_status": "approved_for_mvp_exit",
        "approver": "structural-review-lead",
    }
    assert manifest["sheet_count"] == 21
    assert manifest["format_coverage"] == {
        "dwg_dxf": True,
        "pdf_pdf": True,
        "cad_pdf_blocked": True,
    }
    assert manifest["cad_policy_evidence"] == {
        "block_text_detection_without_expansion": True,
    }
    assert set(manifest["structural_coverage"]) == set(prepare.STRUCTURAL_COVERAGE_TERMS)
    assert manifest["ground_truth"]["row_count"] == 6
    assert set(manifest["operator_dry_run"]["workflow_checks"]) == set(
        prepare.REQUIRED_OPERATOR_WORKFLOW_CHECKS
    )
    assert manifest["path_leakage_audit"]["leak_count"] == 0
    assert manifest["selected_zone_performance"]["status"] == "passed"
    assert manifest["selected_zone_performance"]["completed_outputs"] == 2
    assert manifest["selected_zone_performance"]["telemetry_outputs"] == 2
    p5_g24 = manifest["p5_g24_visual_asset_policy"]
    assert p5_g24["status"] == "passed"
    assert p5_g24["completed_output_count"] == 2
    assert p5_g24["outputs_with_manifests"] == 2
    assert p5_g24["manifest_count"] == 2
    assert manifest["readiness"]["status"] == "ready"
    assert manifest["readiness"]["issue_count"] == 0
    assert manifest["readiness"]["issues"] == []
    assert result["summary"]["cad_policy_evidence"]["block_text_detection_without_expansion"] is True
    assert result["summary"]["p5_g24_visual_asset_policy"]["status"] == "passed"
    assert (tmp_path / "sharable_path_audit_summary.json").exists()


def test_prepare_customer_grade_requires_visual_asset_manifest_refs(tmp_path: Path) -> None:
    result = _run_prepare_ready_fixture(tmp_path, include_visual_asset_manifest=False)

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert result["status"] == "incomplete"
    assert manifest["p5_g24_visual_asset_policy"]["status"] == "failed"
    assert "p5_g24_visual_asset_policy.status must be passed for customer-grade visual asset evidence" in result["issues"]
    assert any("no visual asset manifest references" in issue for issue in result["issues"])
    assert manifest["readiness"]["status"] == "incomplete"


def test_prepare_customer_grade_rejects_failed_visual_asset_nonblank_probe(tmp_path: Path) -> None:
    result = _run_prepare_ready_fixture(tmp_path, visual_asset_nonblank_status="failed")

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert result["status"] == "incomplete"
    assert manifest["p5_g24_visual_asset_policy"]["status"] == "failed"
    assert "p5_g24_visual_asset_policy.status must be passed for customer-grade visual asset evidence" in result["issues"]
    assert any("nonblank_probe_status must be passed" in issue for issue in result["issues"])


def test_prepare_preserves_p5_g7_forced_tile_eviction_proof_without_counting_as_customer_corpus(
    tmp_path: Path,
) -> None:
    proof_dir = tmp_path / "p5_g7_tile_eviction_proof"
    release_manifest = tmp_path / "p5_g7_release_manifest.json"
    _write_validation_summary(
        proof_dir,
        kind="pdf",
        completed_pairs=3,
        queue_summary="forced tile eviction proof",
        forced_tile_eviction=True,
        tile_cache_mb=0.25,
    )
    release_manifest.write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "name": "realset_validation",
                        "env_overrides": {"DRAWING_COMPARE_TILE_CACHE_MB": "0.25"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = _run_prepare_ready_fixture(
        tmp_path,
        p5_g7_tile_eviction_proof_dirs=[proof_dir],
        p5_g7_tile_eviction_release_manifests=[release_manifest],
        require_p5_g7_tile_eviction_proof=True,
        p5_g6_tile_cache_mb=0.25,
    )

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    p5_g7 = manifest["p5_g7_forced_tile_eviction"]
    assert result["status"] == "ready"
    assert result["summary"]["completed_pairs"] == 21
    assert result["summary"]["sheet_count"] == 21
    assert p5_g7["status"] == "passed"
    assert p5_g7["required"] is True
    assert p5_g7["proof_count"] == 1
    assert p5_g7["proofs"][0]["configured_tile_cache_mb"] == 0.25
    assert p5_g7["proofs"][0]["evicted_pair_count"] == 2
    assert p5_g7["release_manifests"][0]["tile_cache_env_mb_values"] == [0.25]
    assert "p5_g7_tile_eviction_proof" in p5_g7["proofs"][0]["result_dir"]


def test_prepare_records_p5_g16_replay_artifact_for_final_audit_discovery(tmp_path: Path) -> None:
    replay_json = tmp_path / "pdf" / "p5_g16_real_corpus_replay.json"
    replay_json.parent.mkdir(parents=True)
    _write_p5_g16_replay(replay_json)

    result = _run_prepare_ready_fixture(
        tmp_path,
        p5_g16_benchmark_json=[replay_json],
    )

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    expected_ref = "pdf/p5_g16_real_corpus_replay.json"
    assert result["status"] == "ready"
    assert manifest["artifacts"]["p5_g16_real_corpus_replay_json"] == expected_ref
    assert manifest["artifacts"]["p5_g16_real_corpus_replay_jsons"] == [expected_ref]
    p5_g16 = manifest["performance_benchmarks"]["p5_g16_real_corpus_replay"]
    assert p5_g16["status"] == "passed"
    assert p5_g16["benchmark_json"] == expected_ref
    assert result["summary"]["p5_g16_real_corpus_replay"]["passed_count"] == 1


def test_prepare_records_p5_g22_gui_soak_artifact_for_final_audit_discovery(tmp_path: Path) -> None:
    soak_json = tmp_path / "pdf" / "p5_g22_actual_gui_soak.json"
    soak_json.parent.mkdir(parents=True)
    _write_p5_g22_gui_soak(soak_json)

    result = _run_prepare_ready_fixture(
        tmp_path,
        p5_g22_gui_soak_json=[soak_json],
    )

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    expected_ref = "pdf/p5_g22_actual_gui_soak.json"
    assert result["status"] == "ready"
    assert manifest["artifacts"]["p5_g22_actual_gui_soak_json"] == expected_ref
    assert manifest["artifacts"]["p5_g22_actual_gui_soak_jsons"] == [expected_ref]
    p5_g22 = manifest["performance_benchmarks"]["p5_g22_actual_gui_soak"]
    assert p5_g22["status"] == "passed"
    assert p5_g22["benchmark_json"] == expected_ref
    assert p5_g22["native_resource_summary"]["measurement_available"] is True
    assert p5_g22["worker_tree_summary"]["cleanup_ok"] is True
    assert p5_g22["shared_summary_count"] == 1
    assert result["summary"]["p5_g22_actual_gui_soak"]["passed_count"] == 1


def test_prepare_records_p5_g26_selection_latency_artifact_for_final_audit_discovery(tmp_path: Path) -> None:
    soak_json = tmp_path / "pdf" / "p5_g26_selection_latency_soak.json"
    soak_json.parent.mkdir(parents=True)
    _write_p5_g26_selection_latency(soak_json)

    result = _run_prepare_ready_fixture(
        tmp_path,
        p5_g26_selection_latency_json=[soak_json],
    )

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    expected_ref = "pdf/p5_g26_selection_latency_soak.json"
    assert result["status"] == "ready"
    assert manifest["artifacts"]["p5_g26_selection_latency_json"] == expected_ref
    assert manifest["artifacts"]["p5_g26_selection_latency_jsons"] == [expected_ref]
    p5_g26 = manifest["performance_benchmarks"]["p5_g26_selection_latency"]
    assert p5_g26["status"] == "passed"
    assert p5_g26["benchmark_json"] == expected_ref
    assert p5_g26["required_gate_count"] == len(prepare.P5_G26_REQUIRED_GATES)
    assert result["summary"]["p5_g26_selection_latency"]["passed_count"] == 1


def test_prepare_rejects_failed_p5_g26_selection_latency_gate(tmp_path: Path) -> None:
    soak_json = tmp_path / "pdf" / "p5_g26_selection_latency_soak.json"
    soak_json.parent.mkdir(parents=True)
    _write_p5_g26_selection_latency(
        soak_json,
        failed_gate="p5_g26_zone_selection_p95_ms",
    )

    result = _run_prepare_ready_fixture(
        tmp_path,
        p5_g26_selection_latency_json=[soak_json],
    )

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    p5_g26 = manifest["performance_benchmarks"]["p5_g26_selection_latency"]
    assert result["status"] == "incomplete"
    assert p5_g26["status"] == "failed"
    assert "p5_g26_selection_latency.status must be passed when provided" in result["issues"]
    assert any(
        "required gates failed: p5_g26_zone_selection_p95_ms" in issue
        for issue in result["issues"]
    )


def test_prepare_records_p5_g27_selected_zone_crop_artifact_for_final_audit_discovery(tmp_path: Path) -> None:
    soak_json = tmp_path / "pdf" / "p5_g27_selected_zone_crop_soak.json"
    soak_json.parent.mkdir(parents=True)
    _write_p5_g27_selected_zone_crop(soak_json)

    result = _run_prepare_ready_fixture(
        tmp_path,
        p5_g27_selected_zone_crop_json=[soak_json],
    )

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    expected_ref = "pdf/p5_g27_selected_zone_crop_soak.json"
    assert result["status"] == "ready"
    assert manifest["artifacts"]["p5_g27_selected_zone_crop_json"] == expected_ref
    assert manifest["artifacts"]["p5_g27_selected_zone_crop_jsons"] == [expected_ref]
    p5_g27 = manifest["performance_benchmarks"]["p5_g27_selected_zone_crop"]
    assert p5_g27["status"] == "passed"
    assert p5_g27["benchmark_json"] == expected_ref
    assert p5_g27["required_gate_count"] == len(prepare.P5_G27_REQUIRED_GATES)
    assert result["summary"]["p5_g27_selected_zone_crop"]["passed_count"] == 1


def test_prepare_rejects_failed_p5_g27_selected_zone_crop_gate(tmp_path: Path) -> None:
    soak_json = tmp_path / "pdf" / "p5_g27_selected_zone_crop_soak.json"
    soak_json.parent.mkdir(parents=True)
    _write_p5_g27_selected_zone_crop(
        soak_json,
        failed_gate="p5_g27_crop_visible_before_vector_focus",
    )

    result = _run_prepare_ready_fixture(
        tmp_path,
        p5_g27_selected_zone_crop_json=[soak_json],
    )

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    p5_g27 = manifest["performance_benchmarks"]["p5_g27_selected_zone_crop"]
    assert result["status"] == "incomplete"
    assert p5_g27["status"] == "failed"
    assert "p5_g27_selected_zone_crop.status must be passed when provided" in result["issues"]
    assert any(
        "required gates failed: p5_g27_crop_visible_before_vector_focus" in issue
        for issue in result["issues"]
    )


def test_prepare_records_p5_g28_cache_plateau_artifact_for_standalone_audit_discovery(
    tmp_path: Path,
) -> None:
    soak_json = tmp_path / "pdf" / "p5_g28_cache_plateau_soak.json"
    soak_json.parent.mkdir(parents=True)
    _write_p5_g28_cache_plateau(soak_json)

    result = _run_prepare_ready_fixture(
        tmp_path,
        p5_g28_cache_plateau_json=[soak_json],
    )

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    expected_ref = "pdf/p5_g28_cache_plateau_soak.json"
    assert result["status"] == "ready"
    assert manifest["artifacts"]["p5_g28_cache_plateau_json"] == expected_ref
    assert manifest["artifacts"]["p5_g28_cache_plateau_jsons"] == [expected_ref]
    p5_g28 = manifest["performance_benchmarks"]["p5_g28_cache_plateau"]
    assert p5_g28["status"] == "passed"
    assert p5_g28["required_for_customer_grade"] is False
    assert p5_g28["benchmark_json"] == expected_ref
    assert p5_g28["required_gate_count"] == len(prepare.P5_G28_REQUIRED_GATES)
    assert p5_g28["artifacts"][0]["tile_byte_limit_eviction_reason_count"] == 2
    assert result["summary"]["p5_g28_cache_plateau"]["passed_count"] == 1
    assert "p5_g28_cache_plateau_1" in manifest["provenance"]["input_file_hashes"]


def test_prepare_rejects_p5_g28_cache_plateau_without_eviction_reason(
    tmp_path: Path,
) -> None:
    soak_json = tmp_path / "pdf" / "p5_g28_cache_plateau_soak.json"
    soak_json.parent.mkdir(parents=True)
    _write_p5_g28_cache_plateau(soak_json, include_reason=False)

    result = _run_prepare_ready_fixture(
        tmp_path,
        p5_g28_cache_plateau_json=[soak_json],
    )

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    p5_g28 = manifest["performance_benchmarks"]["p5_g28_cache_plateau"]
    assert result["status"] == "incomplete"
    assert p5_g28["status"] == "failed"
    assert "p5_g28_cache_plateau.status must be passed when provided" in result["issues"]
    assert any(
        "p5_g28_contract.eviction_reason_counts.byte_limit must be > 0" in issue
        for issue in result["issues"]
    )
    assert any(
        "required gates failed: p5_g28_tile_cache_eviction_reason_present" in issue
        for issue in result["issues"]
    )


def test_prepare_rejects_p5_g28_without_cache_category_breakdown(
    tmp_path: Path,
) -> None:
    soak_json = tmp_path / "pdf" / "p5_g28_cache_plateau_soak.json"
    soak_json.parent.mkdir(parents=True)
    _write_p5_g28_cache_plateau(soak_json)
    payload = json.loads(soak_json.read_text(encoding="utf-8"))
    contract = payload["p5_g28_contract"]
    contract.pop("cache_category_breakdown")
    contract["cache_category_breakdown_present"] = False
    contract["passed"] = False
    payload["status"] = "failed"
    for gate in payload["gates"]:
        if gate["name"] == "p5_g28_cache_category_breakdown_present":
            gate["passed"] = False
    soak_json.write_text(json.dumps(payload), encoding="utf-8")

    result = _run_prepare_ready_fixture(
        tmp_path,
        p5_g28_cache_plateau_json=[soak_json],
    )

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    p5_g28 = manifest["performance_benchmarks"]["p5_g28_cache_plateau"]
    assert result["status"] == "incomplete"
    assert p5_g28["status"] == "failed"
    assert any(
        "p5_g28_contract.cache_category_breakdown missing" in issue
        for issue in p5_g28["artifacts"][0]["issues"]
    )


def test_prepare_rejects_p5_g28_invalid_live_cache_counters(tmp_path: Path) -> None:
    soak_json = tmp_path / "pdf" / "p5_g28_cache_plateau_soak.json"
    soak_json.parent.mkdir(parents=True, exist_ok=True)
    _write_p5_g28_cache_plateau(soak_json)
    payload = json.loads(soak_json.read_text(encoding="utf-8"))
    payload["p5_g28_contract"]["live_cache_counters"] = {
        "supplied": True,
        "source_count": 1,
        "observed_category_count": 1,
        "passed": False,
        "within_limits": False,
        "invalid_counter_count": 1,
        "tail_slope_ok": False,
        "tail_slope_max_bytes_per_run": 100,
        "tail_slope_target_bytes_per_run": 0,
        "tail_slope_invalid_category_count": 1,
        "issues": ["display_list: retained_bytes must be <= byte_limit"],
        "categories": {
            "display_list": {
                "observed": True,
                "sample_count": 2,
                "retained_bytes": 2000,
                "byte_limit": 1000,
                "eviction_count": 0,
                "evicted_estimated_bytes": 0,
                "tail_slope_ok": False,
                "tail_slope_bytes_per_run": 100,
                "tail_slope_target_bytes_per_run": 0,
                "within_limit": False,
            }
        },
    }
    payload["p5_g28_contract"]["live_cache_counters_supplied"] = True
    payload["p5_g28_contract"]["live_cache_counters_source_count"] = 1
    payload["p5_g28_contract"]["live_cache_counters_observed_category_count"] = 1
    payload["p5_g28_contract"]["live_cache_counters_within_limits"] = False
    payload["p5_g28_contract"]["live_cache_counters_invalid_counter_count"] = 1
    soak_json.write_text(json.dumps(payload), encoding="utf-8")

    result = _run_prepare_ready_fixture(
        tmp_path,
        p5_g28_cache_plateau_json=[soak_json],
    )

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    p5_g28 = manifest["performance_benchmarks"]["p5_g28_cache_plateau"]
    assert p5_g28["status"] == "failed"
    assert p5_g28["artifacts"][0]["live_cache_counters_supplied"] is True
    assert p5_g28["artifacts"][0]["live_cache_counters_tail_slope_ok"] is False
    assert p5_g28["artifacts"][0]["live_cache_counters_tail_slope_max_bytes_per_run"] == 100
    assert any(
        "p5_g28_contract.live_cache_counters.passed must be true when supplied" in issue
        for issue in p5_g28["artifacts"][0]["issues"]
    )
    assert any(
        "p5_g28_contract.live_cache_counters.tail_slope_ok must be true when supplied"
        in issue
        for issue in p5_g28["artifacts"][0]["issues"]
    )


def test_prepare_rejects_mixed_p5_g28_cache_plateau_artifacts(
    tmp_path: Path,
) -> None:
    passing_json = tmp_path / "pdf" / "p5_g28_cache_plateau_soak.json"
    failing_json = tmp_path / "pdf" / "bad" / "p5_g28_cache_plateau_soak.json"
    passing_json.parent.mkdir(parents=True)
    failing_json.parent.mkdir(parents=True)
    _write_p5_g28_cache_plateau(passing_json)
    _write_p5_g28_cache_plateau(failing_json, include_reason=False)

    result = _run_prepare_ready_fixture(
        tmp_path,
        p5_g28_cache_plateau_json=[passing_json, failing_json],
    )

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    p5_g28 = manifest["performance_benchmarks"]["p5_g28_cache_plateau"]
    assert result["status"] == "incomplete"
    assert p5_g28["status"] == "failed"
    assert p5_g28["passed_count"] == 1
    assert p5_g28["artifact_count"] == 2
    assert "p5_g28_cache_plateau.status must be passed when provided" in result["issues"]


def test_prepare_rejects_p5_g28_required_gate_marked_not_required_but_failed(
    tmp_path: Path,
) -> None:
    soak_json = tmp_path / "pdf" / "p5_g28_cache_plateau_soak.json"
    soak_json.parent.mkdir(parents=True)
    _write_p5_g28_cache_plateau(
        soak_json,
        failed_gate="p5_g28_prune_p95_ms",
        required_false_gate="p5_g28_prune_p95_ms",
    )

    result = _run_prepare_ready_fixture(
        tmp_path,
        p5_g28_cache_plateau_json=[soak_json],
    )

    assert result["status"] == "incomplete"
    assert any(
        "required gates failed: p5_g28_prune_p95_ms" in issue
        for issue in result["issues"]
    )


def test_prepare_rejects_p5_g22_gui_soak_without_shared_summaries(tmp_path: Path) -> None:
    soak_json = tmp_path / "pdf" / "p5_g22_actual_gui_soak.json"
    soak_json.parent.mkdir(parents=True)
    soak_json.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "benchmark_id": "p5_g22_actual_gui_soak",
                "profile": "actual_gui_customer_corpus_soak",
                "status": "passed",
                "summary": {},
            }
        ),
        encoding="utf-8",
    )

    result = _run_prepare_ready_fixture(
        tmp_path,
        p5_g22_gui_soak_json=[soak_json],
    )

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    p5_g22 = manifest["performance_benchmarks"]["p5_g22_actual_gui_soak"]
    assert result["status"] == "incomplete"
    assert p5_g22["status"] == "failed"
    assert p5_g22["shared_summary_count"] == 0
    assert (
        "p5_g22_actual_gui_soak shared native/worker summaries are required for all provided artifacts"
        in result["issues"]
    )


def test_prepare_requires_p5_g7_forced_tile_eviction_when_requested(tmp_path: Path) -> None:
    result = _run_prepare_ready_fixture(
        tmp_path,
        require_p5_g7_tile_eviction_proof=True,
        p5_g6_tile_cache_mb=0.25,
    )

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert result["status"] == "incomplete"
    assert manifest["p5_g7_forced_tile_eviction"]["status"] == "failed"
    assert "p5_g7_forced_tile_eviction.status must be passed when required" in result["issues"]


def test_prepare_rejects_p5_g7_tile_cache_cap_mismatch(tmp_path: Path) -> None:
    proof_dir = tmp_path / "p5_g7_tile_eviction_proof"
    _write_validation_summary(
        proof_dir,
        kind="pdf",
        completed_pairs=3,
        queue_summary="forced tile eviction proof",
        forced_tile_eviction=True,
        tile_cache_mb=0.5,
    )

    result = _run_prepare_ready_fixture(
        tmp_path,
        p5_g7_tile_eviction_proof_dirs=[proof_dir],
        require_p5_g7_tile_eviction_proof=True,
        p5_g6_tile_cache_mb=0.25,
    )

    p5_g7 = result["summary"]["p5_g7_forced_tile_eviction"]
    assert result["status"] == "incomplete"
    assert p5_g7["status"] == "failed"
    assert "configured_tile_cache_mb=0.5 != 0.25" in "\n".join(p5_g7["issues"])
    assert "p5_g7_forced_tile_eviction.status must be passed when required" in result["issues"]


def test_prepare_rejects_p5_g7_proof_in_results_dir(tmp_path: Path) -> None:
    proof_dir = tmp_path / "p5_g7_tile_eviction_proof"
    _write_validation_summary(
        proof_dir,
        kind="pdf",
        completed_pairs=5,
        queue_summary="forced tile eviction proof",
        forced_tile_eviction=True,
        tile_cache_mb=0.25,
    )

    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(cad_dir, kind="cad", completed_pairs=1, queue_summary="member structural text")
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir, proof_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
        p5_g6_tile_cache_mb=0.25,
    )

    assert result["status"] == "incomplete"
    assert result["summary"]["completed_pairs"] == 26
    assert result["summary"]["p5_g7_forced_tile_eviction_results_dir_rejections"]
    assert any("must be passed via --p5-g7-tile-eviction-proof-dir" in issue for issue in result["issues"])


def test_prepare_customer_evidence_rejects_invalid_review_decision_truth_enum(tmp_path: Path) -> None:
    decision_csv = tmp_path / "custom_review_decision_truth.csv"
    _write_review_decision_truth_csv(decision_csv)
    decision_csv.write_text(
        decision_csv.read_text(encoding="utf-8").replace(",true_positive,", ",maybe,", 1),
        encoding="utf-8",
    )

    result = _run_prepare_ready_fixture(tmp_path, review_decision_truth=decision_csv)

    assert result["status"] == "incomplete"
    assert result["summary"]["review_decision_quality"]["status"] == "failed"
    assert any("invalid human_label maybe" in issue for issue in result["issues"])


def test_prepare_customer_evidence_rejects_dataset_strata_rows_exceeding_sheet_count(tmp_path: Path) -> None:
    strata_csv = tmp_path / "custom_dataset_strata.csv"
    _write_dataset_strata_csv(strata_csv, rows=22)

    result = _run_prepare_ready_fixture(tmp_path, dataset_strata=strata_csv)

    assert result["status"] == "incomplete"
    assert result["summary"]["dataset_strata"]["status"] == "failed"
    assert any("dataset_strata rows 22 must equal sheet_count 21" in issue for issue in result["issues"])


def test_prepare_customer_evidence_rejects_template_review_decision_truth_path(tmp_path: Path) -> None:
    decision_csv = tmp_path / "review_decision_truth_template.csv"
    _write_review_decision_truth_csv(decision_csv)

    result = _run_prepare_ready_fixture(tmp_path, review_decision_truth=decision_csv)

    assert result["status"] == "incomplete"
    assert any("review_decision_truth CSV must be a customer-approved evidence artifact" in issue for issue in result["issues"])


def test_prepare_customer_evidence_writes_relative_artifact_refs(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    evidence_dir = tmp_path / "evidence"
    manifest_path = evidence_dir / "customer_evidence_manifest.json"
    truth_csv = evidence_dir / "review_ground_truth.csv"
    notes = evidence_dir / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_summary="member beam moved structural text",
    )
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    evidence_dir.mkdir()
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    assert result["status"] == "ready"
    manifest_text = manifest_path.read_text(encoding="utf-8")
    audit_text = (evidence_dir / "sharable_path_audit_summary.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in manifest_text
    assert str(tmp_path) not in audit_text
    assert "C:\\" not in manifest_text
    assert "C:\\" not in audit_text
    manifest = json.loads(manifest_text)
    artifact_ref = manifest["operator_dry_run"]["artifacts"]["confirmed_export_artifact"]
    assert artifact_ref.startswith("..")


def test_prepare_customer_evidence_requires_cad_block_text_no_expand_evidence(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_summary="member beam moved structural text",
        cad_block_text_no_expand=False,
    )
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result["status"] == "incomplete"
    assert manifest["readiness"]["status"] == "incomplete"
    assert manifest["cad_policy_evidence"]["block_text_detection_without_expansion"] is False
    assert (
        "cad_policy_evidence.block_text_detection_without_expansion is missing from audited outputs"
        in result["issues"]
    )


def test_prepare_customer_evidence_requires_approved_ground_truth(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_summary="member beam moved structural text",
    )
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="reviewed",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result["status"] == "incomplete"
    assert manifest["ground_truth"]["status"] == "reviewed"
    assert "ground_truth.status must be approved" in result["issues"]


def test_prepare_customer_evidence_accepts_korean_structural_reviewer_role(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_summary="member beam moved structural text",
    )
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes, reviewer_role="구조 검토 책임자")
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="구조 검토 책임자",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    assert result["status"] == "ready"
    assert result["issues"] == []


def test_prepare_customer_evidence_accepts_utf8_bom_operator_notes(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(cad_dir, kind="cad", completed_pairs=1, queue_summary="member structural text")
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes, encoding="utf-8-sig")
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="utf8-bom-notes",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    assert result["status"] == "ready"
    assert result["issues"] == []


def test_prepare_customer_evidence_rejects_truth_csv_missing_required_schema(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_summary="member beam moved structural text",
    )
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    truth_csv.write_text("drawing_label,category,summary_contains\nS1,mixed,D13@100\n", encoding="utf-8")
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    assert result["status"] == "incomplete"
    assert any("review_ground_truth CSV missing required columns" in issue for issue in result["issues"])


def test_prepare_customer_evidence_rejects_copied_truth_template_example_rows(
    tmp_path: Path,
) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_summary="member beam moved structural text",
    )
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    truth_csv.write_text(
        "drawing_label,category,summary_contains,source_format,detection_source,bbox_status,notes\n"
        "S-001,member|mixed,BEAM;added,cad,cad_entity,exact,member add/delete/move example\n",
        encoding="utf-8",
    )
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    assert result["status"] == "incomplete"
    assert "template/example marker" in "\n".join(result["issues"])


def test_prepare_customer_evidence_requires_structural_review_lead_role(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_summary="member beam moved structural text",
    )
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="generic-operator-role",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    assert result["status"] == "incomplete"
    assert any("operator reviewer role must be a structural review lead/team lead role" in issue for issue in result["issues"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["readiness"]["status"] == "incomplete"


def test_prepare_customer_evidence_requires_matching_reviewer_role_in_notes(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_summary="member beam moved structural text",
    )
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    notes.write_text(
        "Operator dry run passed.\n"
        "Reviewed by structural_review_lead.\n"
        "Operator notes:\n"
        "Reviewed S2401 zone Z-001 with synchronized Before/After zoom, Korean summary, "
        "confirmed-only export, and path audit leak_count=0.\n"
        + "\n".join(f"- [x] {check_id}" for check_id in prepare.REQUIRED_OPERATOR_WORKFLOW_CHECKS),
        encoding="utf-8",
    )
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="missing-role-in-notes",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    assert result["status"] == "incomplete"
    assert any("operator notes file must include matching structural reviewer role" in issue for issue in result["issues"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["readiness"]["status"] == "incomplete"


def test_prepare_customer_evidence_requires_substantive_operator_notes(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_summary="member beam moved structural text",
    )
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    notes.write_text(
        "Operator dry run passed.\n"
        "reviewer_role: structural_review_lead\n"
        "Operator notes:\n"
        + "\n".join(f"- [x] {check_id}" for check_id in prepare.REQUIRED_OPERATOR_WORKFLOW_CHECKS),
        encoding="utf-8",
    )
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="checklist-only-notes",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    assert result["status"] == "incomplete"
    assert any("substantive dry-run review notes" in issue for issue in result["issues"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["readiness"]["status"] == "incomplete"


def test_prepare_customer_evidence_rejects_template_truth_and_operator_notes(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth_template.csv"
    notes = tmp_path / "operator_dry_run_checklist_template.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_summary="member beam moved structural text",
    )
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    issues = "\n".join(result["issues"])
    assert result["status"] == "incomplete"
    assert "review_ground_truth CSV must be a customer-approved evidence artifact" in issues
    assert "operator notes file must be a completed operator dry-run artifact" in issues
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["readiness"]["status"] == "incomplete"
    assert manifest["readiness"]["issue_count"] == len(result["issues"])
    assert manifest["readiness"]["issues"] == result["issues"]
    assert "Do not use this manifest as final MVP completion evidence" in manifest["readiness"]["warning"]


def test_prepare_customer_evidence_rejects_request_sheet_as_operator_notes(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "customer_evidence_request_ko.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_summary="member beam moved structural text",
    )
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    issues = "\n".join(result["issues"])
    assert result["status"] == "incomplete"
    assert "operator notes file must be a completed operator dry-run artifact" in issues


def test_prepare_customer_evidence_requires_dwg_and_dxf_sources(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_summary="member beam moved structural text",
        source_extensions=("dwg", "dwg"),
    )
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result["status"] == "incomplete"
    assert manifest["format_coverage"]["dwg_dxf"] is False
    assert "format_coverage.dwg_dxf is missing from audited outputs" in result["issues"]


def test_prepare_customer_evidence_requires_pdf_sources_on_both_sides(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_summary="member beam moved structural text",
    )
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
        source_extensions=("pdf", "png"),
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result["status"] == "incomplete"
    assert manifest["format_coverage"]["pdf_pdf"] is False
    assert "format_coverage.pdf_pdf is missing from audited outputs" in result["issues"]


def test_prepare_customer_evidence_accepts_cad_pdf_block_csv_evidence(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(cad_dir, kind="cad", completed_pairs=1, queue_summary="member structural text")
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=0)
    (blocked_dir / "blocked_pairs.csv").write_text(
        "a_path,b_path,a_kind,b_kind,reason\n"
        "old.dwg,new.pdf,cad,pdf,CAD/PDF cross-family comparison is blocked\n",
        encoding="utf-8",
    )
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    assert result["status"] == "ready"
    assert result["summary"]["format_coverage"]["cad_pdf_blocked"] is True
    assert str(blocked_dir / "blocked_pairs.csv") in result["summary"]["cad_pdf_block_evidence"]


def test_prepare_customer_evidence_requires_clear_cad_pdf_block_reason(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(cad_dir, kind="cad", completed_pairs=1, queue_summary="member structural text")
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=0)
    (blocked_dir / "blocked_pairs.csv").write_text(
        "a_path,b_path,a_kind,b_kind,reason\n"
        "old.dwg,new.pdf,cad,pdf,manual review\n",
        encoding="utf-8",
    )
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result["status"] == "incomplete"
    assert manifest["format_coverage"]["cad_pdf_blocked"] is False
    assert "format_coverage.cad_pdf_blocked is missing from audited outputs" in result["issues"]


def test_prepare_customer_evidence_requires_workbench_acceptance_summary(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_summary="member structural text",
        include_workbench_acceptance_summary=False,
    )
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
        include_workbench_acceptance_summary=False,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result["status"] == "incomplete"
    assert manifest["workbench_acceptance"]["status"] == "failed"
    assert any("workbench_acceptance_summary.json" in issue for issue in result["issues"])


def test_prepare_customer_evidence_requires_selected_zone_sync_acceptance(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_summary="member structural text",
    )
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
        workbench_item9b=False,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result["status"] == "incomplete"
    assert manifest["workbench_acceptance"]["status"] == "failed"
    assert "9b." in manifest["workbench_acceptance"]["failures"][0]
    assert any("5/8/8b/9b/9c/10" in issue for issue in result["issues"])


def test_prepare_customer_evidence_requires_nonblocking_zone_render_acceptance(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_summary="member structural text",
    )
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
        workbench_item9c=False,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result["status"] == "incomplete"
    assert manifest["workbench_acceptance"]["status"] == "failed"
    assert "9c." in manifest["workbench_acceptance"]["failures"][0]
    assert any("5/8/8b/9b/9c/10" in issue for issue in result["issues"])


def test_prepare_customer_evidence_manifest_reports_incomplete_evidence(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    confirmed = tmp_path / "missing_confirmed.png"
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=1,
        queue_summary="D13@100 D13@200",
        review_truth_rows=1,
        leak_count=1,
    )
    truth_csv.write_text("drawing_label,category,summary_contains\nS1,mixed,D13@100\n", encoding="utf-8")

    result = prepare.prepare_manifest(
        result_dirs=[pdf_dir],
        out_path=manifest_path,
        dataset_id="incomplete",
        dataset_source_kind="customer_grade",
        dataset_source_description="Incomplete fixture.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="lead",
        validation_date="2026-05-11",
        ground_truth_owner="lead",
        review_ground_truth=truth_csv,
        ground_truth_status="reviewed",
        operator_reviewer_role="lead",
        operator_notes_file=None,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    assert result["status"] == "incomplete"
    assert any("format_coverage.dwg_dxf" in issue for issue in result["issues"])
    assert any("sheet_count must be >= 20" in issue for issue in result["issues"])
    assert any("operator notes_file or screenshots_dir is required" in issue for issue in result["issues"])
    assert any("confirmed export artifact not found" in issue for issue in result["issues"])
    assert any("path leakage audit must report leak_count=0" in issue for issue in result["issues"])


def test_prepare_customer_evidence_rejects_synthetic_provenance(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(cad_dir, kind="cad", completed_pairs=1, queue_summary="member structural text")
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="synthetic-gap-probe",
        dataset_source_kind="synthetic",
        dataset_source_description="Synthetic smoke bundle, not customer-grade evidence.",
        dataset_approval_status="synthetic_probe",
        dataset_approver="codex",
        validation_date="2026-05-11",
        ground_truth_owner="lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    assert result["status"] == "incomplete"
    assert any("dataset_provenance.source_kind" in issue for issue in result["issues"])
    assert any("dataset_provenance.approval_status" in issue for issue in result["issues"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["evidence_level"] == "synthetic"


def test_prepare_customer_evidence_requires_explicit_sharable_audit(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="member section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid structural text",
        review_truth_rows=1,
        include_sharable_audit=False,
    )
    truth_csv.write_text("drawing_label,category,summary_contains\nS1,mixed,D13@100\n", encoding="utf-8")
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[pdf_dir],
        out_path=manifest_path,
        dataset_id="missing-sharable-audit",
        dataset_source_kind="customer_grade",
        dataset_source_description="Missing sharable audit fixture.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="lead",
        validation_date="2026-05-11",
        ground_truth_owner="lead",
        review_ground_truth=truth_csv,
        ground_truth_status="reviewed",
        operator_reviewer_role="lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
        required_structural_coverage=["d13_spacing_change"],
    )

    assert result["status"] == "incomplete"
    assert result["summary"]["missing_sharable_audit"] == [str(pdf_dir)]
    assert any("sharable_audit.leak_count" in issue for issue in result["issues"])
    audit_payload = json.loads((tmp_path / "sharable_path_audit_summary.json").read_text(encoding="utf-8"))
    assert audit_payload["status"] == "failed"
    assert audit_payload["missing_sharable_audit"] == [str(pdf_dir)]


def test_prepare_customer_evidence_requires_operator_workflow_checklist(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(cad_dir, kind="cad", completed_pairs=1, queue_summary="member structural text")
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    notes.write_text("Operator dry run passed without explicit checklist ids.", encoding="utf-8")
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    assert result["status"] == "incomplete"
    assert any("operator workflow checklist missing" in issue for issue in result["issues"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["operator_dry_run"]["workflow_checks"] == []


def test_prepare_customer_evidence_rejects_unchecked_operator_checklist(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(cad_dir, kind="cad", completed_pairs=1, queue_summary="member structural text")
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    notes.write_text(
        "\n".join(f"- [ ] {check_id}" for check_id in prepare.REQUIRED_OPERATOR_WORKFLOW_CHECKS),
        encoding="utf-8",
    )
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    assert result["status"] == "incomplete"
    assert any("operator workflow checklist missing" in issue for issue in result["issues"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["operator_dry_run"]["workflow_checks"] == []


def test_prepare_customer_evidence_requires_confirmed_artifact_under_audited_output(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = tmp_path / "manual_copy" / "pair_confirmed.png"
    _write_validation_summary(cad_dir, kind="cad", completed_pairs=1, queue_summary="member structural text")
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    assert result["status"] == "incomplete"
    assert any("confirmed export artifact must be a *_confirmed.* file" in issue for issue in result["issues"])


def test_prepare_customer_evidence_rejects_confirmed_artifact_with_unsupported_extension(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.txt"
    _write_validation_summary(cad_dir, kind="cad", completed_pairs=1, queue_summary="member structural text")
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_text("not a cloud/report artifact", encoding="utf-8")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    assert result["status"] == "incomplete"
    assert any(".png/.pdf/.dxf" in issue for issue in result["issues"])


def test_prepare_customer_evidence_rejects_slow_first_review_ready(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(cad_dir, kind="cad", completed_pairs=1, queue_summary="member structural text")
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
        total_s=1_901.0,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    assert result["status"] == "incomplete"
    assert result["summary"]["first_review_ready"]["status"] == "failed"
    assert any("first review-ready total_s=1901.0 exceeds 1800.0" in issue for issue in result["issues"])


def test_prepare_customer_evidence_requires_top_review_queue_first(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="member section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid structural text",
        review_truth_rows=6,
        review_queue_mode="raw_counts",
        top_per_drawing=10,
    )
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[pdf_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    assert result["status"] == "incomplete"
    assert result["summary"]["first_review_ready"]["status"] == "failed"
    assert any("review_queue.mode must be structural_core" in issue for issue in result["issues"])
    assert any("review_queue.top_per_drawing must be 3..5" in issue for issue in result["issues"])


def test_prepare_customer_evidence_requires_selected_zone_perf_evidence(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_summary="member structural text",
        zone_crop_count=-1,
    )
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
        zone_cold_p95_ms=10_001.0,
        zone_hit_p95_ms=2_001.0,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    assert result["status"] == "incomplete"
    assert result["summary"]["selected_zone_performance"]["status"] == "failed"
    assert any("missing selected-zone telemetry" in issue for issue in result["issues"])
    assert any("selected-zone cold_p95=10001.0 exceeds 10000.0" in issue for issue in result["issues"])
    assert any("selected-zone cache_hit_p95=2001.0 exceeds 2000.0" in issue for issue in result["issues"])


def test_prepare_customer_evidence_rejects_over_50_sheet_set(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(cad_dir, kind="cad", completed_pairs=1, queue_summary="member structural text")
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=50,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    assert result["status"] == "incomplete"
    assert result["summary"]["sheet_count"] == 51
    assert result["summary"]["sheet_count_range"] == {"min": 20, "max": 50}
    assert any("sheet_count must be <= 50" in issue for issue in result["issues"])


def test_prepare_customer_evidence_requires_ai_policy_fallback(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    manifest_path = tmp_path / "customer_evidence_manifest.json"
    truth_csv = tmp_path / "review_ground_truth.csv"
    notes = tmp_path / "operator_notes.md"
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    _write_validation_summary(cad_dir, kind="cad", completed_pairs=1, queue_summary="member structural text")
    _write_validation_summary(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_summary="section dimension D13@100 D13@200 SHD13@100 SHD13@200 grid",
        review_truth_rows=6,
        include_ai_policy=False,
    )
    _write_validation_summary(blocked_dir, kind="cad", completed_pairs=0, cad_pdf_blocked_pairs=1)
    _write_truth_csv(truth_csv)
    _write_operator_notes(notes)
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    result = prepare.prepare_manifest(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        out_path=manifest_path,
        dataset_id="customer-grade-fixture",
        dataset_source_kind="customer_grade",
        dataset_source_description="Approved customer-grade fixture for MVP exit.",
        dataset_approval_status="approved_for_mvp_exit",
        dataset_approver="structural-review-lead",
        validation_date="2026-05-11",
        ground_truth_owner="structural-review-lead",
        review_ground_truth=truth_csv,
        ground_truth_status="approved",
        operator_reviewer_role="structural_review_lead",
        operator_notes_file=notes,
        operator_screenshots_dir=None,
        confirmed_export_artifact=confirmed,
    )

    assert result["status"] == "incomplete"
    assert result["summary"]["ai_policy"]["status"] == "failed"
    assert any("ai_policy must show optional AI" in issue for issue in result["issues"])


def test_prepare_customer_evidence_cli_returns_nonzero_for_incomplete(tmp_path: Path, capsys) -> None:
    pdf_dir = tmp_path / "pdf"
    truth_csv = tmp_path / "review_ground_truth.csv"
    _write_validation_summary(pdf_dir, kind="pdf", completed_pairs=1, queue_summary="D13@100", review_truth_rows=1)
    truth_csv.write_text("drawing_label,category,summary_contains\nS1,mixed,D13@100\n", encoding="utf-8")

    code = prepare.main(
        [
            "--results-dir",
            str(pdf_dir),
            "--out",
            str(tmp_path / "customer_evidence_manifest.json"),
            "--dataset-id",
            "incomplete",
            "--dataset-source-kind",
            "customer_grade",
            "--dataset-source-description",
            "Incomplete fixture.",
            "--dataset-approval-status",
            "approved_for_mvp_exit",
            "--dataset-approver",
            "lead",
            "--ground-truth-owner",
            "lead",
            "--review-ground-truth",
            str(truth_csv),
            "--operator-reviewer-role",
            "lead",
            "--confirmed-export-artifact",
            str(tmp_path / "missing.png"),
        ]
    )

    assert code == 1
    stdout = capsys.readouterr().out
    stdout.encode("ascii")
    assert json.loads(stdout)["status"] == "incomplete"
