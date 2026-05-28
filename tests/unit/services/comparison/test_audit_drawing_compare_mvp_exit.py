"""Tests for the Drawing Compare MVP exit audit script."""

from __future__ import annotations

import json
import hashlib
import zipfile
from pathlib import Path

from scripts import audit_drawing_compare_mvp_exit as audit
from src.services.comparison.visual_asset import build_visual_asset_cache_key


def _write_result(
    path: Path,
    *,
    kind: str,
    completed_pairs: int,
    queue_item: dict | None = None,
    blocked_pairs: int = 0,
    cad_pdf_blocked_pairs: int | None = None,
    structural_review_recall: float | None = None,
    review_ground_truth_rows: int = 1,
    leak_count: int = 0,
    zone_crop_count: int = 1,
    zone_cold_p95_ms: float = 500.0,
    zone_hit_p95_ms: float = 25.0,
    total_s: float = 10.0,
    include_sharable_audit: bool = True,
    preflight_checks: list[dict] | None = None,
    bbox_coordinate_space: str | None = None,
    include_ai_policy: bool = True,
    viewer_render_policy: str = "top-issues",
    review_queue_mode: str = "structural_core",
    top_per_drawing: int = 5,
    source_extensions: tuple[str, str] | None = None,
    include_workbench_acceptance_summary: bool = True,
    cad_policy: dict | None = None,
) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "_SUCCESS").write_text(
        json.dumps({"run_id": "run_test", "status": "completed"}),
        encoding="utf-8",
    )
    (path / "run_manifest.json").write_text(
        json.dumps({"run_id": "run_test", "status": "completed"}),
        encoding="utf-8",
    )
    queue_item = queue_item or {}
    if queue_item:
        queue_item.setdefault("bbox_status", "exact")
        queue_item.setdefault("priority_rank", 1)
    summary = {
        "output_dir": str(path),
        "input": {
            "viewer_render_policy": viewer_render_policy,
        },
        "outputs": {
            "quality_gate_json": str(path / "quality_gate.json"),
            "preflight_report_json": str(path / "preflight_report.json"),
            "review_dashboard_json": "change_artifacts/review_dashboard.json",
            "viewer_manifest_json": "viewer/viewer_manifest.json",
            "ai_policy_json": str(path / "ai_policy.json"),
        },
        "timings": {"total_s": total_s},
        "files": {
            "a_kind_counts": {kind: 1},
            "b_kind_counts": {kind: 1},
        },
        "matching": {
            "cad_pdf_blocked_pairs": (
                blocked_pairs if cad_pdf_blocked_pairs is None else cad_pdf_blocked_pairs
            ),
            "blocked_pairs": blocked_pairs,
        },
        "comparison": {
            "completed_pairs": completed_pairs,
            "failed_pairs": 0,
        },
        "change_artifacts": {
            "artifacts": _source_artifacts(kind, completed_pairs, source_extensions),
        },
        "preflight_result": {
            "status": "passed",
            "checks": preflight_checks if preflight_checks is not None else _preflight_checks(),
        },
        "quality_gate": {"status": "passed", "issues": []},
        "quality": {},
        "review_dashboard": {
            "review_queue": {
                "mode": review_queue_mode,
                "top_per_drawing": top_per_drawing,
                "blocked_count": blocked_pairs,
                "items": [queue_item] if queue_item else [],
            }
        },
        "viewer_perf_summary": {
            "zone_crop_count": zone_crop_count,
            "zone_crop_cold_ms": {"p95": zone_cold_p95_ms},
            "zone_crop_cache_hit_ms": {"p95": zone_hit_p95_ms},
        },
        "viewer_package": {
            "viewer_manifest": "viewer/viewer_manifest.json",
            "rendered_pair_count": completed_pairs,
            "lazy_pair_count": 0,
        },
    }
    if completed_pairs > 0:
        summary["runtime_budget"] = {
            "schema_version": 4,
            "peak_working_set_mb": 128.0,
            "peak_rss_mb": 128.0,
            "peak_disk_spool_mb": 0.1,
            "first_review_ready_s": min(total_s, 5.0),
            "total_s": total_s,
            "sample_count": 1,
            "sampler_active": True,
            "notes": [],
            "native_resource_platform": "windows",
            "native_resource_available": True,
            "native_resource_sample_count": 1,
            "start_process_handle_count": 10,
            "final_process_handle_count": 10,
            "peak_process_handle_count": 10,
            "process_handle_positive_delta": 0,
            "start_gdi_handle_count": 5,
            "final_gdi_handle_count": 5,
            "peak_gdi_handle_count": 5,
            "gdi_handle_positive_delta": 0,
            "start_user_handle_count": 6,
            "final_user_handle_count": 6,
            "peak_user_handle_count": 6,
            "user_handle_positive_delta": 0,
            "start_worker_process_count": 0,
            "final_worker_process_count": 0,
            "peak_worker_process_count": 0,
            "worker_process_positive_delta": 0,
            "native_resource_notes": [],
        }
        summary["perf_events_summary"] = {
            "schema_version": 1,
            "status": "ready",
            "event_count": 5,
            "summary_input_bytes": 512,
            "summary_elapsed_ms": 1.0,
            "stage_counts": {"scan": 1, "match": 1, "compare": 1, "artifact": 1, "viewer": 1},
            "event_counts": {"completed": 5},
        }
        summary["selected_zone_evidence"] = {
            "renders": [{"bbox_status": "exact"} for _ in range(max(1, zone_crop_count))]
        }
        summary["p5_g3_realset_gate"] = {
            "schema_version": 1,
            "status": "passed",
            "requested": True,
            "failures": [],
            "evidence": {
                "comparison": {
                    "status": "passed",
                    "completed_pairs": completed_pairs,
                    "minimum_completed_pairs": 1,
                },
                "runtime_budget": {
                    "status": "passed",
                    "sampler_active": True,
                    "sample_count": 1,
                },
                "viewer_perf_summary": {
                    "status": "passed",
                    "event_count": 5,
                    "minimum_event_count": 1,
                },
                "selected_zone_evidence": {
                    "status": "passed",
                    "render_count": max(1, zone_crop_count),
                    "failure_count": 0,
                    "minimum_render_count": 1,
                },
                "nonblank": {
                    "status": "passed",
                    "checked": 1,
                    "nonblank_count": 1,
                    "minimum_nonblank_images": 1,
                },
                "tile_manifest": {
                    "status": "passed",
                    "manifest_path": "viewer/tiles_manifest.json",
                    "pair_count": 1,
                    "require_eviction": False,
                    "evicted_pair_count": 0,
                    "evicted_estimated_bytes": 0,
                    "stale_manifest_count": 0,
                    "missing_pair_manifest_count": 0,
                    "orphan_payload_bytes": 0,
                    "max_orphan_payload_bytes": 0,
                },
            },
        }
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
    if kind == "cad":
        summary["input"]["cad_policy"] = cad_policy or {
            "expand_blocks": False,
            "block_text_detection": True,
        }
    if include_ai_policy:
        summary["ai_policy"] = _ai_policy()
    if include_sharable_audit:
        summary["sharable_audit"] = {"leak_count": leak_count, "leaks": []}
    if structural_review_recall is not None:
        summary["review_ground_truth"] = {
            "rows": review_ground_truth_rows,
            "passed_rows": review_ground_truth_rows,
            "recall": structural_review_recall,
        }
        summary["quality"]["structural_review_recall"] = structural_review_recall
    (path / "validation_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (path / "quality_gate.json").write_text(json.dumps(summary["quality_gate"]), encoding="utf-8")
    if include_ai_policy:
        (path / "ai_policy.json").write_text(json.dumps(summary["ai_policy"]), encoding="utf-8")
    if completed_pairs > 0:
        _write_change_zones_csv(
            path / "change_artifacts" / "change_zones.csv",
            kind=kind,
            queue_item=queue_item,
            bbox_coordinate_space=bbox_coordinate_space,
        )
    if blocked_pairs:
        (path / "blocked_pairs.csv").write_text(
            "a_path,b_path,a_kind,b_kind,reason\n"
            "old.dwg,new.pdf,cad,pdf,CAD/PDF cross-family comparison is blocked\n",
            encoding="utf-8",
        )
    if completed_pairs > 0 and include_workbench_acceptance_summary:
        _write_workbench_acceptance_summary(path / "workbench_acceptance_summary.json", item8=True, item10=True)
    if completed_pairs > 0:
        _write_viewer_manifest_with_visual_asset(path)


def _source_artifacts(
    kind: str,
    completed_pairs: int,
    source_extensions: tuple[str, str] | None,
) -> list[dict]:
    if completed_pairs <= 0:
        return []
    if source_extensions is None:
        source_extensions = ("dwg", "dxf") if kind == "cad" else (kind, kind)
    before_ext, after_ext = source_extensions
    return [
        {
            "source_a": f"<redacted>/before.{before_ext}",
            "source_b": f"<redacted>/after.{after_ext}",
        }
    ]


def _write_viewer_manifest_with_visual_asset(path: Path) -> None:
    manifest_rel = Path("viewer/visual_assets/S21-0001/after/source_pdf/visual_asset_manifest.json")
    manifest_ref = str(manifest_rel).replace("\\", "/")
    probe_rel = Path("viewer/visual_assets/S21-0001/after/source_pdf/nonblank_probe.json")
    probe_ref = str(probe_rel).replace("\\", "/")
    probe_target_rel = Path("viewer/images/S21-0001_after.png")
    probe_target_ref = str(probe_target_rel).replace("\\", "/")
    probe_target_path = path / probe_target_rel
    probe_target_path.parent.mkdir(parents=True, exist_ok=True)
    probe_target_path.write_bytes(b"not a real png but stable nonblank audit target")
    probe_target_hash = hashlib.sha256(probe_target_path.read_bytes()).hexdigest()
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
        "status": "passed",
        "method": "pixel_nonblank_probe",
        "asset_path": "viewer/pages/S21-0001_after.pdf",
        "asset_hash": "test-asset-hash",
        "asset_size": 10,
        "probe_target_path": probe_target_ref,
        "probe_target_hash": probe_target_hash,
        "probe_target_size": probe_target_path.stat().st_size,
        "source_hash": source_hash,
        "cache_key_hash": cache_key_hash,
        "page_index": 0,
        "dpi": 80,
        "pixel_width": 10,
        "pixel_height": 10,
        "mean": 200.0,
        "channel_ranges": [10, 10, 10],
        "extrema": [[0, 10], [0, 10], [0, 10]],
        "nonblank": True,
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
                "reason_code": "",
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
                "nonblank_probe_status": "passed",
                "metadata": {
                    "nonblank_probe": probe_ref,
                    "nonblank_probe_hash": probe_payload["probe_hash"],
                    "probe_target_path": probe_target_ref,
                    "probe_target_hash": probe_target_hash,
                    "probe_method": "pixel_nonblank_probe",
                },
            }
        ),
        encoding="utf-8",
    )
    viewer_manifest = {
        "schema_version": 2,
        "visual_asset_manifest_count": 1,
        "visual_asset_manifest_paths": [manifest_ref],
        "pairs": [
            {
                "pair_id": "S21-0001",
                "visual_asset_manifest_paths": [manifest_ref],
                "visual_assets": {
                    "after": {
                        "source_pdf": {
                            "manifest_path": manifest_ref,
                            "asset_kind": "source_pdf",
                            "status": "ready",
                            "cache_key_hash": cache_key_hash,
                            "nonblank_probe_status": "passed",
                        }
                    }
                },
            }
        ],
    }
    viewer_root = path / "viewer"
    viewer_root.mkdir(parents=True, exist_ok=True)
    (viewer_root / "viewer_manifest.json").write_text(json.dumps(viewer_manifest), encoding="utf-8")
    (viewer_root / "tiles_manifest.json").write_text(
        json.dumps({"schema_version": 1, "pairs": {}, "pair_count": 0}),
        encoding="utf-8",
    )


def _write_change_zones_csv(
    path: Path,
    *,
    kind: str,
    queue_item: dict,
    bbox_coordinate_space: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    source_format = str(queue_item.get("source_format") or kind)
    detection_source = str(
        queue_item.get("detection_source") or ("pdf_visual" if kind == "pdf" else "cad_entity")
    )
    bbox_status = str(queue_item.get("bbox_status") or "exact")
    if bbox_coordinate_space is None:
        bbox_coordinate_space = "image_pixels" if kind == "pdf" else "cad_world"
    path.write_text(
        "pair_id,zone_id,source_format,detection_source,bbox_status,bbox_coordinate_space,entity_types\n"
        f"pair,C-001,{source_format},{detection_source},{bbox_status},{bbox_coordinate_space},"
        f"{'PDF_REGION | PDF_TEXT' if kind == 'pdf' else 'ATTRIB'}\n",
        encoding="utf-8",
    )


def _preflight_checks() -> list[dict]:
    return [
        {"name": name, "status": "ok"}
        for name in sorted(audit.REQUIRED_PREFLIGHT_CHECKS)
    ]


def test_selected_zone_perf_evidence_surfaces_fallback_reason_codes(tmp_path: Path) -> None:
    summary = {
        "comparison": {"completed_pairs": 1},
        "viewer_perf_summary": {
            "zone_crop_count": 2,
            "zone_crop_cold_ms": {"p95": 500.0},
            "zone_crop_cache_hit_ms": {"p95": 25.0},
            "reason_code_counts": {"missing_page_bbox": 2},
            "renderer_backend_counts": {"pdf-page-bbox-required": 2},
        },
    }

    check = audit._check_selected_zone_perf(
        [{"path": tmp_path, "summary": summary}],
        [summary],
        evidence_level="customer_grade",
        max_cold_zone_render_ms=10_000.0,
        max_cache_hit_zone_render_ms=2_000.0,
    )

    assert check.passed is True
    evidence = "\n".join(check.evidence)
    assert "missing_page_bbox" in evidence
    assert "pdf-page-bbox-required" in evidence


def test_parse_args_accepts_require_p5_g3_realset_gate(tmp_path: Path) -> None:
    result_dir = tmp_path / "validation"
    result_dir.mkdir()

    args = audit.parse_args(
        [
            "--results-dir",
            str(result_dir),
            "--require-p5-g3-realset-gate",
            "--require-p5-g3-tile-eviction",
            "--p5-g3-min-tile-evicted-pairs",
            "2",
            "--p5-g3-min-tile-evicted-bytes",
            "4096",
            "--p5-g6-tile-cache-mb",
            "0.25",
        ]
    )

    assert args.require_p5_g3_realset_gate is True
    assert args.require_p5_g3_tile_eviction is True
    assert args.p5_g3_min_tile_evicted_pairs == 2
    assert args.p5_g3_min_tile_evicted_bytes == 4096
    assert args.p5_g6_tile_cache_mb == 0.25


def test_parse_args_accepts_require_p5_g6_tile_eviction_aliases(tmp_path: Path) -> None:
    result_dir = tmp_path / "validation"
    result_dir.mkdir()

    args = audit.parse_args(
        [
            "--results-dir",
            str(result_dir),
            "--require-p5-g6-tile-eviction",
            "--p5-g6-min-tile-evicted-pairs",
            "3",
            "--p5-g6-min-tile-evicted-bytes",
            "8192",
        ]
    )

    assert args.require_p5_g3_tile_eviction is True
    assert args.p5_g3_min_tile_evicted_pairs == 3
    assert args.p5_g3_min_tile_evicted_bytes == 8192


def test_parse_args_accepts_p5_g16_benchmark_json(tmp_path: Path) -> None:
    result_dir = tmp_path / "validation"
    result_dir.mkdir()
    benchmark_a = tmp_path / "p5_g16_a.json"
    benchmark_b = tmp_path / "p5_g16_b.json"

    args = audit.parse_args(
        [
            "--results-dir",
            str(result_dir),
            "--p5-g16-benchmark-json",
            str(benchmark_a),
            "--p5-g16-real-corpus-replay",
            str(benchmark_b),
            "--require-p5-g16-real-corpus-replay",
        ]
    )

    assert args.p5_g16_benchmark_json == [benchmark_a, benchmark_b]
    assert args.require_p5_g16_real_corpus_replay is True


def test_parse_args_accepts_p5_g22_gui_soak_json(tmp_path: Path) -> None:
    result_dir = tmp_path / "validation"
    result_dir.mkdir()
    soak_a = tmp_path / "p5_g22_a.json"
    soak_b = tmp_path / "p5_g22_b.json"

    args = audit.parse_args(
        [
            "--results-dir",
            str(result_dir),
            "--p5-g22-gui-soak-json",
            str(soak_a),
            "--p5-g22-actual-gui-soak",
            str(soak_b),
            "--require-p5-g22-actual-gui-soak",
        ]
    )

    assert args.p5_g22_gui_soak_json == [soak_a, soak_b]
    assert args.require_p5_g22_actual_gui_soak is True


def test_parse_args_accepts_p5_g26_selection_latency_json(tmp_path: Path) -> None:
    result_dir = tmp_path / "validation"
    result_dir.mkdir()
    soak_a = tmp_path / "p5_g26_a.json"
    soak_b = tmp_path / "p5_g26_b.json"

    args = audit.parse_args(
        [
            "--results-dir",
            str(result_dir),
            "--p5-g26-selection-latency-json",
            str(soak_a),
            "--p5-g26-selection-latency-soak",
            str(soak_b),
            "--require-p5-g26-selection-latency-soak",
        ]
    )

    assert args.p5_g26_selection_latency_json == [soak_a, soak_b]
    assert args.require_p5_g26_selection_latency_soak is True


def test_parse_args_accepts_p5_g27_selected_zone_crop_json(tmp_path: Path) -> None:
    result_dir = tmp_path / "validation"
    result_dir.mkdir()
    soak_a = tmp_path / "p5_g27_a.json"
    soak_b = tmp_path / "p5_g27_b.json"

    args = audit.parse_args(
        [
            "--results-dir",
            str(result_dir),
            "--p5-g27-selected-zone-crop-json",
            str(soak_a),
            "--p5-g27-selected-zone-crop-soak",
            str(soak_b),
            "--require-p5-g27-selected-zone-crop-soak",
        ]
    )

    assert args.p5_g27_selected_zone_crop_json == [soak_a, soak_b]
    assert args.require_p5_g27_selected_zone_crop_soak is True


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
        "heuristic_only": {
            "result_count": 1,
            "classifier_used": "heuristic",
            "summary_ko": "휴리스틱 분류",
        },
        "fallback_without_model": {
            "result_count": 1,
            "configured_embedding": True,
            "embedding_backend_id": "auto",
            "classifier_used": "heuristic",
            "summary_ko": "휴리스틱 fallback",
        },
    }


def _queue_item(
    source_format: str,
    detection_source: str,
    *,
    entity_types: list[str] | None = None,
    added_count: int = 0,
    deleted_count: int = 0,
    modified_count: int = 1,
) -> dict:
    pair_uuid = f"pair_{source_format}"
    return {
        "queue_key": f"{pair_uuid}:C-001",
        "pair_uuid": pair_uuid,
        "zone_id": "C-001",
        "drawing_label": "S2401",
        "category": "mixed",
        "priority_score": 100.0,
        "reason_ko": "구조 핵심 변경입니다.",
        "change_summary_ko": (
            "Member moved, section dimension changed, "
            "SLAB REBAR D13@100 -> SLAB REBAR D13@200, "
            "SHD13@100 -> SHD13@200, GRID structural text changed"
        ),
        "source_format": source_format,
        "detection_source": detection_source,
        "bbox_status": "exact",
        "review_status": "needs_review",
        "added_count": added_count,
        "deleted_count": deleted_count,
        "modified_count": modified_count,
        "entity_types": entity_types or (["ATTRIB"] if source_format == "cad" else ["PDF_TEXT"]),
    }


def _write_release_manifest(path: Path) -> None:
    prompt_checklist = path.parent / "mvp_exit_prompt_to_artifact_checklist.md"
    prompt_checklist_text = (
        "# Drawing Compare MVP Prompt-to-Artifact Checklist\n"
        + "\n".join(audit.REQUIRED_PROMPT_TO_ARTIFACT_CHECKLIST_TERMS)
        + "\n"
    )
    prompt_checklist.write_text(prompt_checklist_text, encoding="utf-8")
    package_audit = path.parent / "customer_package_path_audit.json"
    package_audit_payload = {
        "status": "passed",
        "leak_count": 0,
        "scanned_files": 10,
        "scanned_app_first_party_files": 25,
        "scanned_binary_files": 1,
        "disallowed_file_count": 0,
    }
    package_audit.write_text(json.dumps(package_audit_payload), encoding="utf-8")
    first_party_app_probe = "app/DrawingCompareWorkbench/_internal/src/services/comparison/customer_audit_probe.py"
    package_manifest_payload = {
        "schema_version": 1,
        "package_type": "customer_shareable",
        "internal_release_manifest_included": False,
        "contents": list(audit.REQUIRED_CUSTOMER_PACKAGE_CONTENTS) + [first_party_app_probe],
    }
    package_zip = path.parent / "DrawingCompareWorkbench_customer_shareable.zip"
    with zipfile.ZipFile(package_zip, "w") as archive:
        for entry in package_manifest_payload["contents"]:
            if entry == "customer_package_manifest.json":
                archive.writestr(entry, json.dumps(package_manifest_payload))
            elif entry == "customer_package_path_audit.json":
                archive.writestr(entry, json.dumps(package_audit_payload))
            elif entry == "mvp_exit_prompt_to_artifact_checklist.md":
                archive.writestr(entry, prompt_checklist_text)
            elif entry == "customer_evidence_request_ko.md":
                archive.writestr(
                    entry,
                    "# Drawing Compare 고객급 증거 요청서\n"
                    "review_ground_truth.csv\n"
                    "review_decision_truth.csv\n"
                    "dataset_strata.csv\n"
                    "operator_dry_run_notes.md\n"
                    "customer_grade\n"
                    "status=passed\n",
                )
            elif entry == first_party_app_probe:
                archive.writestr(entry, "# customer package first-party scan probe\n")
            else:
                archive.writestr(entry, "placeholder")
    package_manifest = path.parent / "customer_package_manifest.json"
    package_manifest.write_text(json.dumps(package_manifest_payload), encoding="utf-8")
    path.write_text(
        json.dumps(
            {
                "status": "passed",
                "artifacts": {
                    "mvp_exit_prompt_to_artifact_checklist": prompt_checklist.name,
                    "customer_shareable_package_zip": package_zip.name,
                    "customer_shareable_package_manifest": package_manifest.name,
                    "customer_shareable_package_path_audit": package_audit.name,
                },
                "steps": [
                    {"name": "pyinstaller_build", "status": "passed"},
                    {"name": "packaged_app_launch_smoke", "status": "passed"},
                    {"name": "customer_shareable_package_path_audit", "status": "passed"},
                    {"name": "workbench_acceptance_smoke", "status": "passed"},
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_workbench_acceptance_summary(
    path: Path,
    *,
    item8: bool,
    item10: bool,
    item5: bool = True,
    item8b: bool = True,
    item9b: bool = True,
    item9c: bool = True,
) -> None:
    passed_count = int(item5) + int(item8) + int(item8b) + int(item9b) + int(item9c) + int(item10)
    failed_count = (
        int(not item5)
        + int(not item8)
        + int(not item8b)
        + int(not item9b)
        + int(not item9c)
        + int(not item10)
    )
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "passed" if item5 and item8 and item8b and item9b and item9c and item10 else "failed",
                "passed": passed_count,
                "failed": failed_count,
                "total": 6,
                "checks": [
                    {
                        "name": "5. review_queue first-screen Top 이슈 + 점프/필터",
                        "passed": item5,
                        "detail": "test item 5",
                    },
                    {
                        "name": "8. Workbench confirmed 판정 → confirmed-only 구름마크 export",
                        "passed": item8,
                        "detail": "test item 8",
                    },
                    {
                        "name": "8b. Workbench 보류/오탐 판정 → confirmed-only export 제외",
                        "passed": item8b,
                        "detail": "test item 8b",
                    },
                    {
                        "name": "9b. selected-zone Before/After synchronized focus/window",
                        "passed": item9b,
                        "detail": "test item 9b",
                    },
                    {
                        "name": "9c. selected-zone render subprocess timeout + responsive UI loop",
                        "passed": item9c,
                        "detail": "test item 9c",
                    },
                    {
                        "name": "10. confirmed-only 검토 보고서 PDF 생성 + path leakage audit",
                        "passed": item10,
                        "detail": "test item 10",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def _check_by_name(report: dict, name: str) -> dict:
    return next(check for check in report["checks"] if check["name"] == name)


def _write_large_dwg_probe(
    path: Path,
    *,
    elapsed_s: float = 55.0,
    total: int = 350_178,
    in_memory: int = 50_000,
    stream_complete: bool = True,
    progress_event_count: int = 6,
) -> None:
    progress_events = [
        {"message": "DXF conversion started"},
        {"message": "New file loaded"},
        {"message": "Old file entity extraction"},
        {"message": "New file entity extraction"},
        {"message": "DXF compare started"},
        {"message": "DXF compare done: LINE"},
    ][:progress_event_count]
    path.write_text(
        json.dumps(
            {
                "elapsed_s": elapsed_s,
                "total": total,
                "change_records_in_memory": in_memory,
                "metadata": {
                    "large_drawing_mode": "active",
                    "change_zone_stream_complete": stream_complete,
                    "change_zone_record_count": total,
                    "change_records_in_memory": in_memory,
                },
                "progress_event_count": progress_event_count,
                "progress_events_tail": progress_events,
                "stream_exists": True,
                "stream_bytes": 1024,
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_p5_g16_replay_json(
    path: Path,
    validation_summary: Path,
    customer_manifest: Path,
    *,
    status: str = "passed",
    failed_gate: str | None = None,
    validation_sha: str | None = None,
) -> None:
    gate_names = sorted(audit.P5_G16_REQUIRED_GATES)
    gates = [
        {
            "name": name,
            "domain": "unit",
            "passed": name != failed_gate,
            "observed": 0,
            "threshold": 0,
            "actual": 0,
            "target": 0,
            "op": "==",
            "required": True,
            "detail": "",
        }
        for name in gate_names
    ]
    payload = {
        "schema_version": 1,
        "benchmark_id": "p5_g16_real_corpus_replay",
        "profile": "real_corpus_artifact_replay",
        "status": status,
        "source": {
            "validation_summary": {
                "sha256": validation_sha if validation_sha is not None else _sha256(validation_summary),
            },
        },
        "args": {
            "require_customer_corpus": True,
        },
        "environment": {
            "psutil_available": True,
            "allow_missing_psutil": False,
        },
        "corpus": {
            "evidence_level": "customer_grade",
            "sheet_count": 20,
            "has_dwg_dxf": True,
            "has_pdf_pdf": True,
            "manifest_sha256": _sha256(customer_manifest),
        },
        "summary": {
            "visit_count": 100,
            "completed_visit_count": 100,
            "replay_completed": True,
            "zone_render_artifact_count": 1,
            "page_artifact_count": 1,
            "blank_zone_output_count": 0,
            "missing_zone_image_count": 0,
            "stale_result_visible_count": 0,
            "fallback_missing_reason_count": 0,
            "timeout_count": 0,
            "cancel_count": 0,
            "rss_measurement_available": True,
        },
        "gates": gates,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    _write_p5_g22_actual_gui_soak_json(
        path.parent / "p5_g22_actual_gui_soak.json",
        validation_summary,
        customer_manifest,
    )
    _write_p5_g26_selection_latency_soak_json(
        path.parent / "p5_g26_selection_latency_soak.json",
    )
    _write_p5_g27_selected_zone_crop_soak_json(
        path.parent / "p5_g27_selected_zone_crop_soak.json",
    )


def _write_p5_g22_actual_gui_soak_json(
    path: Path,
    validation_summary: Path,
    customer_manifest: Path,
    *,
    status: str = "passed",
    failed_gate: str | None = None,
    validation_sha: str | None = None,
) -> None:
    gate_names = sorted(audit.P5_G22_REQUIRED_GATES)
    gates = [
        {
            "name": name,
            "domain": "unit",
            "passed": name != failed_gate,
            "observed": 0,
            "threshold": 0,
            "actual": 0,
            "target": 0,
            "op": "==",
            "required": True,
            "detail": "",
        }
        for name in gate_names
    ]
    payload = {
        "schema_version": 1,
        "benchmark_id": "p5_g22_actual_gui_soak",
        "profile": "actual_gui_customer_corpus_soak",
        "status": status,
        "source": {
            "validation_summary": {
                "sha256": validation_sha if validation_sha is not None else _sha256(validation_summary),
            },
        },
        "args": {
            "require_customer_corpus": True,
            "skip_zone_render_workers": False,
        },
        "environment": {
            "psutil_available": True,
            "allow_missing_psutil": False,
            "allow_missing_native_resources": False,
        },
        "corpus": {
            "evidence_level": "customer_grade",
            "sheet_count": 20,
            "has_dwg_dxf": True,
            "has_pdf_pdf": True,
            "manifest_sha256": _sha256(customer_manifest),
        },
        "summary": {
            "visit_count": 100,
            "completed_visit_count": 100,
            "gui_soak_completed": True,
            "drawing_selection_ms": {"p95_ms": 100.0},
            "page_navigation_count": 0,
            "zone_selection_count": 1,
            "zone_selection_ms": {"p95_ms": 100.0},
            "event_loop_gap_ms": {"max_ms": 100.0, "over_500ms_count": 0},
            "blank_view_count": 0,
            "stale_active_pair_count": 0,
            "stale_active_zone_count": 0,
            "viewer_perf_stale_count": 0,
            "rss_measurement_available": True,
            "native_resource_measurement_available": True,
            "native_resource_summary": {
                "measurement_available": True,
                "rss_slope": {"available": True, "positive_end_delta": 0.0},
                "process_handle_slope": {"available": True, "positive_end_delta": 0.0},
                "open_file_descriptor_slope": {"available": False, "positive_end_delta": None},
                "gdi_handle_slope": {"available": True, "positive_end_delta": 0.0},
                "user_handle_slope": {"available": True, "positive_end_delta": 0.0},
                "positive_end_deltas": {
                    "rss_mb": 0.0,
                    "process_handle_count": 0.0,
                    "open_file_descriptor_count": None,
                    "gdi_handle_count": 0.0,
                    "user_handle_count": 0.0,
                },
            },
            "worker_cleanup_ok": True,
            "worker_tree_summary": {
                "snapshot_start": {"active_worker_count": 0},
                "snapshot_after_cleanup": {"active_worker_count": 0},
                "cleanup_ok": True,
                "orphan_worker_count": 0,
            },
            "orphan_worker_count": 0,
        },
        "samples": [{} for _ in range(100)],
        "gates": gates,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_p5_g26_selection_latency_soak_json(
    path: Path,
    *,
    status: str = "passed",
    failed_gate: str | None = None,
    omit_gate: str | None = None,
) -> None:
    gate_names = sorted(audit.P5_G26_REQUIRED_GATES)
    gates = [
        {
            "name": name,
            "domain": "unit",
            "passed": name != failed_gate,
            "observed": 0,
            "threshold": 0,
            "actual": 0,
            "target": 0,
            "op": "==",
            "required": True,
            "detail": "",
        }
        for name in gate_names
        if name != omit_gate
    ]
    payload = {
        "schema_version": "workbench-gui-hotpath-benchmark/v1",
        "benchmark_id": "p5_g26_selection_latency_soak",
        "profile": "selection_latency_hard_gate",
        "status": status,
        "p5_g26_required_gate_names": gate_names,
        "p5_g26_contract": {
            "wp_a_passed": failed_gate is None and status == "passed",
            "wp_b_passed": failed_gate is None and status == "passed",
            "has_zone_selection_evidence": True,
            "zone_selection_p95_ms": 20.0,
            "zone_selection_background_work_count": 0,
            "cad_to_pdf_hot_path_count": 0,
        },
        "p5_g26_evidence": {
            "wp_a_passed": failed_gate is None and status == "passed",
            "wp_b_passed": failed_gate is None and status == "passed",
            "has_zone_selection_evidence": True,
        },
        "gates": gates,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_p5_g27_selected_zone_crop_soak_json(
    path: Path,
    *,
    status: str = "passed",
    failed_gate: str | None = None,
    omit_gate: str | None = None,
    include_real_renderer_bridge: bool = True,
    failed_bridge_gate: str | None = None,
) -> None:
    gate_names = sorted(audit.P5_G27_REQUIRED_GATES)
    bridge_gate_names = sorted(audit.P5_G27_REAL_RENDERER_BRIDGE_REQUIRED_GATES)
    gates = [
        {
            "name": name,
            "domain": "unit",
            "passed": name != failed_gate,
            "observed": 0,
            "threshold": 0,
            "actual": 0,
            "target": 0,
            "op": "==",
            "required": True,
            "detail": "",
        }
        for name in gate_names
        if name != omit_gate
    ]
    if include_real_renderer_bridge:
        gates.extend(
            {
                "name": name,
                "domain": "unit",
                "passed": name != failed_bridge_gate,
                "observed": 0,
                "threshold": 0,
                "actual": 0,
                "target": 0,
                "op": "==",
                "required": True,
                "detail": "",
            }
            for name in bridge_gate_names
        )
    payload = {
        "schema_version": "workbench-gui-hotpath-benchmark/v1",
        "benchmark_id": "p5_g27_selected_zone_crop_soak",
        "profile": "selected_zone_crop_first_lifecycle",
        "status": status,
        "p5_g27_required_gate_names": gate_names,
        "p5_g27_contract": {
            "crop_first_result_visible": failed_gate is None and status == "passed",
            "crop_visible_before_vector_focus": failed_gate is None and status == "passed",
            "crop_visible_p95_ms": 20.0,
            "vector_failure_does_not_clear_background": failed_gate is None and status == "passed",
            "has_selected_zone_crop_first_evidence": True,
            "worker_cleanup_ok": True,
            "blank_selected_zone_count": 0,
            "stale_result_visible_count": 0,
            "cancel_without_visible_regression_count": 0,
            "timeout_count": 0,
            "fallback_missing_reason_count": 0,
            "orphan_worker_count": 0,
        },
        "p5_g27_evidence": {
            "crop_first_result_visible": failed_gate is None and status == "passed",
            "crop_visible_before_vector_focus": failed_gate is None and status == "passed",
            "vector_failure_does_not_clear_background": failed_gate is None and status == "passed",
            "has_selected_zone_crop_first_evidence": True,
            "worker_cleanup_ok": True,
        },
        "gates": gates,
    }
    if include_real_renderer_bridge:
        payload["p5_g27_real_renderer_bridge_required_gate_names"] = bridge_gate_names
        payload["p5_g27_real_renderer_bridge"] = {
            "bridge_present": True,
            "benchmark_id": "p5_g16_real_corpus_replay",
            "profile": "real_corpus_artifact_replay",
            "status": "passed",
            "p5_g16_passed": failed_bridge_gate is None,
            "real_renderer_quality_passed": failed_bridge_gate is None,
            "validation_summary_sha256": "0" * 64,
            "viewer_root_present": True,
            "zone_render_artifact_count": 1,
            "blank_zone_output_count": 0,
            "missing_zone_image_count": 0,
            "fallback_missing_reason_count": 0,
            "stale_result_visible_count": 0,
            "timeout_count": 0,
            "cancel_count": 0,
        }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_customer_evidence_manifest(path: Path) -> None:
    truth_csv = path.parent / "review_ground_truth.csv"
    decision_csv = path.parent / "review_decision_truth.csv"
    strata_csv = path.parent / "dataset_strata.csv"
    large_probe = path.parent / "large_dwg_probe.json"
    audit_json = path.parent / "sharable_path_audit.json"
    notes_file = path.parent / "operator_dry_run_notes.md"
    screenshots_dir = path.parent / "operator_screenshots"
    confirmed_export = path.parent / "pdf" / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    truth_csv.write_text(
        "drawing_label,category,summary_contains,source_format,detection_source,bbox_status,notes\n"
        "S2401,mixed,D13@100,pdf,pdf_text|pdf_ocr,exact|page_fallback,D13 before\n"
        "S2402,mixed,D13@200,pdf,pdf_visual|hybrid,exact|page_fallback,D13 after\n"
        "S2403,grid,GRID A,pdf,pdf_text|pdf_visual,exact|page_fallback,grid\n"
        "S2404,mixed,SECTION DIMENSION,pdf,pdf_text|hybrid,exact|page_fallback,dimension\n"
        "S2405,mixed,SHD13@100,cad,cad_entity,exact,SHD13\n"
        "S2406,mixed,STRUCTURAL NOTE,cad,cad_entity,exact,note\n",
        encoding="utf-8",
    )
    buckets = list(audit.STRUCTURAL_COVERAGE_TERMS)
    decision_csv.write_text(
        "pair_uuid,zone_id,drawing_label,structural_bucket,human_label,source_format,detection_source,bbox_status,notes\n"
        + "\n".join(
            f"pair-{index:03d},zone-{index:03d},S{index:03d},{buckets[index % len(buckets)]},"
            f"true_positive,{'cad' if index % 2 == 0 else 'pdf'},"
            f"{'cad_entity' if index % 2 == 0 else 'pdf_text'},exact,reviewed"
            for index in range(24)
        )
        + "\n",
        encoding="utf-8",
    )
    sheet_types = ["plan", "section", "detail", "schedule_like"]
    strata_csv.write_text(
        "pair_uuid,drawing_label,format_pair,sheet_type,risk_class,large_dwg,block_text_case,negative_control,notes\n"
        + "\n".join(
            f"pair-{index:03d},S{index:03d},"
            f"{'dwg_dxf' if index < 8 else 'pdf_pdf' if index < 16 else 'cad_pdf_blocked'},"
            f"{sheet_types[index % len(sheet_types)]},{'raster_pdf' if index in {8, 9} else 'standard'},"
            f"{str(index in {0, 1}).lower()},{str(index in {0, 1}).lower()},"
            f"{str(index in {2, 3}).lower()},stratified"
            for index in range(20)
        )
        + "\n",
        encoding="utf-8",
    )
    _write_large_dwg_probe(large_probe)
    audit_json.write_text(json.dumps({"status": "passed", "leak_count": 0}), encoding="utf-8")
    notes_file.write_text(
        "Operator dry run passed.\n"
        "reviewer_role: structural_review_lead\n"
        "Operator notes:\n"
        "Reviewed S2401 zone Z-001 with synchronized Before/After zoom, Korean summary, "
        "confirmed/hold decisions, confirmed-only export, and path audit leak_count=0.\n"
        + "\n".join(f"- [x] {check_id}" for check_id in audit.REQUIRED_OPERATOR_WORKFLOW_CHECKS),
        encoding="utf-8",
    )
    screenshots_dir.mkdir()
    (screenshots_dir / "01_dashboard.png").write_bytes(b"png")
    confirmed_export.parent.mkdir()
    confirmed_export.write_bytes(b"png")
    manifest_payload: dict[str, Any] = (
            {
                "schema_version": 1,
                "evidence_level": "customer_grade",
                "dataset_id": "customer-grade-fixture",
                "dataset_provenance": {
                    "source_kind": "customer_grade",
                    "source_description": "Approved customer-grade fixture for MVP exit.",
                    "approval_status": "approved_for_mvp_exit",
                    "approver": "structural-review-lead",
                },
                "validation_date": "2026-05-11",
                "sheet_count": 20,
                "ground_truth_owner": "structural-review-lead",
                "format_coverage": {
                    "dwg_dxf": True,
                    "pdf_pdf": True,
                    "cad_pdf_blocked": True,
                },
                "structural_coverage": [
                    "member_add_delete_move",
                    "section_dimension_change",
                    "d13_spacing_change",
                    "shd13_spacing_change",
                    "grid_change",
                    "structural_text_change",
                ],
                "ground_truth": {
                    "status": "approved",
                    "row_count": 6,
                    "review_ground_truth_csv": truth_csv.name,
                },
                "review_decision_quality": {
                    "status": "passed",
                    "path": decision_csv.name,
                    "review_decision_truth_csv": decision_csv.name,
                    "labeled_rows": 24,
                    "true_positive_count": 24,
                    "false_positive_count": 0,
                    "hold_count": 0,
                    "overall_precision": 1.0,
                    "false_positive_rate": 0.0,
                    "bucket_labeled_rows": {bucket: 4 for bucket in buckets},
                    "bucket_precision": {bucket: 1.0 for bucket in buckets},
                },
                "dataset_strata": {
                    "status": "passed",
                    "path": strata_csv.name,
                    "dataset_strata_csv": strata_csv.name,
                    "rows": 20,
                    "format_pair_counts": {
                        "dwg_dxf": 8,
                        "pdf_pdf": 8,
                        "cad_pdf_blocked": 4,
                    },
                    "sheet_type_counts": {
                        "plan": 5,
                        "section": 5,
                        "detail": 5,
                        "schedule_like": 5,
                    },
                    "cad_rows": 8,
                    "raster_or_low_quality_rows": 2,
                    "large_dwg_rows": 2,
                    "block_text_rows": 2,
                    "negative_control_rows": 2,
                },
                "first_interactive_readiness": {
                    "status": "passed",
                    "completed_outputs": 2,
                    "measured_outputs": 2,
                    "max_review_dashboard_ready_s": 10.0,
                    "max_first_top_issue_ready_s": 10.0,
                    "max_viewer_metadata_ready_s": 12.0,
                },
                "bbox_quality": {
                    "status": "passed",
                    "bbox_status_count": 4,
                    "exact_count": 4,
                    "relative_only_count": 0,
                    "page_fallback_count": 0,
                    "relative_only_ratio": 0.0,
                    "page_fallback_ratio": 0.0,
                    "top_priority_relative_only": False,
                },
                "large_dwg_resource_probe": {
                    "status": "passed",
                    "path": large_probe.name,
                    "peak_rss_mb": 1024.0,
                    "progress_max_gap_s": 2.0,
                    "cancel_probe": {
                        "status": "passed",
                        "cancel_to_idle_s": 2.0,
                        "partial_outputs_cleaned": True,
                        "worker_processes_left": 0,
                    },
                },
                "operator_dry_run": {
                    "status": "passed",
                    "reviewer_role": "structural_review_lead",
                    "confirmed_export_checked": True,
                    "workflow_checks": list(audit.REQUIRED_OPERATOR_WORKFLOW_CHECKS),
                    "artifacts": {
                        "notes_file": notes_file.name,
                        "screenshots_dir": screenshots_dir.name,
                        "confirmed_export_artifact": confirmed_export.relative_to(path.parent).as_posix(),
                    },
                },
                "path_leakage_audit": {
                    "status": "passed",
                    "leak_count": 0,
                    "audit_json": audit_json.name,
                },
                "cad_policy_evidence": {
                    "block_text_detection_without_expansion": True,
                },
                "selected_zone_performance": {
                    "status": "passed",
                    "completed_outputs": 2,
                    "telemetry_outputs": 2,
                    "max_cold_zone_render_ms": 10000.0,
                    "max_cache_hit_zone_render_ms": 2000.0,
                    "max_cold_p95_ms": 500.0,
                    "max_cache_hit_p95_ms": 25.0,
                },
                "workbench_acceptance": {
                    "status": "passed",
                    "summary_count": 1,
                    "passed_summary_count": 1,
                    "required_items": list(audit.REQUIRED_WORKBENCH_ACCEPTANCE_ITEMS),
                    "summaries": ["pdf/workbench_acceptance_summary.json"],
                    "failures": [],
                },
                "readiness": {
                    "status": "ready",
                    "issue_count": 0,
                    "issues": [],
                    "warning": "ready for final customer_grade exit audit",
                },
            }
        )
    # Plan §17 F6 — attach manifest provenance so the new
    # ``customer_grade_manifest_provenance`` audit gate accepts the
    # fixture. Recompute on every fixture build so manifest mutations
    # in individual tests trigger expected hash failures.
    from src.services.comparison.manifest_provenance import (
        build_provenance,
        compute_file_sha256,
    )
    input_file_hashes: dict[str, str] = {
        "review_ground_truth_csv": compute_file_sha256(truth_csv),
        "review_decision_truth_csv": compute_file_sha256(decision_csv),
        "dataset_strata_csv": compute_file_sha256(strata_csv),
        "large_dwg_probe_json": compute_file_sha256(large_probe),
        "operator_notes_file": compute_file_sha256(notes_file),
        "confirmed_export_artifact": compute_file_sha256(confirmed_export),
    }
    manifest_payload["provenance"] = build_provenance(
        manifest_payload,
        input_file_hashes=input_file_hashes,
        tool_version="test-fixture",
    )
    path.write_text(
        json.dumps(manifest_payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _mutate_customer_manifest_with_provenance(path: Path, **updates: dict) -> None:
    manifest_payload = json.loads(path.read_text(encoding="utf-8"))
    manifest_payload.pop("provenance", None)
    manifest_payload.update(updates)
    from src.services.comparison.manifest_provenance import (
        build_provenance,
        compute_file_sha256,
    )

    confirmed_export = (
        path.parent
        / manifest_payload["operator_dry_run"]["artifacts"]["confirmed_export_artifact"]
    )
    input_file_hashes: dict[str, str] = {
        "review_ground_truth_csv": compute_file_sha256(
            path.parent / manifest_payload["ground_truth"]["review_ground_truth_csv"]
        ),
        "review_decision_truth_csv": compute_file_sha256(
            path.parent / manifest_payload["review_decision_quality"]["review_decision_truth_csv"]
        ),
        "dataset_strata_csv": compute_file_sha256(
            path.parent / manifest_payload["dataset_strata"]["dataset_strata_csv"]
        ),
        "large_dwg_probe_json": compute_file_sha256(
            path.parent / manifest_payload["large_dwg_resource_probe"]["path"]
        ),
        "operator_notes_file": compute_file_sha256(
            path.parent / manifest_payload["operator_dry_run"]["artifacts"]["notes_file"]
        ),
        "confirmed_export_artifact": compute_file_sha256(confirmed_export),
    }
    manifest_payload["provenance"] = build_provenance(
        manifest_payload,
        input_file_hashes=input_file_hashes,
        tool_version="test-fixture",
    )
    path.write_text(json.dumps(manifest_payload, ensure_ascii=False), encoding="utf-8")
    replay_path = path.parent / "pdf" / "p5_g16_real_corpus_replay.json"
    if replay_path.exists():
        replay_payload = json.loads(replay_path.read_text(encoding="utf-8"))
        replay_payload.setdefault("corpus", {})["manifest_sha256"] = _sha256(path)
        replay_path.write_text(json.dumps(replay_payload), encoding="utf-8")
    gui_soak_path = path.parent / "pdf" / "p5_g22_actual_gui_soak.json"
    if gui_soak_path.exists():
        gui_soak_payload = json.loads(gui_soak_path.read_text(encoding="utf-8"))
        gui_soak_payload.setdefault("corpus", {})["manifest_sha256"] = _sha256(path)
        gui_soak_path.write_text(json.dumps(gui_soak_payload), encoding="utf-8")


def _complete_customer_audit_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    _write_p5_g16_replay_json(
        pdf_dir / "p5_g16_real_corpus_replay.json",
        pdf_dir / "validation_summary.json",
        customer_manifest,
    )
    return cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest


def test_mvp_exit_audit_passes_with_complete_evidence(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    _write_p5_g16_replay_json(
        pdf_dir / "p5_g16_real_corpus_replay.json",
        pdf_dir / "validation_summary.json",
        customer_manifest,
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    assert report["status"] == "passed"
    assert report["summary"]["failed"] == 0


def test_customer_grade_audit_requires_p5_g22_actual_gui_soak_by_default(
    tmp_path: Path,
) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    (pdf_dir / "p5_g22_actual_gui_soak.json").unlink()

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g22_actual_gui_soak")
    assert report["status"] == "failed"
    assert check["passed"] is False
    assert "artifact missing" in check["detail"]


def test_customer_grade_audit_rejects_failed_p5_g22_actual_gui_soak_gate(
    tmp_path: Path,
) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    _write_p5_g22_actual_gui_soak_json(
        pdf_dir / "p5_g22_actual_gui_soak.json",
        pdf_dir / "validation_summary.json",
        customer_manifest,
        failed_gate="p5_g22_blank_view_count",
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g22_actual_gui_soak")
    assert report["status"] == "failed"
    assert check["passed"] is False
    assert "required gates failed: p5_g22_blank_view_count" in check["detail"]


def test_customer_grade_audit_requires_p5_g26_selection_latency_soak_by_default(
    tmp_path: Path,
) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    (pdf_dir / "p5_g26_selection_latency_soak.json").unlink()

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g26_selection_latency_soak")
    assert report["status"] == "failed"
    assert check["passed"] is False
    assert "artifact missing" in check["detail"]


def test_customer_grade_audit_rejects_failed_p5_g26_selection_latency_gate(
    tmp_path: Path,
) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    _write_p5_g26_selection_latency_soak_json(
        pdf_dir / "p5_g26_selection_latency_soak.json",
        failed_gate="p5_g26_zone_selection_p95_ms",
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g26_selection_latency_soak")
    assert report["status"] == "failed"
    assert check["passed"] is False
    assert "required gates failed: p5_g26_zone_selection_p95_ms" in check["detail"]


def test_customer_grade_audit_rejects_missing_p5_g26_required_gate(
    tmp_path: Path,
) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    _write_p5_g26_selection_latency_soak_json(
        pdf_dir / "p5_g26_selection_latency_soak.json",
        omit_gate="p5_g26_zone_selection_telemetry_count",
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g26_selection_latency_soak")
    assert report["status"] == "failed"
    assert check["passed"] is False
    assert "required gates missing: p5_g26_zone_selection_telemetry_count" in check["detail"]


def test_customer_grade_audit_requires_p5_g27_selected_zone_crop_soak_by_default(
    tmp_path: Path,
) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    (pdf_dir / "p5_g27_selected_zone_crop_soak.json").unlink()

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g27_selected_zone_crop_soak")
    assert report["status"] == "failed"
    assert check["passed"] is False
    assert "artifact missing" in check["detail"]


def test_customer_grade_audit_rejects_failed_p5_g27_selected_zone_crop_gate(
    tmp_path: Path,
) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    _write_p5_g27_selected_zone_crop_soak_json(
        pdf_dir / "p5_g27_selected_zone_crop_soak.json",
        failed_gate="p5_g27_crop_visible_before_vector_focus",
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g27_selected_zone_crop_soak")
    assert report["status"] == "failed"
    assert check["passed"] is False
    assert "required gates failed: p5_g27_crop_visible_before_vector_focus" in check["detail"]


def test_customer_grade_audit_rejects_missing_p5_g27_required_gate(
    tmp_path: Path,
) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    _write_p5_g27_selected_zone_crop_soak_json(
        pdf_dir / "p5_g27_selected_zone_crop_soak.json",
        omit_gate="p5_g27_crop_visible_before_vector_focus",
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g27_selected_zone_crop_soak")
    assert report["status"] == "failed"
    assert check["passed"] is False
    assert "required gates missing: p5_g27_crop_visible_before_vector_focus" in check["detail"]


def test_customer_grade_audit_rejects_p5_g27_without_real_renderer_bridge(
    tmp_path: Path,
) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    _write_p5_g27_selected_zone_crop_soak_json(
        pdf_dir / "p5_g27_selected_zone_crop_soak.json",
        include_real_renderer_bridge=False,
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g27_selected_zone_crop_soak")
    assert report["status"] == "failed"
    assert check["passed"] is False
    assert "p5_g27_real_renderer_bridge missing" in check["detail"]
    assert "real renderer bridge gates missing" in check["detail"]


def test_customer_grade_audit_rejects_failed_p5_g27_real_renderer_bridge(
    tmp_path: Path,
) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    _write_p5_g27_selected_zone_crop_soak_json(
        pdf_dir / "p5_g27_selected_zone_crop_soak.json",
        failed_bridge_gate="p5_g27_real_renderer_bridge_p5_g16_passed",
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g27_selected_zone_crop_soak")
    assert report["status"] == "failed"
    assert check["passed"] is False
    assert "p5_g27_real_renderer_bridge.p5_g16_passed must be true" in check["detail"]
    assert "real renderer bridge gates failed" in check["detail"]


def test_customer_grade_audit_rejects_p5_g22_missing_shared_summaries(
    tmp_path: Path,
) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    soak_path = pdf_dir / "p5_g22_actual_gui_soak.json"
    payload = json.loads(soak_path.read_text(encoding="utf-8"))
    payload["summary"].pop("native_resource_summary", None)
    payload["summary"].pop("worker_tree_summary", None)
    soak_path.write_text(json.dumps(payload), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g22_actual_gui_soak")
    assert check["passed"] is False
    assert "summary.native_resource_summary missing" in check["detail"]
    assert "summary.worker_tree_summary missing" in check["detail"]


def test_customer_grade_audit_requires_p5_g24_visual_asset_manifests(tmp_path: Path) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    viewer_manifest_path = pdf_dir / "viewer" / "viewer_manifest.json"
    viewer_manifest = json.loads(viewer_manifest_path.read_text(encoding="utf-8"))
    viewer_manifest["visual_asset_manifest_paths"] = []
    viewer_manifest["pairs"][0]["visual_asset_manifest_paths"] = []
    viewer_manifest["pairs"][0]["visual_assets"] = {}
    viewer_manifest_path.write_text(json.dumps(viewer_manifest), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g24_visual_asset_policy")
    assert report["status"] == "failed"
    assert check["passed"] is False
    assert "no visual asset manifest references" in check["detail"]


def test_customer_grade_audit_rejects_visual_asset_manifest_policy_violation(tmp_path: Path) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    visual_manifest_path = (
        pdf_dir
        / "viewer"
        / "visual_assets"
        / "S21-0001"
        / "after"
        / "source_pdf"
        / "visual_asset_manifest.json"
    )
    visual_manifest = json.loads(visual_manifest_path.read_text(encoding="utf-8"))
    visual_manifest["nonblank_probe_status"] = "not_probed"
    visual_manifest_path.write_text(json.dumps(visual_manifest), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g24_visual_asset_policy")
    assert report["status"] == "failed"
    assert check["passed"] is False
    assert "nonblank_probe_status must be passed" in check["detail"]


def test_customer_grade_audit_rejects_missing_visual_asset_probe_artifact(tmp_path: Path) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    probe_path = pdf_dir / "viewer" / "visual_assets" / "S21-0001" / "after" / "source_pdf" / "nonblank_probe.json"
    probe_path.unlink()

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g24_visual_asset_policy")
    assert report["status"] == "failed"
    assert check["passed"] is False
    assert "nonblank probe artifact not found" in check["detail"]


def test_customer_grade_audit_rejects_visual_asset_probe_target_hash_mismatch(tmp_path: Path) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    target_path = pdf_dir / "viewer" / "images" / "S21-0001_after.png"
    target_path.write_bytes(b"changed target bytes")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g24_visual_asset_policy")
    assert report["status"] == "failed"
    assert check["passed"] is False
    assert "probe_target_hash does not match target file" in check["detail"]


def test_customer_grade_audit_requires_runtime_budget_by_default(tmp_path: Path) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    summary_path = pdf_dir / "validation_summary.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    payload.pop("runtime_budget", None)
    summary_path.write_text(json.dumps(payload), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "runtime_budget_measurement")
    assert check["passed"] is False
    assert "runtime_budget block missing" in check["detail"]


def test_customer_grade_audit_requires_perf_events_summary_by_default(tmp_path: Path) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    summary_path = pdf_dir / "validation_summary.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    payload.pop("perf_events_summary", None)
    summary_path.write_text(json.dumps(payload), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "perf_events_summary_measurement")
    assert check["passed"] is False
    assert "perf_events_summary block missing" in check["detail"]


def test_customer_grade_audit_requires_p5_g16_benchmark_by_default(tmp_path: Path) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    (pdf_dir / "p5_g16_real_corpus_replay.json").unlink()

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g16_real_corpus_replay")
    assert check["passed"] is False
    assert "artifact missing" in check["detail"]


def test_customer_grade_audit_accepts_default_sibling_p5_g16_json(tmp_path: Path) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g16_real_corpus_replay")
    assert check["passed"] is True
    assert report["status"] == "passed"


def test_p5_g16_benchmark_rejects_failed_gate(tmp_path: Path) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    _write_p5_g16_replay_json(
        pdf_dir / "p5_g16_real_corpus_replay.json",
        pdf_dir / "validation_summary.json",
        customer_manifest,
        status="failed",
        failed_gate="blank_zone_output_count",
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g16_real_corpus_replay")
    assert check["passed"] is False
    assert "status=failed" in check["detail"]
    assert "blank_zone_output_count" in check["detail"]


def test_p5_g16_benchmark_rejects_stale_validation_summary_hash(tmp_path: Path) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    summary_path = pdf_dir / "validation_summary.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    payload["comparison"]["completed_pairs"] = 21
    summary_path.write_text(json.dumps(payload), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g16_real_corpus_replay")
    assert check["passed"] is False
    assert "validation_summary.sha256 does not match" in check["detail"]


def test_customer_grade_audit_requires_p5_g3_realset_gate_by_default(tmp_path: Path) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    summary_path = pdf_dir / "validation_summary.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    payload.pop("p5_g3_realset_gate", None)
    summary_path.write_text(json.dumps(payload), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g3_realset_release_gate")
    assert check["passed"] is False
    assert "p5_g3_realset_gate block missing" in check["detail"]


def test_customer_grade_audit_rejects_not_requested_p5_g3_gate(tmp_path: Path) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    summary_path = pdf_dir / "validation_summary.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    payload["p5_g3_realset_gate"] = {
        "schema_version": 1,
        "status": "not_requested",
        "requested": False,
        "failures": [],
        "evidence": {},
    }
    summary_path.write_text(json.dumps(payload), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g3_realset_release_gate")
    assert check["passed"] is False
    assert "p5_g3_realset_gate.requested is not true" in check["detail"]


def test_p5_g3_realset_gate_reports_tile_manifest_failures(tmp_path: Path) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    summary_path = pdf_dir / "validation_summary.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    payload["p5_g3_realset_gate"]["status"] = "failed"
    payload["p5_g3_realset_gate"]["failures"] = [
        "tile_manifest: orphan_payload_bytes=4096 > 0"
    ]
    payload["p5_g3_realset_gate"]["evidence"]["tile_manifest"]["status"] = "failed"
    payload["p5_g3_realset_gate"]["evidence"]["tile_manifest"]["orphan_payload_bytes"] = 4096
    summary_path.write_text(json.dumps(payload), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g3_realset_release_gate")
    assert check["passed"] is False
    assert "p5_g3_realset_gate.tile_manifest.status=failed" in check["detail"]
    assert "orphan_payload_bytes=4096" in check["detail"]
    assert "viewer package manifest materialisation" in "\n".join(check["evidence"])


def test_audit_can_require_p5_g3_tile_eviction_evidence(tmp_path: Path) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="synthetic",
        require_p5_g3_tile_eviction=True,
        p5_g3_min_tile_evicted_pairs=1,
        p5_g3_min_tile_evicted_bytes=1,
    )

    check = _check_by_name(report, "p5_g3_realset_release_gate")
    assert check["passed"] is False
    assert "tile_manifest.require_eviction is not true" in check["detail"]
    assert "tile_manifest.evicted_pair_count=0 < 1" in check["detail"]


def test_audit_passes_required_p5_g3_tile_eviction_evidence(tmp_path: Path) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    for result_dir in (cad_dir, pdf_dir):
        summary_path = result_dir / "validation_summary.json"
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        tile = payload["p5_g3_realset_gate"]["evidence"]["tile_manifest"]
        tile["require_eviction"] = True
        tile["evicted_pair_count"] = 2
        tile["evicted_estimated_bytes"] = 4096
        tile["configured_tile_cache_mb"] = 0.25
        tile["tile_cache_env_mb"] = "0.25"
        tile["byte_limit"] = 262144
        summary_path.write_text(json.dumps(payload), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="synthetic",
        require_p5_g3_tile_eviction=True,
        p5_g3_min_tile_evicted_pairs=2,
        p5_g3_min_tile_evicted_bytes=4096,
        p5_g6_tile_cache_mb=0.25,
    )

    check = _check_by_name(report, "p5_g3_realset_release_gate")
    assert check["passed"] is True
    evidence = "\n".join(check["evidence"])
    assert "configured_tile_cache_mb=0.25" in evidence
    assert "tile_cache_env_mb=0.25" in evidence


def test_audit_fails_p5_g6_tile_cache_cap_mismatch(tmp_path: Path) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    for result_dir in (cad_dir, pdf_dir):
        summary_path = result_dir / "validation_summary.json"
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        tile = payload["p5_g3_realset_gate"]["evidence"]["tile_manifest"]
        tile["require_eviction"] = True
        tile["evicted_pair_count"] = 2
        tile["evicted_estimated_bytes"] = 4096
        tile["configured_tile_cache_mb"] = 0.5
        tile["tile_cache_env_mb"] = "0.5"
        tile["byte_limit"] = 524288
        summary_path.write_text(json.dumps(payload), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="synthetic",
        require_p5_g3_tile_eviction=True,
        p5_g3_min_tile_evicted_pairs=2,
        p5_g3_min_tile_evicted_bytes=4096,
        p5_g6_tile_cache_mb=0.25,
    )

    check = _check_by_name(report, "p5_g3_realset_release_gate")
    assert check["passed"] is False
    assert "configured_tile_cache_mb=0.5 != 0.25" in check["detail"]
    assert "tile_cache_env_mb=0.5 != 0.25" in check["detail"]
    assert "byte_limit=524288 != 262144" in check["detail"]


def test_customer_grade_audit_surfaces_passed_p5_g7_forced_tile_eviction_manifest(
    tmp_path: Path,
) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    _mutate_customer_manifest_with_provenance(
        customer_manifest,
        p5_g7_forced_tile_eviction={
            "schema_version": 1,
            "status": "passed",
            "required": True,
            "expected_tile_cache_mb": 0.25,
            "proof_count": 1,
            "passed_proof_count": 1,
            "proofs": [{"status": "passed"}],
            "release_manifests": [],
            "issues": [],
        },
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    assert report["status"] == "passed"
    check = _check_by_name(report, "p5_g7_forced_tile_eviction_manifest")
    assert check["passed"] is True
    assert "expected_tile_cache_mb=0.25" in check["evidence"]


def test_p5_g7_manifest_does_not_satisfy_required_p5_g3_tile_eviction(
    tmp_path: Path,
) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    _mutate_customer_manifest_with_provenance(
        customer_manifest,
        p5_g7_forced_tile_eviction={
            "schema_version": 1,
            "status": "passed",
            "required": True,
            "expected_tile_cache_mb": 0.25,
            "proof_count": 1,
            "passed_proof_count": 1,
            "proofs": [{"status": "passed"}],
            "release_manifests": [],
            "issues": [],
        },
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
        require_p5_g3_tile_eviction=True,
        p5_g3_min_tile_evicted_pairs=1,
        p5_g3_min_tile_evicted_bytes=1,
        p5_g6_tile_cache_mb=0.25,
    )

    assert _check_by_name(report, "p5_g7_forced_tile_eviction_manifest")["passed"] is True
    p5_g3_check = _check_by_name(report, "p5_g3_realset_release_gate")
    assert p5_g3_check["passed"] is False
    assert "tile_manifest.require_eviction is not true" in p5_g3_check["detail"]


def test_customer_grade_audit_fails_inconsistent_p5_g7_forced_tile_eviction_manifest(
    tmp_path: Path,
) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = (
        _complete_customer_audit_fixture(tmp_path)
    )
    _mutate_customer_manifest_with_provenance(
        customer_manifest,
        p5_g7_forced_tile_eviction={
            "schema_version": 1,
            "status": "failed",
            "required": True,
            "expected_tile_cache_mb": 0.25,
            "proof_count": 0,
            "passed_proof_count": 0,
            "proofs": [],
            "release_manifests": [],
            "issues": ["required P5-G7 forced tile-eviction proof is missing or failed"],
        },
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "p5_g7_forced_tile_eviction_manifest")
    assert check["passed"] is False
    assert "required=true requires status=passed" in check["detail"]
    assert report["status"] == "failed"


def test_customer_grade_audit_fails_low_review_decision_precision(tmp_path: Path) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = _complete_customer_audit_fixture(tmp_path)
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    manifest["review_decision_quality"]["status"] = "failed"
    manifest["review_decision_quality"]["overall_precision"] = 0.84
    customer_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "review_queue_precision")
    assert report["status"] == "failed"
    assert check["passed"] is False
    assert "overall_precision" in check["detail"]


def test_customer_grade_audit_fails_insufficient_dataset_strata(tmp_path: Path) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = _complete_customer_audit_fixture(tmp_path)
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    manifest["dataset_strata"]["status"] = "failed"
    manifest["dataset_strata"]["cad_rows"] = 7
    customer_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "dataset_strata_coverage")
    assert report["status"] == "failed"
    assert check["passed"] is False
    assert "cad_rows" in check["detail"]


def test_customer_grade_audit_fails_slow_first_interactive_ready(tmp_path: Path) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = _complete_customer_audit_fixture(tmp_path)
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    manifest["first_interactive_readiness"]["status"] = "failed"
    manifest["first_interactive_readiness"]["max_review_dashboard_ready_s"] = 601.0
    customer_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "first_interactive_ready")
    assert report["status"] == "failed"
    assert check["passed"] is False
    assert "first_interactive_readiness.status" in check["detail"]


def test_customer_grade_audit_fails_large_dwg_resource_probe_budget(tmp_path: Path) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = _complete_customer_audit_fixture(tmp_path)
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    manifest["large_dwg_resource_probe"]["status"] = "failed"
    manifest["large_dwg_resource_probe"]["peak_rss_mb"] = 4097.0
    customer_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "large_dwg_resource_and_cancel_probe")
    assert report["status"] == "failed"
    assert check["passed"] is False
    assert "peak_rss_mb" in check["detail"]


def test_customer_grade_audit_fails_pdf_bbox_relative_only_top_issue(tmp_path: Path) -> None:
    cad_dir, pdf_dir, blocked_dir, release_manifest, customer_manifest = _complete_customer_audit_fixture(tmp_path)
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    manifest["bbox_quality"]["status"] = "failed"
    manifest["bbox_quality"]["top_priority_relative_only"] = True
    customer_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "pdf_selected_zone_bbox_quality")
    assert report["status"] == "failed"
    assert check["passed"] is False
    assert "top_priority_relative_only" in check["detail"]


def test_mvp_exit_audit_accepts_required_large_dwg_probe(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    large_dwg_probe = tmp_path / "large_dwg_probe.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    _write_large_dwg_probe(large_dwg_probe)
    _write_p5_g16_replay_json(
        pdf_dir / "p5_g16_real_corpus_replay.json",
        pdf_dir / "validation_summary.json",
        customer_manifest,
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        large_dwg_probe=large_dwg_probe,
        require_large_dwg_probe=True,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    assert report["status"] == "passed"
    assert _check_by_name(report, "large_dwg_performance_probe")["passed"] is True


def test_mvp_exit_audit_requires_large_dwg_probe_when_requested(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))

    report = audit.run_audit(
        result_dirs=[cad_dir],
        require_large_dwg_probe=True,
        max_total_pairs=50,
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert "large_dwg_performance_probe" in failed
    assert "--large-dwg-probe is required" in failed["large_dwg_performance_probe"]["detail"]


def test_mvp_exit_audit_rejects_slow_or_unstreamed_large_dwg_probe(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    large_dwg_probe = tmp_path / "large_dwg_probe.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_large_dwg_probe(
        large_dwg_probe,
        elapsed_s=180.0,
        total=90_000,
        in_memory=75_000,
        stream_complete=False,
        progress_event_count=0,
    )

    report = audit.run_audit(
        result_dirs=[cad_dir],
        large_dwg_probe=large_dwg_probe,
        require_large_dwg_probe=True,
        max_total_pairs=50,
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    detail = failed["large_dwg_performance_probe"]["detail"]
    assert "elapsed_s=180.0 exceeds" in detail
    assert "below required" in detail
    assert "change_records_in_memory=75000 exceeds" in detail
    assert "metadata.change_zone_stream_complete must be true" in detail
    assert "progress_event_count=0 below required" in detail


def test_mvp_exit_audit_cli_outputs_ascii_safe_json(tmp_path: Path, capsys) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    out = tmp_path / "audit.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    _write_p5_g16_replay_json(
        pdf_dir / "p5_g16_real_corpus_replay.json",
        pdf_dir / "validation_summary.json",
        customer_manifest,
    )

    code = audit.main(
        [
            "--results-dir",
            str(cad_dir),
            "--results-dir",
            str(pdf_dir),
            "--results-dir",
            str(blocked_dir),
            "--release-manifest",
            str(release_manifest),
            "--customer-evidence-manifest",
            str(customer_manifest),
            "--min-total-pairs",
            "20",
            "--evidence-level",
            "customer_grade",
            "--out",
            str(out),
        ]
    )

    stdout = capsys.readouterr().out
    out_text = out.read_text(encoding="utf-8")
    assert code == 0
    out_text.encode("ascii")
    stdout.encode("ascii")
    assert json.loads(out_text)["status"] == "passed"
    assert json.loads(stdout)["status"] == "passed"


def test_customer_grade_audit_accepts_korean_structural_reviewer_role(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    _write_p5_g16_replay_json(
        pdf_dir / "p5_g16_real_corpus_replay.json",
        pdf_dir / "validation_summary.json",
        customer_manifest,
    )
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    manifest["operator_dry_run"]["reviewer_role"] = "구조 검토 책임자"
    notes_path = customer_manifest.parent / manifest["operator_dry_run"]["artifacts"]["notes_file"]
    notes_path.write_text(
        "Operator dry run passed.\n"
        "reviewer_role: 구조 검토 책임자\n"
        "Operator notes:\n"
        "Reviewed S2401 zone Z-001 with synchronized Before/After zoom, Korean summary, "
        "confirmed-only export, and path audit leak_count=0.\n"
        + "\n".join(f"- [x] {check_id}" for check_id in audit.REQUIRED_OPERATOR_WORKFLOW_CHECKS),
        encoding="utf-8",
    )
    # Plan §17 F6 — this test legitimately mutates both the manifest body
    # AND the operator notes file after fixture build. Recompute the
    # provenance block so the customer_grade_manifest_provenance gate
    # accepts the new content (otherwise the gate would correctly flag
    # the legitimate edit as a tamper).
    from src.services.comparison.manifest_provenance import (
        build_provenance,
        compute_file_sha256,
    )
    truth_path = customer_manifest.parent / manifest["ground_truth"]["review_ground_truth_csv"]
    confirmed_export_artifact = (
        customer_manifest.parent
        / manifest["operator_dry_run"]["artifacts"]["confirmed_export_artifact"]
    )
    manifest["provenance"] = build_provenance(
        {k: v for k, v in manifest.items() if k != "provenance"},
        input_file_hashes={
            "review_ground_truth_csv": compute_file_sha256(truth_path),
            "operator_notes_file": compute_file_sha256(notes_path),
            "confirmed_export_artifact": compute_file_sha256(confirmed_export_artifact),
        },
        tool_version="test-fixture",
    )
    customer_manifest.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    _write_p5_g16_replay_json(
        pdf_dir / "p5_g16_real_corpus_replay.json",
        pdf_dir / "validation_summary.json",
        customer_manifest,
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    assert report["status"] == "passed"
    assert report["summary"]["failed"] == 0


def test_customer_grade_audit_accepts_utf16_operator_notes(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    _write_p5_g16_replay_json(
        pdf_dir / "p5_g16_real_corpus_replay.json",
        pdf_dir / "validation_summary.json",
        customer_manifest,
    )
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    notes_path = customer_manifest.parent / manifest["operator_dry_run"]["artifacts"]["notes_file"]
    notes_path.write_text(
        "reviewer_role: structural_review_lead\n"
        "Operator notes:\n"
        "Reviewed S2401 zone Z-001 with synchronized Before/After zoom, Korean summary, "
        "confirmed-only export, and path audit leak_count=0.\n"
        + "\n".join(f"- [x] {check_id}" for check_id in audit.REQUIRED_OPERATOR_WORKFLOW_CHECKS),
        encoding="utf-16",
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    assert report["status"] == "passed"
    assert report["summary"]["failed"] == 0


def test_customer_grade_audit_rejects_copied_truth_template_example_rows(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    (tmp_path / "review_ground_truth.csv").write_text(
        "drawing_label,category,summary_contains,source_format,detection_source,bbox_status,notes\n"
        "S-001,member|mixed,BEAM;added,cad,cad_entity,exact,member add/delete/move example\n",
        encoding="utf-8",
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "customer_grade_evidence_declared")
    assert report["status"] == "failed"
    assert not check["passed"]
    assert "template/example marker" in check["detail"]


def test_customer_grade_audit_rejects_template_truth_and_operator_notes(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)

    truth_template = tmp_path / "review_ground_truth_template.csv"
    notes_template = tmp_path / "operator_dry_run_checklist_template.md"
    truth_template.write_text((tmp_path / "review_ground_truth.csv").read_text(encoding="utf-8"), encoding="utf-8")
    notes_template.write_text((tmp_path / "operator_dry_run_notes.md").read_text(encoding="utf-8"), encoding="utf-8")
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    manifest["ground_truth"]["review_ground_truth_csv"] = truth_template.name
    manifest["operator_dry_run"]["artifacts"]["notes_file"] = notes_template.name
    customer_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "customer_grade_evidence_declared")
    assert report["status"] == "failed"
    assert not check["passed"]
    assert "review_ground_truth_csv must not reference a template" in check["detail"]
    assert "notes_file must not reference a template" in check["detail"]


def test_customer_grade_audit_rejects_truth_csv_missing_required_schema(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    (tmp_path / "review_ground_truth.csv").write_text(
        "drawing_label,category,summary_contains\nS2401,mixed,D13@100\n",
        encoding="utf-8",
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "customer_grade_evidence_declared")
    assert report["status"] == "failed"
    assert not check["passed"]
    assert "review_ground_truth CSV missing required columns" in check["detail"]


def test_customer_grade_audit_rejects_incomplete_manifest_readiness(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    manifest["readiness"] = {
        "status": "incomplete",
        "issue_count": 1,
        "issues": ["operator notes missing"],
    }
    customer_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "customer_grade_evidence_declared")
    assert report["status"] == "failed"
    assert not check["passed"]
    assert "manifest.readiness.status must be ready" in check["detail"]
    assert "manifest.readiness.issues must be empty" in check["detail"]


def test_customer_grade_audit_requires_manifest_readiness_block(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    manifest.pop("readiness")
    customer_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "customer_grade_evidence_declared")
    assert report["status"] == "failed"
    assert not check["passed"]
    assert "manifest.readiness block is required" in check["detail"]


def test_customer_grade_audit_requires_manifest_cad_policy_evidence(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    manifest.pop("cad_policy_evidence")
    customer_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert "customer_grade_evidence_declared" in failed
    assert (
        "manifest.cad_policy_evidence.block_text_detection_without_expansion must be true"
        in failed["customer_grade_evidence_declared"]["detail"]
    )


def test_customer_grade_audit_requires_approved_ground_truth_status(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    manifest["ground_truth"]["status"] = "reviewed"
    customer_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert "customer_grade_evidence_declared" in failed
    assert (
        "manifest.ground_truth.status must be approved"
        in failed["customer_grade_evidence_declared"]["detail"]
    )


def test_customer_grade_audit_requires_customer_evidence_manifest(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    release_manifest = tmp_path / "release_manifest.json"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
    )
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=release_manifest,
        min_total_pairs=1,
        evidence_level="customer_grade",
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert report["status"] == "failed"
    assert "customer_grade_evidence_declared" in failed
    assert "--customer-evidence-manifest is required" in failed["customer_grade_evidence_declared"]["detail"]


def test_mvp_exit_audit_requires_top_issue_policy_for_each_completed_output(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    release_manifest = tmp_path / "release_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_queue_mode="raw_counts",
        top_per_drawing=10,
    )
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir],
        release_manifest=release_manifest,
        min_total_pairs=20,
        evidence_level="synthetic",
    )

    check = _check_by_name(report, "top_structural_review_queue_first")
    assert report["status"] == "failed"
    assert not check["passed"]
    assert "review_queue.mode=raw_counts" in check["detail"]
    assert "top_per_drawing=10" in check["detail"]


def test_customer_grade_manifest_must_match_audited_evidence(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=1,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    _write_p5_g16_replay_json(
        pdf_dir / "p5_g16_real_corpus_replay.json",
        pdf_dir / "validation_summary.json",
        customer_manifest,
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert report["status"] == "failed"
    assert "customer_grade_evidence_declared" in failed
    assert "row_count=6 exceeds audited review_ground_truth rows=1" in failed["customer_grade_evidence_declared"]["detail"]


def test_customer_grade_manifest_requires_dataset_provenance(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    manifest["dataset_provenance"] = {
        "source_kind": "synthetic",
        "source_description": "Synthetic smoke data.",
        "approval_status": "synthetic_probe",
        "approver": "codex",
    }
    customer_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert report["status"] == "failed"
    assert "customer_grade_evidence_declared" in failed
    detail = failed["customer_grade_evidence_declared"]["detail"]
    assert "dataset_provenance.source_kind" in detail
    assert "dataset_provenance.approval_status" in detail


def test_customer_grade_manifest_requires_structural_review_lead_role(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    manifest["operator_dry_run"]["reviewer_role"] = "lead"
    customer_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert report["status"] == "failed"
    assert "customer_grade_evidence_declared" in failed
    assert "reviewer_role must be a structural review lead/team lead role" in failed[
        "customer_grade_evidence_declared"
    ]["detail"]


def test_customer_grade_manifest_requires_matching_reviewer_role_in_notes(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    notes_path = customer_manifest.parent / manifest["operator_dry_run"]["artifacts"]["notes_file"]
    notes_path.write_text(
        "Operator dry run passed.\n"
        "Reviewed by structural_review_lead.\n"
        "Operator notes:\n"
        "Reviewed S2401 zone Z-001 with synchronized Before/After zoom, Korean summary, "
        "confirmed-only export, and path audit leak_count=0.\n"
        + "\n".join(f"- [x] {check_id}" for check_id in audit.REQUIRED_OPERATOR_WORKFLOW_CHECKS),
        encoding="utf-8",
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert report["status"] == "failed"
    assert "customer_grade_evidence_declared" in failed
    assert "notes_file must include matching reviewer_role" in failed["customer_grade_evidence_declared"]["detail"]


def test_customer_grade_manifest_requires_substantive_operator_notes(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    notes_path = customer_manifest.parent / manifest["operator_dry_run"]["artifacts"]["notes_file"]
    notes_path.write_text(
        "Operator dry run passed.\n"
        "reviewer_role: structural_review_lead\n"
        "Operator notes:\n"
        + "\n".join(f"- [x] {check_id}" for check_id in audit.REQUIRED_OPERATOR_WORKFLOW_CHECKS),
        encoding="utf-8",
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert report["status"] == "failed"
    assert "customer_grade_evidence_declared" in failed
    assert "notes_file must include substantive operator dry-run review notes" in failed[
        "customer_grade_evidence_declared"
    ]["detail"]


def test_customer_grade_manifest_rejects_absolute_path_leakage(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    confirmed = pdf_dir / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    manifest["operator_dry_run"]["artifacts"]["confirmed_export_artifact"] = str(confirmed.resolve())
    customer_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert report["status"] == "failed"
    assert "customer_grade_evidence_declared" in failed
    assert "customer_evidence_manifest.json must not contain absolute/cache/temp path leakage" in failed[
        "customer_grade_evidence_declared"
    ]["detail"]


def test_customer_grade_manifest_rejects_audit_json_absolute_path_leakage(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    audit_path = customer_manifest.parent / manifest["path_leakage_audit"]["audit_json"]
    audit_payload = json.loads(audit_path.read_text(encoding="utf-8"))
    audit_payload["sources"] = [{"result_dir": str(pdf_dir.resolve()), "leak_count": 0}]
    audit_path.write_text(json.dumps(audit_payload), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert report["status"] == "failed"
    assert "customer_grade_evidence_declared" in failed
    assert "path_leakage_audit.audit_json must not contain absolute/cache/temp path leakage" in failed[
        "customer_grade_evidence_declared"
    ]["detail"]


def test_customer_grade_audit_accepts_cad_pdf_block_csv_evidence(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(
        blocked_dir,
        kind="cad",
        completed_pairs=0,
        blocked_pairs=1,
        cad_pdf_blocked_pairs=0,
    )
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    _write_p5_g16_replay_json(
        pdf_dir / "p5_g16_real_corpus_replay.json",
        pdf_dir / "validation_summary.json",
        customer_manifest,
    )

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    assert report["status"] == "passed"
    assert report["summary"]["failed"] == 0


def test_customer_grade_audit_requires_workbench_acceptance_summary(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_item=_queue_item("cad", "cad_entity"),
        include_workbench_acceptance_summary=False,
    )
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
        include_workbench_acceptance_summary=False,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    check = _check_by_name(report, "confirmed_only_cloud_and_report_export")
    assert report["status"] == "failed"
    assert check["passed"] is False
    assert "acceptance_items_5_8_8b_9b_9c_10_passed=missing_required" in check["detail"]


def test_customer_grade_manifest_requires_operator_workflow_checklist(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    manifest["operator_dry_run"]["workflow_checks"] = ["input_selection"]
    notes_path = customer_manifest.parent / manifest["operator_dry_run"]["artifacts"]["notes_file"]
    notes_path.write_text("Operator dry run passed. input_selection only.", encoding="utf-8")
    customer_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert report["status"] == "failed"
    assert "customer_grade_evidence_declared" in failed
    detail = failed["customer_grade_evidence_declared"]["detail"]
    assert "workflow_checks missing" in detail
    assert "notes_file missing checklist ids" in detail


def test_customer_grade_manifest_rejects_unchecked_operator_checklist(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    notes_path = customer_manifest.parent / manifest["operator_dry_run"]["artifacts"]["notes_file"]
    notes_path.write_text(
        "\n".join(f"- [ ] {check_id}" for check_id in audit.REQUIRED_OPERATOR_WORKFLOW_CHECKS),
        encoding="utf-8",
    )
    customer_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert report["status"] == "failed"
    assert "customer_grade_evidence_declared" in failed
    assert "notes_file missing checklist ids" in failed["customer_grade_evidence_declared"]["detail"]


def test_customer_grade_manifest_requires_confirmed_artifact_under_audited_output(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    outside = tmp_path / "manual_copy" / "pair_confirmed.png"
    outside.parent.mkdir()
    outside.write_bytes(b"png")
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    manifest["operator_dry_run"]["artifacts"]["confirmed_export_artifact"] = str(
        outside.relative_to(tmp_path).as_posix()
    )
    customer_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert report["status"] == "failed"
    assert "customer_grade_evidence_declared" in failed
    assert "confirmed_export_artifact must be a *_confirmed.* file" in failed[
        "customer_grade_evidence_declared"
    ]["detail"]


def test_customer_grade_manifest_rejects_confirmed_artifact_with_unsupported_extension(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_item=_queue_item("cad", "cad_entity"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "pair_confirmed.txt").write_text("not a cloud/report artifact", encoding="utf-8")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    manifest["operator_dry_run"]["artifacts"]["confirmed_export_artifact"] = str(
        Path("pdf") / "artifacts" / "pair_confirmed.txt"
    )
    customer_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert report["status"] == "failed"
    assert "customer_grade_evidence_declared" in failed
    assert ".png/.pdf/.dxf" in failed["customer_grade_evidence_declared"]["detail"]


def test_cad_structural_text_policy_requires_modified_grouping(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_item=_queue_item(
            "cad",
            "cad_entity",
            entity_types=["ATTRIB"],
            added_count=1,
            deleted_count=1,
            modified_count=0,
        ),
    )
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert report["status"] == "failed"
    assert "cad_structural_text_modified_grouping" in failed
    assert "@100->@200 evidence grouped as modified_count>0" in failed[
        "cad_structural_text_modified_grouping"
    ]["detail"]


def test_cad_block_text_detection_requires_unexpanded_policy_evidence(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    _write_result(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_item=_queue_item("cad", "cad_entity", entity_types=["INSERT", "ATTRIB"]),
        cad_policy={"expand_blocks": True, "block_text_detection": True},
    )

    report = audit.run_audit(
        result_dirs=[cad_dir],
        min_total_pairs=1,
        max_total_pairs=5,
        required_structural_coverage=["d13_spacing_change"],
    )

    check = _check_by_name(report, "cad_block_text_detection_without_expansion")
    assert check["passed"] is False
    assert "input.cad_policy.expand_blocks=false" in check["detail"]


def test_dwg_dxf_cad_support_requires_both_extensions(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    _write_result(
        cad_dir,
        kind="cad",
        completed_pairs=1,
        queue_item=_queue_item("cad", "cad_entity"),
        source_extensions=("dwg", "dwg"),
    )

    report = audit.run_audit(
        result_dirs=[cad_dir],
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    check = _check_by_name(report, "dwg_dxf_cad_support")
    assert check["passed"] is False
    assert "dwg_evidence=True" in check["detail"]
    assert "dxf_evidence=False" in check["detail"]


def test_pdf_pdf_support_requires_pdf_sources_on_both_sides(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=1,
        queue_item=_queue_item("pdf", "pdf_visual"),
        source_extensions=("pdf", "png"),
    )

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    check = _check_by_name(report, "pdf_pdf_support")
    assert check["passed"] is False
    assert "pdf_pair=True" in check["detail"]
    assert "pdf_review_queue=True" in check["detail"]
    assert "pdf_source_evidence=False" in check["detail"]


def test_mvp_exit_audit_requires_explicit_sharable_audit_evidence(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    release_manifest = tmp_path / "release_manifest.json"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        include_sharable_audit=False,
    )
    _write_release_manifest(release_manifest)

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=release_manifest,
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert "sharable_path_leakage_zero" in failed
    assert "missing sharable_audit.leak_count" in failed["sharable_path_leakage_zero"]["detail"]


def test_mvp_exit_audit_requires_required_preflight_checks(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    release_manifest = tmp_path / "release_manifest.json"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        preflight_checks=[{"name": "source_a", "status": "ok"}],
    )
    _write_release_manifest(release_manifest)

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=release_manifest,
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert "preflight_passed" in failed
    assert "missing preflight checks" in failed["preflight_passed"]["detail"]
    assert "pymupdf" in failed["preflight_passed"]["detail"]
    assert "oda_converter" in failed["preflight_passed"]["detail"]


def test_mvp_exit_audit_requires_ai_optional_fallback_evidence(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    release_manifest = tmp_path / "release_manifest.json"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        include_ai_policy=False,
    )
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=release_manifest,
        min_total_pairs=1,
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert "ai_optional_heuristic_fallback" in failed
    assert "missing ai_policy evidence" in failed["ai_optional_heuristic_fallback"]["detail"]


def test_mvp_exit_audit_rejects_bad_ai_fallback_policy(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    release_manifest = tmp_path / "release_manifest.json"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
    )
    summary_path = pdf_dir / "validation_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["ai_policy"]["fallback_without_model"]["classifier_used"] = "embedding"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=release_manifest,
        min_total_pairs=1,
    )

    check = _check_by_name(report, "ai_optional_heuristic_fallback")
    assert check["passed"] is False
    assert "fallback_without_model.classifier_used must be heuristic" in check["detail"]


def test_mvp_exit_audit_fails_when_any_selected_zone_perf_exceeds_budget(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    slow_dir = tmp_path / "slow"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1, zone_crop_count=0)
    _write_result(
        slow_dir,
        kind="pdf",
        completed_pairs=1,
        queue_item=_queue_item("pdf", "pdf_visual"),
        zone_cold_p95_ms=15_000.0,
        zone_hit_p95_ms=3_000.0,
    )
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir, slow_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert report["status"] == "failed"
    assert "selected_zone_render_perf" in failed
    assert "cold_p95=15000.0" in failed["selected_zone_render_perf"]["detail"]
    assert "hit_p95=3000.0" in failed["selected_zone_render_perf"]["detail"]


def test_mvp_exit_audit_fails_when_first_review_ready_exceeds_budget(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    release_manifest = tmp_path / "release_manifest.json"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        total_s=1_900.0,
    )
    _write_release_manifest(release_manifest)

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=release_manifest,
        min_total_pairs=20,
        required_structural_coverage=["d13_spacing_change"],
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert "first_review_ready_within_30min" in failed
    assert "total_s=1900.0 > 1800.0" in failed["first_review_ready_within_30min"]["detail"]


def test_mvp_exit_audit_rejects_full_viewer_prerender_policy(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    release_manifest = tmp_path / "release_manifest.json"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        viewer_render_policy="all",
    )
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=release_manifest,
        min_total_pairs=1,
    )

    check = _check_by_name(report, "viewer_metadata_first_render_policy")
    assert check["passed"] is False
    assert "viewer_render_policy=all" in check["detail"]


def test_mvp_exit_audit_enforces_twenty_to_fifty_sheet_upper_bound(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    release_manifest = tmp_path / "release_manifest.json"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=51,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
    )
    _write_release_manifest(release_manifest)

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=release_manifest,
        min_total_pairs=20,
        max_total_pairs=50,
        required_structural_coverage=["d13_spacing_change"],
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert "twenty_to_fifty_sheet_scale" in failed
    assert "completed_pairs=51, required=20..50" in failed["twenty_to_fifty_sheet_scale"]["detail"]


def test_customer_grade_selected_zone_perf_requires_each_completed_output_telemetry(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
        zone_crop_count=0,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1, zone_crop_count=0)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert report["status"] == "failed"
    assert "selected_zone_render_perf" in failed
    assert "missing selected-zone telemetry" in failed["selected_zone_render_perf"]["detail"]
    assert str(pdf_dir) in failed["selected_zone_render_perf"]["detail"]


def test_customer_grade_manifest_requires_selected_zone_perf_summary(tmp_path: Path) -> None:
    cad_dir = tmp_path / "cad"
    pdf_dir = tmp_path / "pdf"
    blocked_dir = tmp_path / "blocked"
    release_manifest = tmp_path / "release_manifest.json"
    customer_manifest = tmp_path / "customer_evidence_manifest.json"
    _write_result(cad_dir, kind="cad", completed_pairs=1, queue_item=_queue_item("cad", "cad_entity"))
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        review_ground_truth_rows=6,
    )
    _write_result(blocked_dir, kind="cad", completed_pairs=0, blocked_pairs=1)
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)
    _write_customer_evidence_manifest(customer_manifest)
    manifest = json.loads(customer_manifest.read_text(encoding="utf-8"))
    manifest["selected_zone_performance"] = {
        "status": "failed",
        "completed_outputs": 2,
        "telemetry_outputs": 1,
        "max_cold_p95_ms": 10001.0,
        "max_cache_hit_p95_ms": 2001.0,
    }
    customer_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert report["status"] == "failed"
    assert "customer_grade_evidence_declared" in failed
    detail = failed["customer_grade_evidence_declared"]["detail"]
    assert "manifest.selected_zone_performance.status must be passed" in detail
    assert "telemetry_outputs must cover every completed output" in detail
    assert "max_cold_p95_ms 10001.0 exceeds 10000.0" in detail
    assert "max_cache_hit_p95_ms 2001.0 exceeds 2000.0" in detail


def test_cad_pdf_blocking_requires_explicit_cad_pdf_evidence(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    release_manifest = tmp_path / "release_manifest.json"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        blocked_pairs=1,
        cad_pdf_blocked_pairs=0,
        structural_review_recall=1.0,
    )
    (pdf_dir / "blocked_pairs.csv").write_text(
        "a_path,b_path,a_kind,b_kind,reason\n"
        "old.pdf,new.pdf,pdf,pdf,manual block\n",
        encoding="utf-8",
    )
    _write_release_manifest(release_manifest)

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=release_manifest,
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    failed_names = {check["name"] for check in report["checks"] if not check["passed"]}
    assert "cad_pdf_cross_compare_blocked" in failed_names


def test_cad_pdf_blocking_requires_clear_block_reason(tmp_path: Path) -> None:
    blocked_dir = tmp_path / "blocked"
    _write_result(
        blocked_dir,
        kind="cad",
        completed_pairs=0,
        blocked_pairs=1,
        cad_pdf_blocked_pairs=1,
    )
    (blocked_dir / "blocked_pairs.csv").write_text(
        "a_path,b_path,a_kind,b_kind,reason\n"
        "old.dwg,new.pdf,cad,pdf,manual review\n",
        encoding="utf-8",
    )

    report = audit.run_audit(
        result_dirs=[blocked_dir],
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    check = _check_by_name(report, "cad_pdf_cross_compare_blocked")
    assert check["passed"] is False
    assert "clear blocked reason" in check["detail"]


def test_pdf_bbox_policy_requires_image_pixels_change_zone_rows(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    release_manifest = tmp_path / "release_manifest.json"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=20,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
        bbox_coordinate_space="cad_world",
    )
    (pdf_dir / "artifacts").mkdir()
    (pdf_dir / "artifacts" / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=release_manifest,
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    failed = {check["name"]: check for check in report["checks"] if not check["passed"]}
    assert "pdf_bbox_image_pixels_policy" in failed
    assert "bbox_coordinate_space=cad_world" in failed["pdf_bbox_image_pixels_policy"]["detail"]


def test_review_queue_unit_key_must_match_pair_uuid_and_zone_id(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    queue_item = _queue_item("pdf", "pdf_visual")
    queue_item["queue_key"] = "pair:wrong-zone"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=1,
        queue_item=queue_item,
        structural_review_recall=1.0,
    )

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=None,
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    check = _check_by_name(report, "review_queue_required_fields")
    assert check["passed"] is False
    assert "queue_key must equal pair_uuid:zone_id (pair_pdf:C-001)" in check["detail"]


def test_review_queue_unit_key_requires_non_empty_pair_uuid_and_zone_id(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    queue_item = _queue_item("pdf", "pdf_visual")
    queue_item["pair_uuid"] = ""
    queue_item["zone_id"] = ""
    queue_item["queue_key"] = ""
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=1,
        queue_item=queue_item,
        structural_review_recall=1.0,
    )

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=None,
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    check = _check_by_name(report, "review_queue_required_fields")
    assert check["passed"] is False
    assert "pair_uuid is empty" in check["detail"]
    assert "zone_id is empty" in check["detail"]


def test_review_queue_unit_key_must_be_unique(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    queue_item = _queue_item("pdf", "pdf_visual")
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=1,
        queue_item=queue_item,
        structural_review_recall=1.0,
    )
    summary_path = pdf_dir / "validation_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    duplicate = dict(queue_item)
    duplicate["priority_score"] = 99.0
    summary["review_dashboard"]["review_queue"]["items"].append(duplicate)
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=None,
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    check = _check_by_name(report, "review_queue_required_fields")
    assert check["passed"] is False
    assert "duplicate review_queue unit pair_pdf:C-001" in check["detail"]


def test_review_queue_status_must_be_canonical(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    queue_item = _queue_item("pdf", "pdf_visual")
    queue_item["review_status"] = "ignored"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=1,
        queue_item=queue_item,
        structural_review_recall=1.0,
    )

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=None,
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    check = _check_by_name(report, "review_queue_required_fields")
    assert check["passed"] is False
    assert "review_status must be one of" in check["detail"]


def test_review_queue_source_format_must_be_canonical(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    queue_item = _queue_item("pdf", "pdf_visual")
    queue_item["source_format"] = "dgn"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=1,
        queue_item=queue_item,
        structural_review_recall=1.0,
    )

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=None,
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    check = _check_by_name(report, "review_queue_required_fields")
    assert check["passed"] is False
    assert "source_format must be one of" in check["detail"]


def test_review_queue_detection_source_and_bbox_status_must_match_policy(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    queue_item = _queue_item("pdf", "pdf_visual")
    queue_item["detection_source"] = "cad_entity"
    queue_item["bbox_status"] = "cad_world"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=1,
        queue_item=queue_item,
        structural_review_recall=1.0,
    )

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=None,
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    check = _check_by_name(report, "review_queue_required_fields")
    assert check["passed"] is False
    assert "PDF detection_source must be one of" in check["detail"]
    assert "bbox_status must be one of" in check["detail"]


def test_mvp_exit_audit_rejects_raw_jsonl_streams_in_sharable_outputs(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=1,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
    )
    stream_path = pdf_dir / "compare_state" / "streams" / "pair_raw.jsonl"
    stream_path.parent.mkdir(parents=True)
    stream_path.write_text('{"raw": true}\n', encoding="utf-8")

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=None,
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    check = _check_by_name(report, "sharable_raw_jsonl_streams_absent")
    assert check["passed"] is False
    assert "raw stream files found: 1" in check["detail"]
    assert str(stream_path) in check["evidence"]


def test_confirmed_export_requires_concrete_artifacts_not_release_step_only(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    release_manifest = tmp_path / "release_manifest.json"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=1,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
    )
    _write_release_manifest(release_manifest)

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=release_manifest,
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    check = _check_by_name(report, "confirmed_only_cloud_and_report_export")
    assert check["passed"] is False
    assert "confirmed_files=0" in check["detail"]
    assert "report_files=0" in check["detail"]
    assert "release_manifest_workbench_acceptance_smoke=ignored_for_artifact_gate" in check["detail"]


def test_confirmed_export_acceptance_summary_items_must_pass(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    release_manifest = tmp_path / "release_manifest.json"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=1,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
    )
    confirmed_dir = pdf_dir / "artifacts" / "confirmed_clouds"
    confirmed_dir.mkdir(parents=True)
    (confirmed_dir / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_workbench_acceptance_summary(pdf_dir / "workbench_acceptance_summary.json", item8=True, item10=False)
    _write_release_manifest(release_manifest)

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=release_manifest,
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    check = _check_by_name(report, "confirmed_only_cloud_and_report_export")
    assert check["passed"] is False
    assert "acceptance_summaries=1" in check["detail"]
    assert "failed required check(s): 10." in check["detail"]


def test_confirmed_export_acceptance_summary_requires_non_confirmed_decision_check(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    release_manifest = tmp_path / "release_manifest.json"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=1,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
    )
    confirmed_dir = pdf_dir / "artifacts" / "confirmed_clouds"
    confirmed_dir.mkdir(parents=True)
    (confirmed_dir / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_workbench_acceptance_summary(
        pdf_dir / "workbench_acceptance_summary.json",
        item8=True,
        item8b=False,
        item10=True,
    )
    _write_release_manifest(release_manifest)

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=release_manifest,
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    check = _check_by_name(report, "confirmed_only_cloud_and_report_export")
    assert check["passed"] is False
    assert "acceptance_summaries=1" in check["detail"]
    assert "failed required check(s): 8b." in check["detail"]


def test_confirmed_export_acceptance_summary_requires_selected_zone_sync_check(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    release_manifest = tmp_path / "release_manifest.json"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=1,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
    )
    confirmed_dir = pdf_dir / "artifacts" / "confirmed_clouds"
    confirmed_dir.mkdir(parents=True)
    (confirmed_dir / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_workbench_acceptance_summary(
        pdf_dir / "workbench_acceptance_summary.json",
        item8=True,
        item8b=True,
        item9b=False,
        item10=True,
    )
    _write_release_manifest(release_manifest)

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=release_manifest,
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    check = _check_by_name(report, "confirmed_only_cloud_and_report_export")
    assert check["passed"] is False
    assert "acceptance_summaries=1" in check["detail"]
    assert "failed required check(s): 9b." in check["detail"]


def test_confirmed_export_acceptance_summary_requires_nonblocking_zone_render_check(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    release_manifest = tmp_path / "release_manifest.json"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=1,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
    )
    confirmed_dir = pdf_dir / "artifacts" / "confirmed_clouds"
    confirmed_dir.mkdir(parents=True)
    (confirmed_dir / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_workbench_acceptance_summary(
        pdf_dir / "workbench_acceptance_summary.json",
        item8=True,
        item8b=True,
        item9c=False,
        item10=True,
    )
    _write_release_manifest(release_manifest)

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=release_manifest,
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    check = _check_by_name(report, "confirmed_only_cloud_and_report_export")
    assert check["passed"] is False
    assert "acceptance_summaries=1" in check["detail"]
    assert "failed required check(s): 9c." in check["detail"]


def test_confirmed_export_acceptance_summary_requires_review_queue_first_screen_check(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    release_manifest = tmp_path / "release_manifest.json"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=1,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
    )
    confirmed_dir = pdf_dir / "artifacts" / "confirmed_clouds"
    confirmed_dir.mkdir(parents=True)
    (confirmed_dir / "pair_confirmed.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_workbench_acceptance_summary(
        pdf_dir / "workbench_acceptance_summary.json",
        item5=False,
        item8=True,
        item8b=True,
        item10=True,
    )
    _write_release_manifest(release_manifest)

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=release_manifest,
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    check = _check_by_name(report, "confirmed_only_cloud_and_report_export")
    assert check["passed"] is False
    assert "acceptance_summaries=1" in check["detail"]
    assert "failed required check(s): 5." in check["detail"]


def test_confirmed_export_rejects_unconfirmed_files_in_confirmed_cloud_dir(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    release_manifest = tmp_path / "release_manifest.json"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=1,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
    )
    confirmed_dir = pdf_dir / "artifacts" / "confirmed_clouds"
    confirmed_dir.mkdir(parents=True)
    (confirmed_dir / "pair_confirmed.png").write_bytes(b"png")
    (confirmed_dir / "pair_false_positive.png").write_bytes(b"png")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=release_manifest,
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    check = _check_by_name(report, "confirmed_only_cloud_and_report_export")
    assert check["passed"] is False
    assert "unexpected_confirmed_cloud_files=1" in check["detail"]


def test_confirmed_export_rejects_unsupported_confirmed_extension(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    release_manifest = tmp_path / "release_manifest.json"
    _write_result(
        pdf_dir,
        kind="pdf",
        completed_pairs=1,
        queue_item=_queue_item("pdf", "pdf_visual"),
        structural_review_recall=1.0,
    )
    confirmed_dir = pdf_dir / "artifacts" / "confirmed_clouds"
    confirmed_dir.mkdir(parents=True)
    (confirmed_dir / "pair_confirmed.txt").write_text("not a cloud/report artifact", encoding="utf-8")
    (pdf_dir / "artifacts" / "review_report_test.pdf").write_bytes(b"%PDF")
    _write_release_manifest(release_manifest)

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=release_manifest,
        min_total_pairs=1,
        required_structural_coverage=["d13_spacing_change"],
    )

    check = _check_by_name(report, "confirmed_only_cloud_and_report_export")
    assert check["passed"] is False
    assert "confirmed_files=0" in check["detail"]
    assert "unexpected_confirmed_cloud_files=1" in check["detail"]


def test_mvp_exit_audit_fails_without_review_ground_truth(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdf"
    release_manifest = tmp_path / "release_manifest.json"
    _write_result(pdf_dir, kind="pdf", completed_pairs=20, queue_item=_queue_item("pdf", "pdf_visual"))
    _write_release_manifest(release_manifest)

    report = audit.run_audit(
        result_dirs=[pdf_dir],
        release_manifest=release_manifest,
        min_total_pairs=1,
    )

    failed_names = {check["name"] for check in report["checks"] if not check["passed"]}
    assert report["status"] == "failed"
    assert "structural_review_queue_recall" in failed_names


def test_customer_grade_release_manifest_requires_prompt_to_artifact_checklist(tmp_path: Path) -> None:
    release_manifest = tmp_path / "release_manifest.json"
    release_manifest.write_text(
        json.dumps(
            {
                "status": "passed",
                "artifacts": {},
                "steps": [
                    {"name": "pyinstaller_build", "status": "passed"},
                    {"name": "packaged_app_launch_smoke", "status": "passed"},
                ],
            }
        ),
        encoding="utf-8",
    )
    release = json.loads(release_manifest.read_text(encoding="utf-8"))

    check = audit._check_release_manifest(
        release,
        release_manifest,
        evidence_level="customer_grade",
    )

    assert check.passed is False
    assert "mvp_exit_prompt_to_artifact_checklist" in check.detail


def test_customer_grade_release_manifest_rejects_incomplete_prompt_to_artifact_checklist(tmp_path: Path) -> None:
    release_manifest = tmp_path / "release_manifest.json"
    checklist = tmp_path / "mvp_exit_prompt_to_artifact_checklist.md"
    checklist.write_text("# Drawing Compare MVP Prompt-to-Artifact Checklist\n", encoding="utf-8")
    release_manifest.write_text(
        json.dumps(
            {
                "status": "passed",
                "artifacts": {
                    "mvp_exit_prompt_to_artifact_checklist": checklist.name,
                },
                "steps": [
                    {"name": "pyinstaller_build", "status": "passed"},
                    {"name": "packaged_app_launch_smoke", "status": "passed"},
                ],
            }
        ),
        encoding="utf-8",
    )
    release = json.loads(release_manifest.read_text(encoding="utf-8"))

    check = audit._check_release_manifest(
        release,
        release_manifest,
        evidence_level="customer_grade",
    )

    assert check.passed is False
    assert "missing required term" in check.detail
    assert "DWG/DXF comparison supported" in check.detail


def test_customer_grade_release_manifest_requires_manifest_self_check_terms(tmp_path: Path) -> None:
    release_manifest = tmp_path / "release_manifest.json"
    checklist = tmp_path / "mvp_exit_prompt_to_artifact_checklist.md"
    checklist.write_text(
        "# Drawing Compare MVP Prompt-to-Artifact Checklist\n"
        + "\n".join(
            term
            for term in audit.REQUIRED_PROMPT_TO_ARTIFACT_CHECKLIST_TERMS
            if term != "customer_evidence_manifest_summaries"
        ),
        encoding="utf-8",
    )
    release_manifest.write_text(
        json.dumps(
            {
                "status": "passed",
                "artifacts": {
                    "mvp_exit_prompt_to_artifact_checklist": checklist.name,
                },
                "steps": [
                    {"name": "pyinstaller_build", "status": "passed"},
                    {"name": "packaged_app_launch_smoke", "status": "passed"},
                ],
            }
        ),
        encoding="utf-8",
    )
    release = json.loads(release_manifest.read_text(encoding="utf-8"))

    check = audit._check_release_manifest(
        release,
        release_manifest,
        evidence_level="customer_grade",
    )

    assert check.passed is False
    assert "mvp_exit_prompt_to_artifact_checklist missing required term(s)" in check.detail
    assert "customer_evidence_manifest_summaries" in check.detail


def test_release_manifest_requires_customer_shareable_package_path_audit(tmp_path: Path) -> None:
    release_manifest = tmp_path / "release_manifest.json"
    release_manifest.write_text(
        json.dumps(
            {
                "status": "passed",
                "artifacts": {},
                "steps": [
                    {"name": "pyinstaller_build", "status": "passed"},
                    {"name": "packaged_app_launch_smoke", "status": "passed"},
                ],
            }
        ),
        encoding="utf-8",
    )
    release = json.loads(release_manifest.read_text(encoding="utf-8"))

    check = audit._check_release_manifest(release, release_manifest)

    assert check.passed is False
    assert "customer_shareable_package_path_audit" in check.detail


def test_release_manifest_requires_customer_shareable_package_zip_and_manifest(tmp_path: Path) -> None:
    release_manifest = tmp_path / "release_manifest.json"
    package_audit = tmp_path / "customer_package_path_audit.json"
    package_audit.write_text(
        json.dumps(
            {
                "status": "passed",
                "leak_count": 0,
                "scanned_files": 10,
                "scanned_app_first_party_files": 25,
            }
        ),
        encoding="utf-8",
    )
    release_manifest.write_text(
        json.dumps(
            {
                "status": "passed",
                "artifacts": {
                    "customer_shareable_package_path_audit": package_audit.name,
                },
                "steps": [
                    {"name": "pyinstaller_build", "status": "passed"},
                    {"name": "packaged_app_launch_smoke", "status": "passed"},
                    {"name": "customer_shareable_package_path_audit", "status": "passed"},
                ],
            }
        ),
        encoding="utf-8",
    )
    release = json.loads(release_manifest.read_text(encoding="utf-8"))

    check = audit._check_release_manifest(release, release_manifest)

    assert check.passed is False
    assert "customer_shareable_package_zip" in check.detail
    assert "customer_shareable_package_manifest" in check.detail


def test_release_manifest_rejects_customer_shareable_package_manifest_with_internal_manifest(
    tmp_path: Path,
) -> None:
    release_manifest = tmp_path / "release_manifest.json"
    _write_release_manifest(release_manifest)
    release = json.loads(release_manifest.read_text(encoding="utf-8"))
    package_manifest = tmp_path / release["artifacts"]["customer_shareable_package_manifest"]
    manifest = json.loads(package_manifest.read_text(encoding="utf-8"))
    manifest["internal_release_manifest_included"] = True
    package_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    check = audit._check_release_manifest(release, release_manifest)

    assert check.passed is False
    assert "internal_release_manifest_included must be false" in check.detail


def test_release_manifest_rejects_customer_shareable_package_missing_required_contents(
    tmp_path: Path,
) -> None:
    release_manifest = tmp_path / "release_manifest.json"
    _write_release_manifest(release_manifest)
    release = json.loads(release_manifest.read_text(encoding="utf-8"))
    package_manifest = tmp_path / release["artifacts"]["customer_shareable_package_manifest"]
    manifest = json.loads(package_manifest.read_text(encoding="utf-8"))
    manifest["contents"] = [
        entry
        for entry in audit.REQUIRED_CUSTOMER_PACKAGE_CONTENTS
        if entry != "app/DrawingCompareWorkbench/DrawingCompareWorkbench.exe"
    ]
    package_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    check = audit._check_release_manifest(release, release_manifest)

    assert check.passed is False
    assert "customer_shareable_package_manifest.contents missing required entries" in check.detail
    assert "app/DrawingCompareWorkbench/DrawingCompareWorkbench.exe" in check.detail


def test_release_manifest_rejects_customer_shareable_zip_missing_required_contents(
    tmp_path: Path,
) -> None:
    release_manifest = tmp_path / "release_manifest.json"
    _write_release_manifest(release_manifest)
    release = json.loads(release_manifest.read_text(encoding="utf-8"))
    package_zip = tmp_path / release["artifacts"]["customer_shareable_package_zip"]
    with zipfile.ZipFile(package_zip, "w") as archive:
        for entry in audit.REQUIRED_CUSTOMER_PACKAGE_CONTENTS:
            if entry != "cli/audit_drawing_compare_mvp_exit.py":
                archive.writestr(entry, "placeholder")

    check = audit._check_release_manifest(release, release_manifest)

    assert check.passed is False
    assert "customer_shareable_package_zip missing required entries" in check.detail
    assert "cli/audit_drawing_compare_mvp_exit.py" in check.detail


def test_release_manifest_rejects_customer_shareable_zip_mojibake_request_sheet(
    tmp_path: Path,
) -> None:
    release_manifest = tmp_path / "release_manifest.json"
    _write_release_manifest(release_manifest)
    release = json.loads(release_manifest.read_text(encoding="utf-8"))
    package_zip = tmp_path / release["artifacts"]["customer_shareable_package_zip"]
    package_manifest = json.loads(
        (tmp_path / release["artifacts"]["customer_shareable_package_manifest"]).read_text(encoding="utf-8")
    )
    package_audit = json.loads(
        (tmp_path / release["artifacts"]["customer_shareable_package_path_audit"]).read_text(encoding="utf-8")
    )
    prompt_checklist = (
        "# Drawing Compare MVP Prompt-to-Artifact Checklist\n"
        + "\n".join(audit.REQUIRED_PROMPT_TO_ARTIFACT_CHECKLIST_TERMS)
        + "\n"
    )
    with zipfile.ZipFile(package_zip, "w") as archive:
        for entry in package_manifest["contents"]:
            if entry == "customer_package_manifest.json":
                archive.writestr(entry, json.dumps(package_manifest))
            elif entry == "customer_package_path_audit.json":
                archive.writestr(entry, json.dumps(package_audit))
            elif entry == "mvp_exit_prompt_to_artifact_checklist.md":
                archive.writestr(entry, prompt_checklist)
            elif entry == "customer_evidence_request_ko.md":
                archive.writestr(entry, "Drawing Compare mojibake request placeholder\n")
            elif entry.startswith("app/DrawingCompareWorkbench/_internal/src/"):
                archive.writestr(entry, "# first-party source\n")
            else:
                archive.writestr(entry, "placeholder")

    check = audit._check_release_manifest(release, release_manifest)

    assert check.passed is False
    assert (
        "customer_shareable_package_zip customer_evidence_request_ko.md "
        "missing required term(s)"
    ) in check.detail
    assert "Drawing Compare 고객급 증거 요청서" in check.detail


def test_customer_grade_release_manifest_rejects_customer_zip_stale_prompt_checklist(
    tmp_path: Path,
) -> None:
    release_manifest = tmp_path / "release_manifest.json"
    _write_release_manifest(release_manifest)
    release = json.loads(release_manifest.read_text(encoding="utf-8"))
    package_zip = tmp_path / release["artifacts"]["customer_shareable_package_zip"]
    package_manifest = json.loads(
        (tmp_path / release["artifacts"]["customer_shareable_package_manifest"]).read_text(encoding="utf-8")
    )
    package_audit = json.loads(
        (tmp_path / release["artifacts"]["customer_shareable_package_path_audit"]).read_text(encoding="utf-8")
    )
    with zipfile.ZipFile(package_zip, "w") as archive:
        for entry in package_manifest["contents"]:
            if entry == "customer_package_manifest.json":
                archive.writestr(entry, json.dumps(package_manifest))
            elif entry == "customer_package_path_audit.json":
                archive.writestr(entry, json.dumps(package_audit))
            elif entry == "mvp_exit_prompt_to_artifact_checklist.md":
                archive.writestr(entry, "# stale checklist\nDWG/DXF comparison supported\n")
            elif entry.startswith("app/DrawingCompareWorkbench/_internal/src/"):
                archive.writestr(entry, "# first-party source\n")
            else:
                archive.writestr(entry, "placeholder")

    check = audit._check_release_manifest(
        release,
        release_manifest,
        evidence_level="customer_grade",
    )

    assert check.passed is False
    assert (
        "customer_shareable_package_zip mvp_exit_prompt_to_artifact_checklist.md "
        "missing required term(s)"
    ) in check.detail
    assert "customer_evidence_manifest_summaries" in check.detail


def test_release_manifest_rejects_customer_shareable_zip_internal_release_manifest(
    tmp_path: Path,
) -> None:
    release_manifest = tmp_path / "release_manifest.json"
    _write_release_manifest(release_manifest)
    release = json.loads(release_manifest.read_text(encoding="utf-8"))
    package_zip = tmp_path / release["artifacts"]["customer_shareable_package_zip"]
    with zipfile.ZipFile(package_zip, "a") as archive:
        archive.writestr("release_manifest.json", json.dumps({"internal": True}))

    check = audit._check_release_manifest(release, release_manifest)

    assert check.passed is False
    assert "customer_shareable_package_zip must not include internal release_manifest.json" in check.detail


def test_release_manifest_rejects_customer_shareable_zip_python_bytecode(
    tmp_path: Path,
) -> None:
    release_manifest = tmp_path / "release_manifest.json"
    _write_release_manifest(release_manifest)
    release = json.loads(release_manifest.read_text(encoding="utf-8"))
    package_zip = tmp_path / release["artifacts"]["customer_shareable_package_zip"]
    with zipfile.ZipFile(package_zip, "a") as archive:
        archive.writestr(
            "app/DrawingCompareWorkbench/_internal/src/services/__pycache__/leaky.cpython-312.pyc",
            b"C:\\Users\\user\\.codex\\worktrees\\45ea\\02.TEKLA_MCP",
        )

    check = audit._check_release_manifest(release, release_manifest)

    assert check.passed is False
    assert "contains disallowed bytecode/cache entries" in check.detail


def test_release_manifest_rejects_customer_shareable_zip_actual_payload_path_leak(
    tmp_path: Path,
) -> None:
    release_manifest = tmp_path / "release_manifest.json"
    _write_release_manifest(release_manifest)
    release = json.loads(release_manifest.read_text(encoding="utf-8"))
    package_zip = tmp_path / release["artifacts"]["customer_shareable_package_zip"]
    package_manifest = json.loads(
        (tmp_path / release["artifacts"]["customer_shareable_package_manifest"]).read_text(encoding="utf-8")
    )
    package_audit = json.loads(
        (tmp_path / release["artifacts"]["customer_shareable_package_path_audit"]).read_text(encoding="utf-8")
    )
    with zipfile.ZipFile(package_zip, "w") as archive:
        for entry in package_manifest["contents"]:
            if entry == "customer_package_manifest.json":
                archive.writestr(entry, json.dumps(package_manifest))
            elif entry == "customer_package_path_audit.json":
                archive.writestr(entry, json.dumps(package_audit))
            elif entry == "app/DrawingCompareWorkbench/DrawingCompareWorkbench.exe":
                archive.writestr(
                    entry,
                    b"bundle path C:\\Users\\user\\.codex\\worktrees\\45ea\\02.TEKLA_MCP\\dist",
                )
            elif entry.startswith("app/DrawingCompareWorkbench/_internal/src/"):
                archive.writestr(entry, "# first-party source\n")
            else:
                archive.writestr(entry, "placeholder")

    check = audit._check_release_manifest(release, release_manifest)

    assert check.passed is False
    assert "customer_shareable_package_zip actual payload leak_count must be 0" in check.detail
    assert "app/DrawingCompareWorkbench/DrawingCompareWorkbench.exe" in check.detail


def test_release_manifest_rejects_customer_shareable_zip_stale_path_audit(
    tmp_path: Path,
) -> None:
    release_manifest = tmp_path / "release_manifest.json"
    _write_release_manifest(release_manifest)
    release = json.loads(release_manifest.read_text(encoding="utf-8"))
    package_zip = tmp_path / release["artifacts"]["customer_shareable_package_zip"]
    package_manifest = json.loads(
        (tmp_path / release["artifacts"]["customer_shareable_package_manifest"]).read_text(encoding="utf-8")
    )
    zip_audit_payload = {
        "status": "failed",
        "leak_count": 1,
        "scanned_files": 10,
        "scanned_app_first_party_files": 25,
    }
    with zipfile.ZipFile(package_zip, "w") as archive:
        for entry in audit.REQUIRED_CUSTOMER_PACKAGE_CONTENTS:
            if entry == "customer_package_manifest.json":
                archive.writestr(entry, json.dumps(package_manifest))
            elif entry == "customer_package_path_audit.json":
                archive.writestr(entry, json.dumps(zip_audit_payload))
            else:
                archive.writestr(entry, "placeholder")

    check = audit._check_release_manifest(release, release_manifest)

    assert check.passed is False
    assert "customer_shareable_package_zip customer_package_path_audit.json.status must be passed" in check.detail
    assert "customer_shareable_package_zip customer_package_path_audit.json.leak_count must be 0" in check.detail


def test_release_manifest_rejects_customer_shareable_package_disallowed_file_count(
    tmp_path: Path,
) -> None:
    release_manifest = tmp_path / "release_manifest.json"
    _write_release_manifest(release_manifest)
    release = json.loads(release_manifest.read_text(encoding="utf-8"))
    package_audit = tmp_path / release["artifacts"]["customer_shareable_package_path_audit"]
    audit_payload = json.loads(package_audit.read_text(encoding="utf-8"))
    audit_payload["status"] = "passed"
    audit_payload["leak_count"] = 0
    audit_payload["disallowed_file_count"] = 1
    package_audit.write_text(json.dumps(audit_payload), encoding="utf-8")

    check = audit._check_release_manifest(release, release_manifest)

    assert check.passed is False
    assert "customer_shareable_package_path_audit.disallowed_file_count must be 0" in check.detail


def test_release_manifest_requires_customer_package_path_audit_scan_coverage(
    tmp_path: Path,
) -> None:
    release_manifest = tmp_path / "release_manifest.json"
    _write_release_manifest(release_manifest)
    release = json.loads(release_manifest.read_text(encoding="utf-8"))
    package_audit = tmp_path / release["artifacts"]["customer_shareable_package_path_audit"]
    audit_payload = json.loads(package_audit.read_text(encoding="utf-8"))
    audit_payload["scanned_files"] = 0
    audit_payload["scanned_app_first_party_files"] = 0
    audit_payload["scanned_binary_files"] = 0
    package_audit.write_text(json.dumps(audit_payload), encoding="utf-8")

    check = audit._check_release_manifest(release, release_manifest)

    assert check.passed is False
    assert "customer_shareable_package_path_audit.scanned_files must be > 0" in check.detail
    assert (
        "customer_shareable_package_path_audit.scanned_app_first_party_files must be > 0"
        in check.detail
    )
    assert "customer_shareable_package_path_audit.scanned_binary_files must be > 0" in check.detail


def test_release_manifest_rejects_customer_shareable_package_path_leaks(tmp_path: Path) -> None:
    release_manifest = tmp_path / "release_manifest.json"
    package_audit = tmp_path / "customer_package_path_audit.json"
    package_audit.write_text(
        json.dumps({"status": "failed", "leak_count": 1}),
        encoding="utf-8",
    )
    release_manifest.write_text(
        json.dumps(
            {
                "status": "passed",
                "artifacts": {
                    "customer_shareable_package_path_audit": package_audit.name,
                },
                "steps": [
                    {"name": "pyinstaller_build", "status": "passed"},
                    {"name": "packaged_app_launch_smoke", "status": "passed"},
                    {"name": "customer_shareable_package_path_audit", "status": "failed"},
                ],
            }
        ),
        encoding="utf-8",
    )
    release = json.loads(release_manifest.read_text(encoding="utf-8"))

    check = audit._check_release_manifest(release, release_manifest)

    assert check.passed is False
    assert "customer_shareable_package_path_audit.status must be passed" in check.detail
    assert "customer_shareable_package_path_audit.leak_count must be 0" in check.detail
