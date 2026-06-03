# -*- coding: utf-8 -*-
"""Drawing Compare operational preflight checks."""

from pathlib import Path

from src.services.comparison.dwg_backend import COMMERCIAL_SDK_ADAPTER_ENV
from src.services.comparison.preflight import run_preflight


def test_preflight_surfaces_mvp_operational_checks(tmp_path: Path) -> None:
    source_a = tmp_path / "old"
    source_b = tmp_path / "new"
    output = tmp_path / "out"
    source_a.mkdir()
    source_b.mkdir()

    result = run_preflight(source_a=source_a, source_b=source_b, output_dir=output)
    names = {check.name for check in result.checks}

    assert {
        "source_a",
        "source_b",
        "output_dir",
        "dxf_cache_dir",
        "compare_state_dir",
        "disk_space",
        "temp_dir",
        "rtree",
        "oda_converter",
        "dwg_version_support",
        "pymupdf",
        "pdf_support",
        "font_support",
        "preview_dependencies",
    }.issubset(names)
    assert any(check.name == "windows_long_path" for check in result.checks)


def test_preflight_rejects_unsupported_dwg_version_before_compare(tmp_path: Path) -> None:
    source_a = tmp_path / "old.dwg"
    source_b = tmp_path / "new.dwg"
    output = tmp_path / "out"
    source_a.write_bytes(b"AC1032" + b"\0" * 32)
    source_b.write_bytes(b"AC1032" + b"\0" * 32)

    result = run_preflight(source_a=source_a, source_b=source_b, output_dir=output)
    check = next(item for item in result.checks if item.name == "dwg_version_support")

    assert result.status == "failed"
    assert check.status == "error"
    assert "AC1032" in check.message
    assert "converted DXF" in check.message
    assert {item["code"] for item in check.details["unsupported"]} == {"AC1032"}


def test_preflight_allows_unsupported_dwg_with_explicit_user_converter(tmp_path: Path) -> None:
    source_a = tmp_path / "old.dwg"
    source_b = tmp_path / "new.dwg"
    output = tmp_path / "out"
    converter = tmp_path / "customer-converter.exe"
    source_a.write_bytes(b"AC1032" + b"\0" * 32)
    source_b.write_bytes(b"AC1032" + b"\0" * 32)
    converter.write_text("", encoding="utf-8")

    result = run_preflight(
        source_a=source_a,
        source_b=source_b,
        output_dir=output,
        dwg_backend_mode="user_converter",
        user_converter_path=converter,
    )
    user_converter = next(item for item in result.checks if item.name == "user_converter")
    dwg_support = next(item for item in result.checks if item.name == "dwg_version_support")

    assert result.status in {"passed", "warning"}
    assert user_converter.status == "ok"
    assert dwg_support.status == "warning"
    assert "user_converter" in dwg_support.message


def test_preflight_allows_unsupported_dwg_with_approved_commercial_sdk(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_a = tmp_path / "old.dwg"
    source_b = tmp_path / "new.dwg"
    output = tmp_path / "out"
    source_a.write_bytes(b"AC1032" + b"\0" * 32)
    source_b.write_bytes(b"AC1032" + b"\0" * 32)
    _write_commercial_plugin(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(COMMERCIAL_SDK_ADAPTER_ENV, "preflight_commercial_adapter:create_adapter")

    result = run_preflight(
        source_a=source_a,
        source_b=source_b,
        output_dir=output,
        dwg_backend_mode="commercial_sdk",
        allowed_dwg_license_ids=("MIT", "INTERNAL", "COMMERCIAL-APPROVED"),
    )
    commercial = next(item for item in result.checks if item.name == "commercial_dwg_sdk")
    dwg_support = next(item for item in result.checks if item.name == "dwg_version_support")

    assert result.status in {"passed", "warning"}
    assert commercial.status == "ok"
    assert commercial.details["license_id"] == "COMMERCIAL-APPROVED"
    assert dwg_support.status == "warning"
    assert "commercial_sdk" in dwg_support.message


def test_preflight_rejects_commercial_sdk_without_license_allowlist(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_a = tmp_path / "old.dwg"
    source_b = tmp_path / "new.dwg"
    output = tmp_path / "out"
    source_a.write_bytes(b"AC1032" + b"\0" * 32)
    source_b.write_bytes(b"AC1032" + b"\0" * 32)
    _write_commercial_plugin(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(COMMERCIAL_SDK_ADAPTER_ENV, "preflight_commercial_adapter:create_adapter")

    result = run_preflight(
        source_a=source_a,
        source_b=source_b,
        output_dir=output,
        dwg_backend_mode="commercial_sdk",
    )
    commercial = next(item for item in result.checks if item.name == "commercial_dwg_sdk")

    assert result.status == "failed"
    assert commercial.status == "error"
    assert "license is not explicitly allowed" in commercial.message


def _write_commercial_plugin(tmp_path: Path) -> None:
    plugin = tmp_path / "preflight_commercial_adapter.py"
    plugin.write_text(
        "\n".join(
            [
                "from src.services.comparison.dwg_backend import DWG_BACKEND_COMMERCIAL_SDK",
                "from src.services.comparison.dwg_importer import DwgJsonFixtureAdapter",
                "",
                "class PreflightCommercialAdapter(DwgJsonFixtureAdapter):",
                "    name = 'preflight-commercial-fixture'",
                "    version = '2026.1'",
                "    license_id = 'COMMERCIAL-APPROVED'",
                "    backend_mode = DWG_BACKEND_COMMERCIAL_SDK",
                "    implementation_status = 'approved_plugin'",
                "    approval_required = True",
                "",
                "    def supports_version(self, version):",
                "        return True",
                "",
                "def create_adapter():",
                "    return PreflightCommercialAdapter()",
                "",
            ]
        ),
        encoding="utf-8",
    )
