"""Guards for the PyInstaller spec's clean-machine bundling contract.

The shipped exe must bundle the native libraries / data files of the deps that
the runtime actually uses, and must NOT drag in the inert AI-tier heavyweights.
These are text-level assertions (no PyInstaller needed) so they run in any CI and
fail loudly if someone drops a collect_all target or an exclude.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_SPEC = Path(__file__).resolve().parents[3] / "DrawingCompareWorkbench.spec"


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


def test_inert_ai_heavyweights_are_excluded(spec_text: str) -> None:
    # The opt-in AI/OCR tier ships inert (no models); its heavyweights must stay
    # out of the default build. onnxruntime alone is ~200 MB.
    for pkg in ("onnxruntime", "torch", "easyocr", "paddleocr", "transformers"):
        assert f'"{pkg}"' in spec_text, f"{pkg} should be in spec excludes (dead bloat)"
