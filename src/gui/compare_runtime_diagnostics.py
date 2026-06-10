# -*- coding: utf-8 -*-
"""GUI-facing diagnostics for compare runtime failures."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping, Optional

from src.services.comparison.dwg_backend import (
    DWG_BACKEND_ODA_CONVERTER,
    normalize_dwg_backend_mode,
)
from src.services.comparison.dwg_dxf_fallback import resolve_dwg_dxf_fallback_pair

_UNSUPPORTED_DWG_MARKER = "DWG input version is unsupported by the native adapter"
_DWG_CODE_RE = re.compile(r"\bAC\d{4}\b")


def default_gui_dwg_backend_mode(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Propagate an explicit DWG backend env selection into GUI run requests."""

    values = os.environ if env is None else env
    raw_mode = values.get("DRAWING_COMPARE_DWG_BACKEND")
    if not raw_mode:
        return None
    try:
        normalized = normalize_dwg_backend_mode(raw_mode)
    except ValueError:
        return None
    if normalized == DWG_BACKEND_ODA_CONVERTER:
        return DWG_BACKEND_ODA_CONVERTER
    return None


def format_auto_compare_error(exc: BaseException, request: Any) -> str:
    """Return a concise user-facing error, with special handling for DWG version failures."""

    message = str(exc)
    if _UNSUPPORTED_DWG_MARKER not in message:
        return message

    codes = _unsupported_dwg_codes(message)
    status = _dwg_status()
    supported = ", ".join(status.get("dwg_supported_versions") or []) or "unknown"
    oda_available = bool(status.get("oda_converter"))
    output_dir = str(getattr(request, "output_dir", "") or "")
    fallback_line = _fallback_diagnostic_line(request)
    code_suffix = f" ({', '.join(codes)})" if codes else ""
    # Live-failure finding (2026-06-10 22:37 run): ODA was installed on the
    # machine, yet an AC1032 pair with no sibling DXF was rejected — the
    # pipeline's auto-convert is gated behind an EXPLICIT backend opt-in
    # (CAD_FORMAT_SUPPORT_POLICY forbids automatic ODA invocation), and the
    # error never told the user that the opt-in exists. Name it here so the
    # failure is self-explanatory; enabling stays a deliberate user/policy
    # decision.
    oda_optin_line = ""
    if oda_available and default_gui_dwg_backend_mode() is None:
        oda_optin_line = (
            "ODA is installed but automatic conversion requires an explicit "
            "opt-in: set DRAWING_COMPARE_DWG_BACKEND=oda_converter and restart "
            "the app (ODA-free default per CAD_FORMAT_SUPPORT_POLICY — confirm "
            "your organization's policy before enabling)."
        )
    lines = [
        f"Compare cannot read the selected DWG version with the native adapter{code_suffix}.",
        f"Native DWG support in this environment is limited to: {supported}.",
        f"ODA File Converter available: {'yes' if oda_available else 'no'}.",
        oda_optin_line,
        fallback_line,
        (
            "Use converted DXF inputs, or place converted files under "
            "dxf_registered/before and dxf_registered/after next to the DWG folder."
        ),
        "For single-file compare, same-folder .dxf files with matching stems are auto-detected.",
    ]
    if output_dir:
        lines.append(f"Run output: {output_dir}")
    return "\n".join(line for line in lines if line)


def _dwg_status() -> dict[str, Any]:
    try:
        from src.services.comparison.dwg_differ import DwgDiffer

        status = DwgDiffer.get_status()
        return status if isinstance(status, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _unsupported_dwg_codes(message: str) -> list[str]:
    marker = "unsupported by the native adapter:"
    if marker in message:
        segment = message.split(marker, 1)[1].split(".", 1)[0]
        codes = sorted(set(_DWG_CODE_RE.findall(segment)))
        if codes:
            return codes
    return sorted(set(_DWG_CODE_RE.findall(message)))


def _fallback_diagnostic_line(request: Any) -> str:
    try:
        source_a = Path(str(getattr(request, "source_a")))
        source_b = Path(str(getattr(request, "source_b")))
    except Exception:  # noqa: BLE001
        return ""
    try:
        resolution = resolve_dwg_dxf_fallback_pair(source_a, source_b)
    except Exception:  # noqa: BLE001
        return "Converted DXF fallback check could not be completed."
    diagnostics = resolution.diagnostics if isinstance(resolution.diagnostics, dict) else {}
    candidates = diagnostics.get("fallback_candidates")
    if resolution.used:
        return (
            "Converted DXF fallback was found; rerun with the same nearby DXF layout "
            "or select the effective DXF paths directly."
        )
    if isinstance(candidates, list) and not candidates:
        return "Converted DXF fallback candidates: none."
    if isinstance(candidates, list):
        return f"Converted DXF fallback candidates: {len(candidates)}."
    return ""


__all__ = ["default_gui_dwg_backend_mode", "format_auto_compare_error"]
