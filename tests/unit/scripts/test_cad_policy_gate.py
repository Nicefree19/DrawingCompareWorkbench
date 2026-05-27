from __future__ import annotations

from pathlib import Path

from scripts.cad_policy_gate import scan_repo


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_policy_gate_rejects_oda_required_wording_and_pymupdf_auto(tmp_path: Path) -> None:
    _write(tmp_path / "requirements.txt", "-r requirements-core.txt\n")
    _write(tmp_path / "src/services/comparison/dwg_differ.py", "raise RuntimeError('DWG comparison requires ODA File Converter')\n")
    _write(tmp_path / "src/services/comparison/dxf_renderer.py", "fallback_chain = ['pymupdf', 'matplotlib']\n")
    _write(tmp_path / "scripts/release_environment_check.py", "runtime_modules = {\n    'fitz': _import_status('fitz')\n}\n")
    _write(tmp_path / ".github/workflows/cad-format-regression.yml", "steps:\n  - run: python scripts\\cad_format_regression.py\n")

    codes = {violation.code for violation in scan_repo(tmp_path)}

    assert "CAD_POLICY_ODA_REQUIRED_WORDING" in codes
    assert "CAD_POLICY_DEFAULT_PYMUPDF_AUTO" in codes
    assert "CAD_POLICY_PYMUPDF_REQUIRED_RUNTIME" in codes
    assert "CAD_POLICY_CI_DIFF_CHECK" in codes
    assert "CAD_POLICY_CI_POLICY_GATE" in codes
    assert "CAD_POLICY_DWG_CLEANROOM_CONTRACT_MISSING" in codes


def test_policy_gate_rejects_unverified_dwg_support_claims(tmp_path: Path) -> None:
    _write(tmp_path / "requirements.txt", "-r requirements-core.txt\n")
    _write(tmp_path / "README.md", "AC1032 native DWG supported\nDWG fully supported\n")
    _write(
        tmp_path / "docs/CAD_FORMAT_SUPPORT_POLICY.md",
        "AC1024 DWG support available in customer builds\n",
    )
    _write(
        tmp_path / "docs/DWG_CLEANROOM_FORMAT_CONTRACT.md",
        "\n".join(
            [
                "DWG-CLEANROOM-SECTION-MAP-CONTRACT-v1",
                "ODA File Converter",
                "GPL or AGPL",
                "approved_format_contract_required",
                "AC1024",
                "AC1032",
                "Evidence Packet",
                "Reference title",
                "Source URL/path",
                "Allowed use",
                "Approval status",
                "pending",
                "blocked",
            ]
        ),
    )
    _write(
        tmp_path / "src/services/comparison/dwg_cleanroom_contract.py",
        "CLEANROOM_CONTRACT_ID='DWG-CLEANROOM-SECTION-MAP-CONTRACT-v1'\n"
        'contracts={"AC1024": dict(approval_status="blocked", approved_reference_available=False), '
        '"AC1032": dict(approval_status="blocked", approved_reference_available=False)}\n',
    )

    codes = {violation.code for violation in scan_repo(tmp_path)}

    assert "CAD_POLICY_AC1032_SUPPORTED_WORDING" in codes
    assert "CAD_POLICY_AC1024_SUPPORTED_WORDING" in codes
    assert "CAD_POLICY_DWG_FULLY_SUPPORTED_WORDING" in codes


def test_policy_gate_accepts_customer_safe_wording_and_ci(tmp_path: Path) -> None:
    _write(tmp_path / "requirements.txt", "-r requirements-core.txt\n")
    _write(tmp_path / "src/services/comparison/dwg_differ.py", "msg = 'Legacy DWG-to-DXF fallback is disabled or not configured'\n")
    _write(tmp_path / "src/services/comparison/dxf_renderer.py", "fallback_chain = ['matplotlib']\n")
    _write(
        tmp_path / "scripts/release_environment_check.py",
        "runtime_modules = {\n    'ezdxf': _import_status('ezdxf')\n}\n"
        "optional_or_licensed_modules = {'fitz': {'required': False}}\n",
    )
    _write(
        tmp_path / ".github/workflows/cad-format-regression.yml",
        "steps:\n  - run: git diff --check\n  - run: python scripts\\cad_policy_gate.py\n",
    )
    _write(
        tmp_path / "docs/DWG_CLEANROOM_FORMAT_CONTRACT.md",
        "\n".join(
            [
                "DWG-CLEANROOM-SECTION-MAP-CONTRACT-v1",
                "ODA File Converter",
                "GPL or AGPL",
                "approved_format_contract_required",
                "AC1024",
                "AC1032",
                "Evidence Packet",
                "Reference title",
                "Source URL/path",
                "Allowed use",
                "Approval status",
                "pending",
                "blocked",
            ]
        ),
    )
    _write(
        tmp_path / "src/services/comparison/dwg_cleanroom_contract.py",
        "CLEANROOM_CONTRACT_ID='DWG-CLEANROOM-SECTION-MAP-CONTRACT-v1'\n"
        'contracts={"AC1024": dict(approval_status="blocked", approved_reference_available=False), '
        '"AC1032": dict(approval_status="blocked", approved_reference_available=False)}\n',
    )

    assert scan_repo(tmp_path) == []


def test_policy_gate_rejects_default_enabled_nonapproved_cad_visual_backend(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/services/comparison/render_backend_registry.py",
        'CAD_VISUAL_BACKENDS = {"qcad_professional_cli": {"enabled_by_default": True}}\n',
    )

    codes = {violation.code for violation in scan_repo(tmp_path)}

    assert "CAD_POLICY_NON_APPROVED_VISUAL_BACKEND_DEFAULT_ENABLED" in codes


def test_policy_gate_rejects_nonapproved_cad_visual_auto_fallback_chain(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/services/comparison/cad_visual_conversion_worker.py",
        'fallback_chain = ["qcad_pro", "aspose_cad", "ghostscript"]\n',
    )

    codes = {violation.code for violation in scan_repo(tmp_path)}

    assert "CAD_POLICY_NON_APPROVED_VISUAL_BACKEND_AUTO_CHAIN" in codes


def test_policy_gate_rejects_cad_visual_conversion_in_viewer_hot_path(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/services/comparison/viewer_package.py",
        "result = registry.convert_cad_visual(request)\n",
    )
    _write(
        tmp_path / "src/gui/drawing_compare_workbench.py",
        "result = convert_cad_visual_in_subprocess(request)\n",
    )

    codes = {violation.code for violation in scan_repo(tmp_path)}

    assert "CAD_POLICY_CAD_VISUAL_HOT_PATH_CONVERSION" in codes


def test_policy_gate_accepts_disabled_cad_visual_backend_descriptors(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/services/comparison/render_backend_registry.py",
        'CAD_VISUAL_BACKENDS = {"qcad_professional_cli": {"enabled_by_default": False}}\n'
        'DEFAULT_CAD_VISUAL_BACKENDS = []\n',
    )

    codes = {violation.code for violation in scan_repo(tmp_path)}

    assert "CAD_POLICY_NON_APPROVED_VISUAL_BACKEND_DEFAULT_ENABLED" not in codes
    assert "CAD_POLICY_NON_APPROVED_VISUAL_BACKEND_AUTO_CHAIN" not in codes
