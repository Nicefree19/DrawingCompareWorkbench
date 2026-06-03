from __future__ import annotations

import json
from pathlib import Path

from scripts.build_dwg_all_version_support_evidence import build_report, main


def test_build_support_evidence_aggregates_real_pairs_and_baselines(tmp_path: Path) -> None:
    summary_a = _summary(
        tmp_path / "a.json",
        [
            _version("AC1024", "before-a.dwg", "after-a.dwg", compare_status="partial"),
            _version("AC1018", "same.dwg", "same-copy.dwg", pair_kind="single_file_duplicated_import_baseline"),
        ],
    )
    summary_b = _summary(
        tmp_path / "b.json",
        [
            _version("AC1024", "before-b.dwg", "after-b.dwg", compare_status="timeout"),
        ],
    )

    report = build_report([summary_a, summary_b], root=Path.cwd())

    ac1024 = report["versions"]["AC1024"]
    assert ac1024["sample_count"] == 4
    assert ac1024["real_pair_count"] == 2
    assert ac1024["converted_dxf_baseline_count"] == 1
    assert ac1024["fallback_supported"] is True
    ac1018 = report["versions"]["AC1018"]
    assert ac1018["sample_count"] == 1
    assert ac1018["real_pair_count"] == 0
    assert ac1018["converted_dxf_baseline_count"] == 0


def test_build_support_evidence_cli_writes_manifest(tmp_path: Path) -> None:
    summary = _summary(
        tmp_path / "summary.json",
        [_version("AC1032", "old.dwg", "new.dwg", compare_status="partial")],
    )
    out = tmp_path / "evidence.json"

    exit_code = main(["--summary", str(summary), "--out", str(out)])

    assert exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "dwg-all-version-support-evidence/v1"
    assert payload["versions"]["AC1032"]["converted_dxf_baseline_count"] == 1


def test_build_support_evidence_counts_approved_commercial_sdk_native_baselines(tmp_path: Path) -> None:
    summary = _summary(
        tmp_path / "commercial.json",
        [
            _version("AC1032", "before-a.dwg", "after-a.dwg", compare_status="ok", native_compare=True),
            _version("AC1032", "before-b.dwg", "after-b.dwg", compare_status="partial", native_compare=True),
        ],
        compare_source="dwg",
        dwg_backend_mode="commercial_sdk",
    )

    report = build_report([summary], root=Path.cwd())

    ac1032 = report["versions"]["AC1032"]
    assert ac1032["native_supported"] is True
    assert ac1032["native_baseline_count"] == 2
    assert ac1032["native_backend_modes"] == ["commercial_sdk"]
    assert report["summary"]["versions_with_native_baselines"] == ["AC1032"]


def test_build_support_evidence_rejects_converted_dxf_bridge_as_native_baseline(tmp_path: Path) -> None:
    summary = _summary(
        tmp_path / "commercial-dxf-bridge.json",
        [
            _version(
                "AC1032",
                "before-a.dwg",
                "after-a.dwg",
                compare_status="ok",
                native_compare=True,
                bridge_metadata={
                    "commercial_dwg_json_bridge": {
                        "evidence_scope": "converted_dxf_bridge",
                        "uses_converted_dxf": True,
                        "converted_dxf_path": "before-a.dxf",
                    }
                },
            )
        ],
        compare_source="dwg",
        dwg_backend_mode="commercial_sdk",
    )

    report = build_report([summary], root=Path.cwd())

    ac1032 = report["versions"]["AC1032"]
    assert ac1032["converted_dxf_baseline_count"] == 1
    assert ac1032["native_supported"] is False
    assert ac1032["native_baseline_count"] == 0
    assert report["summary"]["versions_with_native_baselines"] == []


def test_build_support_evidence_rejects_unspecified_json_bridge_as_native_baseline(tmp_path: Path) -> None:
    summary = _summary(
        tmp_path / "commercial-unspecified-bridge.json",
        [
            _version(
                "AC1032",
                "before-a.dwg",
                "after-a.dwg",
                compare_status="ok",
                native_compare=True,
                bridge_metadata={
                    "commercial_dwg_json_bridge": {
                        "adapter": "commercial-dwg-json-bridge",
                        "backend_mode": "commercial_sdk",
                    }
                },
            )
        ],
        compare_source="dwg",
        dwg_backend_mode="commercial_sdk",
    )

    report = build_report([summary], root=Path.cwd())

    ac1032 = report["versions"]["AC1032"]
    assert ac1032["native_supported"] is False
    assert ac1032["native_baseline_count"] == 0


def test_build_support_evidence_accepts_native_marked_json_bridge_baseline(tmp_path: Path) -> None:
    summary = _summary(
        tmp_path / "commercial-native-bridge.json",
        [
            _version(
                "AC1032",
                "before-a.dwg",
                "after-a.dwg",
                compare_status="ok",
                native_compare=True,
                bridge_metadata={
                    "commercial_dwg_json_bridge": {
                        "adapter": "commercial-dwg-json-bridge",
                        "backend_mode": "commercial_sdk",
                        "evidence_scope": "native_dwg_bridge",
                        "uses_native_dwg": True,
                    }
                },
            )
        ],
        compare_source="dwg",
        dwg_backend_mode="commercial_sdk",
    )

    report = build_report([summary], root=Path.cwd())

    ac1032 = report["versions"]["AC1032"]
    assert ac1032["native_supported"] is True
    assert ac1032["native_baseline_count"] == 1
    assert report["summary"]["versions_with_native_baselines"] == ["AC1032"]


def _summary(
    path: Path,
    versions: list[dict],
    *,
    compare_source: str = "dxf",
    dwg_backend_mode: str = "user_converter",
) -> Path:
    path.write_text(
        json.dumps(
            {
                "status": "partial",
                "sample_pack": str(path.parent),
                "limits": {
                    "compare_source": compare_source,
                    "dwg_backend_mode": dwg_backend_mode,
                },
                "versions": versions,
            }
        ),
        encoding="utf-8",
    )
    return path


def _version(
    code: str,
    before: str,
    after: str,
    *,
    pair_kind: str = "compact_likely_revision_pair",
    compare_status: str = "partial",
    native_compare: bool = False,
    bridge_metadata: dict | None = None,
) -> dict:
    compare = {"status": compare_status}
    if native_compare:
        compare["imports"] = {
            "a": {"status": "ok", "adapter_metadata": bridge_metadata or {}},
            "b": {"status": "partial", "adapter_metadata": bridge_metadata or {}},
        }
    return {
        "version": code,
        "pair_kind": pair_kind,
        "dwg_inputs": {
            "before": {
                "path": before,
                "exists": True,
                "detected_header": code,
                "header_matches_version": True,
            },
            "after": {
                "path": after,
                "exists": True,
                "detected_header": code,
                "header_matches_version": True,
            },
        },
        "outputs": {
            "before": [
                {
                    "exists": True,
                    "detected_acadver": code,
                    "header_matches_expected": True,
                }
            ],
            "after": [
                {
                    "exists": True,
                    "detected_acadver": code,
                    "header_matches_expected": True,
                }
            ],
        },
        "imports": {
            "before": {"status": "partial"},
            "after": {"status": "partial"},
        },
        "compare": compare,
        "validation_errors": [],
    }
