# -*- coding: utf-8 -*-
"""Render/processing failure codes for silent fallback visibility.

S1.1 of the silent-fallback visibility roadmap. When the comparison or
viewer pipeline degrades to a fallback path (cached DXF, Canvas
skeleton, heuristic AI classification, etc.), call sites emit a
``RenderFailureCode`` so that:

- the GUI badge layer (``FailureBadge`` widget, S1.4) shows the user a
  honest yellow/red chip with the Korean message;
- logs include a stable code instead of free text;
- regression tests can assert which fallback paths were exercised.

This module is **pure Python** (no Qt, no ezdxf, no I/O) so it imports
in microseconds and is safe to use from worker subprocesses, tests, and
the GUI process alike.

Reference: ``docs/work-memory/S1_FAILURE_VISIBILITY_IMPLEMENTATION_PLAN.md``.
Pattern mirrors ``src/services/comparison/render_modes.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, FrozenSet, Literal, Tuple

#: 11 codes (1 OK + 10 fallback). String literal — JSON-serialisable as-is.
#: Added in S1.3.1: ``dwg_vector_normalise_failed`` to distinguish a
#: failed-then-cached path (warn) from a normal cache reuse (info).
RenderFailureCode = Literal[
    "ok",
    "dwg_unsupported_version",
    "vector_draw_partial",
    "vector_draw_failed",
    "backend_fallback_qquickwidget",
    "backend_fallback_canvas_skeleton",
    "ai_heuristic_fallback",
    "dwg_using_cached_dxf",
    "dwg_vector_normalise_failed",
    "zone_crop_stale",
    "zone_crop_cancelled",
]

ALL_FAILURE_CODES: Final[Tuple[RenderFailureCode, ...]] = (
    "ok",
    "dwg_unsupported_version",
    "vector_draw_partial",
    "vector_draw_failed",
    "backend_fallback_qquickwidget",
    "backend_fallback_canvas_skeleton",
    "ai_heuristic_fallback",
    "dwg_using_cached_dxf",
    "dwg_vector_normalise_failed",
    "zone_crop_stale",
    "zone_crop_cancelled",
)

Severity = Literal["info", "warn", "error"]

#: Three-tier severity — drives badge colour in ``FailureBadge`` (S1.4).
SEVERITIES: Final[Tuple[Severity, ...]] = ("info", "warn", "error")


@dataclass(frozen=True)
class FailureCodeInfo:
    """Diagnostic + user-facing presentation for one failure code.

    The GUI badge layer (S1.4) and the logging adapter both read these
    values, so Korean strings live here and the UI never invents its own
    copy.
    """

    code: RenderFailureCode
    severity: Severity
    message_ko: str
    suggested_action_ko: str
    requires_user_action: bool

    def to_payload(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message_ko": self.message_ko,
            "suggested_action_ko": self.suggested_action_ko,
            "requires_user_action": self.requires_user_action,
        }


#: Master code table. Adding/removing rows here is the single point of
#: change for the failure-code taxonomy.
FAILURE_CODE_INFO: Final[dict[RenderFailureCode, FailureCodeInfo]] = {
    "ok": FailureCodeInfo(
        code="ok",
        severity="info",
        message_ko="정상 — 모든 렌더링이 의도대로 동작 중입니다.",
        suggested_action_ko="",
        requires_user_action=False,
    ),
    "dwg_unsupported_version": FailureCodeInfo(
        code="dwg_unsupported_version",
        severity="warn",
        message_ko=(
            "⚠️ AC1015 외 DWG 버전 — 호환 캐시 DXF로 비교 중입니다. "
            "결과는 원본 DWG가 아닐 수 있습니다."
        ),
        suggested_action_ko="원본 DWG를 사용하려면 AC1015(R2000) DXF로 사전 변환하세요.",
        requires_user_action=True,
    ),
    "vector_draw_partial": FailureCodeInfo(
        code="vector_draw_partial",
        severity="warn",
        message_ko="⚠️ 일부 엔티티만 렌더링됨 — 표시되지 않은 엔티티가 있을 수 있습니다.",
        suggested_action_ko="로그에서 누락 엔티티 종류를 확인하세요.",
        requires_user_action=False,
    ),
    "vector_draw_failed": FailureCodeInfo(
        code="vector_draw_failed",
        severity="error",
        message_ko="🔴 벡터 렌더링 실패 — 스켈레톤/래스터 fallback이 표시됩니다.",
        suggested_action_ko="원본 도면에 손상되거나 비표준 엔티티가 있는지 확인하세요.",
        requires_user_action=True,
    ),
    "backend_fallback_qquickwidget": FailureCodeInfo(
        code="backend_fallback_qquickwidget",
        severity="warn",
        message_ko="⚠️ Qt Quick 위젯을 사용할 수 없어 호환 모드로 동작 중입니다.",
        suggested_action_ko="패키지 설치/그래픽 드라이버를 확인하세요.",
        requires_user_action=True,
    ),
    "backend_fallback_canvas_skeleton": FailureCodeInfo(
        code="backend_fallback_canvas_skeleton",
        severity="info",
        message_ko="ℹ️ QSGLineItem 모듈 없음 — 표준 Canvas 렌더링을 사용합니다 (정상 동작).",
        suggested_action_ko="",
        requires_user_action=False,
    ),
    "ai_heuristic_fallback": FailureCodeInfo(
        code="ai_heuristic_fallback",
        severity="info",
        message_ko="ℹ️ AI 임베딩 모델이 없어 휴리스틱 분류로 동작 중입니다.",
        suggested_action_ko=(
            "ai_models/ 디렉토리에 Qwen3-Embedding-0.6B GGUF 또는 "
            "onnx_mxbai_large를 배치하면 AI 분류가 활성화됩니다."
        ),
        requires_user_action=True,
    ),
    "dwg_using_cached_dxf": FailureCodeInfo(
        code="dwg_using_cached_dxf",
        severity="info",
        message_ko="ℹ️ 이전에 변환된 DXF 캐시를 재사용 중입니다.",
        suggested_action_ko="원본 DWG가 변경됐다면 캐시를 삭제하고 다시 시도하세요.",
        requires_user_action=False,
    ),
    "dwg_vector_normalise_failed": FailureCodeInfo(
        code="dwg_vector_normalise_failed",
        severity="warn",
        message_ko=(
            "⚠️ DWG 벡터 정규화 실패 — 이전에 변환된 DXF 캐시로 대체했습니다. "
            "원본 도면이 변경됐다면 결과가 실제와 다를 수 있습니다."
        ),
        suggested_action_ko=(
            "원본 DWG를 다시 사용하려면 캐시를 삭제하고 재시도하거나, "
            "CAD 도구에서 DXF로 다시 export하세요."
        ),
        requires_user_action=True,
    ),
    "zone_crop_stale": FailureCodeInfo(
        code="zone_crop_stale",
        severity="info",
        message_ko="ℹ️ 이전 선택의 zone crop 결과가 무시됐습니다 (새 선택이 우선).",
        suggested_action_ko="",
        requires_user_action=False,
    ),
    "zone_crop_cancelled": FailureCodeInfo(
        code="zone_crop_cancelled",
        severity="info",
        message_ko="ℹ️ Zone crop 작업이 취소됐습니다.",
        suggested_action_ko="다시 선택하여 재시도하세요.",
        requires_user_action=False,
    ),
}


#: Severity-bucketed code sets — drives badge colour in FailureBadge (S1.4).
INFO_CODES: Final[FrozenSet[RenderFailureCode]] = frozenset(
    c for c, info in FAILURE_CODE_INFO.items() if info.severity == "info"
)
WARN_CODES: Final[FrozenSet[RenderFailureCode]] = frozenset(
    c for c, info in FAILURE_CODE_INFO.items() if info.severity == "warn"
)
ERROR_CODES: Final[FrozenSet[RenderFailureCode]] = frozenset(
    c for c, info in FAILURE_CODE_INFO.items() if info.severity == "error"
)

#: Codes the FailureBadge should hide entirely. Only "ok" qualifies.
HIDDEN_CODES: Final[FrozenSet[RenderFailureCode]] = frozenset({"ok"})

#: Codes that signal "the user should do something about this".
USER_ACTION_REQUIRED_CODES: Final[FrozenSet[RenderFailureCode]] = frozenset(
    c for c, info in FAILURE_CODE_INFO.items() if info.requires_user_action
)


def is_valid_code(value: object) -> bool:
    """Return True if ``value`` is one of the 10 enum strings."""

    return isinstance(value, str) and value in ALL_FAILURE_CODES


def info_for(code: RenderFailureCode) -> FailureCodeInfo:
    """Look up the diagnostic info for a code. Defensive fallback to 'ok'.

    Returning 'ok' for an unknown code keeps the UI from crashing on a
    stale enum value (e.g. forward-compat with a future release).
    """

    return FAILURE_CODE_INFO.get(code, FAILURE_CODE_INFO["ok"])


def describe(code: RenderFailureCode) -> str:
    """One-line Korean message suitable for badge tooltip / status bar."""

    return info_for(code).message_ko


def severity_of(code: RenderFailureCode) -> Severity:
    """Return the severity tier of a code (info/warn/error)."""

    return info_for(code).severity


def to_payload(code: RenderFailureCode) -> dict[str, object]:
    """JSON-serialisable payload for log/UI emission."""

    return info_for(code).to_payload()


def highest_severity(*codes: RenderFailureCode) -> Severity:
    """Return the highest severity among the given codes.

    Used by FailureBadge (S1.4) when multiple fallbacks are active at
    once — the badge picks the worst severity for its colour. Unknown
    codes are ignored. With no arguments returns "info".
    """

    rank = {"info": 0, "warn": 1, "error": 2}
    best: Severity = "info"
    best_rank = -1
    for code in codes:
        if not is_valid_code(code):
            continue
        sev = severity_of(code)
        if rank[sev] > best_rank:
            best_rank = rank[sev]
            best = sev
    return best


# ---------------------------------------------------------------------------
# S1.3.3 — DwgFailureCode bridge
# ---------------------------------------------------------------------------


def from_dwg_failure_code(dwg_code: str) -> RenderFailureCode:
    """Map a ``DwgFailureCode`` string to a ``RenderFailureCode``.

    The DWG importer (``src/services/comparison/dwg_importer.py``)
    defines its own taxonomy of failure codes
    (``DwgFailureCode.UNSUPPORTED_VERSION`` etc.) — they remain
    untouched so existing CAD pipeline behaviour is preserved. This
    helper bridges those codes into the RenderFailureCode taxonomy
    used by the GUI badge (S1.4).

    Unmapped codes default to ``"vector_draw_failed"`` so the badge
    surfaces a generic error rather than masking it. Adding a finer
    mapping later (e.g. ``DWG_IMPORT_TIMEOUT`` → a dedicated timeout
    code) only requires extending the table here.

    Args:
        dwg_code: A ``DwgFailureCode`` value (string), e.g.
            ``"DWG_UNSUPPORTED_VERSION"``. Type is plain ``str`` so we
            avoid importing the heavy ``dwg_importer`` module from this
            pure-Python failure-code module.

    Returns:
        The closest matching RenderFailureCode. Falls back to
        ``"vector_draw_failed"`` for any code not in the table.
    """

    mapping: dict[str, RenderFailureCode] = {
        "DWG_UNSUPPORTED_VERSION": "dwg_unsupported_version",
    }
    return mapping.get(dwg_code, "vector_draw_failed")


__all__ = [
    "RenderFailureCode",
    "ALL_FAILURE_CODES",
    "Severity",
    "SEVERITIES",
    "FailureCodeInfo",
    "FAILURE_CODE_INFO",
    "INFO_CODES",
    "WARN_CODES",
    "ERROR_CODES",
    "HIDDEN_CODES",
    "USER_ACTION_REQUIRED_CODES",
    "is_valid_code",
    "info_for",
    "describe",
    "severity_of",
    "to_payload",
    "highest_severity",
    "from_dwg_failure_code",
]
