# -*- coding: utf-8 -*-
"""Tests for streaming tiles_manifest API (audit-gates §10.5 Phase B).

기존 ``merge_tiles_manifest``는 매번 전체 manifest를 읽고 다시 쓴다 →
S20-class large DWG에서 메모리/디스크 폭증의 한 원인. streaming JSONL
API는 단일 pair record만 메모리에 유지하면서 append-only로 누적.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.comparison.viewer_tile_cache import (
    TILES_MANIFEST_JSON,
    TILES_MANIFEST_JSONL,
    append_pair_to_tiles_manifest_jsonl,
    iter_tiles_manifest_jsonl,
    materialise_tiles_manifest_from_jsonl,
)


def _pair_record(uuid: str, *, tile_count: int = 5, overlay_tile_count: int = 2) -> dict:
    return {
        "pair_uuid": uuid,
        "tile_size": 512,
        "tile_count": tile_count,
        "overlay_tile_count": overlay_tile_count,
        "cache_key": f"key_{uuid}",
    }


class TestAppendStream:
    def test_append_creates_jsonl_file(self, tmp_path: Path):
        viewer_root = tmp_path / "viewer"
        path = append_pair_to_tiles_manifest_jsonl(viewer_root, _pair_record("p1"))
        assert path.exists()
        assert path.name == TILES_MANIFEST_JSONL
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["pair_uuid"] == "p1"

    def test_append_is_atomic_append(self, tmp_path: Path):
        viewer_root = tmp_path / "viewer"
        for uuid in ("p1", "p2", "p3"):
            append_pair_to_tiles_manifest_jsonl(viewer_root, _pair_record(uuid))
        lines = (viewer_root / TILES_MANIFEST_JSONL).read_text(
            encoding="utf-8"
        ).strip().splitlines()
        assert len(lines) == 3
        uuids = [json.loads(line)["pair_uuid"] for line in lines]
        assert uuids == ["p1", "p2", "p3"]

    def test_append_creates_directory_if_missing(self, tmp_path: Path):
        viewer_root = tmp_path / "deep" / "nested" / "viewer"
        append_pair_to_tiles_manifest_jsonl(viewer_root, _pair_record("p1"))
        assert (viewer_root / TILES_MANIFEST_JSONL).exists()


class TestIterStream:
    def test_iter_yields_records_in_order(self, tmp_path: Path):
        viewer_root = tmp_path / "viewer"
        for uuid in ("a", "b", "c"):
            append_pair_to_tiles_manifest_jsonl(viewer_root, _pair_record(uuid))
        records = list(iter_tiles_manifest_jsonl(viewer_root))
        assert [r["pair_uuid"] for r in records] == ["a", "b", "c"]

    def test_iter_empty_when_jsonl_missing(self, tmp_path: Path):
        viewer_root = tmp_path / "no_jsonl"
        viewer_root.mkdir()
        assert list(iter_tiles_manifest_jsonl(viewer_root)) == []

    def test_iter_skips_blank_lines(self, tmp_path: Path):
        viewer_root = tmp_path / "viewer"
        viewer_root.mkdir()
        (viewer_root / TILES_MANIFEST_JSONL).write_text(
            json.dumps(_pair_record("p1")) + "\n\n\n" + json.dumps(_pair_record("p2")) + "\n",
            encoding="utf-8",
        )
        records = list(iter_tiles_manifest_jsonl(viewer_root))
        assert len(records) == 2

    def test_iter_skips_malformed_lines(self, tmp_path: Path):
        viewer_root = tmp_path / "viewer"
        viewer_root.mkdir()
        (viewer_root / TILES_MANIFEST_JSONL).write_text(
            json.dumps(_pair_record("p1")) + "\n" + "garbage{not_json\n" + json.dumps(_pair_record("p2")) + "\n",
            encoding="utf-8",
        )
        records = list(iter_tiles_manifest_jsonl(viewer_root))
        assert [r["pair_uuid"] for r in records] == ["p1", "p2"]


class TestMaterialise:
    def test_basic_consolidation(self, tmp_path: Path):
        viewer_root = tmp_path / "viewer"
        append_pair_to_tiles_manifest_jsonl(viewer_root, _pair_record("p1", tile_count=10))
        append_pair_to_tiles_manifest_jsonl(viewer_root, _pair_record("p2", tile_count=20))
        json_path = materialise_tiles_manifest_from_jsonl(viewer_root)
        assert json_path.exists()
        assert json_path.name == TILES_MANIFEST_JSON
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == 1
        assert payload["pair_count"] == 2
        assert payload["tile_count"] == 30  # 10 + 20
        assert payload["overlay_tile_count"] == 4  # 2 + 2
        assert set(payload["pairs"].keys()) == {"p1", "p2"}
        assert payload["pairs"]["p1"]["tile_count"] == 10
        assert payload["pairs"]["p2"]["tile_count"] == 20

    def test_dedup_keeps_latest_for_same_pair(self, tmp_path: Path):
        viewer_root = tmp_path / "viewer"
        append_pair_to_tiles_manifest_jsonl(viewer_root, _pair_record("p1", tile_count=5))
        append_pair_to_tiles_manifest_jsonl(viewer_root, _pair_record("p1", tile_count=15))
        payload = json.loads(materialise_tiles_manifest_from_jsonl(viewer_root).read_text(encoding="utf-8"))
        assert payload["pair_count"] == 1
        assert payload["pairs"]["p1"]["tile_count"] == 15  # last write wins

    def test_keep_jsonl_default_true(self, tmp_path: Path):
        viewer_root = tmp_path / "viewer"
        append_pair_to_tiles_manifest_jsonl(viewer_root, _pair_record("p1"))
        materialise_tiles_manifest_from_jsonl(viewer_root)
        assert (viewer_root / TILES_MANIFEST_JSONL).exists()

    def test_keep_jsonl_false_removes_streaming_log(self, tmp_path: Path):
        viewer_root = tmp_path / "viewer"
        append_pair_to_tiles_manifest_jsonl(viewer_root, _pair_record("p1"))
        materialise_tiles_manifest_from_jsonl(viewer_root, keep_jsonl=False)
        assert not (viewer_root / TILES_MANIFEST_JSONL).exists()
        assert (viewer_root / TILES_MANIFEST_JSON).exists()

    def test_empty_jsonl_yields_zero_pair_manifest(self, tmp_path: Path):
        viewer_root = tmp_path / "viewer"
        viewer_root.mkdir()
        json_path = materialise_tiles_manifest_from_jsonl(viewer_root)
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        assert payload["pair_count"] == 0
        assert payload["tile_count"] == 0
        assert payload["pairs"] == {}

    def test_invalid_tile_size_falls_back_to_default(self, tmp_path: Path):
        viewer_root = tmp_path / "viewer"
        record = _pair_record("p1")
        record["tile_size"] = "garbage"
        append_pair_to_tiles_manifest_jsonl(viewer_root, record)
        payload = json.loads(materialise_tiles_manifest_from_jsonl(viewer_root).read_text(encoding="utf-8"))
        assert payload["tile_size"] == 512  # default


class TestMemoryFootprint:
    """Smoke check that streaming API does not require holding all records.

    Builds a synthetic 500-pair stream and verifies iter_* yields one at a
    time. Real memory enforcement is verified by integration / manual S20
    reproduction.
    """

    def test_iter_does_not_load_all_at_once(self, tmp_path: Path):
        viewer_root = tmp_path / "viewer"
        for i in range(500):
            append_pair_to_tiles_manifest_jsonl(viewer_root, _pair_record(f"p{i}"))
        seen = 0
        # Pull only the first 5 records — the generator must support partial
        # consumption without loading the rest.
        gen = iter_tiles_manifest_jsonl(viewer_root)
        for _ in range(5):
            next(gen)
            seen += 1
        assert seen == 5

    def test_consolidation_with_large_stream(self, tmp_path: Path):
        viewer_root = tmp_path / "viewer"
        for i in range(200):
            append_pair_to_tiles_manifest_jsonl(viewer_root, _pair_record(f"p{i}", tile_count=i))
        payload = json.loads(materialise_tiles_manifest_from_jsonl(viewer_root).read_text(encoding="utf-8"))
        assert payload["pair_count"] == 200
        # Sum 0..199 = 19900
        assert payload["tile_count"] == sum(range(200))

    def test_concurrent_appends_do_not_interleave(self, tmp_path: Path):
        """Plan §19 A-2 (Agent A finding A1) — concurrent appends to
        the same JSONL file must produce complete, parseable records
        even from multiple threads. The hardened impl uses one
        ``os.write`` per record on a single FD opened ``O_APPEND``
        per call, which keeps writes atomic up to PIPE_BUF (4 KiB) on
        POSIX and removes the worst interleaving window on Windows.
        """
        import threading

        viewer_root = tmp_path / "viewer_concurrent"
        viewer_root.mkdir()

        def _worker(start: int) -> None:
            for i in range(start, start + 25):
                append_pair_to_tiles_manifest_jsonl(
                    viewer_root, _pair_record(f"pair-{i:04d}", tile_count=i)
                )

        threads = [
            threading.Thread(target=_worker, args=(start,))
            for start in (0, 25, 50, 75)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Every line must be valid JSON — no torn-record corruption.
        jsonl_path = viewer_root / "tiles_manifest.jsonl"
        lines = jsonl_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 100, (
            f"expected 100 lines from 4 threads x 25 records, got {len(lines)}"
        )
        for line in lines:
            json.loads(line)  # would raise on partial / interleaved bytes
