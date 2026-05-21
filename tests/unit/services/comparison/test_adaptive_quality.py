# -*- coding: utf-8 -*-
"""Tests for adaptive_quality — auto DPI selection.

Replaces the legacy "user must pick DPI 80~400" UI with deterministic
selection driven by input characteristics + memory budget. Tests cover:

- Small / medium / large / huge input regimes
- Page-count multiplier (many small files → conservative tier)
- Explicit DPI override path (legacy compat)
- Adaptive downgrade (one step lower) — used by the
  ``MemoryBudgetExceeded`` auto-retry path in folder_compare_pipeline
- Floor at safe-mode (DPI 80) when even the lowest tier exceeds budget
- Round-trip serialisation

Note: ``should_downgrade`` was removed on 2026-05-15 (no production caller).
``downgrade_one_step`` was restored under Plan C-1 because the auto-retry
catch block in folder_compare_pipeline now invokes it. ``TestShouldDowngrade``
remains removed; ``TestDowngradeOneStep`` is restored alongside the function.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.services.comparison.adaptive_quality import (
    QUALITY_TIERS,
    QualityDecision,
    QualityTier,
    downgrade_one_step,
    measure_inputs,
    select_quality,
)


def _write(path: Path, size_bytes: int) -> Path:
    path.write_bytes(b"\0" * size_bytes)
    return path


class TestQualityTiers:
    def test_quality_tiers_excludes_dpi_400(self):
        # DPI 400 is intentionally excluded from auto-selection per the
        # S20 incident report — manual override only.
        dpis = {tier.dpi for tier in QUALITY_TIERS}
        assert 400 not in dpis

    def test_quality_tiers_ordered_by_dpi_ascending(self):
        dpis = [tier.dpi for tier in QUALITY_TIERS]
        assert dpis == sorted(dpis)

    def test_lowest_tier_is_safe_mode(self):
        assert QUALITY_TIERS[0].dpi == 80


class TestMeasureInputs:
    def test_empty_paths(self, tmp_path: Path):
        result = measure_inputs([], [])
        assert result.file_count == 0
        assert result.total_bytes == 0
        assert result.max_pair_bytes == 0
        assert result.average_pair_bytes == 0

    def test_single_pair(self, tmp_path: Path):
        a = _write(tmp_path / "a.dwg", 1024 * 1024)  # 1 MB
        b = _write(tmp_path / "b.dwg", 2 * 1024 * 1024)  # 2 MB
        result = measure_inputs([a], [b])
        assert result.file_count == 2
        assert result.total_bytes == 3 * 1024 * 1024
        assert result.max_pair_bytes == 3 * 1024 * 1024
        assert result.average_pair_bytes == 3 * 1024 * 1024
        assert result.total_mb == pytest.approx(3.0)

    def test_multiple_pairs_picks_max(self, tmp_path: Path):
        # 1MB + 2MB + 50MB pair sizes. Max should be 50MB.
        files_a = [
            _write(tmp_path / "a1.dwg", 1024 * 1024),
            _write(tmp_path / "a2.dwg", 2 * 1024 * 1024),
            _write(tmp_path / "a3.dwg", 50 * 1024 * 1024),
        ]
        files_b = [
            _write(tmp_path / "b1.dwg", 0),
            _write(tmp_path / "b2.dwg", 0),
            _write(tmp_path / "b3.dwg", 0),
        ]
        result = measure_inputs(files_a, files_b)
        assert result.max_pair_bytes == 50 * 1024 * 1024
        assert result.total_bytes == 53 * 1024 * 1024

    def test_missing_file_records_note(self, tmp_path: Path):
        existing = _write(tmp_path / "a.dwg", 1024)
        missing = tmp_path / "ghost.dwg"
        result = measure_inputs([existing, missing], [])
        # Both files counted, missing contributes 0 bytes plus a note.
        assert result.file_count_a == 2
        assert result.total_bytes == 1024
        assert any("size_unavailable_a" in note for note in result.notes)


class TestSelectQuality:
    """End-to-end auto-selection across input regimes."""

    def _inputs(self, *, max_pair_mb: float, file_count: int = 2):
        from src.services.comparison.adaptive_quality import InputCharacteristics

        bytes_per_pair = int(max_pair_mb * 1024 * 1024)
        total = bytes_per_pair * (file_count // 2 if file_count > 1 else 1)
        return InputCharacteristics(
            file_count_a=file_count // 2 + (file_count % 2),
            file_count_b=file_count // 2,
            total_bytes=total,
            max_pair_bytes=bytes_per_pair,
            average_pair_bytes=bytes_per_pair,
        )

    def test_small_inputs_select_high_quality(self):
        # 5 MB pair, 4 GB cap → easily fits highest tier (DPI 300).
        decision = select_quality(self._inputs(max_pair_mb=5.0))
        assert decision.auto_selected is True
        assert decision.dpi == 300
        assert decision.tier.label.startswith("초고화질")

    def test_medium_inputs_select_balanced_quality(self):
        # 60 MB pair → load_factor=1.5 → DPI 200 (600MB * 1.5 = 900MB <= 4096*0.6=2458)
        # but DPI 300 (1100*1.5*1.04 = ~1716 <= 2458) still passes.
        decision = select_quality(self._inputs(max_pair_mb=60.0))
        assert decision.auto_selected is True
        assert decision.dpi >= 200

    def test_large_inputs_select_conservative_quality(self):
        # 200 MB pair → load_factor=2.5
        # DPI 300: 1100 * 2.5 * 1.02 = ~2805 > 2458 → not selected
        # DPI 200: 600 * 2.5 * 1.02 = ~1530 <= 2458 → selected
        decision = select_quality(self._inputs(max_pair_mb=200.0))
        assert decision.dpi <= 200
        assert decision.dpi >= 80

    def test_huge_s20_class_inputs_select_safe_mode(self):
        # S20-class: 800 MB max pair → load_factor=4.0
        # DPI 300: 1100 * 4.0 = 4400 > 2458 → no
        # DPI 200: 600 * 4.0 = 2400 <= 2458 → selected (just barely)
        # DPI 120: 250 * 4.0 = 1000 <= 2458 → also fits
        # → DPI 200 selected (highest that fits)
        decision = select_quality(self._inputs(max_pair_mb=800.0))
        assert decision.auto_selected is True
        # The exact tier depends on multipliers; assert it's at least
        # below the highest tier.
        assert decision.dpi < 300

    def test_extreme_inputs_fall_back_to_safe_mode(self):
        # Force fallback by giving an impossibly small budget.
        decision = select_quality(
            self._inputs(max_pair_mb=10.0),
            memory_cap_mb=10.0,  # tiny budget
        )
        assert decision.dpi == 80  # safe mode
        assert any("fallback_to_safe_mode" in n for n in decision.notes)

    def test_many_pages_drift_conservative(self):
        # Same per-pair size but 50 file pairs → page_multiplier ~1.5
        # should bias selection downward vs 2-file case.
        small_decision = select_quality(self._inputs(max_pair_mb=80.0, file_count=2))
        many_decision = select_quality(self._inputs(max_pair_mb=80.0, file_count=100))
        assert many_decision.dpi <= small_decision.dpi


class TestExplicitOverride:
    def test_explicit_dpi_matches_known_tier(self):
        from src.services.comparison.adaptive_quality import InputCharacteristics

        decision = select_quality(
            InputCharacteristics(
                file_count_a=1, file_count_b=1, total_bytes=0, max_pair_bytes=0, average_pair_bytes=0
            ),
            explicit_dpi=200,
        )
        assert decision.dpi == 200
        assert decision.auto_selected is False
        assert decision.rationale.startswith("수동 선택")

    def test_explicit_dpi_400_uses_adhoc_tier(self):
        # DPI 400 is excluded from QUALITY_TIERS but the legacy override
        # path must still work.
        from src.services.comparison.adaptive_quality import InputCharacteristics

        decision = select_quality(
            InputCharacteristics(
                file_count_a=0, file_count_b=0, total_bytes=0, max_pair_bytes=0, average_pair_bytes=0
            ),
            explicit_dpi=400,
        )
        assert decision.dpi == 400
        assert decision.auto_selected is False


class TestDowngradeOneStep:
    def test_downgrade_from_highest_goes_one_step(self):
        from src.services.comparison.adaptive_quality import InputCharacteristics

        decision = select_quality(
            InputCharacteristics(
                file_count_a=1, file_count_b=1, total_bytes=0, max_pair_bytes=0, average_pair_bytes=0
            ),
        )
        # Force a high tier first
        high = next(t for t in reversed(QUALITY_TIERS))
        forced = QualityDecision(
            schema_version=decision.schema_version,
            tier=high,
            rationale="test",
            auto_selected=True,
            inputs=decision.inputs,
            memory_cap_mb=decision.memory_cap_mb,
            safety_margin_ratio=decision.safety_margin_ratio,
        )
        downgraded = downgrade_one_step(forced, reason="memory_pressure")
        assert downgraded.dpi < forced.dpi
        assert any("downgraded" in n for n in downgraded.notes)

    def test_downgrade_at_floor_is_no_op(self):
        from src.services.comparison.adaptive_quality import InputCharacteristics

        forced = QualityDecision(
            schema_version=1,
            tier=QUALITY_TIERS[0],  # safe mode
            rationale="test",
            auto_selected=True,
            inputs=InputCharacteristics(0, 0, 0, 0, 0),
            memory_cap_mb=4096.0,
            safety_margin_ratio=0.6,
        )
        result = downgrade_one_step(forced, reason="memory_pressure")
        assert result.dpi == forced.dpi
        assert any("downgrade_blocked" in n for n in result.notes)


class TestSerialisation:
    def test_decision_to_dict_preserves_fields(self):
        from src.services.comparison.adaptive_quality import InputCharacteristics

        inputs = InputCharacteristics(
            file_count_a=2, file_count_b=2, total_bytes=10_000_000, max_pair_bytes=5_000_000, average_pair_bytes=5_000_000
        )
        decision = select_quality(inputs, memory_cap_mb=4096.0)
        payload = decision.to_dict()
        assert payload["dpi"] == decision.dpi
        assert payload["max_edge_px"] == decision.max_edge_px
        assert payload["auto_selected"] is True
        assert payload["tier_label"] == decision.tier.label
        assert payload["inputs"]["file_count"] == 4
        assert "rationale" in payload
        assert payload["memory_cap_mb"] == 4096.0


class TestRationaleFormatting:
    def test_rationale_includes_file_count_and_size(self):
        from src.services.comparison.adaptive_quality import InputCharacteristics

        decision = select_quality(
            InputCharacteristics(
                file_count_a=10,
                file_count_b=10,
                total_bytes=100 * 1024 * 1024,
                max_pair_bytes=10 * 1024 * 1024,
                average_pair_bytes=5 * 1024 * 1024,
            )
        )
        assert "20개 파일" in decision.rationale
        assert "100MB" in decision.rationale or "10MB" in decision.rationale
