"""Pure viewer source / artifact-path resolution helpers for the workbench.

Fourth satellite extraction of the ``drawing_compare_workbench`` god-object
(tech-debt audit MONO-4; follows overlay/summary/bbox modules). These resolve
the real or package-local file a lightweight viewport should render from a
``viewer_manifest`` entry, honoring the ``<redacted>`` paths that customer-
shareable exports write. Pure path logic with no Qt and no widget state;
``drawing_compare_workbench`` re-imports each so call sites are unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


def _is_redacted_artifact_path(value: Any) -> bool:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return False
    return (
        text.startswith("<redacted>/")
        or text.startswith("&lt;redacted&gt;/")
        or text.startswith("/redacted/")
    )


def _resolve_viewer_artifact_path(value: Any, viewer_root: Optional[Path]) -> Optional[Path]:
    """Resolve a path stored in ``viewer_manifest.json``.

    Sharable exports rewrite absolute paths to package-relative values such as
    ``viewer/images/...``. The manifest itself lives in ``<output>/viewer``, so
    those paths are relative to the package root, not always the manifest
    directory. Try both roots before giving up.
    """

    text = str(value or "").strip()
    if not text or _is_redacted_artifact_path(text):
        return None
    path = Path(text)
    if path.is_absolute():
        return path

    roots: list[Path] = []
    if viewer_root:
        root = Path(viewer_root)
        roots.extend([root, root.parent])

    candidates = [path]
    candidates.extend(root / path for root in roots)
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue

    if viewer_root:
        root = Path(viewer_root)
        first_part = path.parts[0] if path.parts else ""
        if first_part and first_part.lower() == root.name.lower():
            return root.parent / path
        return root / path
    return path


def _existing_pdf_file(value: Any) -> Optional[Path]:
    text = str(value or "").strip()
    if not text or _is_redacted_artifact_path(text):
        return None
    try:
        path = Path(text)
        if path.suffix.lower() == ".pdf" and path.exists():
            return path
    except (OSError, ValueError, RuntimeError):
        return None
    return None


def _resolve_pdf_viewer_source_path(
    viewer_pair: dict,
    side: str,
    viewer_root: Optional[Path],
) -> tuple[Optional[Path], str]:
    """Resolve the PDF file a lightweight viewport should render.

    Sharable exports intentionally redact ``source_a``/``source_b``. The
    workbench must therefore prefer real source paths only when available and
    fall back to package-local PDF copies for customer-shareable runs.
    """

    if side not in {"before", "after"}:
        raise ValueError(f"Unsupported PDF viewer side: {side}")

    source_key = "source_a" if side == "before" else "source_b"
    source_path = _existing_pdf_file(viewer_pair.get(source_key))
    if source_path is not None:
        return source_path, source_key

    package_keys = (
        ("before_page_pdf", "page_pdf") if side == "before" else ("after_page_pdf", "page_pdf")
    )
    for key in package_keys:
        package_path = _resolve_viewer_artifact_path(viewer_pair.get(key), viewer_root)
        if package_path is None:
            continue
        try:
            if package_path.suffix.lower() == ".pdf" and package_path.exists():
                return package_path, key
        except (OSError, ValueError, RuntimeError):
            continue

    return None, "missing"
