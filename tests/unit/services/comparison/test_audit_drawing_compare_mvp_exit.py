"""Tests for the Drawing Compare MVP exit audit script."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from scripts import audit_drawing_compare_mvp_exit as audit


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
        summary["selected_zone_evidence"] = {
            "renders": [{"bbox_status": "exact"} for _ in range(max(1, zone_crop_count))]
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

    report = audit.run_audit(
        result_dirs=[cad_dir, pdf_dir, blocked_dir],
        release_manifest=release_manifest,
        customer_evidence_manifest=customer_manifest,
        min_total_pairs=20,
        evidence_level="customer_grade",
    )

    assert report["status"] == "passed"
    assert report["summary"]["failed"] == 0


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
