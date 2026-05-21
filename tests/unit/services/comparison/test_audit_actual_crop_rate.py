# -*- coding: utf-8 -*-
"""Tests for the actual_crop_rate audit gate (recommendation #2).

기존 acceptance 9b 가 ``synchronized_relative_fallback`` 만으로도 pass
가능했던 약점을 보강. 본 테스트는:

- ``--require-actual-crop-rate-pdf`` 강제
- ``--require-actual-crop-rate-cad`` 강제
- ``--require-actual-crop-rate-overall`` 강제
- selected_zone_evidence renders inline / disk 폴백 둘 다 확인
- 게이트 미설정 시 회귀 영향 0
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts import audit_drawing_compare_mvp_exit as audit


def _summary_with_renders(
    *,
    completed_pairs: int = 5,
    renders: list[dict[str, Any]] | None = None,
    output_dir: str = "/tmp/test_run",
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "output_dir": output_dir,
        "comparison": {"completed_pairs": completed_pairs, "failed_pairs": 0},
    }
    if renders is not None:
        summary["selected_zone_evidence"] = {"renders": renders}
    return summary


def _actual_crop_pdf_render() -> dict[str, Any]:
    return {
        "visual_fidelity": "pdf_render",
        "render_lifecycle": "ready",
        "renderer_backend": "pdf-image-crop",
    }


def _actual_crop_cad_render() -> dict[str, Any]:
    return {
        "visual_fidelity": "cad_render",
        "render_lifecycle": "ready",
        "renderer_backend": "ezdxf-matplotlib-zone",
    }


def _skipped_pdf_render() -> dict[str, Any]:
    return {
        "visual_fidelity": "relative_overlay",
        "render_lifecycle": "skipped_missing_page_bbox",
        "renderer_backend": "pdf-page-bbox-required",
    }


def _loaded_for(summary: dict[str, Any], path: Path) -> dict[str, Any]:
    return {"path": path, "summary": summary, "summary_path": path / "validation_summary.json"}


class TestPdfActualCropRate:
    """--require-actual-crop-rate-pdf 강제."""

    def test_passes_when_all_pdf_actual_crop(self, tmp_path: Path):
        renders = [_actual_crop_pdf_render() for _ in range(5)]
        summary = _summary_with_renders(renders=renders)
        result = audit._check_actual_crop_rate(
            [summary],
            [_loaded_for(summary, tmp_path)],
            require_actual_crop_rate_pdf=0.85,
            require_actual_crop_rate_cad=None,
            require_actual_crop_rate_overall=None,
        )
        assert result.passed is True
        assert result.name == "selected_zone_actual_crop_rate"

    def test_fails_when_pdf_rate_below_threshold(self, tmp_path: Path):
        # 2 actual + 3 skipped → 0.4 < 0.85
        renders = [_actual_crop_pdf_render()] * 2 + [_skipped_pdf_render()] * 3
        summary = _summary_with_renders(renders=renders)
        result = audit._check_actual_crop_rate(
            [summary],
            [_loaded_for(summary, tmp_path)],
            require_actual_crop_rate_pdf=0.85,
            require_actual_crop_rate_cad=None,
            require_actual_crop_rate_overall=None,
        )
        assert result.passed is False
        assert "pdf actual_crop_rate=0.4000" in result.detail

    def test_fails_when_no_pdf_renders_present(self, tmp_path: Path):
        # All CAD - PDF unmeasurable.
        renders = [_actual_crop_cad_render() for _ in range(3)]
        summary = _summary_with_renders(renders=renders)
        result = audit._check_actual_crop_rate(
            [summary],
            [_loaded_for(summary, tmp_path)],
            require_actual_crop_rate_pdf=0.85,
            require_actual_crop_rate_cad=None,
            require_actual_crop_rate_overall=None,
        )
        assert result.passed is False
        assert "unmeasurable" in result.detail


class TestCadActualCropRate:
    """--require-actual-crop-rate-cad 강제 (CAD 는 보통 100%)."""

    def test_passes_when_all_cad_actual_crop(self, tmp_path: Path):
        renders = [_actual_crop_cad_render() for _ in range(4)]
        summary = _summary_with_renders(renders=renders)
        result = audit._check_actual_crop_rate(
            [summary],
            [_loaded_for(summary, tmp_path)],
            require_actual_crop_rate_pdf=None,
            require_actual_crop_rate_cad=0.95,
            require_actual_crop_rate_overall=None,
        )
        assert result.passed is True

    def test_fails_when_cad_partial_skipped(self, tmp_path: Path):
        # 2 actual + 1 background-missing → 2/3 ≈ 0.667 < 0.95
        renders = [
            _actual_crop_cad_render(),
            _actual_crop_cad_render(),
            {
                "visual_fidelity": "cad_render",
                "render_lifecycle": "failed",
                "renderer_backend": "ezdxf-matplotlib-zone",
            },
        ]
        summary = _summary_with_renders(renders=renders)
        result = audit._check_actual_crop_rate(
            [summary],
            [_loaded_for(summary, tmp_path)],
            require_actual_crop_rate_pdf=None,
            require_actual_crop_rate_cad=0.95,
            require_actual_crop_rate_overall=None,
        )
        assert result.passed is False
        assert "cad actual_crop_rate=0.6667" in result.detail


class TestOverallActualCropRate:
    """--require-actual-crop-rate-overall 강제 (혼합 source)."""

    def test_passes_when_overall_above_threshold(self, tmp_path: Path):
        renders = (
            [_actual_crop_cad_render()] * 5
            + [_actual_crop_pdf_render()] * 4
            + [_skipped_pdf_render()] * 1
        )  # 9/10 = 0.9
        summary = _summary_with_renders(renders=renders)
        result = audit._check_actual_crop_rate(
            [summary],
            [_loaded_for(summary, tmp_path)],
            require_actual_crop_rate_pdf=None,
            require_actual_crop_rate_cad=None,
            require_actual_crop_rate_overall=0.85,
        )
        assert result.passed is True

    def test_fails_when_overall_below_threshold(self, tmp_path: Path):
        renders = [_skipped_pdf_render()] * 5 + [_actual_crop_pdf_render()] * 1
        summary = _summary_with_renders(renders=renders)
        result = audit._check_actual_crop_rate(
            [summary],
            [_loaded_for(summary, tmp_path)],
            require_actual_crop_rate_pdf=None,
            require_actual_crop_rate_cad=None,
            require_actual_crop_rate_overall=0.85,
        )
        assert result.passed is False
        assert "overall actual_crop_rate=0.1667" in result.detail


class TestDiskFallback:
    """summary 에 inline 데이터가 없을 때 selected_zone_evidence.json 로드."""

    def test_loads_renders_from_disk_when_inline_missing(self, tmp_path: Path):
        evidence_path = tmp_path / "viewer" / "selected_zone_evidence.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps({"renders": [_actual_crop_pdf_render()] * 3}),
            encoding="utf-8",
        )
        summary = _summary_with_renders(renders=None)  # no inline data
        result = audit._check_actual_crop_rate(
            [summary],
            [_loaded_for(summary, tmp_path)],
            require_actual_crop_rate_pdf=0.85,
            require_actual_crop_rate_cad=None,
            require_actual_crop_rate_overall=None,
        )
        assert result.passed is True

    def test_fails_when_disk_and_inline_both_missing(self, tmp_path: Path):
        summary = _summary_with_renders(renders=None)
        result = audit._check_actual_crop_rate(
            [summary],
            [_loaded_for(summary, tmp_path)],
            require_actual_crop_rate_pdf=0.85,
            require_actual_crop_rate_cad=None,
            require_actual_crop_rate_overall=None,
        )
        assert result.passed is False
        assert "renders missing" in result.detail


class TestRunAuditIntegration:
    """run_audit 의 신규 인자가 작동하는지 확인."""

    def test_run_audit_accepts_new_kwargs(self):
        report = audit.run_audit(
            result_dirs=[],
            require_actual_crop_rate_pdf=0.85,
            require_actual_crop_rate_cad=0.95,
            require_actual_crop_rate_overall=0.90,
        )
        check_names = {check["name"] for check in report["checks"]}
        assert "selected_zone_actual_crop_rate" in check_names
