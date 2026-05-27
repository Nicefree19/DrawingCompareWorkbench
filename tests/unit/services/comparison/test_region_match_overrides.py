from __future__ import annotations

import json
from pathlib import Path

from src.services.comparison.detail_region_matcher import match_sheet_regions
from src.services.comparison.region_match_overrides import (
    RegionMatchOverride,
    load_region_match_overrides,
    write_region_match_overrides,
)
from src.services.comparison.sheet_region_detector import SheetRegion


def _region(region_id: str, *, drawing_number: str = "D-101") -> SheetRegion:
    return SheetRegion(
        region_id=region_id,
        source_path=f"{region_id}.dxf",
        source_format="dxf",
        bbox=(0.0, 0.0, 3000.0, 1800.0),
        width=3000.0,
        height=1800.0,
        area=5_400_000.0,
        entity_count=20,
        entity_histogram={"LINE": 18, "TEXT": 2},
        layer_histogram={"BEAM": 18, "TITLE": 2},
        title_text="PIER CAP DETAIL",
        drawing_number=drawing_number,
        detection_method="cad_frame",
    )


def test_region_match_override_round_trips_json(tmp_path: Path) -> None:
    path = tmp_path / "region_match_overrides.json"
    overrides = (
        RegionMatchOverride(
            before_region_id="before-r1",
            after_region_id="after-r7",
            status="manual_match",
            reason="reviewed by user",
        ),
    )

    written = write_region_match_overrides(overrides, path, pair_id="pair-a")
    loaded = load_region_match_overrides(written, pair_id="pair-a")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert loaded == overrides


def test_region_match_override_preserves_legacy_positional_arguments() -> None:
    override = RegionMatchOverride("before-r1", "after-r1", "manual_match", "reviewed")

    assert override.before_region_id == "before-r1"
    assert override.after_region_id == "after-r1"
    assert override.status == "manual_match"
    assert override.reason == "reviewed"
    assert override.pair_id == ""


def test_region_match_override_filters_per_item_pair_id(tmp_path: Path) -> None:
    path = tmp_path / "manual_region_matches.json"
    write_region_match_overrides(
        (
            RegionMatchOverride(
                before_region_id="before-a",
                after_region_id="after-a",
                pair_id="pair-a",
            ),
            RegionMatchOverride(
                before_region_id="before-b",
                after_region_id="after-b",
                pair_id="pair-b",
            ),
        ),
        path,
    )

    loaded = load_region_match_overrides(path, pair_id="pair-b")

    assert len(loaded) == 1
    assert loaded[0].before_region_id == "before-b"


def test_manual_region_match_override_wins_over_conflicting_numbers() -> None:
    before = _region("before-r1", drawing_number="D-101")
    after = _region("after-r1", drawing_number="D-999")

    summary = match_sheet_regions(
        [before],
        [after],
        pair_id="pair-a",
        overrides=(
            RegionMatchOverride(
                before_region_id="before-r1",
                after_region_id="after-r1",
                reason="user confirmed matching detail",
            ),
        ),
    )

    assert summary.manual_matched_count == 1
    assert summary.auto_matched_count == 0
    match = summary.matches[0]
    assert match.status == "manual_matched"
    assert match.before_region_id == "before-r1"
    assert match.after_region_id == "after-r1"
    assert match.component_scores["manual_override"] == 1.0


def test_manual_unmatched_override_removes_region_from_auto_matching() -> None:
    before = _region("before-r1")
    after = _region("after-r1")

    summary = match_sheet_regions(
        [before],
        [after],
        pair_id="pair-a",
        overrides=(
            RegionMatchOverride(
                before_region_id="before-r1",
                status="unmatched_before",
                reason="detail intentionally deleted",
            ),
        ),
    )

    assert summary.auto_matched_count == 0
    assert summary.manual_matched_count == 0
    assert summary.unmatched_before_count == 1
    assert summary.unmatched_after_count == 1
    assert summary.matches[0].reasons == ("detail intentionally deleted",)
