from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import scripts.validate_adr004_version_sample_pack as validator
from scripts.validate_adr004_version_sample_pack import (
    build_report,
    detect_dwg_header,
    detect_dxf_acadver,
    render_markdown,
    validate_manifest,
)
from src.services.comparison.import_pipeline import ImportPipelineResult


def _write_dxf(path: Path, acadver: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "0",
                "SECTION",
                "2",
                "HEADER",
                "9",
                "$ACADVER",
                "1",
                acadver,
                "0",
                "ENDSEC",
                "0",
                "EOF",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _write_dwg(path: Path, code: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(code.encode("ascii") + b"\x00sample")
    return path


def _sample_pack(tmp_path: Path, *, output_acadver: str = "AC1024", manifest_acadver: str = "AC1024") -> Path:
    before_dxf = _write_dxf(tmp_path / "AC1024/dxf_registered/before/before.dxf", output_acadver)
    after_dxf = _write_dxf(tmp_path / "AC1024/dxf_registered/after/after.dxf", output_acadver)
    before_dwg = _write_dwg(tmp_path / "AC1024/before/before.dwg", "AC1024")
    after_dwg = _write_dwg(tmp_path / "AC1024/after/after.dwg", "AC1024")
    manifest = {
        "schema_version": 1,
        "versions": {
            "AC1024": {
                "dwg_code": "AC1024",
                "dxf_output_version": "ACAD2010",
                "pair_kind": "unit_test_pair",
                "sample_before_dwg": str(before_dwg),
                "sample_after_dwg": str(after_dwg),
                "outputs": {
                    "before": [
                        {
                            "path": str(before_dxf),
                            "size": before_dxf.stat().st_size,
                            "sha256": "before",
                            "acadver": manifest_acadver,
                        }
                    ],
                    "after": [
                        {
                            "path": str(after_dxf),
                            "size": after_dxf.stat().st_size,
                            "sha256": "after",
                            "acadver": manifest_acadver,
                        }
                    ],
                },
            }
        },
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path


def test_detects_dxf_acadver_and_dwg_header(tmp_path: Path) -> None:
    dxf = _write_dxf(tmp_path / "sample.dxf", "AC1032")
    dwg = _write_dwg(tmp_path / "sample.dwg", "AC1032")

    assert detect_dxf_acadver(dxf) == "AC1032"
    assert detect_dwg_header(dwg) == "AC1032"


def test_validate_manifest_rejects_missing_outputs() -> None:
    errors = validate_manifest({"schema_version": 1, "versions": {"AC1024": {"dwg_code": "AC1024"}}})

    assert "versions.AC1024.outputs is required" in errors
    assert "versions.AC1024.outputs.before must be a non-empty list" in errors


def test_build_report_checks_headers_without_running_smoke(tmp_path: Path) -> None:
    sample_pack = _sample_pack(tmp_path)

    report = build_report(sample_pack, run_import=False, run_compare=False, root=Path.cwd())

    assert report["status"] == "ok"
    assert report["summary"]["header_mismatch_count"] == 0
    assert report["summary"]["import_status_counts"] == {"skipped": 2}
    assert report["summary"]["compare_status_counts"] == {"skipped": 1}
    markdown = render_markdown(report)
    assert "ADR-004 Version Sample Pack Validation" in markdown
    assert "| AC1024 | before | AC1024 | AC1024 |" in markdown


def test_build_report_can_filter_requested_versions(tmp_path: Path) -> None:
    sample_pack = _sample_pack(tmp_path)

    report = build_report(
        sample_pack,
        run_import=False,
        run_compare=False,
        root=Path.cwd(),
        only_versions={"AC1024"},
    )

    assert report["status"] == "ok"
    assert report["summary"]["version_count"] == 1
    assert [record["version"] for record in report["versions"]] == ["AC1024"]


def test_build_report_can_compare_registered_dxf_outputs(monkeypatch, tmp_path: Path) -> None:
    sample_pack = _sample_pack(tmp_path)
    calls = []

    def fake_compare_worker(source_a: Path, source_b: Path, **kwargs):
        calls.append((source_a, source_b, kwargs))
        return {"status": "ok", "summary": {"total_changes": 0}}

    monkeypatch.setattr(validator, "_run_compare_worker", fake_compare_worker)

    report = build_report(
        sample_pack,
        run_import=False,
        run_compare=True,
        compare_source="dxf",
        root=Path.cwd(),
    )

    assert report["status"] == "ok"
    assert calls
    assert calls[0][0].suffix == ".dxf"
    assert calls[0][1].suffix == ".dxf"
    assert report["limits"]["compare_source"] == "dxf"


def test_build_report_passes_commercial_sdk_backend_to_dwg_compare_worker(monkeypatch, tmp_path: Path) -> None:
    sample_pack = _sample_pack(tmp_path)
    calls = []

    def fake_compare_worker(source_a: Path, source_b: Path, **kwargs):
        calls.append((source_a, source_b, kwargs))
        return {
            "status": "ok",
            "summary": {"total_changes": 0},
            "imports": {"a": {"status": "ok"}, "b": {"status": "ok"}},
        }

    monkeypatch.setattr(validator, "_run_compare_worker", fake_compare_worker)

    report = build_report(
        sample_pack,
        run_import=False,
        run_compare=True,
        compare_source="dwg",
        dwg_backend_mode="commercial_sdk",
        allowed_dwg_license_ids=("MIT", "INTERNAL", "COMMERCIAL-APPROVED"),
        root=Path.cwd(),
    )

    assert report["status"] == "ok"
    assert calls
    assert calls[0][0].suffix == ".dwg"
    assert calls[0][1].suffix == ".dwg"
    assert calls[0][2]["dwg_backend_mode"] == "commercial_sdk"
    assert calls[0][2]["allowed_dwg_license_ids"] == ("MIT", "INTERNAL", "COMMERCIAL-APPROVED")
    assert report["limits"]["dwg_backend_mode"] == "commercial_sdk"
    assert report["limits"]["allowed_dwg_license_ids"] == ["MIT", "INTERNAL", "COMMERCIAL-APPROVED"]


def test_compare_worker_accepts_backend_args_and_builds_pipeline_options(monkeypatch, tmp_path: Path, capsys) -> None:
    captured = {}

    class FakeImportResult:
        status = "ok"

        def to_dict(self):
            return {"status": "ok", "warnings": [], "import_report": {}}

    class FakePipeline:
        def __init__(self, options):
            captured["options"] = options

        def compare(self, source_a, source_b):
            return SimpleNamespace(
                status="ok",
                error_code=None,
                message="",
                diff=SimpleNamespace(summary={"total_changes": 0}),
                elapsed_ms=1,
                imports={"a": FakeImportResult(), "b": FakeImportResult()},
                input_resolution={},
                warnings=[],
            )

    monkeypatch.setattr("src.services.comparison.import_pipeline.ComparePipeline", FakePipeline)

    exit_code = validator._compare_worker_main(
        [
            "--source-a",
            str(tmp_path / "before.dwg"),
            "--source-b",
            str(tmp_path / "after.dwg"),
            "--max-entities",
            "100",
            "--max-dxf-tokens",
            "200",
            "--import-timeout-seconds",
            "3",
            "--dwg-backend",
            "commercial_sdk",
            "--dwg-allowed-license-id",
            "MIT",
            "--dwg-allowed-license-id",
            "COMMERCIAL-APPROVED",
        ]
    )

    options = captured["options"]
    assert exit_code == 0
    assert options.import_options.dwg_backend_mode == "commercial_sdk"
    assert options.import_options.allowed_dwg_license_ids == ("MIT", "COMMERCIAL-APPROVED")
    assert options.import_options.allow_oda_fallback is False
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_compare_worker_enables_oda_fallback_only_for_oda_backend(monkeypatch, tmp_path: Path, capsys) -> None:
    captured = {}

    class FakeImportResult:
        status = "ok"

        def to_dict(self):
            return {"status": "ok", "warnings": [], "import_report": {}}

    class FakePipeline:
        def __init__(self, options):
            captured["options"] = options

        def compare(self, source_a, source_b):
            return SimpleNamespace(
                status="ok",
                error_code=None,
                message="",
                diff=SimpleNamespace(summary={"total_changes": 0}),
                elapsed_ms=1,
                imports={"a": FakeImportResult(), "b": FakeImportResult()},
                input_resolution={},
                warnings=[],
            )

    monkeypatch.setattr("src.services.comparison.import_pipeline.ComparePipeline", FakePipeline)

    exit_code = validator._compare_worker_main(
        [
            "--source-a",
            str(tmp_path / "before.dwg"),
            "--source-b",
            str(tmp_path / "after.dwg"),
            "--max-entities",
            "100",
            "--max-dxf-tokens",
            "200",
            "--import-timeout-seconds",
            "3",
            "--dwg-backend",
            "oda_converter",
        ]
    )

    options = captured["options"]
    assert exit_code == 0
    assert options.import_options.dwg_backend_mode == "oda_converter"
    assert options.import_options.allow_oda_fallback is True
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_compact_import_pipeline_result_preserves_adapter_metadata() -> None:
    result = ImportPipelineResult(
        source_path="before.dwg",
        source_format="dwg",
        status="partial",
        importer="DwgImporter",
        canonical_drawing={
            "entities": [],
            "layers": [],
            "metadata": {
                "adapter_metadata": {
                    "commercial_dwg_json_bridge": {
                        "evidence_scope": "converted_dxf_bridge",
                        "uses_converted_dxf": True,
                    }
                }
            },
            "import_report": {
                "adapter": {
                    "backend_mode": "commercial_sdk",
                    "license_id": "INTERNAL",
                }
            },
        },
        import_report={
            "adapter": {
                "backend_mode": "commercial_sdk",
                "license_id": "INTERNAL",
            },
        },
    )

    compact = validator._compact_import_pipeline_result(result)

    assert compact["adapter"]["backend_mode"] == "commercial_sdk"
    assert compact["adapter_metadata"]["commercial_dwg_json_bridge"]["uses_converted_dxf"] is True


def test_build_report_fails_when_requested_version_is_missing(tmp_path: Path) -> None:
    sample_pack = _sample_pack(tmp_path)

    report = build_report(
        sample_pack,
        run_import=False,
        run_compare=False,
        root=Path.cwd(),
        only_versions={"AC1032"},
    )

    assert report["status"] == "failed"
    assert report["summary"]["version_count"] == 0
    assert "requested version not found in manifest: AC1032" in report["manifest_errors"]


def test_build_report_fails_on_dxf_acadver_mismatch(tmp_path: Path) -> None:
    sample_pack = _sample_pack(tmp_path, output_acadver="AC1032", manifest_acadver="AC1024")

    report = build_report(sample_pack, run_import=False, run_compare=False, root=Path.cwd())

    assert report["status"] == "failed"
    assert report["summary"]["header_mismatch_count"] == 2
    assert any("DXF $ACADVER mismatch" in error for error in report["validation_errors"])
