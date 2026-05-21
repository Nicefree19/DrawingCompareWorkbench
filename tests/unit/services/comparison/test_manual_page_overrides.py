# -*- coding: utf-8 -*-
"""Unit tests for the Phase H4 manual page override store + applier.

Coverage targets:
    - PageOverrideEntry (de)serialisation, ``new_entry`` timestamp
    - load_overrides:
        * missing file → empty
        * malformed JSON → empty + warning (no crash)
        * unknown schema → best-effort parse
        * malformed entries → skipped
    - save_overrides: round-trips + atomic-ish (tmp file removed on success)
    - upsert_override: replace-by-page_a, sorting, isolation across pair_ids
    - remove_override: prunes empty buckets
    - apply_overrides:
        * empty → unchanged
        * out-of-range → skipped + warning, valid kept
        * "force unmatched" (page_b == -1) → no matched pair created
        * conflict on page_a → original auto pair dropped
        * conflict on page_b → original auto pair dropped
        * leftover pages become UNMATCHED_A / UNMATCHED_B
        * sort order: matched first by (page_a, page_b)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.comparison.manual_page_overrides import (
    SCHEMA_NAME,
    SCHEMA_VERSION,
    PageOverrideEntry,
    apply_overrides,
    load_overrides,
    new_entry,
    remove_override,
    save_overrides,
    upsert_override,
)
from src.services.comparison.page_matcher import (
    PageMatchCandidate,
    PageMatchStatus,
)


# ---------------------------------------------------------------------------
# PageOverrideEntry / new_entry
# ---------------------------------------------------------------------------


def test_entry_to_from_dict_roundtrip() -> None:
    e = PageOverrideEntry(
        page_a=2, page_b=5, reason="moved", user="alice", timestamp="2026-01-01T00:00:00+00:00",
    )
    d = e.to_dict()
    assert d == {
        "page_a": 2, "page_b": 5, "reason": "moved",
        "user": "alice", "timestamp": "2026-01-01T00:00:00+00:00",
    }
    assert PageOverrideEntry.from_dict(d) == e


def test_new_entry_stamps_timestamp() -> None:
    e = new_entry(0, 1, reason="r", user="u")
    assert e.page_a == 0 and e.page_b == 1 and e.reason == "r" and e.user == "u"
    # ISO 8601 UTC with timezone offset
    assert e.timestamp.endswith("+00:00")
    assert "T" in e.timestamp


# ---------------------------------------------------------------------------
# load_overrides — robustness
# ---------------------------------------------------------------------------


def test_load_overrides_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_overrides(tmp_path / "nonexistent.json") == {}


def test_load_overrides_malformed_json_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    assert load_overrides(p) == {}


def test_load_overrides_top_level_not_mapping_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "list.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_overrides(p) == {}


def test_load_overrides_unknown_schema_still_parses(tmp_path: Path) -> None:
    p = tmp_path / "future.json"
    p.write_text(json.dumps({
        "schema": "manual_page_override.v999",
        "overrides": {"pair_x": [{"page_a": 0, "page_b": 1}]},
    }), encoding="utf-8")
    out = load_overrides(p)
    assert "pair_x" in out
    assert out["pair_x"][0].page_a == 0


def test_load_overrides_skips_malformed_entries(tmp_path: Path) -> None:
    p = tmp_path / "mixed.json"
    p.write_text(json.dumps({
        "schema": SCHEMA_NAME,
        "overrides": {
            "pair_x": [
                {"page_a": 0, "page_b": 1},   # ok
                "not a dict",                  # skipped
                {"page_a": "abc", "page_b": 2},  # skipped (non-int page_a)
                {"page_a": 3, "page_b": 4},    # ok
            ],
            "pair_y": "not a list",  # whole bucket skipped
        },
    }), encoding="utf-8")
    out = load_overrides(p)
    assert "pair_y" not in out
    assert len(out["pair_x"]) == 2
    assert {e.page_a for e in out["pair_x"]} == {0, 3}


# ---------------------------------------------------------------------------
# save_overrides — atomic write + roundtrip
# ---------------------------------------------------------------------------


def test_save_overrides_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "nested" / "overrides.json"
    overrides = {
        "pair_x": [
            new_entry(0, 2, reason="r1", user="alice"),
            new_entry(3, 7, user="bob"),
        ],
        "pair_y": [new_entry(0, 0, reason="confirm sequential")],
    }
    saved = save_overrides(p, overrides)
    assert saved == p
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["schema"] == SCHEMA_NAME
    assert data["version"] == SCHEMA_VERSION
    assert "saved_at" in data
    assert set(data["overrides"].keys()) == {"pair_x", "pair_y"}
    assert len(data["overrides"]["pair_x"]) == 2

    reloaded = load_overrides(p)
    assert set(reloaded.keys()) == {"pair_x", "pair_y"}
    assert reloaded["pair_x"][0].page_a == 0
    assert reloaded["pair_x"][0].reason == "r1"


def test_save_overrides_drops_empty_buckets(tmp_path: Path) -> None:
    p = tmp_path / "out.json"
    save_overrides(p, {"pair_x": [], "pair_y": [new_entry(0, 0)]})
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "pair_x" not in data["overrides"]
    assert "pair_y" in data["overrides"]


def test_save_overrides_no_tmp_leftover(tmp_path: Path) -> None:
    p = tmp_path / "out.json"
    save_overrides(p, {"pair_x": [new_entry(0, 0)]})
    leftovers = [child for child in tmp_path.iterdir() if child.suffix == ".tmp"]
    assert leftovers == []


# ---------------------------------------------------------------------------
# upsert / remove
# ---------------------------------------------------------------------------


def test_upsert_replaces_same_page_a_and_sorts() -> None:
    overrides: dict[str, list[PageOverrideEntry]] = {}
    upsert_override(overrides, "pair_x", new_entry(2, 5))
    upsert_override(overrides, "pair_x", new_entry(0, 1))
    upsert_override(overrides, "pair_x", new_entry(2, 7, reason="updated"))  # replaces (2, 5)

    bucket = overrides["pair_x"]
    assert len(bucket) == 2
    assert bucket[0].page_a == 0
    assert bucket[1].page_a == 2
    assert bucket[1].page_b == 7
    assert bucket[1].reason == "updated"


def test_upsert_isolates_pair_ids() -> None:
    overrides: dict[str, list[PageOverrideEntry]] = {}
    upsert_override(overrides, "pair_x", new_entry(0, 1))
    upsert_override(overrides, "pair_y", new_entry(0, 2))
    assert overrides["pair_x"][0].page_b == 1
    assert overrides["pair_y"][0].page_b == 2


def test_remove_prunes_empty_buckets() -> None:
    overrides: dict[str, list[PageOverrideEntry]] = {}
    upsert_override(overrides, "pair_x", new_entry(0, 1))
    remove_override(overrides, "pair_x", page_a=0)
    assert "pair_x" not in overrides


def test_remove_keeps_other_entries() -> None:
    overrides: dict[str, list[PageOverrideEntry]] = {}
    upsert_override(overrides, "pair_x", new_entry(0, 1))
    upsert_override(overrides, "pair_x", new_entry(2, 5))
    remove_override(overrides, "pair_x", page_a=0)
    assert len(overrides["pair_x"]) == 1
    assert overrides["pair_x"][0].page_a == 2


def test_remove_unknown_page_a_is_noop() -> None:
    overrides: dict[str, list[PageOverrideEntry]] = {}
    upsert_override(overrides, "pair_x", new_entry(0, 1))
    remove_override(overrides, "pair_x", page_a=9)
    assert len(overrides["pair_x"]) == 1


# ---------------------------------------------------------------------------
# apply_overrides — core logic
# ---------------------------------------------------------------------------


def _matched(pa: int, pb: int, status: PageMatchStatus = PageMatchStatus.AUTO_CONFIRMED) -> PageMatchCandidate:
    return PageMatchCandidate(
        page_a_index=pa, page_b_index=pb, score=0.9, status=status,
        score_breakdown={"drawing_number": 1.0, "title": 0.8},
    )


def _unmatched_a(pa: int) -> PageMatchCandidate:
    return PageMatchCandidate(
        page_a_index=pa, page_b_index=-1, score=0.0,
        status=PageMatchStatus.UNMATCHED_A,
    )


def _unmatched_b(pb: int) -> PageMatchCandidate:
    return PageMatchCandidate(
        page_a_index=-1, page_b_index=pb, score=0.0,
        status=PageMatchStatus.UNMATCHED_B,
    )


def test_apply_no_overrides_returns_copy() -> None:
    cands = [_matched(0, 0), _matched(1, 1)]
    out = apply_overrides(cands, [], n_a=2, n_b=2)
    assert len(out) == 2
    assert out is not cands  # new list


def test_apply_force_pair_replaces_existing() -> None:
    # Auto matched 0↔0 and 1↔1; user forces 1↔2 instead.
    cands = [_matched(0, 0), _matched(1, 1)]
    out = apply_overrides(cands, [new_entry(1, 2)], n_a=2, n_b=3)

    matched = [(c.page_a_index, c.page_b_index, c.status) for c in out if c.is_matched]
    assert (0, 0, PageMatchStatus.AUTO_CONFIRMED) in matched
    assert (1, 2, PageMatchStatus.AUTO_CONFIRMED) in matched
    # Original (1, 1) replaced
    assert (1, 1, PageMatchStatus.AUTO_CONFIRMED) not in matched

    # Manual entry should carry the marker score_breakdown
    manual = [c for c in out if c.page_a_index == 1 and c.page_b_index == 2][0]
    assert "manual_override" in manual.score_breakdown
    assert manual.score == 1.0

    # Unmatched: B page 1 (was matched, now freed)
    unmatched_b = sorted(c.page_b_index for c in out if c.status == PageMatchStatus.UNMATCHED_B)
    assert unmatched_b == [1]


def test_apply_drops_pair_when_b_targeted_by_other_override() -> None:
    # 0↔0 and 1↔1 auto. User overrides 0↔1 — that should drop the (1↔1) auto pair too,
    # because B page 1 is now occupied by the override.
    cands = [_matched(0, 0), _matched(1, 1)]
    out = apply_overrides(cands, [new_entry(0, 1)], n_a=2, n_b=2)

    matched = [(c.page_a_index, c.page_b_index) for c in out if c.is_matched]
    assert matched == [(0, 1)]
    # Page 1 in A and page 0 in B both freed
    unmatched_a = sorted(c.page_a_index for c in out if c.status == PageMatchStatus.UNMATCHED_A)
    unmatched_b = sorted(c.page_b_index for c in out if c.status == PageMatchStatus.UNMATCHED_B)
    assert unmatched_a == [1]
    assert unmatched_b == [0]


def test_apply_force_unmatched() -> None:
    # 0↔0 auto. User forces page_a=0 to unmatched.
    cands = [_matched(0, 0), _matched(1, 1)]
    out = apply_overrides(cands, [new_entry(0, -1, reason="false positive")], n_a=2, n_b=2)

    matched = [(c.page_a_index, c.page_b_index) for c in out if c.is_matched]
    assert matched == [(1, 1)]
    unmatched_a = sorted(c.page_a_index for c in out if c.status == PageMatchStatus.UNMATCHED_A)
    unmatched_b = sorted(c.page_b_index for c in out if c.status == PageMatchStatus.UNMATCHED_B)
    assert unmatched_a == [0]
    assert unmatched_b == [0]


def test_apply_promotes_review_required_via_override() -> None:
    # Original was REVIEW_REQUIRED 0↔0. User confirms it (same pages).
    cands = [_matched(0, 0, status=PageMatchStatus.REVIEW_REQUIRED)]
    out = apply_overrides(cands, [new_entry(0, 0, reason="confirmed by user")], n_a=1, n_b=1)
    matched = [c for c in out if c.is_matched]
    assert len(matched) == 1
    assert matched[0].status == PageMatchStatus.AUTO_CONFIRMED
    assert "manual_override" in matched[0].score_breakdown


def test_apply_validates_page_ranges() -> None:
    cands = [_matched(0, 0)]
    out = apply_overrides(
        cands,
        [
            new_entry(99, 0),    # page_a out of range
            new_entry(0, 99),    # page_b out of range
            new_entry(0, -2),    # invalid sentinel
        ],
        n_a=2, n_b=2,
    )
    # All 3 invalid → list returned unchanged (a copy)
    matched = [(c.page_a_index, c.page_b_index) for c in out if c.is_matched]
    assert matched == [(0, 0)]


def test_apply_unmatched_recompute_when_no_auto_match_existed() -> None:
    # Auto-matcher returned only unmatched candidates (1 in A, 2 in B, no overlap).
    cands = [
        _unmatched_a(0),
        _unmatched_b(0),
        _unmatched_b(1),
    ]
    # User forces A page 0 to match B page 1.
    out = apply_overrides(cands, [new_entry(0, 1)], n_a=1, n_b=2)
    matched = [(c.page_a_index, c.page_b_index) for c in out if c.is_matched]
    assert matched == [(0, 1)]
    unmatched_b = sorted(c.page_b_index for c in out if c.status == PageMatchStatus.UNMATCHED_B)
    assert unmatched_b == [0]


def test_apply_last_write_wins_inside_single_call() -> None:
    cands = [_matched(0, 0)]
    out = apply_overrides(
        cands,
        [
            new_entry(0, 1, reason="first"),
            new_entry(0, 2, reason="second"),
        ],
        n_a=1, n_b=3,
    )
    matched = [c for c in out if c.is_matched]
    assert len(matched) == 1
    assert (matched[0].page_a_index, matched[0].page_b_index) == (0, 2)


def test_apply_sort_order_matched_first_then_unmatched() -> None:
    cands = [_matched(0, 0), _matched(1, 1), _matched(2, 2)]
    out = apply_overrides(
        cands,
        [
            new_entry(1, -1),  # force page 1 unmatched
        ],
        n_a=3, n_b=3,
    )
    statuses = [c.status for c in out]
    # Matched candidates first
    matched_indices = [i for i, s in enumerate(statuses) if s in {
        PageMatchStatus.AUTO_CONFIRMED, PageMatchStatus.REVIEW_REQUIRED,
    }]
    unmatched_indices = [i for i, s in enumerate(statuses) if s in {
        PageMatchStatus.UNMATCHED_A, PageMatchStatus.UNMATCHED_B,
    }]
    assert all(mi < ui for mi in matched_indices for ui in unmatched_indices)


# ---------------------------------------------------------------------------
# Integration with file IO + apply (one end-to-end test)
# ---------------------------------------------------------------------------


def test_e2e_save_load_apply(tmp_path: Path) -> None:
    """Round-trip: build override → save → load → apply → matched pair."""

    overrides_path = tmp_path / "overrides.json"
    overrides: dict[str, list[PageOverrideEntry]] = {}
    upsert_override(
        overrides,
        "pair_abc",
        new_entry(0, 2, reason="auto picked wrong sheet", user="qa"),
    )
    save_overrides(overrides_path, overrides)

    loaded = load_overrides(overrides_path)
    assert "pair_abc" in loaded

    auto_cands = [_matched(0, 0), _matched(1, 1), _matched(2, 2)]
    out = apply_overrides(auto_cands, loaded["pair_abc"], n_a=3, n_b=3)
    matched = sorted(
        ((c.page_a_index, c.page_b_index) for c in out if c.is_matched),
    )
    # 0→2 forced; 1→1 keeps (B page 1 still free); 2→2 dropped (B page 2 stolen by override)
    assert (0, 2) in matched
    assert (1, 1) in matched
    assert (2, 2) not in matched
