# -*- coding: utf-8 -*-
"""Selected-zone render outcome classification.

외부 감사 리뷰 권고 ② 대응 모듈. 기존 ``zone_render_service`` 의
``RenderResult`` schema 를 변경하지 않고, ``visual_fidelity`` 와
``render_lifecycle`` 두 필드의 조합에서 audit-친화적 outcome 을 도출.

기존 schema 와의 관계
=====================
- ``visual_fidelity``: ``cad_render`` / ``pdf_render`` / ``relative_overlay``
- ``render_lifecycle``: ``ready`` / ``skipped_missing_page_bbox``

이 두 값을 조합해 4종 outcome 으로 분류:

- ``actual_crop``: background + bbox 모두 OK, before/after 같은 page-space
  로 crop 됨 (PDF) 또는 CAD world-window 로 정확히 렌더됨
- ``relative_overlay``: bbox 는 있으나 background 가 없어 상대 overlay
  로만 표시 (PDF 폴백)
- ``skipped_missing_page_bbox``: page-space bbox 자체가 부재해 crop
  스킵된 PDF
- ``skipped_missing_background``: background 이미지 부재 (드물지만 가능)

분류 규칙
=========
- ``visual_fidelity == "cad_render" and render_lifecycle == "ready"``
  → ``actual_crop``
- ``visual_fidelity == "pdf_render" and render_lifecycle == "ready"``
  → ``actual_crop``
- ``visual_fidelity == "relative_overlay"`` and lifecycle ==
  ``"skipped_missing_page_bbox"`` → ``skipped_missing_page_bbox``
- 기타 lifecycle != "ready" → ``skipped_missing_background``
- 기타 → ``relative_overlay``

audit gate 산출
===============
- ``actual_crop_available_rate`` = actual_crop / total
- 별도 source format 별 분리: PDF / CAD

회귀 영향: derived helper only — RenderResult schema 미변경.

Author: TEKLA_MCP Team
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Optional


class RenderOutcome(str, Enum):
    """zone render 결과 분류 (audit-친화적)."""

    ACTUAL_CROP = "actual_crop"
    RELATIVE_OVERLAY = "relative_overlay"
    SKIPPED_MISSING_PAGE_BBOX = "skipped_missing_page_bbox"
    SKIPPED_MISSING_BACKGROUND = "skipped_missing_background"


@dataclass(frozen=True)
class ZoneOutcomeStats:
    """RenderResult 컬렉션의 outcome 분포 통계.

    Attributes:
        total: 전체 zone 수
        actual_crop: ACTUAL_CROP 수
        relative_overlay: RELATIVE_OVERLAY 수
        skipped_missing_page_bbox: SKIPPED_MISSING_PAGE_BBOX 수
        skipped_missing_background: SKIPPED_MISSING_BACKGROUND 수
        cad_actual_crop / cad_total: CAD source 한정 분리
        pdf_actual_crop / pdf_total: PDF source 한정 분리
        notes: 분류 시 보조 정보
    """

    total: int = 0
    actual_crop: int = 0
    relative_overlay: int = 0
    skipped_missing_page_bbox: int = 0
    skipped_missing_background: int = 0
    cad_actual_crop: int = 0
    cad_total: int = 0
    pdf_actual_crop: int = 0
    pdf_total: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def actual_crop_available_rate(self) -> Optional[float]:
        """전체 zone 중 실제 crop 가용 비율. total=0 → None."""
        if self.total <= 0:
            return None
        return self.actual_crop / self.total

    @property
    def cad_actual_crop_rate(self) -> Optional[float]:
        if self.cad_total <= 0:
            return None
        return self.cad_actual_crop / self.cad_total

    @property
    def pdf_actual_crop_rate(self) -> Optional[float]:
        if self.pdf_total <= 0:
            return None
        return self.pdf_actual_crop / self.pdf_total

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "actual_crop": self.actual_crop,
            "relative_overlay": self.relative_overlay,
            "skipped_missing_page_bbox": self.skipped_missing_page_bbox,
            "skipped_missing_background": self.skipped_missing_background,
            "cad_actual_crop": self.cad_actual_crop,
            "cad_total": self.cad_total,
            "pdf_actual_crop": self.pdf_actual_crop,
            "pdf_total": self.pdf_total,
            "actual_crop_available_rate": (
                round(self.actual_crop_available_rate, 4)
                if self.actual_crop_available_rate is not None
                else None
            ),
            "cad_actual_crop_rate": (
                round(self.cad_actual_crop_rate, 4)
                if self.cad_actual_crop_rate is not None
                else None
            ),
            "pdf_actual_crop_rate": (
                round(self.pdf_actual_crop_rate, 4)
                if self.pdf_actual_crop_rate is not None
                else None
            ),
            "notes": list(self.notes),
        }


def classify_render_result(
    *,
    visual_fidelity: Optional[str],
    render_lifecycle: Optional[str],
) -> RenderOutcome:
    """Derive a single ``RenderOutcome`` from RenderResult flags.

    분류 규칙은 모듈 docstring 참고. 알 수 없는 조합은 보수적으로
    ``RELATIVE_OVERLAY`` 로 분류해 audit 가 통과 처리하지 않도록 한다.
    """
    fidelity = (visual_fidelity or "").strip().lower()
    lifecycle = (render_lifecycle or "").strip().lower()

    if lifecycle == "skipped_missing_page_bbox":
        return RenderOutcome.SKIPPED_MISSING_PAGE_BBOX
    if lifecycle and lifecycle != "ready":
        # Other "skipped_*" / "failed" lifecycle states fold into the
        # background-missing bucket so audit can still flag them.
        return RenderOutcome.SKIPPED_MISSING_BACKGROUND
    if fidelity in {"cad_render", "pdf_render"} and lifecycle == "ready":
        return RenderOutcome.ACTUAL_CROP
    if fidelity == "relative_overlay":
        return RenderOutcome.RELATIVE_OVERLAY
    return RenderOutcome.RELATIVE_OVERLAY


def aggregate_zone_outcomes(
    payloads: Iterable[dict[str, Any]],
) -> ZoneOutcomeStats:
    """RenderResult.to_dict() 컬렉션 → outcome 분포 통계.

    각 payload 는 다음 키를 가진다고 가정 (없으면 안전하게 무시):
    - ``visual_fidelity``: str
    - ``render_lifecycle``: str
    - ``source_format`` 또는 ``renderer_backend``: str (CAD vs PDF 구분)

    ``renderer_backend`` 가 ``"pdf-"`` 로 시작하면 PDF source 로 간주.
    그렇지 않으면 CAD source. 값이 둘 다 없으면 source-aware 카운터에서
    제외하지만 total 카운터에는 포함.
    """
    total = 0
    actual_crop = 0
    relative_overlay = 0
    skipped_bbox = 0
    skipped_background = 0
    cad_total = 0
    cad_actual = 0
    pdf_total = 0
    pdf_actual = 0
    notes: list[str] = []

    for payload in payloads:
        if not isinstance(payload, dict):
            notes.append(f"non_dict_payload_skipped:{type(payload).__name__}")
            continue
        outcome = classify_render_result(
            visual_fidelity=payload.get("visual_fidelity"),
            render_lifecycle=payload.get("render_lifecycle"),
        )
        total += 1
        if outcome == RenderOutcome.ACTUAL_CROP:
            actual_crop += 1
        elif outcome == RenderOutcome.RELATIVE_OVERLAY:
            relative_overlay += 1
        elif outcome == RenderOutcome.SKIPPED_MISSING_PAGE_BBOX:
            skipped_bbox += 1
        elif outcome == RenderOutcome.SKIPPED_MISSING_BACKGROUND:
            skipped_background += 1

        source = _classify_source(payload)
        if source == "pdf":
            pdf_total += 1
            if outcome == RenderOutcome.ACTUAL_CROP:
                pdf_actual += 1
        elif source == "cad":
            cad_total += 1
            if outcome == RenderOutcome.ACTUAL_CROP:
                cad_actual += 1

    return ZoneOutcomeStats(
        total=total,
        actual_crop=actual_crop,
        relative_overlay=relative_overlay,
        skipped_missing_page_bbox=skipped_bbox,
        skipped_missing_background=skipped_background,
        cad_actual_crop=cad_actual,
        cad_total=cad_total,
        pdf_actual_crop=pdf_actual,
        pdf_total=pdf_total,
        notes=tuple(notes),
    )


def _classify_source(payload: dict[str, Any]) -> Optional[str]:
    """Return ``"pdf"`` / ``"cad"`` / ``None`` for source classification.

    Priority: explicit ``source_format`` > ``renderer_backend`` prefix > None.
    """
    explicit = str(payload.get("source_format") or "").strip().lower()
    if explicit in {"pdf", "cad"}:
        return explicit
    backend = str(payload.get("renderer_backend") or "").strip().lower()
    if backend.startswith("pdf-"):
        return "pdf"
    if backend.startswith("ezdxf-") or backend.startswith("dxf-") or "matplotlib-zone" in backend:
        return "cad"
    return None


__all__ = [
    "RenderOutcome",
    "ZoneOutcomeStats",
    "aggregate_zone_outcomes",
    "classify_render_result",
]
