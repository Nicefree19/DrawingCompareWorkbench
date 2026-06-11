# -*- coding: utf-8 -*-
"""GUI-facing diagnostics for compare runtime failures."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Tuple

from src.services.comparison.dwg_autoconvert_settings import (
    ODA_DOWNLOAD_URL,
    detect_oda_installation,
    load_dwg_autoconvert_enabled,
)
from src.services.comparison.dwg_backend import (
    DWG_BACKEND_ODA_CONVERTER,
    normalize_dwg_backend_mode,
)
from src.services.comparison.dwg_dxf_fallback import resolve_dwg_dxf_fallback_pair

_UNSUPPORTED_DWG_MARKER = "DWG input version is unsupported by the native adapter"
_DWG_CODE_RE = re.compile(r"\bAC\d{4}\b")


def default_gui_dwg_backend_mode(
    env: Optional[Mapping[str, str]] = None,
    *,
    settings_path: Optional[Path] = None,
    detect: Optional[Callable[[], Tuple[bool, Optional[str]]]] = None,
) -> Optional[str]:
    """Effective DWG backend mode stamped into GUI compare requests.

    Live pilot failure (2026-06-11): the old env-var-only opt-in meant any
    launch that bypassed the packaged .bat silently lost AC1018+ DWG support
    even with ODA installed. Owner decision: installed ODA is used by
    default; env var and the settings-menu choice stay explicit overrides.

    Resolution order:
    1. ``DRAWING_COMPARE_DWG_BACKEND`` — explicit values win both ways
       (``oda_converter`` enables; any other valid mode such as ``native``
       or ``disabled`` keeps the native-only default).
    2. Persisted settings-menu choice (``dwg_autoconvert_settings.json``).
    3. Auto-detect: a locally installed ODA File Converter enables
       auto-conversion; otherwise native-only.
    """

    values = os.environ if env is None else env
    raw_mode = values.get("DRAWING_COMPARE_DWG_BACKEND")
    if raw_mode:
        try:
            normalized: Optional[str] = normalize_dwg_backend_mode(raw_mode)
        except ValueError:
            normalized = None  # unrecognised value falls through to settings/detect
        if normalized == DWG_BACKEND_ODA_CONVERTER:
            return DWG_BACKEND_ODA_CONVERTER
        if normalized is not None:
            return None
    saved = load_dwg_autoconvert_enabled(settings_path)
    if saved is True:
        return DWG_BACKEND_ODA_CONVERTER
    if saved is False:
        return None
    probe = detect if detect is not None else detect_oda_installation
    installed, _path = probe()
    return DWG_BACKEND_ODA_CONVERTER if installed else None


def format_auto_compare_error(exc: BaseException, request: Any) -> str:
    """Return a concise user-facing error, with special handling for DWG version failures."""

    message = str(exc)
    if _UNSUPPORTED_DWG_MARKER not in message:
        return message

    codes = _unsupported_dwg_codes(message)
    status = _dwg_status()
    supported = ", ".join(status.get("dwg_supported_versions") or []) or "unknown"
    output_dir = str(getattr(request, "output_dir", "") or "")
    fallback_line = _fallback_diagnostic_line(request)
    code_suffix = f" ({', '.join(codes)})" if codes else ""
    # Korean-first wording: pilot users are Korean engineers, and the live
    # 2026-06-11 failure showed the English wall of text hid the one thing
    # that mattered (what to do next). Each branch states the situation AND
    # the concrete next action.
    oda_installed, oda_path = detect_oda_installation()
    effective_mode = default_gui_dwg_backend_mode()
    lines = [
        f"이 DWG 버전은 내장 리더로 직접 읽을 수 없습니다{code_suffix}.",
        f"내장 DWG 직접 지원 범위: {supported}.",
    ]
    if oda_installed and effective_mode != DWG_BACKEND_ODA_CONVERTER:
        lines.append(
            f"ODA File Converter 설치됨: {oda_path} — 그러나 DWG 자동 변환이 꺼져 있습니다. "
            "설정 메뉴의 'DWG 자동 변환'을 켜거나, 환경변수 "
            "DRAWING_COMPARE_DWG_BACKEND=oda_converter 로 실행한 뒤 다시 비교하세요."
        )
    elif oda_installed:
        lines.append(
            f"ODA File Converter 설치됨: {oda_path} — 자동 변환이 실패했습니다. "
            "logs 폴더의 오류 로그를 확인하거나, CAD에서 DXF(R2018)로 변환해 다시 시도하세요."
        )
    else:
        lines.append(
            "ODA File Converter가 설치되어 있지 않습니다 — 설치하면 모든 DWG 버전이 "
            f"자동 변환됩니다 (무료): {ODA_DOWNLOAD_URL}"
        )
        lines.append("또는 CAD 프로그램에서 DXF(R2018)로 저장해 그 파일을 선택하세요.")
    lines.append(fallback_line)
    lines.append(
        "사전 변환한 DXF는 자동 인식됩니다: 같은 폴더의 같은 이름 .dxf, 또는 "
        "DWG 폴더 옆 dxf_registered/before·after 폴더."
    )
    if output_dir:
        lines.append(f"실행 결과 폴더: {output_dir}")
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
        return "변환된 DXF 폴백 확인을 완료하지 못했습니다."
    diagnostics = resolution.diagnostics if isinstance(resolution.diagnostics, dict) else {}
    candidates = diagnostics.get("fallback_candidates")
    if resolution.used:
        return (
            "변환된 DXF 폴백을 찾았습니다 — 같은 DXF 배치로 다시 실행하거나, "
            "해당 DXF 경로를 직접 선택하세요."
        )
    if isinstance(candidates, list) and not candidates:
        return "변환된 DXF 폴백 후보: 없음."
    if isinstance(candidates, list):
        return f"변환된 DXF 폴백 후보: {len(candidates)}개."
    return ""


__all__ = ["default_gui_dwg_backend_mode", "format_auto_compare_error"]
