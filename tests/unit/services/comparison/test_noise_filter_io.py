# -*- coding: utf-8 -*-
"""Phase O — unit tests for ``noise_filter_io``.

Mirrors the structure of ``test_ai_classifier_config_io`` (Phase L4).
Covers atomic write / load / corruption recovery / validation paths.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.comparison.noise_filter_io import (
    CONFIG_SCHEMA_VERSION,
    NoiseFilterSettings,
    load_noise_filter_settings,
    save_noise_filter_settings,
)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_default_preserves_legacy_behaviour():
    s = NoiseFilterSettings.default()
    assert s.global_alignment_enabled is True
    assert s.hungarian_max_subset == 200
    assert s.cosmetic_detection_enabled is True
    assert s.suppress_cosmetic_only is False  # legacy: don't hide cosmetic
    assert s.cosmetic_attributes == ("color", "lineweight", "linetype")
    assert s.min_changes_per_zone == 1  # legacy: every change becomes a zone
    assert s.single_entity_noise_score_threshold == pytest.approx(0.7)
    assert s.noise_filter_strength == "medium"


def test_recommended_preset_flips_suppress_and_min_changes():
    s = NoiseFilterSettings.recommended()
    # 권장 프리셋의 핵심 동작 — 사용자 피드백 직접 매핑
    assert s.suppress_cosmetic_only is True
    assert s.min_changes_per_zone == 2
    # 나머지는 default 와 동일
    base = NoiseFilterSettings.default()
    assert s.global_alignment_enabled == base.global_alignment_enabled
    assert s.hungarian_max_subset == base.hungarian_max_subset
    assert s.cosmetic_detection_enabled == base.cosmetic_detection_enabled
    assert s.cosmetic_attributes == base.cosmetic_attributes
    assert s.noise_filter_strength == base.noise_filter_strength


# ---------------------------------------------------------------------------
# Save / load round-trip
# ---------------------------------------------------------------------------


def test_save_and_load_round_trip(tmp_path: Path):
    target = tmp_path / "noise_filter_config.json"
    s = NoiseFilterSettings(
        global_alignment_enabled=False,
        hungarian_max_subset=400,
        cosmetic_detection_enabled=True,
        suppress_cosmetic_only=True,
        cosmetic_attributes=("color",),
        min_changes_per_zone=3,
        single_entity_noise_score_threshold=0.55,
        noise_filter_strength="high",
    )
    written = save_noise_filter_settings(s, path=target)
    assert written == target
    assert target.exists()

    loaded = load_noise_filter_settings(path=target)
    assert loaded.global_alignment_enabled is False
    assert loaded.hungarian_max_subset == 400
    assert loaded.suppress_cosmetic_only is True
    assert loaded.cosmetic_attributes == ("color",)
    assert loaded.min_changes_per_zone == 3
    assert loaded.single_entity_noise_score_threshold == pytest.approx(0.55)
    assert loaded.noise_filter_strength == "high"


def test_save_writes_schema_version(tmp_path: Path):
    target = tmp_path / "config.json"
    save_noise_filter_settings(NoiseFilterSettings.default(), path=target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["schema_version"] == CONFIG_SCHEMA_VERSION
    assert "computed_at_utc" in payload


def test_save_atomicity_no_tmp_left_behind(tmp_path: Path):
    target = tmp_path / "config.json"
    save_noise_filter_settings(NoiseFilterSettings.default(), path=target)
    siblings = [p.name for p in tmp_path.iterdir()]
    assert "config.json" in siblings
    # 어떤 .tmp 파일도 남기지 않아야 함
    for name in siblings:
        assert not name.endswith(".tmp"), f"leftover tmp file: {name}"


# ---------------------------------------------------------------------------
# Missing / corrupt / invalid file behaviour
# ---------------------------------------------------------------------------


def test_load_missing_file_returns_default(tmp_path: Path):
    target = tmp_path / "absent.json"
    s = load_noise_filter_settings(path=target)
    assert s == NoiseFilterSettings.default()


def test_load_corrupt_json_moves_to_bak_and_returns_default(tmp_path: Path):
    target = tmp_path / "corrupt.json"
    target.write_text("{not really json", encoding="utf-8")
    s = load_noise_filter_settings(path=target)
    assert s == NoiseFilterSettings.default()
    # Corrupt file 을 .bak 으로 이동시켰는지 확인
    assert (tmp_path / "corrupt.json.bak").exists()
    assert not target.exists()


def test_load_wrong_schema_version_returns_default(tmp_path: Path):
    target = tmp_path / "old.json"
    payload = NoiseFilterSettings.default().to_dict()
    payload["schema_version"] = "noise_filter.v999"
    target.write_text(json.dumps(payload), encoding="utf-8")
    s = load_noise_filter_settings(path=target)
    assert s == NoiseFilterSettings.default()


def test_load_invalid_strength_returns_default(tmp_path: Path):
    target = tmp_path / "bad_strength.json"
    payload = NoiseFilterSettings.default().to_dict()
    payload["schema_version"] = CONFIG_SCHEMA_VERSION
    payload["noise_filter_strength"] = "ultra-high"  # not in {low/medium/high}
    target.write_text(json.dumps(payload), encoding="utf-8")
    s = load_noise_filter_settings(path=target)
    assert s == NoiseFilterSettings.default()


def test_load_invalid_min_changes_returns_default(tmp_path: Path):
    target = tmp_path / "bad_min.json"
    payload = NoiseFilterSettings.default().to_dict()
    payload["schema_version"] = CONFIG_SCHEMA_VERSION
    payload["min_changes_per_zone"] = 99  # > 10
    target.write_text(json.dumps(payload), encoding="utf-8")
    s = load_noise_filter_settings(path=target)
    assert s == NoiseFilterSettings.default()


def test_load_unknown_cosmetic_attribute_returns_default(tmp_path: Path):
    target = tmp_path / "bad_attrs.json"
    payload = NoiseFilterSettings.default().to_dict()
    payload["schema_version"] = CONFIG_SCHEMA_VERSION
    payload["cosmetic_attributes"] = ["color", "transparency"]  # transparency unknown
    target.write_text(json.dumps(payload), encoding="utf-8")
    s = load_noise_filter_settings(path=target)
    assert s == NoiseFilterSettings.default()


def test_load_root_not_dict_returns_default(tmp_path: Path):
    target = tmp_path / "list.json"
    target.write_text("[1, 2, 3]", encoding="utf-8")
    s = load_noise_filter_settings(path=target)
    assert s == NoiseFilterSettings.default()


# ---------------------------------------------------------------------------
# Forward compat — extra unknown fields are ignored
# ---------------------------------------------------------------------------


def test_load_ignores_unknown_fields(tmp_path: Path):
    target = tmp_path / "future.json"
    payload = NoiseFilterSettings.default().to_dict()
    payload["schema_version"] = CONFIG_SCHEMA_VERSION
    payload["future_phase_p_setting"] = "ignored"
    target.write_text(json.dumps(payload), encoding="utf-8")
    s = load_noise_filter_settings(path=target)
    # Default 와 동일 (unknown 필드는 단순 무시)
    assert s == NoiseFilterSettings.default()


# ---------------------------------------------------------------------------
# to_dict serialisation
# ---------------------------------------------------------------------------


def test_to_dict_uses_list_for_cosmetic_attributes():
    s = NoiseFilterSettings.default()
    d = s.to_dict()
    assert isinstance(d["cosmetic_attributes"], list)
    assert d["cosmetic_attributes"] == ["color", "lineweight", "linetype"]


# ---------------------------------------------------------------------------
# Codex review RV-20260507-003 #3 — validator must NEVER raise
# Even adversarial / malformed JSON must degrade quietly to default().
# ---------------------------------------------------------------------------


def _malformed(target: Path, **overrides) -> None:
    payload = NoiseFilterSettings.default().to_dict()
    payload["schema_version"] = CONFIG_SCHEMA_VERSION
    payload.update(overrides)
    target.write_text(json.dumps(payload), encoding="utf-8")


def test_load_unhashable_strength_returns_default(tmp_path: Path):
    target = tmp_path / "bad.json"
    _malformed(target, noise_filter_strength=[])  # list, not str
    assert load_noise_filter_settings(path=target) == NoiseFilterSettings.default()


def test_load_dict_strength_returns_default(tmp_path: Path):
    target = tmp_path / "bad.json"
    _malformed(target, noise_filter_strength={"key": "value"})
    assert load_noise_filter_settings(path=target) == NoiseFilterSettings.default()


def test_load_nested_cosmetic_attribute_returns_default(tmp_path: Path):
    target = tmp_path / "bad.json"
    _malformed(target, cosmetic_attributes=[["nested"]])
    assert load_noise_filter_settings(path=target) == NoiseFilterSettings.default()


def test_load_dict_in_cosmetic_attributes_returns_default(tmp_path: Path):
    target = tmp_path / "bad.json"
    _malformed(target, cosmetic_attributes=[{"k": "v"}])
    assert load_noise_filter_settings(path=target) == NoiseFilterSettings.default()


def test_load_bool_min_changes_returns_default(tmp_path: Path):
    """``True`` is technically int(1) in Python but it's a JSON boolean
    that the user almost certainly didn't mean as ``1`` zone-promote
    threshold. Reject explicitly."""
    target = tmp_path / "bad.json"
    _malformed(target, min_changes_per_zone=True)
    assert load_noise_filter_settings(path=target) == NoiseFilterSettings.default()


def test_load_string_threshold_returns_default(tmp_path: Path):
    target = tmp_path / "bad.json"
    _malformed(target, single_entity_noise_score_threshold="0.7")
    assert load_noise_filter_settings(path=target) == NoiseFilterSettings.default()


def test_load_bool_hungarian_max_returns_default(tmp_path: Path):
    target = tmp_path / "bad.json"
    _malformed(target, hungarian_max_subset=True)
    assert load_noise_filter_settings(path=target) == NoiseFilterSettings.default()


# ---------------------------------------------------------------------------
# RV-20260508-001 #4 — bool field type guards (3 fields)
# ---------------------------------------------------------------------------


def test_load_string_for_global_alignment_returns_default(tmp_path: Path):
    target = tmp_path / "bad.json"
    _malformed(target, global_alignment_enabled="yes")  # str, not bool
    assert load_noise_filter_settings(path=target) == NoiseFilterSettings.default()


def test_load_int_for_cosmetic_detection_returns_default(tmp_path: Path):
    target = tmp_path / "bad.json"
    _malformed(target, cosmetic_detection_enabled=1)  # int, not bool
    assert load_noise_filter_settings(path=target) == NoiseFilterSettings.default()


def test_load_string_for_suppress_cosmetic_returns_default(tmp_path: Path):
    target = tmp_path / "bad.json"
    _malformed(target, suppress_cosmetic_only="false")  # str, not bool
    assert load_noise_filter_settings(path=target) == NoiseFilterSettings.default()


# ---------------------------------------------------------------------------
# RV-20260508-001 #5 — empty cosmetic_attributes
# ---------------------------------------------------------------------------


def test_load_empty_cosmetic_attributes_returns_default(tmp_path: Path):
    """Empty list creates a silent no-op (detection on, but nothing to
    detect). Reject explicitly so hand-edited JSON gets a clear signal."""
    target = tmp_path / "empty.json"
    _malformed(target, cosmetic_attributes=[])
    assert load_noise_filter_settings(path=target) == NoiseFilterSettings.default()


# ---------------------------------------------------------------------------
# RV-20260508-001 #6 — JSON bomb cap on cosmetic_attributes length
# ---------------------------------------------------------------------------


def test_load_oversized_cosmetic_attributes_returns_default(tmp_path: Path):
    """A 10K-element list materialises a 10K-tuple in memory after
    tuple() coercion. Cap at 32."""
    target = tmp_path / "bomb.json"
    _malformed(target, cosmetic_attributes=["color"] * 10000)
    assert load_noise_filter_settings(path=target) == NoiseFilterSettings.default()


def test_load_attributes_at_cap_passes(tmp_path: Path):
    """Length exactly at the cap passes (with valid entries)."""
    target = tmp_path / "ok.json"
    # Use only valid attribute names (any duplicates allowed for size)
    _malformed(target, cosmetic_attributes=["color"] * 32)
    s = load_noise_filter_settings(path=target)
    # Loaded successfully — tuple of 32 'color' entries
    assert len(s.cosmetic_attributes) == 32
    assert all(a == "color" for a in s.cosmetic_attributes)


def test_load_attributes_above_cap_returns_default(tmp_path: Path):
    """Length 33 (cap+1) is rejected."""
    target = tmp_path / "over.json"
    _malformed(target, cosmetic_attributes=["color"] * 33)
    assert load_noise_filter_settings(path=target) == NoiseFilterSettings.default()


# ---------------------------------------------------------------------------
# RV-20260508-001 #9 — default_noise_filter_config_path returns absolute
# ---------------------------------------------------------------------------


def test_default_path_is_absolute(monkeypatch, tmp_path: Path):
    """Even when LOCALAPPDATA is set to a relative path, the resolved
    config path must be absolute (Path.replace requires it)."""
    from src.services.comparison.noise_filter_io import (
        default_noise_filter_config_path,
    )
    # Set LOCALAPPDATA to a relative-looking path
    monkeypatch.setenv("LOCALAPPDATA", "..\\test_local")
    p = default_noise_filter_config_path()
    assert p.is_absolute(), f"path should be absolute, got: {p}"


# ---------------------------------------------------------------------------
# RV-20260508-001 #11 — schema_version() function removed
# ---------------------------------------------------------------------------


def test_schema_version_function_removed():
    """``schema_version()`` was a thin wrapper around
    ``CONFIG_SCHEMA_VERSION`` with no callers — removed in
    RV-20260508-001 #11. The constant is the single source of truth."""
    import src.services.comparison.noise_filter_io as nf_io
    assert not hasattr(nf_io, "schema_version") or not callable(
        getattr(nf_io, "schema_version", None)
    ), "schema_version() function should be removed"
    # The constant remains
    assert hasattr(nf_io, "CONFIG_SCHEMA_VERSION")
    assert nf_io.CONFIG_SCHEMA_VERSION == "noise_filter.v1"
