from __future__ import annotations

from scripts.release_environment_check import (
    PROJECT_ROOT,
    _console_summary,
    _oda_status,
    collect_environment_report,
)


# Maps each release-gate REQUIRED runtime module (an *import* name) to the
# distribution name that must appear in the declared requirements. Kept here so
# that adding a new required module without declaring its dependency trips this
# test loudly (the silent Hungarian->greedy / SSIM-disabled clean-install
# degradation regression this guards against).
_IMPORT_TO_DISTRIBUTION = {
    "PySide6": "PySide6",
    "ezdxf": "ezdxf",
    "cv2": "opencv-python-headless",
    "numpy": "numpy",
    "PIL": "Pillow",
    "scipy": "scipy",
    "skimage": "scikit-image",
    "openpyxl": "openpyxl",
}


def test_release_environment_reports_oda_as_optional_legacy_fallback() -> None:
    status = _oda_status()

    assert status["required"] is False
    assert "legacy fallback" in status["policy"]


def test_release_environment_keeps_pymupdf_out_of_required_runtime_modules() -> None:
    report = collect_environment_report()

    assert "fitz" not in report["runtime_modules"]
    assert report["optional_or_licensed_modules"]["fitz"]["required"] is False
    assert "disabled unless separately licensed" in report["optional_or_licensed_modules"]["fitz"]["policy"]

    summary = _console_summary(report)
    assert "Optional/licensed modules:" in summary
    assert "required=no" in summary
    assert "ODA Converter: MISSING" not in summary
    assert "required=yes" not in summary


def _declared_distribution_lines() -> list[str]:
    """Actual requirement specifier lines (lowercased) a `pip install` resolves
    across the runtime requirement files (`requirements.txt` pulls in
    `requirements-core.txt`). Comments, blank lines, and `-r` includes are
    excluded so a comment that merely *mentions* a package can never satisfy the
    contract — only a real declared dependency does."""
    lines: list[str] = []
    for name in ("requirements.txt", "requirements-core.txt"):
        path = PROJECT_ROOT / name
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = raw.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("-"):
                continue
            lines.append(stripped.split("#", 1)[0].strip().lower())
    return lines


def test_every_required_runtime_module_is_a_declared_dependency() -> None:
    """Contract: every module the release gate marks REQUIRED must be installable
    from the declared requirements. Guards the silent-fallback regression where
    scipy/scikit-image were REQUIRED but in no manifest, so a clean
    `pip install -r requirements.txt` shipped an app that degraded match quality
    (Hungarian->greedy) and disabled SSIM."""
    report = collect_environment_report()
    required = set(report["runtime_modules"])

    # The mapping must cover every required module — a new required module added
    # without a declared distribution should fail here, not silently degrade.
    missing_mapping = required - set(_IMPORT_TO_DISTRIBUTION)
    assert not missing_mapping, (
        f"runtime_modules has no declared-dependency mapping for {sorted(missing_mapping)}; "
        "add it to _IMPORT_TO_DISTRIBUTION and declare the dependency"
    )

    requirement_lines = _declared_distribution_lines()
    undeclared = [
        module
        for module in required
        if not any(
            _IMPORT_TO_DISTRIBUTION[module].lower() in line for line in requirement_lines
        )
    ]
    assert not undeclared, (
        "release gate marks these REQUIRED but no requirements file declares them: "
        + ", ".join(f"{m} ({_IMPORT_TO_DISTRIBUTION[m]})" for m in sorted(undeclared))
    )
