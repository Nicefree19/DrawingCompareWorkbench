"""Guards for the PyInstaller spec's clean-machine bundling contract.

The shipped exe must bundle the native libraries / data files of the deps that
the runtime actually uses, and must NOT drag in the inert AI-tier heavyweights.
These are text-level assertions (no PyInstaller needed) so they run in any CI and
fail loudly if someone drops a collect_all target or an exclude.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SPEC = _ROOT / "DrawingCompareWorkbench.spec"

# Runtime files the exe loads from the bundle (rendered/dispatched at runtime).
# They ride along via the spec's broad `src`/`scripts` datas entries — but if one
# is deleted from the repo before a build, PyInstaller bundles nothing and the
# clean-machine exe fails silently. Assert they exist so the build has them.
_CRITICAL_BUNDLED_FILES = (
    "scripts/render_viewer_package_subprocess.py",
    "src/gui/assets/drawing_compare/LightweightDrawingViewport.qml",
    "src/gui/assets/drawing_compare/DrawingGpuViewport.qml",
)


@pytest.fixture(scope="module")
def spec_text() -> str:
    return _SPEC.read_text(encoding="utf-8")


def _collect_all_targets(text: str) -> set[str]:
    """Package names inside the `for package_name in (...)` collect_all loop."""
    marker = "for package_name in ("
    start = text.index(marker) + len(marker)
    end = text.index(")", start)
    return {tok.strip().strip("\"'") for tok in text[start:end].split(",") if tok.strip()}


def test_native_dep_packages_are_collected(spec_text: str) -> None:
    # rtree (native libspatialindex) + skimage (SSIM data/submodules) must be
    # collected, or the clean-machine exe silently loses spatial-index
    # acceleration / SSIM. scipy is the established precedent.
    targets = _collect_all_targets(spec_text)
    for pkg in ("scipy", "skimage", "rtree"):
        assert pkg in targets, f"{pkg} missing from spec collect_all → clean-machine bundling gap"


def test_core_runtime_modules_are_hidden_imports(spec_text: str) -> None:
    for module in ("PySide6.QtWidgets", "ezdxf", "cv2", "numpy", "PIL", "scipy", "skimage", "fitz"):
        assert f'"{module}"' in spec_text, f"{module} not registered as a hiddenimport"


def test_spec_bundles_src_and_scripts_dirs(spec_text: str) -> None:
    # The QML viewer assets (src/gui/assets) and the subprocess worker entry
    # (scripts/render_viewer_package_subprocess.py) reach the exe only through
    # these broad datas entries. Removing them silently breaks the viewer +
    # subprocess render on a clean machine.
    assert '(str(ROOT / "src"), "src")' in spec_text, "spec must bundle the src/ tree as datas"
    assert '(str(ROOT / "scripts"), "scripts")' in spec_text, "spec must bundle the scripts/ tree as datas"


def test_critical_runtime_files_present_for_bundling() -> None:
    # Catch accidental deletion before a build — PyInstaller would not error,
    # it would just ship an exe missing these and fail/blank on a clean machine.
    missing = [rel for rel in _CRITICAL_BUNDLED_FILES if not (_ROOT / rel).exists()]
    assert not missing, f"critical bundled runtime files missing from repo: {missing}"


def test_inert_ai_heavyweights_are_excluded(spec_text: str) -> None:
    # The opt-in AI/OCR tier ships inert (no models); its heavyweights must stay
    # out of the default build. onnxruntime alone is ~200 MB.
    for pkg in ("onnxruntime", "torch", "easyocr", "paddleocr", "transformers"):
        assert f'"{pkg}"' in spec_text, f"{pkg} should be in spec excludes (dead bloat)"
