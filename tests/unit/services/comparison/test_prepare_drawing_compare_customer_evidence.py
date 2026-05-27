"""Tests for customer-grade Drawing Compare evidence manifest preparation."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import prepare_drawing_compare_customer_evidence as prepare


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
    assert manifest["readiness"]["status"] == "ready"
    assert manifest["readiness"]["issue_count"] == 0
    assert manifest["readiness"]["issues"] == []
    assert result["summary"]["cad_policy_evidence"]["block_text_detection_without_expansion"] is True
    assert (tmp_path / "sharable_path_audit_summary.json").exists()


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
