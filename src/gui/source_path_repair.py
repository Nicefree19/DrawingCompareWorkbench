from __future__ import annotations

from pathlib import Path
from typing import Any


def has_lossy_path_text(value: Any) -> bool:
    text = str(value or "")
    lossy_sentinels = (
        "\u951f\u65a4",  # common UTF-8/Windows codepage mojibake marker
        "\u5360\uc3d9\uc619",  # mojibake for replacement characters
        "\u5360\uc3d9",
    )
    return (
        any(marker in text for marker in lossy_sentinels)
        or any(ch == "\ufffd" or 0xD800 <= ord(ch) <= 0xDFFF for ch in text)
    )


def registered_dxf_fallback_for_source(value: Any, side: str) -> Path | None:
    """Return the registered DXF fallback next to a DWG input, when present."""

    text = str(value or "").strip()
    if not text:
        return None
    try:
        source = Path(text)
    except (OSError, ValueError, RuntimeError):
        return None
    if source.suffix.lower() != ".dwg":
        return None
    side_dir = "before" if side == "before" else "after"
    candidate = source.parent / "dxf_registered" / side_dir / f"{source.stem}.dxf"
    try:
        return candidate if candidate.is_file() else None
    except (OSError, ValueError, RuntimeError):
        return None
