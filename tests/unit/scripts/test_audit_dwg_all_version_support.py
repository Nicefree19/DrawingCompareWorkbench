from __future__ import annotations

import json
from pathlib import Path

from scripts import audit_dwg_all_version_support as audit


def test_all_version_audit_passes_fallback_scope_with_complete_evidence(tmp_path: Path) -> None:
    evidence = _write_support_evidence(
        tmp_path,
        {
            code: {
                "sample_count": 2,
                "real_pair_count": 2,
                "converted_dxf_baseline_count": 2,
                "fallback_supported": True,
                "default_customer_oda_calls": 0,
            }
            for code in audit.TARGET_DWG_CODES
        },
    )

    report = audit.run_audit(evidence_manifest=evidence)

    assert report["status"] == "passed"
    assert report["summary"]["fallback_missing_versions"] == []
    assert report["summary"]["native_missing_versions"] == list(audit.TARGET_DWG_CODES)
    assert report["next_actions"] == []


def test_all_version_audit_native_scope_requires_native_evidence(tmp_path: Path) -> None:
    evidence = _write_support_evidence(
        tmp_path,
        {
            code: {
                "sample_count": 2,
                "real_pair_count": 2,
                "converted_dxf_baseline_count": 2,
                "fallback_supported": True,
                "native_supported": False,
                "native_baseline_count": 0,
            }
            for code in audit.TARGET_DWG_CODES
        },
    )

    report = audit.run_audit(evidence_manifest=evidence, claim_scope="native")

    assert report["status"] == "failed"
    ac1032 = _version(report, "AC1032")
    assert "native_supported=false" in ac1032["native_blockers"]
    assert any(action["action"] == "implement_or_license_native_backend" for action in report["next_actions"])


def test_all_version_audit_passes_native_scope_with_complete_native_evidence(tmp_path: Path) -> None:
    evidence = _write_support_evidence(
        tmp_path,
        {
            code: {
                "sample_count": 2,
                "real_pair_count": 2,
                "converted_dxf_baseline_count": 2,
                "fallback_supported": True,
                "native_supported": True,
                "native_baseline_count": 2,
                "default_customer_oda_calls": 0,
            }
            for code in audit.TARGET_DWG_CODES
        },
    )

    report = audit.run_audit(evidence_manifest=evidence, claim_scope="native")

    assert report["status"] == "passed"
    assert report["summary"]["native_missing_versions"] == []
    assert report["summary"]["native_ready_versions"] == list(audit.TARGET_DWG_CODES)


def test_all_version_audit_aggregates_current_style_partial_reports(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.json"
    _write_json(
        inventory,
        {
            "version_counts": {"AC1032": 2},
            "root_summaries": [
                {
                    "converted_dxf_fallback_ready": True,
                    "version_counts": {"AC1032": 2},
                }
            ],
        },
    )
    validation = tmp_path / "real-world-validation.json"
    _write_json(
        validation,
        {
            "samples": [
                {"id": "old", "detected_version": "AC1032"},
                {"id": "new", "detected_version": "AC1032"},
            ],
            "pairs": [{"old_sample": "old", "new_sample": "new"}],
        },
    )
    phase0c = tmp_path / "phase0c.json"
    _write_json(
        phase0c,
        {
            "versions": {
                "AC1032": {
                    "compare_baseline_ready": True,
                    "pair_kind": "confirmed_revision_pair_existing_registered_dxf",
                    "import_entity_counts": {"before": 10, "after": 11},
                },
                "AC1024": {
                    "compare_baseline_ready": True,
                    "pair_kind": "compact_likely_revision_pair",
                    "import_entity_counts": {"before": 7, "after": 9},
                },
                "AC1018": {
                    "compare_baseline_ready": False,
                    "pair_kind": "single_file_duplicated_import_baseline",
                    "import_entity_counts": {"before": 5, "after": 5},
                }
            }
        },
    )

    report = audit.run_audit(
        phase0_inventory=inventory,
        phase0c_baselines=phase0c,
        real_world_validation=validation,
    )

    assert report["status"] == "failed"
    assert "AC1032" in report["summary"]["fallback_missing_versions"]
    assert "AC1018" in report["summary"]["fallback_missing_versions"]
    ac1032 = _version(report, "AC1032")
    assert ac1032["sample_count"] == 2
    assert ac1032["real_pair_count"] == 1
    assert ac1032["converted_dxf_baseline_count"] == 1
    assert "real_pair_count=1/2" in ac1032["fallback_blockers"]
    ac1032_actions = [action["action"] for action in ac1032["fallback_next_actions"]]
    assert "confirm_before_after_pairs" in ac1032_actions
    assert "capture_converted_dxf_baselines" in ac1032_actions
    ac1018_actions = [action["action"] for action in _version(report, "AC1018")["fallback_next_actions"]]
    assert _version(report, "AC1018")["sample_count"] == 1
    assert "collect_real_dwg_samples" in ac1018_actions
    assert "establish_approved_route" in ac1018_actions
    ac1024 = _version(report, "AC1024")
    assert ac1024["sample_count"] == 2
    assert "collect_real_dwg_samples" not in [action["action"] for action in ac1024["fallback_next_actions"]]


def test_all_version_audit_accepts_multiple_real_world_validations(tmp_path: Path) -> None:
    ac1018_validation = tmp_path / "ac1018-real-world.json"
    _write_json(
        ac1018_validation,
        {
            "samples": [
                {"id": "old", "detected_version": "AC1018"},
                {"id": "new", "detected_version": "AC1018"},
            ],
            "pairs": [{"old_sample": "old", "new_sample": "new"}],
        },
    )
    ac1021_validation = tmp_path / "ac1021-real-world.json"
    _write_json(
        ac1021_validation,
        {
            "samples": [
                {"id": "old", "detected_version": "AC1021"},
                {"id": "new", "detected_version": "AC1021"},
            ],
            "pairs": [{"old_sample": "old", "new_sample": "new"}],
        },
    )

    report = audit.run_audit(real_world_validation=[ac1018_validation, ac1021_validation])

    assert _version(report, "AC1018")["sample_count"] == 2
    assert _version(report, "AC1018")["real_pair_count"] == 1
    assert _version(report, "AC1021")["sample_count"] == 2
    assert _version(report, "AC1021")["real_pair_count"] == 1
    assert report["inputs"]["real_world_validation"] == [
        str(ac1018_validation),
        str(ac1021_validation),
    ]


def test_all_version_audit_blocks_premature_claim_wording(tmp_path: Path) -> None:
    evidence = _write_support_evidence(
        tmp_path,
        {
            "AC1032": {
                "sample_count": 2,
                "real_pair_count": 2,
                "converted_dxf_baseline_count": 2,
                "fallback_supported": True,
            }
        },
        release_claims=["All DWG versions supported."],
    )

    report = audit.run_audit(evidence_manifest=evidence)

    assert report["status"] == "failed"
    assert report["claim_violations"][0]["scope"] == "fallback"
    assert "AC1018" in report["claim_violations"][0]["missing_versions"]


def test_all_version_audit_cli_writes_report(tmp_path: Path) -> None:
    evidence = _write_support_evidence(
        tmp_path,
        {
            code: {
                "sample_count": 2,
                "real_pair_count": 2,
                "converted_dxf_baseline_count": 2,
                "fallback_supported": True,
            }
            for code in audit.TARGET_DWG_CODES
        },
    )
    out = tmp_path / "audit.json"

    exit_code = audit.main(["--evidence-manifest", str(evidence), "--out", str(out)])

    assert exit_code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "passed"


def _write_support_evidence(
    tmp_path: Path,
    versions: dict,
    *,
    release_claims: list[str] | None = None,
) -> Path:
    path = tmp_path / "support-evidence.json"
    _write_json(
        path,
        {
            "schema_version": "dwg-all-version-support-evidence/v1",
            "versions": versions,
            "release_claims": release_claims or [],
        },
    )
    return path


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _version(report: dict, code: str) -> dict:
    for item in report["versions"]:
        if item["code"] == code:
            return item
    raise AssertionError(f"missing version: {code}")
