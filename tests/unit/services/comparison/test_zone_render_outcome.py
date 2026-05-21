# -*- coding: utf-8 -*-
"""Unit tests for zone_render_outcome classifier (recommendation #2).

Selected-zone 동기화 게이트가 actual crop 없이 overlay fallback 만으로
pass 가능한 약점을 해소하기 위해 도입된 derived outcome 분류기 검증.
"""

from __future__ import annotations

from src.services.comparison.zone_render_outcome import (
    RenderOutcome,
    ZoneOutcomeStats,
    aggregate_zone_outcomes,
    classify_render_result,
)


class TestClassifyRenderResult:
    """visual_fidelity + render_lifecycle 조합별 분류."""

    def test_cad_render_ready_is_actual_crop(self):
        outcome = classify_render_result(
            visual_fidelity="cad_render", render_lifecycle="ready"
        )
        assert outcome is RenderOutcome.ACTUAL_CROP

    def test_pdf_render_ready_is_actual_crop(self):
        outcome = classify_render_result(
            visual_fidelity="pdf_render", render_lifecycle="ready"
        )
        assert outcome is RenderOutcome.ACTUAL_CROP

    def test_relative_overlay_with_skipped_bbox_is_skipped(self):
        outcome = classify_render_result(
            visual_fidelity="relative_overlay",
            render_lifecycle="skipped_missing_page_bbox",
        )
        assert outcome is RenderOutcome.SKIPPED_MISSING_PAGE_BBOX

    def test_relative_overlay_with_ready_is_relative_overlay(self):
        outcome = classify_render_result(
            visual_fidelity="relative_overlay", render_lifecycle="ready"
        )
        assert outcome is RenderOutcome.RELATIVE_OVERLAY

    def test_unknown_lifecycle_is_skipped_missing_background(self):
        outcome = classify_render_result(
            visual_fidelity="cad_render", render_lifecycle="failed"
        )
        assert outcome is RenderOutcome.SKIPPED_MISSING_BACKGROUND

    def test_missing_lifecycle_is_relative_overlay(self):
        # Empty/None lifecycle 는 ready 가 아니므로 actual_crop 으로 분류 안 됨.
        outcome = classify_render_result(
            visual_fidelity="cad_render", render_lifecycle=None
        )
        # cad_render but lifecycle != "ready" → falls through to relative_overlay.
        assert outcome is RenderOutcome.RELATIVE_OVERLAY

    def test_case_insensitive(self):
        outcome = classify_render_result(
            visual_fidelity="CAD_RENDER", render_lifecycle="READY"
        )
        assert outcome is RenderOutcome.ACTUAL_CROP

    def test_empty_strings_default_to_relative_overlay(self):
        outcome = classify_render_result(
            visual_fidelity="", render_lifecycle=""
        )
        assert outcome is RenderOutcome.RELATIVE_OVERLAY


class TestAggregateZoneOutcomes:
    """RenderResult.to_dict() 컬렉션 집계."""

    def test_empty_payloads_yield_zero_stats(self):
        stats = aggregate_zone_outcomes([])
        assert stats.total == 0
        assert stats.actual_crop_available_rate is None
        assert stats.cad_actual_crop_rate is None
        assert stats.pdf_actual_crop_rate is None

    def test_single_actual_crop_pdf(self):
        payloads = [
            {
                "visual_fidelity": "pdf_render",
                "render_lifecycle": "ready",
                "renderer_backend": "pdf-image-crop",
            }
        ]
        stats = aggregate_zone_outcomes(payloads)
        assert stats.total == 1
        assert stats.actual_crop == 1
        assert stats.pdf_total == 1
        assert stats.pdf_actual_crop == 1
        assert stats.actual_crop_available_rate == 1.0
        assert stats.pdf_actual_crop_rate == 1.0
        assert stats.cad_total == 0

    def test_mixed_payloads_compute_separate_rates(self):
        payloads = [
            # CAD: 2 actual_crop, 0 fallback
            {
                "visual_fidelity": "cad_render",
                "render_lifecycle": "ready",
                "renderer_backend": "ezdxf-matplotlib-zone",
            },
            {
                "visual_fidelity": "cad_render",
                "render_lifecycle": "ready",
                "renderer_backend": "ezdxf-matplotlib-zone",
            },
            # PDF: 1 actual_crop, 1 skipped_missing_page_bbox
            {
                "visual_fidelity": "pdf_render",
                "render_lifecycle": "ready",
                "renderer_backend": "pdf-image-crop",
            },
            {
                "visual_fidelity": "relative_overlay",
                "render_lifecycle": "skipped_missing_page_bbox",
                "renderer_backend": "pdf-page-bbox-required",
            },
        ]
        stats = aggregate_zone_outcomes(payloads)
        assert stats.total == 4
        assert stats.actual_crop == 3
        assert stats.skipped_missing_page_bbox == 1
        assert stats.cad_total == 2
        assert stats.cad_actual_crop == 2
        assert stats.cad_actual_crop_rate == 1.0
        assert stats.pdf_total == 2
        assert stats.pdf_actual_crop == 1
        assert stats.pdf_actual_crop_rate == 0.5
        assert stats.actual_crop_available_rate == 0.75

    def test_explicit_source_format_takes_precedence(self):
        payloads = [
            # explicit source_format=cad overrides confused backend
            {
                "visual_fidelity": "cad_render",
                "render_lifecycle": "ready",
                "renderer_backend": "ambiguous",
                "source_format": "cad",
            }
        ]
        stats = aggregate_zone_outcomes(payloads)
        assert stats.cad_total == 1
        assert stats.pdf_total == 0

    def test_unknown_source_excluded_from_split_buckets(self):
        # actual_crop counted in total but neither CAD nor PDF.
        payloads = [
            {
                "visual_fidelity": "cad_render",
                "render_lifecycle": "ready",
                # no source_format, no renderer_backend
            }
        ]
        stats = aggregate_zone_outcomes(payloads)
        assert stats.total == 1
        assert stats.actual_crop == 1
        assert stats.cad_total == 0
        assert stats.pdf_total == 0

    def test_non_dict_payloads_skipped_with_note(self):
        payloads = [
            "garbage",
            None,
            {
                "visual_fidelity": "cad_render",
                "render_lifecycle": "ready",
                "renderer_backend": "ezdxf-matplotlib-zone",
            },
        ]
        stats = aggregate_zone_outcomes(payloads)
        assert stats.total == 1
        assert stats.cad_total == 1
        assert any("non_dict_payload_skipped" in n for n in stats.notes)


class TestZoneOutcomeStatsToDict:
    """JSON round-trip 안정성."""

    def test_to_dict_includes_all_keys(self):
        stats = ZoneOutcomeStats(
            total=10,
            actual_crop=8,
            relative_overlay=1,
            skipped_missing_page_bbox=1,
            skipped_missing_background=0,
            cad_actual_crop=5,
            cad_total=5,
            pdf_actual_crop=3,
            pdf_total=5,
            notes=("info",),
        )
        payload = stats.to_dict()
        expected_keys = {
            "total",
            "actual_crop",
            "relative_overlay",
            "skipped_missing_page_bbox",
            "skipped_missing_background",
            "cad_actual_crop",
            "cad_total",
            "pdf_actual_crop",
            "pdf_total",
            "actual_crop_available_rate",
            "cad_actual_crop_rate",
            "pdf_actual_crop_rate",
            "notes",
        }
        assert set(payload.keys()) == expected_keys
        assert payload["actual_crop_available_rate"] == 0.8
        assert payload["pdf_actual_crop_rate"] == 0.6
        assert payload["cad_actual_crop_rate"] == 1.0

    def test_to_dict_preserves_none_for_zero_total(self):
        stats = ZoneOutcomeStats()
        payload = stats.to_dict()
        assert payload["actual_crop_available_rate"] is None
        assert payload["cad_actual_crop_rate"] is None
        assert payload["pdf_actual_crop_rate"] is None
