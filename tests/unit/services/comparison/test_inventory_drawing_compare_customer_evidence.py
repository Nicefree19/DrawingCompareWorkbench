"""Tests for customer-grade evidence inventory preflight."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import inventory_drawing_compare_customer_evidence as inventory


def _write_validation(
    path: Path,
    *,
    kind: str,
    completed_pairs: int,
    source_extensions: tuple[str, str],
    cad_pdf_blocked: bool = False,
    workbench_acceptance: bool = False,
    review_ground_truth_rows: int | None = None,
    cad_block_text_no_expand: bool = True,
    forced_tile_eviction: bool = False,
    tile_cache_mb: float = 0.25,
) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "_SUCCESS").write_text(json.dumps({"run_id": "run_test"}), encoding="utf-8")
    if cad_pdf_blocked:
        (path / "blocked_pairs.csv").write_text(
            "a_path,b_path,a_kind,b_kind,reason\n"
            "old.dwg,new.pdf,cad,pdf,CAD/PDF cross-family comparison is blocked\n",
            encoding="utf-8",
        )
    summary = {
        "input": {"a": f"before.{source_extensions[0]}", "b": f"after.{source_extensions[1]}"},
        "files": {
            "a_kind_counts": {kind: 1} if kind in {"cad", "pdf"} else {},
            "b_kind_counts": {kind: 1} if kind in {"cad", "pdf"} else {},
        },
        "matching": {"cad_pdf_blocked_pairs": 1 if cad_pdf_blocked else 0},
        "comparison": {"completed_pairs": completed_pairs},
        "change_artifacts": {
            "artifacts": [
                {
                    "source_a": f"<redacted>/before.{source_extensions[0]}",
                    "source_b": f"<redacted>/after.{source_extensions[1]}",
                }
            ]
            if completed_pairs
            else []
        },
        "review_dashboard": {
            "review_queue": {
                "mode": "structural_core",
                "top_per_drawing": 5,
                "items": [
                    {
                        "pair_uuid": "pair",
                        "zone_id": "C-001",
                        "bbox_status": "exact",
                        "priority_rank": 1,
                    }
                ]
                if completed_pairs
                else [],
            }
        },
        "viewer_perf_summary": {
            "zone_crop_count": 2 if completed_pairs else 0,
            "zone_crop_cold_ms": {"p95": 500.0},
            "zone_crop_cache_hit_ms": {"p95": 10.0},
        },
        "sharable_audit": {"leak_count": 0},
        "review_ground_truth": {
            "rows": review_ground_truth_rows if review_ground_truth_rows is not None else (6 if completed_pairs else 0)
        },
        "selected_zone_evidence": {
            "renders": [{"bbox_status": "exact"} for _ in range(2 if completed_pairs else 0)]
        },
        "first_interactive_ready": {
            "schema_version": 1,
            "status": "passed",
            "profile": "standard",
            "speed_profile": False,
            "review_dashboard_ready_s": 10.0,
            "first_top_issue_ready_s": 10.0,
            "viewer_metadata_ready_s": 12.0,
            "thresholds": {
                "review_dashboard_ready_s": 600.0,
                "first_top_issue_ready_s": 600.0,
                "viewer_metadata_ready_s": 900.0,
            },
            "issues": [],
        }
        if completed_pairs
        else None,
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
    if kind == "cad" and completed_pairs and cad_block_text_no_expand:
        summary["input"]["cad_policy"] = {
            "expand_blocks": False,
            "block_text_detection": True,
        }
        summary["review_dashboard"]["review_queue"]["items"] = [
            {
                "pair_uuid": "pair",
                "zone_id": "C-001",
                "queue_key": "pair:C-001",
                "source_format": "cad",
                "detection_source": "cad_entity",
                "bbox_status": "exact",
                "priority_rank": 1,
                "entity_types": ["ATTRIB"],
                "change_summary_ko": "배근 간격 변경: D13@100 -> D13@200",
                "reason_ko": "블록 속성 텍스트의 @100 -> @200 변경입니다.",
                "modified_count": 1,
                "added_count": 0,
                "deleted_count": 0,
            }
        ]
    (path / "validation_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    if workbench_acceptance:
        _write_workbench_acceptance(path / "workbench_acceptance_summary.json")


def _write_workbench_acceptance(path: Path) -> None:
    checks = [
        {"name": f"{prefix} acceptance item", "passed": True}
        for prefix in inventory.REQUIRED_WORKBENCH_ACCEPTANCE_ITEMS
    ]
    path.write_text(json.dumps({"schema_version": 1, "checks": checks}), encoding="utf-8")


def _write_operator_notes(
    path: Path,
    *,
    include_role: bool = True,
    reviewer_role: str = "structural_review_lead",
    encoding: str = "utf-8",
) -> None:
    lines = []
    if include_role:
        lines.append(f"reviewer_role: {reviewer_role}")
    else:
        lines.append("Reviewed by structural_review_lead.")
    lines.append("Operator notes:")
    lines.append(
        "Reviewed S2401 zone Z-001 with synchronized Before/After zoom, "
        "Korean summary, confirmed-only export, and path audit leak_count=0."
    )
    lines.extend(f"- [x] {check}" for check in inventory.REQUIRED_OPERATOR_WORKFLOW_CHECKS)
    path.write_text("\n".join(lines), encoding=encoding)


def _write_truth_csv(path: Path, *, rows: int = 1) -> None:
    body = [
        f"S-{idx:03d},mixed,D13@100,pdf,pdf_text|pdf_visual,exact|page_fallback,row {idx}"
        for idx in range(1, rows + 1)
    ]
    path.write_text(
        "drawing_label,category,summary_contains,source_format,detection_source,bbox_status,notes\n"
        + "\n".join(body)
        + "\n",
        encoding="utf-8",
    )
    _write_review_decision_truth_csv(path.parent / "review_decision_truth.csv")
    _write_dataset_strata_csv(path.parent / "dataset_strata.csv")


def _write_review_decision_truth_csv(path: Path) -> None:
    buckets = [
        "member_add_delete_move",
        "section_dimension_change",
        "d13_spacing_change",
        "shd13_spacing_change",
        "grid_change",
        "structural_text_change",
    ]
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
        format_pair = "dwg_dxf" if index < 8 else "pdf_pdf" if index < 16 else "cad_pdf_blocked"
        risk = "raster_pdf" if index in {8, 9} else "standard"
        lines.append(
            f"pair-{index:03d},S{index:03d},{format_pair},{sheet_types[index % len(sheet_types)]},"
            f"{risk},{str(index in {0, 1}).lower()},{str(index in {0, 1}).lower()},"
            f"{str(index in {2, 3}).lower()},stratified"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_large_dwg_probe(path: Path, *, elapsed_s: float = 55.0) -> None:
    path.write_text(
        json.dumps(
            {
                "elapsed_s": elapsed_s,
                "total": 350_178,
                "change_records_in_memory": 50_000,
                "stream_exists": True,
                "stream_bytes": 12_345,
                "progress_event_count": 6,
                "progress_messages": [
                    "DXF_COMPARE_PROGRESS 0.00",
                    "DXF_COMPARE_PROGRESS 0.20",
                    "DXF_COMPARE_PROGRESS 0.40",
                    "DXF_COMPARE_PROGRESS 0.60",
                    "DXF_COMPARE_PROGRESS 0.80",
                    "DXF_COMPARE_PROGRESS 1.00",
                ],
                "metadata": {
                    "large_drawing_mode": "active",
                    "change_zone_stream_complete": True,
                    "change_zone_record_count": 350_178,
                    "change_records_in_memory": 50_000,
                },
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
                "artifacts": {
                    "customer_evidence_manifest": "customer_evidence_manifest.json",
                    "validation_summary": "validation_summary.json",
                },
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
                "args": {
                    "customer_evidence_manifest": "customer_evidence_manifest.json",
                    "validation_summary": "validation_summary.json",
                },
                "summary": {
                    "completed_visit_count": 100,
                    "blank_view_count": 0,
                    "orphan_worker_count": 0,
                    "native_resource_summary": {"measurement_available": True},
                    "worker_tree_summary": {"cleanup_ok": True, "orphan_worker_count": 0},
                },
            }
        ),
        encoding="utf-8",
    )


def _write_ready_inventory_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    cad = tmp_path / "cad_validation"
    pdf = tmp_path / "pdf_validation"
    blocked = tmp_path / "cad_pdf_block_validation"
    _write_validation(cad, kind="cad", completed_pairs=1, source_extensions=("dwg", "dxf"))
    _write_validation(
        pdf,
        kind="pdf",
        completed_pairs=20,
        source_extensions=("pdf", "pdf"),
        workbench_acceptance=True,
    )
    _write_validation(
        blocked,
        kind="blocked",
        completed_pairs=0,
        source_extensions=("dwg", "pdf"),
        cad_pdf_blocked=True,
    )
    _write_truth_csv(tmp_path / "review_ground_truth.csv")
    _write_operator_notes(tmp_path / "operator_dry_run_notes.md")
    confirmed = pdf / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")
    (tmp_path / "release_manifest.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "steps": [
                    {
                        "name": "realset_validation",
                        "env_overrides": {"DRAWING_COMPARE_TILE_CACHE_MB": "0.25"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    large_dwg_probe = tmp_path / "large_dwg_probe.json"
    _write_large_dwg_probe(large_dwg_probe)
    return cad, pdf, blocked, large_dwg_probe


def test_inventory_reports_ready_for_manifest_when_customer_evidence_is_present(tmp_path: Path) -> None:
    cad = tmp_path / "cad_validation"
    pdf = tmp_path / "pdf_validation"
    blocked = tmp_path / "cad_pdf_block_validation"
    _write_validation(cad, kind="cad", completed_pairs=1, source_extensions=("dwg", "dxf"))
    _write_validation(
        pdf,
        kind="pdf",
        completed_pairs=20,
        source_extensions=("pdf", "pdf"),
        workbench_acceptance=True,
    )
    _write_validation(
        blocked,
        kind="blocked",
        completed_pairs=0,
        source_extensions=("dwg", "pdf"),
        cad_pdf_blocked=True,
    )
    _write_truth_csv(tmp_path / "review_ground_truth.csv")
    _write_operator_notes(tmp_path / "operator_dry_run_notes.md")
    confirmed = pdf / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")
    (tmp_path / "release_manifest.json").write_text(json.dumps({"status": "passed"}), encoding="utf-8")
    large_dwg_probe = tmp_path / "large_dwg_probe.json"
    _write_large_dwg_probe(large_dwg_probe)

    report = inventory.inventory_roots([tmp_path], large_dwg_probe=large_dwg_probe)

    assert report["status"] == "ready_for_manifest"
    assert report["summary"]["completed_pairs"] == 21
    assert report["summary"]["has_dwg_dxf"] is True
    assert report["summary"]["has_pdf_pdf"] is True
    assert report["summary"]["has_cad_pdf_block"] is True
    assert report["summary"]["has_cad_block_text_no_expand"] is True
    assert report["summary"]["large_dwg_probe_passed"] is True
    assert report["summary"]["review_decision_truth_passed"] is True
    assert report["summary"]["dataset_strata_passed"] is True
    assert report["summary"]["first_interactive_ready_passed"] is True
    assert report["summary"]["bbox_quality_passed"] is True
    assert report["summary"]["audited_review_ground_truth_rows"] == 12
    assert report["issues"] == []
    assert report["diagnostics"]["missing_format_coverage"] == []
    assert report["diagnostics"]["validation_outputs_missing_cad_block_text_no_expand"] == []
    assert len(report["diagnostics"]["validation_outputs_with_cad_block_text_no_expand"]) == 1
    assert report["diagnostics"]["validation_outputs_missing_selected_zone_telemetry"] == []
    assert report["diagnostics"]["operator_notes_missing_required_checks"] == []
    assert report["diagnostics"]["operator_notes_candidate_count"] == 1
    assert report["diagnostics"]["operator_notes_all_required_checked"] is True
    assert report["diagnostics"]["operator_notes_with_approved_structural_role"] is True
    assert report["diagnostics"]["operator_notes_with_substantive_review_notes"] is True
    assert report["diagnostics"]["operator_notes_missing_approved_structural_role"] == []
    assert report["diagnostics"]["operator_notes_missing_substantive_review_notes"] == []
    assert report["diagnostics"]["missing_operator_workflow_checks"] == []
    assert report["diagnostics"]["confirmed_export_artifact_count"] == 1
    assert len(report["diagnostics"]["valid_review_ground_truth_csv_candidates"]) == 1
    review_decision = report["diagnostics"]["valid_review_decision_truth_csv_candidates"][0]
    assert review_decision["overall_precision"] == 1.0
    assert review_decision["false_positive_rate"] == 0.0
    assert min(review_decision["bucket_labeled_rows"].values()) >= 2
    strata = report["diagnostics"]["valid_dataset_strata_csv_candidates"][0]
    assert strata["cad_rows"] == 8
    assert strata["format_pair_counts"]["pdf_pdf"] == 8
    assert strata["large_dwg_rows"] == 2
    assert report["first_interactive_readiness"]["status"] == "passed"
    assert report["bbox_quality"]["status"] == "passed"
    assert report["bbox_quality"]["relative_only_ratio"] == 0.0
    assert report["large_dwg_probe"]["resource_probe_status"] == "passed"
    assert report["large_dwg_probe"]["peak_rss_mb"] == 1024.0
    assert report["large_dwg_probe"]["cancel_probe"]["status"] == "passed"
    assert "python scripts\\prepare_drawing_compare_customer_evidence.py" in (
        report["recommended_commands"]["prepare_manifest_command"]
    )
    assert "--ground-truth-status approved" in (
        report["recommended_commands"]["prepare_manifest_command"]
    )
    assert "--review-decision-truth" in report["recommended_commands"]["prepare_manifest_command"]
    assert "--dataset-strata" in report["recommended_commands"]["prepare_manifest_command"]
    assert "python scripts\\audit_drawing_compare_mvp_exit.py" in (
        report["recommended_commands"]["final_audit_command"]
    )
    assert "--large-dwg-probe" in report["recommended_commands"]["final_audit_command"]
    assert "--require-large-dwg-probe" in report["recommended_commands"]["final_audit_command"]


def test_inventory_discovers_p5_g16_replay_and_recommends_pipeline_flags(tmp_path: Path) -> None:
    _cad, pdf, _blocked, large_dwg_probe = _write_ready_inventory_fixture(tmp_path)
    replay_json = pdf / "p5_g16_real_corpus_replay.json"
    _write_p5_g16_replay(replay_json)

    report = inventory.inventory_roots([tmp_path], large_dwg_probe=large_dwg_probe)

    assert report["status"] == "ready_for_manifest"
    assert report["summary"]["p5_g16_real_corpus_replay_count"] == 1
    assert report["summary"]["p5_g16_real_corpus_replay_passed_count"] == 1
    assert report["p5_g16_real_corpus_replays"][0]["status"] == "passed"
    assert str(replay_json) in report["recommended_commands"]["prepare_manifest_command"]
    assert str(replay_json) in report["recommended_commands"]["final_audit_command"]
    assert "--p5-g16-benchmark-json" in report["recommended_commands"]["prepare_manifest_command"]
    assert "--p5-g16-benchmark-json" in report["recommended_commands"]["final_audit_command"]


def test_inventory_discovers_p5_g22_gui_soak_and_recommends_pipeline_flags(tmp_path: Path) -> None:
    _cad, pdf, _blocked, large_dwg_probe = _write_ready_inventory_fixture(tmp_path)
    soak_json = pdf / "p5_g22_actual_gui_soak.json"
    _write_p5_g22_gui_soak(soak_json)

    report = inventory.inventory_roots([tmp_path], large_dwg_probe=large_dwg_probe)

    assert report["status"] == "ready_for_manifest"
    assert report["summary"]["p5_g22_actual_gui_soak_count"] == 1
    assert report["summary"]["p5_g22_actual_gui_soak_passed_count"] == 1
    assert report["p5_g22_actual_gui_soaks"][0]["status"] == "passed"
    assert report["p5_g22_actual_gui_soaks"][0]["shared_summaries_present"] is True
    assert report["diagnostics"]["p5_g22_actual_gui_soak_passed"][0]["path"] == str(soak_json)
    assert report["diagnostics"]["p5_g22_native_resource_summary_passed"][0]["path"] == str(soak_json)
    assert report["diagnostics"]["p5_g22_worker_tree_summary_passed"][0]["path"] == str(soak_json)
    assert report["diagnostics"]["p5_g22_actual_gui_soak_missing_shared_summaries"] == []
    assert str(soak_json) in report["recommended_commands"]["prepare_manifest_command"]
    assert str(soak_json) in report["recommended_commands"]["final_audit_command"]
    assert "--p5-g22-gui-soak-json" in report["recommended_commands"]["prepare_manifest_command"]
    assert "--p5-g22-gui-soak-json" in report["recommended_commands"]["final_audit_command"]


def test_inventory_reports_p5_g7_forced_tile_eviction_candidate_without_counting_as_customer_corpus(
    tmp_path: Path,
) -> None:
    _cad, _pdf, _blocked, large_dwg_probe = _write_ready_inventory_fixture(tmp_path)
    proof = tmp_path / "p5_g7_tile_eviction_proof"
    _write_validation(
        proof,
        kind="pdf",
        completed_pairs=5,
        source_extensions=("pdf", "pdf"),
        forced_tile_eviction=True,
        tile_cache_mb=0.25,
    )

    report = inventory.inventory_roots(
        [tmp_path],
        large_dwg_probe=large_dwg_probe,
        require_p5_g7_forced_tile_eviction=True,
        p5_g6_tile_cache_mb=0.25,
    )

    prepare_command = report["recommended_commands"]["prepare_manifest_command"]
    assert report["status"] == "ready_for_manifest"
    assert report["summary"]["completed_pairs"] == 21
    assert report["summary"]["validation_output_count"] == 4
    assert report["summary"]["customer_validation_output_count"] == 3
    assert report["summary"]["p5_g7_forced_tile_eviction_proof_count"] == 1
    assert report["summary"]["p5_g7_forced_tile_eviction_passed_count"] == 1
    assert report["summary"]["p5_g7_forced_tile_eviction_passed"] is True
    assert report["diagnostics"]["p5_g7_forced_tile_eviction_missing_outputs"] == []
    assert report["diagnostics"]["p5_g7_forced_tile_eviction_passed_outputs"] == [str(proof)]
    assert f'--results-dir "{proof}"' not in prepare_command
    assert f'--p5-g7-tile-eviction-proof-dir "{proof}"' in prepare_command
    assert "--require-p5-g7-tile-eviction-proof" in prepare_command
    assert "--p5-g6-tile-cache-mb 0.25" in prepare_command


def test_inventory_requires_p5_g7_forced_tile_eviction_when_requested(tmp_path: Path) -> None:
    _cad, _pdf, _blocked, large_dwg_probe = _write_ready_inventory_fixture(tmp_path)

    report = inventory.inventory_roots(
        [tmp_path],
        large_dwg_probe=large_dwg_probe,
        require_p5_g7_forced_tile_eviction=True,
        p5_g6_tile_cache_mb=0.25,
    )

    assert report["status"] == "incomplete"
    assert "missing passing P5-G7 forced tile-eviction proof validation output" in report["issues"]
    assert report["summary"]["p5_g7_forced_tile_eviction_proof_count"] == 0
    assert report["diagnostics"]["p5_g7_forced_tile_eviction_missing_outputs"] == [
        "p5_g7_forced_tile_eviction"
    ]


def test_inventory_rejects_p5_g7_tile_cache_cap_mismatch(tmp_path: Path) -> None:
    _cad, _pdf, _blocked, large_dwg_probe = _write_ready_inventory_fixture(tmp_path)
    proof = tmp_path / "p5_g7_tile_eviction_proof"
    _write_validation(
        proof,
        kind="pdf",
        completed_pairs=5,
        source_extensions=("pdf", "pdf"),
        forced_tile_eviction=True,
        tile_cache_mb=0.5,
    )

    report = inventory.inventory_roots(
        [tmp_path],
        large_dwg_probe=large_dwg_probe,
        require_p5_g7_forced_tile_eviction=True,
        p5_g6_tile_cache_mb=0.25,
    )

    p5_g7_issues = report["diagnostics"]["p5_g7_forced_tile_eviction_issues"]
    assert report["status"] == "incomplete"
    assert report["summary"]["completed_pairs"] == 21
    assert report["summary"]["p5_g7_forced_tile_eviction_proof_count"] == 1
    assert report["summary"]["p5_g7_forced_tile_eviction_passed_count"] == 0
    assert "missing passing P5-G7 forced tile-eviction proof validation output" in report["issues"]
    assert str(proof) == p5_g7_issues[0]["path"]
    assert "configured_tile_cache_mb=0.5 != 0.25" in "\n".join(p5_g7_issues[0]["issues"])


def test_inventory_keeps_non_forced_tile_cache_validation_as_customer_corpus(tmp_path: Path) -> None:
    _cad, _pdf, _blocked, large_dwg_probe = _write_ready_inventory_fixture(tmp_path)
    cache_metadata = tmp_path / "cache_metadata_validation"
    _write_validation(
        cache_metadata,
        kind="pdf",
        completed_pairs=1,
        source_extensions=("pdf", "pdf"),
    )
    summary_path = cache_metadata / "validation_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["p5_g3_realset_gate"] = {
        "requested": True,
        "status": "passed",
        "failures": [],
        "evidence": {
            "tile_manifest": {
                "status": "passed",
                "require_eviction": False,
                "configured_tile_cache_mb": 0.25,
                "tile_cache_env_mb": "0.25",
                "byte_limit": 262144,
                "evicted_pair_count": 0,
                "evicted_estimated_bytes": 0,
            }
        },
    }
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    _write_dataset_strata_csv(tmp_path / "dataset_strata.csv", rows=22)

    report = inventory.inventory_roots(
        [tmp_path],
        large_dwg_probe=large_dwg_probe,
        p5_g6_tile_cache_mb=0.25,
    )

    assert report["status"] == "ready_for_manifest"
    assert report["summary"]["completed_pairs"] == 22
    assert report["summary"]["customer_validation_output_count"] == 4
    assert report["summary"]["p5_g7_forced_tile_eviction_proof_count"] == 0


def test_inventory_does_not_use_unrelated_release_manifest_for_p5_g7_proof(tmp_path: Path) -> None:
    _cad, _pdf, _blocked, large_dwg_probe = _write_ready_inventory_fixture(tmp_path)
    proof = tmp_path / "p5_g7_tile_eviction_proof"
    _write_validation(
        proof,
        kind="pdf",
        completed_pairs=5,
        source_extensions=("pdf", "pdf"),
        forced_tile_eviction=True,
        tile_cache_mb=0.25,
    )

    report = inventory.inventory_roots(
        [tmp_path],
        large_dwg_probe=large_dwg_probe,
        require_p5_g7_forced_tile_eviction=True,
        p5_g6_tile_cache_mb=0.25,
    )

    prepare_command = report["recommended_commands"]["prepare_manifest_command"]
    assert f'--p5-g7-tile-eviction-proof-dir "{proof}"' in prepare_command
    assert "--p5-g7-tile-eviction-release-manifest" not in prepare_command


def test_inventory_requires_large_dwg_probe_for_ready_manifest(tmp_path: Path) -> None:
    cad = tmp_path / "cad_validation"
    pdf = tmp_path / "pdf_validation"
    blocked = tmp_path / "cad_pdf_block_validation"
    _write_validation(cad, kind="cad", completed_pairs=1, source_extensions=("dwg", "dxf"))
    _write_validation(
        pdf,
        kind="pdf",
        completed_pairs=20,
        source_extensions=("pdf", "pdf"),
        workbench_acceptance=True,
    )
    _write_validation(
        blocked,
        kind="blocked",
        completed_pairs=0,
        source_extensions=("dwg", "pdf"),
        cad_pdf_blocked=True,
    )
    _write_truth_csv(tmp_path / "review_ground_truth.csv")
    _write_operator_notes(tmp_path / "operator_dry_run_notes.md")
    confirmed = pdf / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    report = inventory.inventory_roots([tmp_path])

    assert report["status"] == "incomplete"
    assert report["summary"]["large_dwg_probe_passed"] is False
    assert "missing passing large-DWG performance/progress probe" in report["issues"]
    assert "--large-dwg-probe \"<large_dwg_probe.json>\"" in (
        report["recommended_commands"]["final_audit_command"]
    )
    assert "--require-large-dwg-probe" in report["recommended_commands"]["final_audit_command"]
    assert report["diagnostics"]["large_dwg_probe_issues"] == [
        "--large-dwg-probe is required for customer-grade readiness"
    ]


def test_inventory_rejects_slow_large_dwg_probe_for_ready_manifest(tmp_path: Path) -> None:
    cad = tmp_path / "cad_validation"
    pdf = tmp_path / "pdf_validation"
    blocked = tmp_path / "cad_pdf_block_validation"
    _write_validation(cad, kind="cad", completed_pairs=1, source_extensions=("dwg", "dxf"))
    _write_validation(
        pdf,
        kind="pdf",
        completed_pairs=20,
        source_extensions=("pdf", "pdf"),
        workbench_acceptance=True,
    )
    _write_validation(
        blocked,
        kind="blocked",
        completed_pairs=0,
        source_extensions=("dwg", "pdf"),
        cad_pdf_blocked=True,
    )
    _write_truth_csv(tmp_path / "review_ground_truth.csv")
    _write_operator_notes(tmp_path / "operator_dry_run_notes.md")
    confirmed = pdf / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")
    large_dwg_probe = tmp_path / "large_dwg_probe.json"
    _write_large_dwg_probe(large_dwg_probe, elapsed_s=121.0)

    report = inventory.inventory_roots([tmp_path], large_dwg_probe=large_dwg_probe)

    assert report["status"] == "incomplete"
    assert report["large_dwg_probe"]["passed"] is False
    assert "missing passing large-DWG performance/progress probe" in report["issues"]
    assert "large-DWG probe elapsed_s must be >0 and <=120" in (
        report["diagnostics"]["large_dwg_probe_issues"]
    )


def test_inventory_allows_probe_filter_release_folder_name_for_customer_evidence(tmp_path: Path) -> None:
    cad = tmp_path / "cad_validation"
    pdf = tmp_path / "pdf_validation"
    blocked = tmp_path / "cad_pdf_block_validation"
    _write_validation(cad, kind="cad", completed_pairs=1, source_extensions=("dwg", "dxf"))
    _write_validation(
        pdf,
        kind="pdf",
        completed_pairs=20,
        source_extensions=("pdf", "pdf"),
        workbench_acceptance=True,
    )
    _write_validation(
        blocked,
        kind="blocked",
        completed_pairs=0,
        source_extensions=("dwg", "pdf"),
        cad_pdf_blocked=True,
    )
    evidence = tmp_path / "customer_probe_filter_evidence"
    evidence.mkdir()
    _write_truth_csv(evidence / "review_ground_truth.csv")
    _write_operator_notes(evidence / "operator_dry_run_notes.md")
    confirmed = pdf / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")
    large_dwg_probe = tmp_path / "large_dwg_probe.json"
    _write_large_dwg_probe(large_dwg_probe)

    report = inventory.inventory_roots([tmp_path], large_dwg_probe=large_dwg_probe)

    assert report["status"] == "ready_for_manifest"
    assert len(report["review_ground_truth_csvs"]) == 1
    assert len(report["operator_notes"]) == 1
    assert "customer_probe_filter_evidence" in report["recommended_commands"]["prepare_manifest_command"]


def test_inventory_summarizes_existing_customer_manifest_readiness(tmp_path: Path) -> None:
    cad = tmp_path / "cad_validation"
    pdf = tmp_path / "pdf_validation"
    blocked = tmp_path / "cad_pdf_block_validation"
    _write_validation(cad, kind="cad", completed_pairs=1, source_extensions=("dwg", "dxf"))
    _write_validation(
        pdf,
        kind="pdf",
        completed_pairs=20,
        source_extensions=("pdf", "pdf"),
        workbench_acceptance=True,
    )
    _write_validation(
        blocked,
        kind="blocked",
        completed_pairs=0,
        source_extensions=("dwg", "pdf"),
        cad_pdf_blocked=True,
    )
    _write_truth_csv(tmp_path / "review_ground_truth.csv")
    _write_operator_notes(tmp_path / "operator_dry_run_notes.md")
    confirmed = pdf / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")
    (tmp_path / "customer_evidence_manifest.json").write_text(
        json.dumps(
            {
                "evidence_level": "customer_grade",
                "readiness": {"status": "ready", "issues": []},
                "ground_truth": {"status": "reviewed"},
                "path_leakage_audit": {"status": "passed", "leak_count": 0},
            }
        ),
        encoding="utf-8",
    )
    large_dwg_probe = tmp_path / "large_dwg_probe.json"
    _write_large_dwg_probe(large_dwg_probe)

    report = inventory.inventory_roots([tmp_path], large_dwg_probe=large_dwg_probe)

    assert report["status"] == "ready_for_manifest"
    assert len(report["customer_evidence_manifest_summaries"]) == 1
    manifest_summary = report["customer_evidence_manifest_summaries"][0]
    assert manifest_summary["ground_truth_status"] == "reviewed"
    assert manifest_summary["self_check_ready"] is False
    assert "manifest.ground_truth.status must be approved" in manifest_summary["issues"]
    assert report["diagnostics"]["customer_evidence_manifest_count"] == 1
    not_ready = report["diagnostics"]["customer_evidence_manifests_not_ready"]
    assert len(not_ready) == 1
    assert not_ready[0]["path"] == manifest_summary["path"]
    missing_approved = report["diagnostics"]["customer_evidence_manifests_missing_approved_ground_truth"]
    assert len(missing_approved) == 1
    assert missing_approved[0]["path"] == manifest_summary["path"]


def test_inventory_portable_paths_redacts_absolute_roots(tmp_path: Path) -> None:
    cad = tmp_path / "cad_validation"
    pdf = tmp_path / "pdf_validation"
    blocked = tmp_path / "cad_pdf_block_validation"
    _write_validation(cad, kind="cad", completed_pairs=1, source_extensions=("dwg", "dxf"))
    _write_validation(
        pdf,
        kind="pdf",
        completed_pairs=20,
        source_extensions=("pdf", "pdf"),
        workbench_acceptance=True,
    )
    _write_validation(
        blocked,
        kind="blocked",
        completed_pairs=0,
        source_extensions=("dwg", "pdf"),
        cad_pdf_blocked=True,
    )
    _write_truth_csv(tmp_path / "review_ground_truth.csv")
    _write_operator_notes(tmp_path / "operator_dry_run_notes.md")
    confirmed = pdf / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")
    (tmp_path / "release_manifest.json").write_text(json.dumps({"status": "passed"}), encoding="utf-8")
    large_dwg_probe = tmp_path / "large_dwg_probe.json"
    _write_large_dwg_probe(large_dwg_probe)

    report = inventory.inventory_roots([tmp_path], large_dwg_probe=large_dwg_probe, portable_paths=True)
    payload = json.dumps(report, ensure_ascii=False)

    assert report["status"] == "ready_for_manifest"
    assert report["roots"] == ["root_1"]
    assert report["path_policy"]["portable_paths"] is True
    assert report["validation_outputs"][0]["path"].startswith("root_1/")
    assert "root_1/" in report["recommended_commands"]["prepare_manifest_command"]
    assert str(tmp_path) not in payload
    assert str(tmp_path).replace("\\", "/") not in payload


def test_inventory_cli_outputs_ascii_safe_json(tmp_path: Path, capsys) -> None:
    cad = tmp_path / "cad_validation"
    pdf = tmp_path / "pdf_validation"
    blocked = tmp_path / "cad_pdf_block_validation"
    out = tmp_path / "inventory.json"
    _write_validation(cad, kind="cad", completed_pairs=1, source_extensions=("dwg", "dxf"))
    _write_validation(
        pdf,
        kind="pdf",
        completed_pairs=20,
        source_extensions=("pdf", "pdf"),
        workbench_acceptance=True,
    )
    _write_validation(
        blocked,
        kind="blocked",
        completed_pairs=0,
        source_extensions=("dwg", "pdf"),
        cad_pdf_blocked=True,
    )
    _write_truth_csv(tmp_path / "review_ground_truth.csv")
    _write_operator_notes(tmp_path / "operator_dry_run_notes.md")
    confirmed = pdf / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")
    (tmp_path / "release_manifest.json").write_text(json.dumps({"status": "passed"}), encoding="utf-8")
    large_dwg_probe = tmp_path / "large_dwg_probe.json"
    _write_large_dwg_probe(large_dwg_probe)

    code = inventory.main([
        "--root",
        str(tmp_path),
        "--large-dwg-probe",
        str(large_dwg_probe),
        "--out",
        str(out),
    ])

    stdout = capsys.readouterr().out
    out_text = out.read_text(encoding="utf-8")
    assert code == 0
    out_text.encode("ascii")
    stdout.encode("ascii")
    assert json.loads(out_text)["status"] == "ready_for_manifest"
    assert json.loads(stdout)["status"] == "ready_for_manifest"


def test_inventory_requires_cad_block_text_no_expand_evidence(tmp_path: Path) -> None:
    cad = tmp_path / "cad_validation"
    pdf = tmp_path / "pdf_validation"
    blocked = tmp_path / "cad_pdf_block_validation"
    _write_validation(
        cad,
        kind="cad",
        completed_pairs=1,
        source_extensions=("dwg", "dxf"),
        cad_block_text_no_expand=False,
    )
    _write_validation(
        pdf,
        kind="pdf",
        completed_pairs=20,
        source_extensions=("pdf", "pdf"),
        workbench_acceptance=True,
    )
    _write_validation(
        blocked,
        kind="blocked",
        completed_pairs=0,
        source_extensions=("dwg", "pdf"),
        cad_pdf_blocked=True,
    )
    _write_truth_csv(tmp_path / "review_ground_truth.csv")
    _write_operator_notes(tmp_path / "operator_dry_run_notes.md")
    confirmed = pdf / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")
    large_dwg_probe = tmp_path / "large_dwg_probe.json"
    _write_large_dwg_probe(large_dwg_probe)

    report = inventory.inventory_roots([tmp_path], large_dwg_probe=large_dwg_probe)

    assert report["status"] == "incomplete"
    assert report["summary"]["has_cad_block_text_no_expand"] is False
    assert "missing CAD block attribute/text no-expand validation evidence" in report["issues"]
    assert report["diagnostics"]["validation_outputs_with_cad_block_text_no_expand"] == []
    assert report["diagnostics"]["validation_outputs_missing_cad_block_text_no_expand"] == [str(cad)]


def test_inventory_accepts_korean_structural_reviewer_role(tmp_path: Path) -> None:
    cad = tmp_path / "cad_validation"
    pdf = tmp_path / "pdf_validation"
    blocked = tmp_path / "cad_pdf_block_validation"
    _write_validation(cad, kind="cad", completed_pairs=1, source_extensions=("dwg", "dxf"))
    _write_validation(
        pdf,
        kind="pdf",
        completed_pairs=20,
        source_extensions=("pdf", "pdf"),
        workbench_acceptance=True,
    )
    _write_validation(
        blocked,
        kind="blocked",
        completed_pairs=0,
        source_extensions=("dwg", "pdf"),
        cad_pdf_blocked=True,
    )
    _write_truth_csv(tmp_path / "review_ground_truth.csv")
    _write_operator_notes(tmp_path / "operator_dry_run_notes.md", reviewer_role="구조검토책임자")
    confirmed = pdf / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")
    large_dwg_probe = tmp_path / "large_dwg_probe.json"
    _write_large_dwg_probe(large_dwg_probe)

    report = inventory.inventory_roots([tmp_path], large_dwg_probe=large_dwg_probe)

    assert report["status"] == "ready_for_manifest"
    assert report["diagnostics"]["operator_notes_with_approved_structural_role"] is True
    assert report["diagnostics"]["operator_notes_with_substantive_review_notes"] is True
    assert report["operator_notes"][0]["matched_reviewer_role"] == "구조검토책임자"
    assert report["operator_notes"][0]["substantive_review_notes"] is True
    assert "구조검토책임자" in report["diagnostics"]["approved_operator_reviewer_roles"]


def test_inventory_accepts_utf16_operator_notes(tmp_path: Path) -> None:
    cad = tmp_path / "cad_validation"
    pdf = tmp_path / "pdf_validation"
    blocked = tmp_path / "cad_pdf_block_validation"
    _write_validation(cad, kind="cad", completed_pairs=1, source_extensions=("dwg", "dxf"))
    _write_validation(
        pdf,
        kind="pdf",
        completed_pairs=20,
        source_extensions=("pdf", "pdf"),
        workbench_acceptance=True,
    )
    _write_validation(
        blocked,
        kind="blocked",
        completed_pairs=0,
        source_extensions=("dwg", "pdf"),
        cad_pdf_blocked=True,
    )
    _write_truth_csv(tmp_path / "review_ground_truth.csv")
    _write_operator_notes(tmp_path / "operator_dry_run_notes.md", encoding="utf-16")
    confirmed = pdf / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")
    large_dwg_probe = tmp_path / "large_dwg_probe.json"
    _write_large_dwg_probe(large_dwg_probe)

    report = inventory.inventory_roots([tmp_path], large_dwg_probe=large_dwg_probe)

    assert report["status"] == "ready_for_manifest"
    assert report["diagnostics"]["operator_notes_all_required_checked"] is True
    assert report["diagnostics"]["operator_notes_with_approved_structural_role"] is True
    assert report["diagnostics"]["operator_notes_with_substantive_review_notes"] is True


def test_inventory_rejects_truth_csv_missing_required_schema(tmp_path: Path) -> None:
    cad = tmp_path / "cad_validation"
    pdf = tmp_path / "pdf_validation"
    blocked = tmp_path / "cad_pdf_block_validation"
    _write_validation(cad, kind="cad", completed_pairs=1, source_extensions=("dwg", "dxf"))
    _write_validation(
        pdf,
        kind="pdf",
        completed_pairs=20,
        source_extensions=("pdf", "pdf"),
        workbench_acceptance=True,
    )
    _write_validation(
        blocked,
        kind="blocked",
        completed_pairs=0,
        source_extensions=("dwg", "pdf"),
        cad_pdf_blocked=True,
    )
    (tmp_path / "review_ground_truth.csv").write_text(
        "drawing_label,category,summary_contains\nS-001,mixed,D13@100\n",
        encoding="utf-8",
    )
    _write_operator_notes(tmp_path / "operator_dry_run_notes.md")
    confirmed = pdf / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    report = inventory.inventory_roots([tmp_path])

    assert report["status"] == "incomplete"
    assert "review_ground_truth CSV missing required schema columns or row values" in report["issues"]
    schema_issues = report["diagnostics"]["review_ground_truth_csv_schema_issues"]
    assert len(schema_issues) == 1
    assert "review_ground_truth CSV missing required columns" in "\n".join(schema_issues[0]["schema_issues"])
    assert "--review-ground-truth \"<review_ground_truth.csv>\"" in (
        report["recommended_commands"]["prepare_manifest_command"]
    )


def test_inventory_rejects_copied_truth_template_example_rows(tmp_path: Path) -> None:
    cad = tmp_path / "cad_validation"
    pdf = tmp_path / "pdf_validation"
    blocked = tmp_path / "cad_pdf_block_validation"
    _write_validation(cad, kind="cad", completed_pairs=1, source_extensions=("dwg", "dxf"))
    _write_validation(
        pdf,
        kind="pdf",
        completed_pairs=20,
        source_extensions=("pdf", "pdf"),
        workbench_acceptance=True,
    )
    _write_validation(
        blocked,
        kind="blocked",
        completed_pairs=0,
        source_extensions=("dwg", "pdf"),
        cad_pdf_blocked=True,
    )
    (tmp_path / "review_ground_truth.csv").write_text(
        "drawing_label,category,summary_contains,source_format,detection_source,bbox_status,notes\n"
        "S-001,member|mixed,BEAM;added,cad,cad_entity,exact,member add/delete/move example\n",
        encoding="utf-8",
    )
    _write_operator_notes(tmp_path / "operator_dry_run_notes.md")
    confirmed = pdf / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    report = inventory.inventory_roots([tmp_path])

    assert report["status"] == "incomplete"
    assert "review_ground_truth CSV missing required schema columns or row values" in report["issues"]
    schema_issues = report["diagnostics"]["review_ground_truth_csv_schema_issues"]
    assert "template/example marker" in "\n".join(schema_issues[0]["schema_issues"])
    assert "--review-ground-truth \"<review_ground_truth.csv>\"" in (
        report["recommended_commands"]["prepare_manifest_command"]
    )


def test_inventory_recommended_commands_use_cli_paths_from_release_cli(
    tmp_path: Path,
    monkeypatch,
) -> None:
    release_cli = tmp_path / "release" / "cli" / "inventory_drawing_compare_customer_evidence.py"
    release_cli.parent.mkdir(parents=True)
    release_cli.write_text("# copied tool", encoding="utf-8")
    monkeypatch.setattr(inventory, "__file__", str(release_cli))

    report = inventory.inventory_roots([tmp_path])

    assert "python cli\\prepare_drawing_compare_customer_evidence.py" in (
        report["recommended_commands"]["prepare_manifest_command"]
    )
    assert "python cli\\audit_drawing_compare_mvp_exit.py" in (
        report["recommended_commands"]["final_audit_command"]
    )


def test_inventory_surfaces_missing_customer_grade_blockers(tmp_path: Path) -> None:
    _write_validation(
        tmp_path / "pdf_validation",
        kind="pdf",
        completed_pairs=5,
        source_extensions=("pdf", "pdf"),
    )

    report = inventory.inventory_roots([tmp_path])

    assert report["status"] == "incomplete"
    issues = "\n".join(report["issues"])
    assert "completed_pairs=5 outside required range 20..50" in issues
    assert "missing completed CAD validation evidence" in issues
    assert "missing CAD-PDF" in issues
    assert "missing operator dry-run notes" in issues
    assert "missing *_confirmed" in issues
    assert report["diagnostics"]["missing_format_coverage"] == ["dwg_dxf", "cad_pdf_blocked"]
    assert report["diagnostics"]["validation_outputs_missing_selected_zone_telemetry"] == []
    assert report["diagnostics"]["operator_notes_candidate_count"] == 0
    assert report["diagnostics"]["operator_notes_all_required_checked"] is False
    assert report["diagnostics"]["operator_notes_with_approved_structural_role"] is False
    assert set(report["diagnostics"]["missing_operator_workflow_checks"]) == set(
        inventory.REQUIRED_OPERATOR_WORKFLOW_CHECKS
    )
    assert report["diagnostics"]["confirmed_export_artifact_count"] == 0


def test_inventory_rejects_operator_notes_without_structural_reviewer_role(tmp_path: Path) -> None:
    cad = tmp_path / "cad_validation"
    pdf = tmp_path / "pdf_validation"
    blocked = tmp_path / "cad_pdf_block_validation"
    _write_validation(cad, kind="cad", completed_pairs=1, source_extensions=("dwg", "dxf"))
    _write_validation(
        pdf,
        kind="pdf",
        completed_pairs=20,
        source_extensions=("pdf", "pdf"),
        workbench_acceptance=True,
    )
    _write_validation(
        blocked,
        kind="blocked",
        completed_pairs=0,
        source_extensions=("dwg", "pdf"),
        cad_pdf_blocked=True,
    )
    _write_truth_csv(tmp_path / "review_ground_truth.csv")
    _write_operator_notes(tmp_path / "operator_dry_run_notes.md", include_role=False)
    confirmed = pdf / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    report = inventory.inventory_roots([tmp_path])

    assert report["status"] == "incomplete"
    assert "missing operator dry-run notes with approved structural review lead/team lead role" in report["issues"]
    assert report["diagnostics"]["operator_notes_all_required_checked"] is True
    assert report["diagnostics"]["operator_notes_with_approved_structural_role"] is False
    missing_role = report["diagnostics"]["operator_notes_missing_approved_structural_role"]
    assert len(missing_role) == 1
    assert missing_role[0]["all_required_checked"] is True


def test_inventory_rejects_operator_notes_without_substantive_notes(tmp_path: Path) -> None:
    cad = tmp_path / "cad_validation"
    pdf = tmp_path / "pdf_validation"
    blocked = tmp_path / "cad_pdf_block_validation"
    _write_validation(cad, kind="cad", completed_pairs=1, source_extensions=("dwg", "dxf"))
    _write_validation(
        pdf,
        kind="pdf",
        completed_pairs=20,
        source_extensions=("pdf", "pdf"),
        workbench_acceptance=True,
    )
    _write_validation(
        blocked,
        kind="blocked",
        completed_pairs=0,
        source_extensions=("dwg", "pdf"),
        cad_pdf_blocked=True,
    )
    _write_truth_csv(tmp_path / "review_ground_truth.csv")
    notes = tmp_path / "operator_dry_run_notes.md"
    notes.write_text(
        "Operator dry run passed.\n"
        "reviewer_role: structural_review_lead\n"
        "Operator notes:\n"
        + "\n".join(f"- [x] {check}" for check in inventory.REQUIRED_OPERATOR_WORKFLOW_CHECKS),
        encoding="utf-8",
    )
    confirmed = pdf / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    report = inventory.inventory_roots([tmp_path])

    assert report["status"] == "incomplete"
    assert "missing operator dry-run notes with substantive review notes" in report["issues"]
    assert report["diagnostics"]["operator_notes_all_required_checked"] is True
    assert report["diagnostics"]["operator_notes_with_approved_structural_role"] is True
    assert report["diagnostics"]["operator_notes_with_substantive_review_notes"] is False
    missing_notes = report["diagnostics"]["operator_notes_missing_substantive_review_notes"]
    assert len(missing_notes) == 1
    assert missing_notes[0]["substantive_review_notes"] is False


def test_inventory_diagnostics_identify_outputs_missing_selected_zone_telemetry(tmp_path: Path) -> None:
    good = tmp_path / "good_validation"
    missing = tmp_path / "missing_selected_zone_validation"
    _write_validation(good, kind="pdf", completed_pairs=20, source_extensions=("pdf", "pdf"))
    _write_validation(missing, kind="cad", completed_pairs=1, source_extensions=("dwg", "dxf"))

    summary_path = missing / "validation_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary.pop("viewer_perf_summary")
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    report = inventory.inventory_roots([tmp_path])

    missing_paths = report["diagnostics"]["validation_outputs_missing_selected_zone_telemetry"]
    assert str(missing) in missing_paths
    assert str(good) not in missing_paths
    assert "--render-selected-zone-evidence" in report["diagnostics"]["selected_zone_evidence_hint"]


def test_inventory_rejects_truth_csv_without_audited_review_ground_truth_metrics(tmp_path: Path) -> None:
    cad = tmp_path / "cad_validation"
    pdf = tmp_path / "pdf_validation"
    blocked = tmp_path / "cad_pdf_block_validation"
    _write_validation(
        cad,
        kind="cad",
        completed_pairs=1,
        source_extensions=("dwg", "dxf"),
        review_ground_truth_rows=0,
    )
    _write_validation(
        pdf,
        kind="pdf",
        completed_pairs=20,
        source_extensions=("pdf", "pdf"),
        workbench_acceptance=True,
        review_ground_truth_rows=0,
    )
    _write_validation(
        blocked,
        kind="blocked",
        completed_pairs=0,
        source_extensions=("dwg", "pdf"),
        cad_pdf_blocked=True,
        review_ground_truth_rows=0,
    )
    _write_truth_csv(tmp_path / "review_ground_truth.csv")
    _write_operator_notes(tmp_path / "operator_dry_run_notes.md")
    confirmed = pdf / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    report = inventory.inventory_roots([tmp_path])

    assert report["status"] == "incomplete"
    assert "missing audited review_ground_truth metrics on validation outputs" in report["issues"]
    assert report["diagnostics"]["audited_review_ground_truth_rows"] == 0
    assert report["diagnostics"]["valid_review_ground_truth_csv_candidates"] == []


def test_inventory_rejects_truth_csv_rows_exceeding_audited_rows(tmp_path: Path) -> None:
    cad = tmp_path / "cad_validation"
    pdf = tmp_path / "pdf_validation"
    blocked = tmp_path / "cad_pdf_block_validation"
    _write_validation(cad, kind="cad", completed_pairs=1, source_extensions=("dwg", "dxf"), review_ground_truth_rows=1)
    _write_validation(
        pdf,
        kind="pdf",
        completed_pairs=20,
        source_extensions=("pdf", "pdf"),
        workbench_acceptance=True,
        review_ground_truth_rows=0,
    )
    _write_validation(blocked, kind="blocked", completed_pairs=0, source_extensions=("dwg", "pdf"), cad_pdf_blocked=True)
    _write_truth_csv(tmp_path / "review_ground_truth.csv", rows=2)
    _write_operator_notes(tmp_path / "operator_dry_run_notes.md")
    confirmed = pdf / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    report = inventory.inventory_roots([tmp_path])

    assert report["status"] == "incomplete"
    issues = "\n".join(report["issues"])
    assert "review_ground_truth CSV rows exceed audited review_ground_truth rows (2>1)" in issues
    assert report["diagnostics"]["audited_review_ground_truth_rows"] == 1
    assert report["diagnostics"]["valid_review_ground_truth_csv_candidates"] == []


def test_inventory_ignores_release_templates_and_handoff_docs(tmp_path: Path) -> None:
    cad = tmp_path / "cad_validation"
    pdf = tmp_path / "pdf_validation"
    blocked = tmp_path / "cad_pdf_block_validation"
    _write_validation(cad, kind="cad", completed_pairs=1, source_extensions=("dwg", "dxf"))
    _write_validation(
        pdf,
        kind="pdf",
        completed_pairs=20,
        source_extensions=("pdf", "pdf"),
        workbench_acceptance=True,
    )
    _write_validation(
        blocked,
        kind="blocked",
        completed_pairs=0,
        source_extensions=("dwg", "pdf"),
        cad_pdf_blocked=True,
    )
    confirmed = pdf / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    release = tmp_path / "release"
    release.mkdir()
    (release / "review_ground_truth_template.csv").write_text(
        "drawing_label,category,summary_contains\nS-001,mixed,D13@100\n",
        encoding="utf-8",
    )
    (release / "operator_dry_run_checklist_template.md").write_text(
        "\n".join(f"- [x] {check}" for check in inventory.REQUIRED_OPERATOR_WORKFLOW_CHECKS),
        encoding="utf-8",
    )
    (release / "PILOT_OPS_OPERATOR_HANDOFF_WINDOWS_LIMITED_RELEASE.md").write_text(
        "\n".join(f"- [x] {check}" for check in inventory.REQUIRED_OPERATOR_WORKFLOW_CHECKS),
        encoding="utf-8",
    )
    (release / "operator_dry_run_customer_closeout.md").write_text(
        "\n".join(
            [
                "reviewer_role: structural_review_lead",
                *[f"- [x] {check}" for check in inventory.REQUIRED_OPERATOR_WORKFLOW_CHECKS],
                "Operator notes: S-101 C-001 checked synced zoom, Korean summary, confirmed export, and path audit.",
            ]
        ),
        encoding="utf-8",
    )
    (release / "customer_evidence_request_ko.md").write_text(
        "\n".join(
            [
                "reviewer_role: structural_review_lead",
                *[f"- [x] {check}" for check in inventory.REQUIRED_OPERATOR_WORKFLOW_CHECKS],
                "Operator notes: request document only; not real dry-run evidence.",
            ]
        ),
        encoding="utf-8",
    )

    report = inventory.inventory_roots([tmp_path])

    assert report["status"] == "incomplete"
    assert "missing non-empty review_ground_truth CSV" in report["issues"]
    assert "missing operator dry-run notes with all required workflow IDs checked" in report["issues"]
    assert report["review_ground_truth_csvs"] == []
    assert report["operator_notes"] == []
    assert report["diagnostics"]["review_ground_truth_csv_candidates"] == []
    assert report["diagnostics"]["valid_review_ground_truth_csv_candidates"] == []
    assert report["diagnostics"]["operator_notes_candidate_count"] == 0


def test_inventory_ignores_probe_customer_evidence_artifacts(tmp_path: Path) -> None:
    cad = tmp_path / "cad_validation"
    pdf = tmp_path / "pdf_validation"
    blocked = tmp_path / "cad_pdf_block_validation"
    _write_validation(cad, kind="cad", completed_pairs=1, source_extensions=("dwg", "dxf"))
    _write_validation(
        pdf,
        kind="pdf",
        completed_pairs=20,
        source_extensions=("pdf", "pdf"),
        workbench_acceptance=True,
    )
    _write_validation(
        blocked,
        kind="blocked",
        completed_pairs=0,
        source_extensions=("dwg", "pdf"),
        cad_pdf_blocked=True,
    )
    confirmed = pdf / "artifacts" / "confirmed_clouds" / "pair_confirmed.png"
    confirmed.parent.mkdir(parents=True)
    confirmed.write_bytes(b"png")

    probe = tmp_path / "drawing_compare_truth_schema_probe_current"
    probe.mkdir()
    _write_truth_csv(probe / "review_ground_truth.csv")
    _write_operator_notes(probe / "operator_dry_run_notes.md")
    (probe / "customer_evidence_manifest.json").write_text(
        json.dumps(
            {
                "evidence_level": "customer_grade",
                "readiness": {"status": "ready", "issues": []},
                "ground_truth": {"status": "approved"},
                "path_leakage_audit": {"status": "passed", "leak_count": 0},
            }
        ),
        encoding="utf-8",
    )

    report = inventory.inventory_roots([tmp_path])

    assert report["status"] == "incomplete"
    assert "missing non-empty review_ground_truth CSV" in report["issues"]
    assert "missing operator dry-run notes with all required workflow IDs checked" in report["issues"]
    assert report["review_ground_truth_csvs"] == []
    assert report["operator_notes"] == []
    assert report["customer_evidence_manifests"] == []
    assert report["diagnostics"]["valid_review_ground_truth_csv_candidates"] == []
    assert report["diagnostics"]["operator_notes_candidate_count"] == 0
    assert report["diagnostics"]["customer_evidence_manifest_count"] == 0
