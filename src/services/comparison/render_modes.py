# -*- coding: utf-8 -*-
"""Render mode enum + transition table for the diff-steered viewer engine.

Phase G evolution of Phase F's 4-state ``BackgroundFidelity``:

    Phase F (v2):                     Phase G (v3):
    ─────────────                     ─────────────
    exact_world_render        →      raster_refined
    exact_world_tile_sparse   →      raster_refined  (or skeleton_preview if tiles only)
    simplified_world_preview  →      skeleton_preview
    relative_only             →      relative_only

    + new states (no v2 equivalent):
      vector_focus      ← per-zone vector micro-pack rendered
      render_pending    ← background worker active
      render_timeout    ← worker exceeded budget, last good frame retained
      render_failed     ← worker errored, fallback to skeleton

The 7-state model is taken verbatim from the "초경량 신형 도면뷰어 엔진" research
report § "상태 모델". Each state has a fixed colour + Korean label that the
QML badge layer renders. Crucially, ``relative_only`` is **never** displayed
as a real background — the watermark introduced in Phase F is preserved.

This module is **pure Python** (no Qt, no ezdxf, no I/O) so it imports in
microseconds and is safe to use from worker subprocesses, tests, and the GUI
process alike.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, FrozenSet, Literal, Optional, Tuple

#: 7-state render mode enum (string literal — JSON-serialisable as-is).
RenderMode = Literal[
    "relative_only",
    "skeleton_preview",
    "vector_focus",
    "raster_refined",
    "render_pending",
    "render_timeout",
    "render_failed",
]

ALL_RENDER_MODES: Final[Tuple[RenderMode, ...]] = (
    "relative_only",
    "skeleton_preview",
    "vector_focus",
    "raster_refined",
    "render_pending",
    "render_timeout",
    "render_failed",
)

#: Modes that represent an authoritative visual surface (i.e. measurement
#: tools may be enabled). The other modes either lack a real background
#: (``relative_only``) or are transient (``render_pending``/``render_timeout``
#: /``render_failed``) and should keep the previous frame visible.
AUTHORITATIVE_MODES: Final[FrozenSet[RenderMode]] = frozenset({
    "skeleton_preview",
    "vector_focus",
    "raster_refined",
})

#: Modes considered "terminal" — the worker is no longer running and the GUI
#: should not show a spinner. ``render_pending`` is the only transient one.
TERMINAL_MODES: Final[FrozenSet[RenderMode]] = frozenset({
    "relative_only",
    "skeleton_preview",
    "vector_focus",
    "raster_refined",
    "render_timeout",
    "render_failed",
})


@dataclass(frozen=True)
class RenderModeStyle:
    """Visual + textual presentation for one render mode.

    The QML badge layer reads these values via the viewer_session bridge
    instead of hardcoding colours/labels in QML — keeps Korean strings + brand
    colours in one place and makes the seven states swap-replaceable.
    """

    label_ko: str
    badge_color: str          # CSS hex, used by the QML badge background
    badge_text_color: str     # readable contrast for the label
    show_watermark: bool      # only relative_only sets this True
    enable_measurement: bool  # measurement tools active in this mode
    is_transient: bool        # render_pending only
    description_ko: str

    def to_dict(self) -> dict[str, object]:
        return {
            "label_ko": self.label_ko,
            "badge_color": self.badge_color,
            "badge_text_color": self.badge_text_color,
            "show_watermark": self.show_watermark,
            "enable_measurement": self.enable_measurement,
            "is_transient": self.is_transient,
            "description_ko": self.description_ko,
        }


#: Master style table. Adding/removing rows here is the single point of change
#: for the 7-state visual identity.
RENDER_MODE_STYLES: Final[dict[RenderMode, RenderModeStyle]] = {
    "relative_only": RenderModeStyle(
        label_ko="🟠 상대 위치 모드",
        badge_color="#F97316",       # orange
        badge_text_color="#FFFFFF",
        show_watermark=True,
        enable_measurement=False,
        is_transient=False,
        description_ko="실배경 없음 — 변경구역 위치만 추정 표시. 측정 비활성.",
    ),
    "skeleton_preview": RenderModeStyle(
        label_ko="⚪ 간략 벡터",
        badge_color="#6B7280",       # gray
        badge_text_color="#FFFFFF",
        show_watermark=False,
        enable_measurement=True,
        is_transient=False,
        description_ko="LOD 0/1 스켈레톤 — 빠른 위치 파악용 개요.",
    ),
    "vector_focus": RenderModeStyle(
        label_ko="🟢 벡터 포커스",
        badge_color="#1F9D55",       # green
        badge_text_color="#FFFFFF",
        show_watermark=False,
        enable_measurement=True,
        is_transient=False,
        description_ko="선택 변경구역의 정밀 벡터 micro-pack — 무한 줌 가능.",
    ),
    "raster_refined": RenderModeStyle(
        label_ko="🔵 실제 렌더",
        badge_color="#0969DA",       # blue
        badge_text_color="#FFFFFF",
        show_watermark=False,
        enable_measurement=True,
        is_transient=False,
        description_ko="실배경 라스터 + 벡터 오버레이 — 가장 높은 정밀도.",
    ),
    "render_pending": RenderModeStyle(
        label_ko="⏳ 렌더 중",
        badge_color="#9CA3AF",       # neutral gray with spinner
        badge_text_color="#FFFFFF",
        show_watermark=False,
        enable_measurement=False,
        is_transient=True,
        description_ko="백그라운드 워커가 실행 중 — 이전 프레임 유지.",
    ),
    "render_timeout": RenderModeStyle(
        label_ko="🟡 시간 초과",
        badge_color="#F59E0B",       # amber
        badge_text_color="#111827",
        show_watermark=False,
        enable_measurement=False,
        is_transient=False,
        description_ko="렌더 시간 초과 — 마지막 정상 프레임 유지. 다시 시도 가능.",
    ),
    "render_failed": RenderModeStyle(
        label_ko="🔴 렌더 실패",
        badge_color="#DC2626",       # red
        badge_text_color="#FFFFFF",
        show_watermark=False,
        enable_measurement=False,
        is_transient=False,
        description_ko="렌더 오류 — 스켈레톤 fallback 표시. 로그를 확인하세요.",
    ),
}


# ---------------------------------------------------------------------------
# State transition table
# ---------------------------------------------------------------------------


# Each tuple lists the "expected next states" from a given current state.
# This is informational (used by the test harness + diagnostics) — not
# enforced at runtime since the viewer_session can move freely between
# states based on cache hits, cancellations, or user actions.
ALLOWED_TRANSITIONS: Final[dict[RenderMode, FrozenSet[RenderMode]]] = {
    "relative_only": frozenset({
        "skeleton_preview", "render_pending", "vector_focus", "raster_refined",
    }),
    "skeleton_preview": frozenset({
        "vector_focus", "raster_refined", "render_pending",
        "render_timeout", "render_failed", "relative_only",
    }),
    "vector_focus": frozenset({
        "raster_refined", "render_pending", "skeleton_preview",
        "render_timeout", "render_failed",
    }),
    "raster_refined": frozenset({
        "vector_focus", "skeleton_preview", "render_pending",
        "render_timeout", "render_failed",
    }),
    "render_pending": frozenset({
        # the worker can settle into any terminal state
        "skeleton_preview", "vector_focus", "raster_refined",
        "render_timeout", "render_failed", "relative_only",
    }),
    "render_timeout": frozenset({
        # user can retry, or move on to another zone
        "render_pending", "skeleton_preview", "vector_focus",
        "raster_refined", "relative_only",
    }),
    "render_failed": frozenset({
        "render_pending", "skeleton_preview", "relative_only",
    }),
}


def is_valid_mode(value: object) -> bool:
    """Return True if ``value`` is one of the 7 enum strings."""

    return isinstance(value, str) and value in ALL_RENDER_MODES


def style_for(mode: RenderMode) -> RenderModeStyle:
    """Look up the visual style for a render mode. Falls back gracefully.

    The fallback uses ``relative_only`` so an unrecognised mode shows the
    orange "상대 위치" badge + watermark — i.e. the safest possible UX.
    """

    return RENDER_MODE_STYLES.get(mode, RENDER_MODE_STYLES["relative_only"])


def transition(from_mode: RenderMode, to_mode: RenderMode) -> RenderMode:
    """Validate a state transition. Returns ``to_mode`` if allowed, else
    leaves the GUI in ``from_mode`` (defensive — never raises).

    Logged transitions are written by the caller (viewer_session) so this
    helper stays pure.
    """

    if not is_valid_mode(to_mode):
        return from_mode
    if not is_valid_mode(from_mode):
        # First transition from "no state" — accept anything valid.
        return to_mode  # type: ignore[return-value]
    allowed = ALLOWED_TRANSITIONS.get(from_mode, frozenset())
    if to_mode == from_mode or to_mode in allowed:
        return to_mode  # type: ignore[return-value]
    # Disallowed transition — stay put. Logged by the caller for
    # diagnostics; we don't raise so the UI never crashes on a stale state.
    return from_mode


def describe(mode: RenderMode) -> str:
    """One-line Korean description suitable for tooltip / status bar."""

    return style_for(mode).description_ko


def best_authoritative(*candidates: Optional[RenderMode]) -> RenderMode:
    """Pick the highest-fidelity authoritative mode from a list of options.

    Used by viewer_session when multiple render results race: prefer
    ``raster_refined`` over ``vector_focus`` over ``skeleton_preview``.
    """

    priority = {"raster_refined": 3, "vector_focus": 2, "skeleton_preview": 1}
    best: RenderMode = "relative_only"
    best_score = 0
    for cand in candidates:
        if cand and cand in priority and priority[cand] > best_score:
            best = cand
            best_score = priority[cand]
    return best


__all__ = [
    "RenderMode",
    "ALL_RENDER_MODES",
    "AUTHORITATIVE_MODES",
    "TERMINAL_MODES",
    "RenderModeStyle",
    "RENDER_MODE_STYLES",
    "ALLOWED_TRANSITIONS",
    "is_valid_mode",
    "style_for",
    "transition",
    "describe",
    "best_authoritative",
]
