from __future__ import annotations

import json
from pathlib import Path

from scripts import validate_accuracy_evidence as evidence


def test_normalize_truth_swaps_obvious_reversed_before_after() -> None:
    files = [
        _file("v_before", "D:/samples/AC1032/before/before.dwg", "AC1032"),
        _file("v_after", "D:/samples/AC1032/after/after.dwg", "AC1032"),
    ]
    pairs = [
        _pair(
            "pair",
            before_file_id="v_after",
            after_file_id="v_before",
        )
    ]

    normalized = evidence.normalize_truth_records(files, pairs, add_identical_controls=False)

    assert normalized[0]["before_file_id"] == "v_before"
    assert normalized[0]["after_file_id"] == "v_after"
    assert normalized[0]["dwg_version"] == "AC1032"
    assert normalized[0]["normalization_actions"] == ["swapped_before_after"]
    assert normalized[0]["accuracy_status"] == "active"


def test_normalize_manifest_merges_duplicate_ids_with_stricter_confidentiality() -> None:
    files = [
        _file("same", "D:/out/sample.dwg", "AC1032", confidentiality="internal"),
        _file("same", "D:/customer/sample.dwg", "AC1032", confidentiality="customer_confidential"),
    ]

    normalized = evidence.normalize_manifest_records(files)

    assert len(normalized) == 1
    assert normalized[0]["confidentiality"] == "customer_confidential"
    assert normalized[0]["absolute_path"] == "D:/customer/sample.dwg"
    assert normalized[0]["alternate_paths"] == ["D:/out/sample.dwg"]
    assert normalized[0]["normalization_actions"] == ["merged_duplicate_file_id"]


def test_normalize_truth_excludes_timeout_and_duplicate_lower_value_pair() -> None:
    files = [
        _file("before", "D:/samples/240111_base.dwg", "AC1032"),
        _file("after", "D:/samples/240111_base_r1.dwg", "AC1032"),
    ]
    pairs = [
        _pair("timeout", before_file_id="before", after_file_id="after", notes="Compare TIMED OUT"),
        _pair("visual", before_file_id="after", after_file_id="before", pair_type="small_geometry_change"),
        _pair("structural", before_file_id="before", after_file_id="after", pair_type="structural_change"),
    ]

    normalized = evidence.normalize_truth_records(files, pairs, add_identical_controls=False)
    by_id = {pair["pair_id"]: pair for pair in normalized}

    assert by_id["timeout"]["accuracy_status"] == "excluded"
    assert by_id["timeout"]["exclusion_reasons"] == ["validation_not_completed"]
    assert by_id["visual"]["accuracy_status"] == "excluded"
    assert by_id["visual"]["exclusion_reasons"] == ["duplicate_normalized_pair"]
    assert by_id["structural"]["accuracy_status"] == "active"


def test_normalize_truth_adds_identical_negative_controls_per_version() -> None:
    files = [
        _file("ac1009", "D:/samples/ac1009.dwg", "AC1009"),
        _file("ac1032_conf", "D:/customer/ac1032.dwg", "AC1032", confidentiality="customer_confidential"),
        _file("ac1032_public", "D:/samples/ac1032.dwg", "AC1032"),
    ]

    normalized = evidence.normalize_truth_records(files, [], add_identical_controls=True)

    controls = [pair for pair in normalized if pair["pair_type"] == "identical"]
    assert [pair["dwg_version"] for pair in controls] == ["AC1009", "AC1032"]
    assert all(pair["before_file_id"] == pair["after_file_id"] for pair in controls)
    assert all(pair["expected_changed"] is False for pair in controls)
    assert {pair["source_confidentiality"] for pair in controls} == {"public"}


def test_normalize_truth_adds_version_resave_controls_from_matrix_groups_only() -> None:
    files = [
        _file("m_ac1009", "D:/out/fixture_version_matrix/AC1009/before/before.dwg", "AC1009"),
        _file("m_ac1012", "D:/out/fixture_version_matrix/AC1012/before/before.dwg", "AC1012"),
        _file("m_ac1014", "D:/out/fixture_version_matrix/AC1014/before/before.dwg", "AC1014"),
        _file("oneoff_ac1032", "D:/out/customer_samples/AC1032/before/before.dwg", "AC1032"),
        _file(
            "conf_ac1024",
            "D:/out/fixture_version_matrix/AC1024/before/before.dwg",
            "AC1024",
            confidentiality="customer_confidential",
        ),
    ]

    normalized = evidence.normalize_truth_records(
        files,
        [],
        add_identical_controls=False,
        add_version_resave_controls=True,
    )

    controls = [pair for pair in normalized if pair["pair_type"] == "version_resave"]
    assert [(pair["before_file_id"], pair["after_file_id"]) for pair in controls] == [
        ("m_ac1009", "m_ac1012"),
        ("m_ac1012", "m_ac1014"),
    ]
    assert all(pair["expected_changed"] is False for pair in controls)
    assert all(pair["source_confidentiality"] == "public" for pair in controls)


def test_build_report_fails_on_active_reversed_pair() -> None:
    files = [
        _file("before", "D:/samples/before.dwg", "AC1032", exists=False),
        _file("after", "D:/samples/after.dwg", "AC1032", exists=False),
    ]
    pairs = [_pair("pair", before_file_id="after", after_file_id="before")]

    report = evidence.build_report(files, pairs)

    assert report["status"] == "failed"
    assert any("appears to have before/after reversed" in error for error in report["errors"])


def test_cli_writes_normalized_truth_and_report(tmp_path: Path) -> None:
    before = tmp_path / "before.dwg"
    after = tmp_path / "after.dwg"
    before.write_text("before", encoding="ascii")
    after.write_text("after", encoding="ascii")
    manifest = tmp_path / "manifest.json"
    truth = tmp_path / "truth.json"
    normalized = tmp_path / "truth_normalized.json"
    report = tmp_path / "report.json"
    report_md = tmp_path / "report.md"
    manifest.write_text(
        json.dumps(
            [
                _file("before", str(before), "AC1032", sha=_sha(before), size=before.stat().st_size),
                _file("after", str(after), "AC1032", sha=_sha(after), size=after.stat().st_size),
            ]
        ),
        encoding="utf-8",
    )
    truth.write_text(
        json.dumps([_pair("pair", before_file_id="after", after_file_id="before")]),
        encoding="utf-8",
    )

    exit_code = evidence.main(
        [
            "--manifest",
            str(manifest),
            "--truth",
            str(truth),
            "--write-normalized",
            str(normalized),
            "--report-json",
            str(report),
            "--report-md",
            str(report_md),
        ]
    )

    payload = json.loads(normalized.read_text(encoding="utf-8"))
    validation = json.loads(report.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["schema_version"] == evidence.TRUTH_SCHEMA_VERSION
    assert payload["pairs"][0]["before_file_id"] == "before"
    assert validation["status"] == "passed"
    assert validation["summary"]["negative_control_count"] == 1
    assert "version_resave negative controls are missing" in validation["warnings"]
    assert report_md.read_text(encoding="utf-8").startswith("# Accuracy Evidence Validation")


def _file(
    file_id: str,
    path: str,
    version: str,
    *,
    confidentiality: str = "public",
    sha: str | None = None,
    size: int = 1,
    exists: bool = True,
) -> dict[str, object]:
    return {
        "file_id": file_id,
        "absolute_path": path if exists else "",
        "sha256": sha or ("0" * 64),
        "file_size_bytes": size,
        "dwg_version": version,
        "source_type": "generated",
        "confidentiality": confidentiality,
        "license_or_permission": "MIT",
        "drawing_category": "simple geometry",
        "complexity_tags": ["simple"],
        "has_model_space": True,
        "has_paper_space": False,
        "has_blocks": False,
        "has_nested_blocks": False,
        "has_text": False,
        "has_dimensions": False,
        "has_hatch": False,
        "notes": "",
    }


def _pair(
    pair_id: str,
    *,
    before_file_id: str,
    after_file_id: str,
    pair_type: str = "small_geometry_change",
    notes: str = "",
) -> dict[str, object]:
    return {
        "pair_id": pair_id,
        "before_file_id": before_file_id,
        "after_file_id": after_file_id,
        "pair_type": pair_type,
        "expected_changed": True,
        "expected_change_count": 1,
        "expected_changes": [
            {
                "sheet": "Model",
                "region_id": "A1",
                "entity_type": "LINE",
                "change_type": "geometry_modification",
                "severity": "structural",
                "approx_bbox": [0, 0, 1, 1],
                "tolerance_class": "structural_position_tolerance_mm",
                "notes": "",
            }
        ],
        "reviewer_status": "agent_draft",
        "confidence": "medium",
        "notes": notes,
    }


def _sha(path: Path) -> str:
    return evidence.sha256_file(path)
