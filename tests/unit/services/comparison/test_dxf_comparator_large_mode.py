"""Large drawing safety and grid near-match tests."""

import logging
import time
from unittest.mock import Mock, patch

from src.services.comparison.comparison_config import ComparisonConfig
from src.services.comparison.dxf_comparator import (
    DxfChange,
    DxfChangeType,
    DxfComparator,
    DxfComparisonResult,
    RTREE_AVAILABLE,
)
from src.services.comparison.dxf_entity_extractor import NormalizedEntity
from src.services.comparison.dwg_differ import DwgDiffer
from src.services.comparison.grid_spatial_index import GridSpatialIndex


def entity(
    hash_value: str,
    location: tuple[float, float],
    entity_type: str = "LINE",
    layer: str = "0",
) -> NormalizedEntity:
    return NormalizedEntity(
        hash=hash_value,
        entity_type=entity_type,
        layer=layer,
        data={"location": location},
        location=location,
    )


def change(
    location: tuple[float, float],
    change_type: DxfChangeType,
    entity_type: str = "LINE",
    layer: str = "0",
) -> DxfChange:
    kwargs = {"old_data": {"location": location}}
    if change_type == DxfChangeType.ADDED:
        kwargs = {"new_data": {"location": location}}
    return DxfChange(
        entity_type=entity_type,
        layer=layer,
        change_type=change_type,
        location=location,
        **kwargs,
    )


def test_duplicate_hash_entities_are_not_collapsed() -> None:
    comparator = DxfComparator(use_spatial_index=False)

    entities_a = {
        "LINE": [
            entity("same-hash", (0.0, 0.0)),
            entity("same-hash", (10.0, 0.0)),
            entity("old-only", (20.0, 0.0)),
        ]
    }
    entities_b = {
        "LINE": [
            entity("same-hash", (0.0, 0.0)),
            entity("new-only", (30.0, 0.0)),
        ]
    }

    result = comparator.compare(entities_a, entities_b)

    assert result.deleted_count == 2
    assert result.added_count == 1
    assert result.total_changes == 3
    assert len([c for c in result.changes if c.change_type == DxfChangeType.DELETED]) == 2


def test_grid_near_match_is_deterministic_and_does_not_reuse_added_candidates() -> None:
    config = ComparisonConfig(use_spatial_index=True, near_match_index="grid")
    comparator = DxfComparator(tolerance=1.0, config=config)

    deleted = [
        change((0.0, 0.0), DxfChangeType.DELETED),
        change((100.0, 0.0), DxfChangeType.DELETED),
    ]
    added = [
        change((0.2, 0.0), DxfChangeType.ADDED),
        change((100.2, 0.0), DxfChangeType.ADDED),
    ]

    matches = comparator.find_near_matches(deleted, added)

    assert comparator._last_index_backend == "grid"
    assert len(matches) == 2
    assert len({id(added_change) for _, added_change in matches}) == 2
    assert matches[0][1] is added[0]
    assert matches[1][1] is added[1]


def test_grid_spatial_index_filters_by_type_layer_and_neighbor_cells() -> None:
    grid = GridSpatialIndex(tolerance=1.0)
    target = change((0.0, 0.0), DxfChangeType.ADDED, entity_type="TEXT", layer="A")
    wrong_layer = change((0.2, 0.0), DxfChangeType.ADDED, entity_type="TEXT", layer="B")
    far = change((10.0, 0.0), DxfChangeType.ADDED, entity_type="TEXT", layer="A")

    grid.insert(1, target)
    grid.insert(2, wrong_layer)
    grid.insert(3, far)

    hits = grid.query(change((0.4, 0.0), DxfChangeType.DELETED, entity_type="TEXT", layer="A"))

    assert hits == [(0.4, 1, target)]


def test_large_mode_truncates_details_but_keeps_full_counts(tmp_path) -> None:
    config = ComparisonConfig(
        large_drawing_mode="force",
        max_change_records_in_memory=2,
        use_spatial_index=False,
    )
    comparator = DxfComparator(config=config)
    comparator.configure_change_zone_stream(tmp_path / "changes.jsonl", pair_id="S-STREAM")
    entities_a = {"LINE": [entity(f"old-{i}", (float(i), 0.0)) for i in range(5)]}
    entities_b = {"LINE": []}

    result = comparator.compare(entities_a, entities_b)

    assert len(result.changes) == 2
    assert result.deleted_count == 5
    assert result.total_changes == 5
    assert result.metadata["large_drawing_mode"] == "active"
    assert result.metadata["truncated_changes"] is True
    assert result.metadata["omitted_change_counts"] == {
        "added": 0,
        "deleted": 3,
        "modified": 0,
    }
    assert result.metadata["change_zone_record_count"] == 5
    assert result.metadata["change_zone_stream_complete"] is True
    assert (tmp_path / "changes.jsonl").exists()


def test_large_mode_near_match_skips_repetitive_geometry(monkeypatch) -> None:
    config = ComparisonConfig(
        large_drawing_mode="force",
        max_change_records_in_memory=20,
        use_spatial_index=False,
    )
    comparator = DxfComparator(config=config)
    seen: dict[str, list[str]] = {}

    def fake_find_near_matches(deleted, added, **_kwargs):
        seen["deleted"] = [change.entity_type for change in deleted]
        seen["added"] = [change.entity_type for change in added]
        return []

    monkeypatch.setattr(comparator, "find_near_matches", fake_find_near_matches)
    entities_a = {
        "LINE": [entity("old-line", (0.0, 0.0), entity_type="LINE")],
        "TEXT": [entity("old-text", (10.0, 0.0), entity_type="TEXT")],
    }
    entities_b = {
        "LINE": [entity("new-line", (0.5, 0.0), entity_type="LINE")],
        "TEXT": [entity("new-text", (10.5, 0.0), entity_type="TEXT")],
    }

    result = comparator.compare_with_modified_detection(entities_a, entities_b)

    assert seen == {"deleted": ["TEXT"], "added": ["TEXT"]}
    assert result.stats["large_near_match_limited"] is True
    assert result.stats["large_near_match_input_counts"] == {
        "deleted": 1,
        "added": 1,
        "skipped_deleted": 1,
        "skipped_added": 1,
    }


def test_layer_move_filter_uses_object_id_lookup(monkeypatch) -> None:
    comparator = DxfComparator()
    matched_deleted = change((0.0, 0.0), DxfChangeType.DELETED)
    matched_added = change((0.0, 0.0), DxfChangeType.ADDED)
    keep_deleted = change((10.0, 0.0), DxfChangeType.DELETED)
    keep_added = change((20.0, 0.0), DxfChangeType.ADDED)
    layer_move = DxfChange(
        entity_type="LINE",
        layer="NEW",
        change_type=DxfChangeType.MODIFIED,
        old_data={"location": (0.0, 0.0)},
        new_data={"location": (0.0, 0.0)},
        location=(0.0, 0.0),
        change_category="layer_move",
    )
    base_result = DxfComparisonResult(
        changes=[matched_deleted, matched_added, keep_deleted, keep_added],
    )

    monkeypatch.setattr(
        comparator,
        "compare_with_modified_detection",
        lambda *_args, **_kwargs: base_result,
    )
    monkeypatch.setattr(
        comparator,
        "_detect_layer_moves",
        lambda _deleted, _added: ([layer_move], {0}, {0}),
    )

    result = comparator.compare_with_layer_statistics({}, {})

    assert matched_deleted not in result.changes
    assert matched_added not in result.changes
    assert keep_deleted in result.changes
    assert keep_added in result.changes
    assert layer_move in result.changes


@patch("src.services.comparison.dwg_differ.ezdxf")
def test_dwg_differ_large_mode_preserves_full_counts_after_truncation(mock_ezdxf, tmp_path) -> None:
    mock_ezdxf.readfile.return_value = Mock()
    config = ComparisonConfig(
        large_drawing_mode="force",
        max_change_records_in_memory=2,
        use_spatial_index=False,
    )
    differ = DwgDiffer(comparison_config=config)
    mock_extractor = Mock()
    mock_extractor.extract.side_effect = [
        {"LINE": [entity(f"old-{index}", (float(index), 0.0)) for index in range(5)]},
        {"LINE": []},
    ]
    mock_extractor.last_stats = {}
    differ._extractor = mock_extractor
    path = tmp_path / "test.dxf"
    path.touch()

    result = differ.compare(path, path)

    assert len(result.changes) == 2
    assert result.deleted_count == 5
    assert result.total_changes == 5
    assert result.metadata["change_counts"] == {"added": 0, "deleted": 5, "modified": 0}
    assert result.metadata["change_records_in_memory"] == 2
    assert result.metadata["truncated_changes"] is True


def test_large_mode_config_round_trips() -> None:
    config = ComparisonConfig(
        large_drawing_mode="force",
        large_entity_threshold=123,
        max_change_records_in_memory=456,
        near_match_index="grid",
    )

    loaded = ComparisonConfig.from_dict(config.to_dict())

    assert loaded.large_drawing_mode == "force"
    assert loaded.large_entity_threshold == 123
    assert loaded.max_change_records_in_memory == 456
    assert loaded.near_match_index == "grid"


def test_explicit_grid_backend_does_not_warn_about_missing_rtree(caplog) -> None:
    if RTREE_AVAILABLE:
        return
    caplog.set_level(logging.WARNING, logger="src.services.comparison.dxf_comparator")

    DxfComparator(config=ComparisonConfig(use_spatial_index=True, near_match_index="grid"))

    assert not any("rtree" in record.getMessage().lower() for record in caplog.records)


# ----------------------------------------------------------------------
# Plan §15 Phase C-1 — comparator hot-loop peak instrumentation
# External auditor #2 finding (CRITICAL): post-hoc truncate cannot detect
# in-flight memory spikes during ``compare()``'s accumulation. These tests
# pin the peak-pre-truncate metric and its inviolable invariant.
# ----------------------------------------------------------------------


def test_compare_records_peak_changes_pre_truncate_for_small_run() -> None:
    """Small run (no truncation) still records the accurate peak.

    With 10 deleted entities and no large-mode truncation, the peak
    must equal the final len(result.changes) == 10.
    """
    comparator = DxfComparator(use_spatial_index=False)
    entities_a = {"LINE": [entity(f"old-{i}", (float(i), 0.0)) for i in range(10)]}
    entities_b: dict = {"LINE": []}

    result = comparator.compare(entities_a, entities_b)

    assert "peak_changes_pre_truncate" in result.stats, (
        "peak_changes_pre_truncate must be recorded on every compare() run"
    )
    assert result.stats["peak_changes_pre_truncate"] == 10, (
        f"expected peak == 10 (no truncation), got "
        f"{result.stats['peak_changes_pre_truncate']}"
    )
    assert result.stats["peak_changes_pre_truncate"] == len(result.changes), (
        "no truncation occurred, so peak must equal final length"
    )


def test_finalize_large_result_truncates_but_preserves_peak(tmp_path) -> None:
    """Truncation reduces ``result.changes`` but the peak survives.

    With ``max_change_records_in_memory=2`` and 5 deleted entities, the
    peak captured during the hot loop is 5, while ``result.changes`` is
    truncated to 2. The peak metric MUST still report the pre-truncate
    high-water mark so operators can detect in-flight memory pressure.
    """
    config = ComparisonConfig(
        large_drawing_mode="force",
        max_change_records_in_memory=2,
        use_spatial_index=False,
    )
    comparator = DxfComparator(config=config)
    comparator.configure_change_zone_stream(
        tmp_path / "changes.jsonl", pair_id="PEAK-TEST"
    )
    entities_a = {"LINE": [entity(f"old-{i}", (float(i), 0.0)) for i in range(5)]}
    entities_b: dict = {"LINE": []}

    result = comparator.compare(entities_a, entities_b)

    # Truncation occurred
    assert len(result.changes) == 2
    assert result.metadata["truncated_changes"] is True
    # Peak survived truncation and reports the true pre-truncate count
    assert result.stats["peak_changes_pre_truncate"] == 5, (
        f"expected peak == 5 (pre-truncate), got "
        f"{result.stats['peak_changes_pre_truncate']}; truncation must "
        f"not reduce the recorded peak"
    )
    assert result.stats["peak_changes_pre_truncate"] > len(result.changes), (
        "with truncation active the peak must strictly exceed the "
        "post-truncate length"
    )


def test_peak_changes_invariant_holds_post_compare() -> None:
    """The peak >= len(result.changes) invariant must hold after compare().

    This is the inviolable contract: if it ever fails, a mutation site
    was added that bypassed ``_record_change``/``_record_changes`` and
    the peak metric is unsafe to publish.
    """
    comparator = DxfComparator(use_spatial_index=False)
    # Mix of added, deleted, and matched (no-op) entities
    entities_a = {
        "LINE": [
            entity("matched", (0.0, 0.0)),
            entity("old-a", (10.0, 0.0)),
            entity("old-b", (20.0, 0.0)),
        ],
        "CIRCLE": [entity("old-c", (30.0, 0.0))],
    }
    entities_b = {
        "LINE": [
            entity("matched", (0.0, 0.0)),
            entity("new-a", (40.0, 0.0)),
        ],
        "CIRCLE": [entity("new-c", (50.0, 0.0))],
    }

    result = comparator.compare(entities_a, entities_b)

    peak = result.stats.get("peak_changes_pre_truncate", 0)
    assert peak >= len(result.changes), (
        f"INVARIANT VIOLATED: peak ({peak}) must be >= "
        f"len(result.changes) ({len(result.changes)}); a mutation site "
        f"likely bypassed _record_change/_record_changes"
    )


# ----------------------------------------------------------------------
# Plan §16 Phase C-2.2 — DwgDiffer metadata propagation
# Validates that the in-band peak captured by DxfComparator survives the
# DxfComparisonResult → ComparisonResult conversion in
# ``DwgDiffer._dxf_to_comparison_result`` so the pipeline harvester can
# read it from ``result.metadata`` and forward to the audit gate.
# ----------------------------------------------------------------------


@patch("src.services.comparison.dwg_differ.ezdxf")
def test_build_comparison_result_propagates_peak_changes_pre_truncate(
    mock_ezdxf, tmp_path
) -> None:
    """``ComparisonResult.metadata`` must expose ``peak_changes_pre_truncate``.

    Without propagation, ``validate_drawing_compare_realset.py`` cannot
    harvest the metric per-pair and the audit gate has nothing to enforce.
    """
    mock_ezdxf.readfile.return_value = Mock()
    config = ComparisonConfig(
        large_drawing_mode="auto",
        use_spatial_index=False,
    )
    differ = DwgDiffer(comparison_config=config)
    mock_extractor = Mock()
    mock_extractor.extract.side_effect = [
        {"LINE": [entity(f"old-{i}", (float(i), 0.0)) for i in range(3)]},
        {"LINE": []},
    ]
    mock_extractor.last_stats = {}
    differ._extractor = mock_extractor
    path = tmp_path / "test.dxf"
    path.touch()

    result = differ.compare(path, path)

    assert "peak_changes_pre_truncate" in result.metadata, (
        "DwgDiffer must propagate peak_changes_pre_truncate so the pipeline "
        "harvester (Plan §16 Phase C-2.2) can read it from ComparisonResult.metadata"
    )
    # Three deleted entities → peak == 3
    assert result.metadata["peak_changes_pre_truncate"] == 3


@patch("src.services.comparison.dwg_differ.ezdxf")
def test_build_comparison_result_propagates_time_to_first_stream_record_ms(
    mock_ezdxf, tmp_path
) -> None:
    """``ComparisonResult.metadata`` must expose ``time_to_first_stream_record_ms``.

    For non-streaming runs the value is None; the key must still exist so
    harvesters can treat presence-as-None and absence-as-bug distinctly.
    """
    mock_ezdxf.readfile.return_value = Mock()
    config = ComparisonConfig(
        large_drawing_mode="auto",
        use_spatial_index=False,
    )
    differ = DwgDiffer(comparison_config=config)
    mock_extractor = Mock()
    mock_extractor.extract.side_effect = [
        {"LINE": [entity("solo", (0.0, 0.0))]},
        {"LINE": []},
    ]
    mock_extractor.last_stats = {}
    differ._extractor = mock_extractor
    path = tmp_path / "test.dxf"
    path.touch()

    result = differ.compare(path, path)

    assert "time_to_first_stream_record_ms" in result.metadata, (
        "DwgDiffer must propagate time_to_first_stream_record_ms (may be None) so "
        "the pipeline harvester (Plan §16 Phase C-2.2) can distinguish "
        "non-streaming runs from a propagation bug"
    )
    # Non-streaming path → value is None (no stream first-write to time)
    assert result.metadata["time_to_first_stream_record_ms"] is None


# ----------------------------------------------------------------------
# Plan §16 Phase C-3.1 — time-to-first-stream-record instrumentation
# Validates DxfComparator.compare() captures the wall time from compare()
# entry to first ``_write_change_zone_stream`` call. Audit gate uses this
# to detect stalled comparators where accumulation runs long before any
# record is streamed.
# ----------------------------------------------------------------------


def test_compare_records_time_to_first_stream_record_ms_on_first_write(
    tmp_path,
) -> None:
    """Streaming compare() must populate ``time_to_first_stream_record_ms``.

    Force large-drawing-mode with a stream path configured so the comparator
    invokes ``_write_change_zone_stream``. The metric must be a positive float.
    """
    config = ComparisonConfig(
        large_drawing_mode="force",
        max_change_records_in_memory=2,
        use_spatial_index=False,
    )
    comparator = DxfComparator(config=config)
    comparator.configure_change_zone_stream(
        tmp_path / "stream.jsonl", pair_id="T2FS-TEST"
    )
    # 100 deleted entities → guarantees streaming write
    entities_a = {"LINE": [entity(f"old-{i}", (float(i), 0.0)) for i in range(100)]}
    entities_b: dict = {"LINE": []}

    result = comparator.compare(entities_a, entities_b)

    assert "time_to_first_stream_record_ms" in result.stats, (
        "compare() must record time_to_first_stream_record_ms when streaming "
        "(Plan §16 Phase C-3.1)"
    )
    value = result.stats["time_to_first_stream_record_ms"]
    assert isinstance(value, float), (
        f"time_to_first_stream_record_ms must be float, got {type(value).__name__}"
    )
    assert value > 0.0, (
        f"time_to_first_stream_record_ms must be > 0 after streaming, got {value}"
    )


def test_time_to_first_stream_record_ms_not_overwritten_on_subsequent_writes(
    tmp_path,
) -> None:
    """Manual second invocation of ``_write_change_zone_stream`` must NOT
    overwrite the first-occurrence value (Plan §16 Phase C-3.1 first-wins).
    """
    config = ComparisonConfig(
        large_drawing_mode="force",
        max_change_records_in_memory=2,
        use_spatial_index=False,
    )
    comparator = DxfComparator(config=config)
    comparator.configure_change_zone_stream(
        tmp_path / "stream2.jsonl", pair_id="OVERWRITE-TEST"
    )
    entities_a = {"LINE": [entity(f"old-{i}", (float(i), 0.0)) for i in range(50)]}
    entities_b: dict = {"LINE": []}

    result = comparator.compare(entities_a, entities_b)
    first_value = result.stats.get("time_to_first_stream_record_ms")
    assert first_value is not None and first_value > 0.0

    # Manually invoke _write_change_zone_stream again with the same path-cleared
    # metadata. The change_zone_stream_path guard at the top of the method
    # returns early when metadata already contains the stream path, so we have
    # to clear it to allow a second invocation. Even then, _stream_first_write_perf
    # being set means the timer-capture branch must skip and the recorded
    # value must NOT change.
    time.sleep(0.01)  # ensure perf_counter would yield a different value
    result.metadata.pop("change_zone_stream_path", None)
    comparator._write_change_zone_stream(result)
    second_value = result.stats.get("time_to_first_stream_record_ms")
    assert second_value == first_value, (
        f"time_to_first_stream_record_ms must be first-occurrence-wins; "
        f"first={first_value}, second={second_value}"
    )


def test_time_to_first_stream_record_ms_within_wall_time_tolerance(
    tmp_path,
) -> None:
    """Recorded value must roughly track wall-time delta (loose tolerance).

    Windows perf_counter + pytest overhead can add tens of ms jitter; we use
    a ±200ms tolerance to keep the test stable.
    """
    config = ComparisonConfig(
        large_drawing_mode="force",
        max_change_records_in_memory=2,
        use_spatial_index=False,
    )
    comparator = DxfComparator(config=config)
    comparator.configure_change_zone_stream(
        tmp_path / "stream3.jsonl", pair_id="WALL-TEST"
    )
    entities_a = {"LINE": [entity(f"old-{i}", (float(i), 0.0)) for i in range(30)]}
    entities_b: dict = {"LINE": []}

    wall_start = time.perf_counter()
    result = comparator.compare(entities_a, entities_b)
    wall_delta_ms = (time.perf_counter() - wall_start) * 1000.0

    recorded = result.stats.get("time_to_first_stream_record_ms")
    assert recorded is not None
    # The recorded value is from compare() entry to first stream write,
    # which is strictly less than the full compare() wall time. So the
    # recorded value must NOT exceed wall_delta_ms by more than 200ms
    # (this only catches gross errors like double-counting or using a
    # wrong epoch).
    assert recorded <= wall_delta_ms + 200.0, (
        f"recorded ({recorded}ms) exceeds wall delta ({wall_delta_ms}ms) "
        f"by more than 200ms tolerance — likely measuring wrong epoch"
    )
