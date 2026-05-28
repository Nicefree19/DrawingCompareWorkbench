# -*- coding: utf-8 -*-
"""Tests for paged viewer overlay storage."""

from __future__ import annotations

import json
from pathlib import Path

from src.services.comparison.viewer_overlay_pages import (
    OverlayPageStore,
    iter_overlay_page_store,
    write_overlay_page_store,
)


def test_overlay_page_store_writes_ordered_pages(tmp_path: Path) -> None:
    overlays = [{"zone_id": f"z{i}", "page_a": i % 2, "page_b": i % 2} for i in range(5)]

    summary = write_overlay_page_store(
        pair_id="pair/with spaces",
        overlays=overlays,
        output_root=tmp_path,
        page_size=2,
    )

    manifest = json.loads(summary.manifest_path.read_text(encoding="utf-8"))
    assert manifest["overlay_count"] == 5
    assert manifest["page_count"] == 3
    assert manifest["page_pair_counts"] == {"0:0": 3, "1:1": 2}
    assert [page["record_count"] for page in manifest["pages"]] == [2, 2, 1]
    assert [overlay["zone_id"] for overlay in iter_overlay_page_store(summary.manifest_path)] == [
        "z0",
        "z1",
        "z2",
        "z3",
        "z4",
    ]


def test_overlay_page_store_skips_missing_or_corrupt_pages(tmp_path: Path) -> None:
    summary = write_overlay_page_store(
        pair_id="pair",
        overlays=[{"zone_id": "a"}, {"zone_id": "b"}, {"zone_id": "c"}],
        output_root=tmp_path,
        page_size=1,
    )
    manifest = json.loads(summary.manifest_path.read_text(encoding="utf-8"))
    Path(manifest["pages"][1]["path"]).unlink()
    Path(manifest["pages"][2]["path"]).write_text("{not-json", encoding="utf-8")

    assert [overlay["zone_id"] for overlay in iter_overlay_page_store(summary.manifest_path)] == ["a"]


def test_overlay_page_store_filters_pdf_page_pairs_and_skips_pages(tmp_path: Path) -> None:
    overlays = [
        {"zone_id": "p0a", "page_a": 0, "page_b": 0},
        {"zone_id": "p0b", "page_a": 0, "page_b": 0},
        {"zone_id": "p1a", "page_a": 1, "page_b": 1},
        {"zone_id": "p1b", "page_a": 1, "page_b": 1},
        {"zone_id": "global"},
    ]
    summary = write_overlay_page_store(
        pair_id="pair",
        overlays=overlays,
        output_root=tmp_path,
        page_size=2,
    )
    store = OverlayPageStore(summary.manifest_path)

    assert [overlay["zone_id"] for overlay in store.iter_visible_pdf_pages(1, 1)] == [
        "p1a",
        "p1b",
        "global",
    ]
    assert store.last_page_files_read == 2
    assert store.last_page_files_skipped == 1
    assert [overlay["zone_id"] for overlay in store.iter_initial(2)] == ["p0a", "p0b"]
    assert store.get_zone("p1b")["page_a"] == 1
