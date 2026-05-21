# -*- coding: utf-8 -*-
"""Tests for the adaptive index cache in zone_render_service (Plan §15 Phase A-3).

External auditor #2 finding M3: ``_MAX_INDEX_CACHE_ENTRIES = 4`` is
hardcoded. A reviewer who navigates through more than 4 pairs in a session
will pay the full DXF parse + bbox + envelope rebuild cost on every
return visit. The fix introduces three behaviours covered by the tests
below:

1. The cache size is overridable via the ``DRAWING_COMPARE_INDEX_CACHE_SIZE``
   environment variable so operators can tune without a redeploy.
2. ``DrawingRenderIndex`` now records ``entity_count`` and
   ``render_time_ms`` so the eviction policy can prefer keeping
   expensive-to-rebuild entries.
3. When the cache is full, the eviction picks the oldest entry whose cost
   (``entity_count * render_time_ms``) is the cheapest among the oldest few.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.services.comparison.zone_render_service import (
    DrawingRenderIndex,
    _INDEX_CACHE,
    _INDEX_CACHE_ORDER,
    _MAX_INDEX_CACHE_ENTRIES,
    _clear_index_cache,
    _evict_to_capacity,
    _resolve_max_cache_entries,
)


@pytest.fixture(autouse=True)
def _isolate_cache():
    """Each test starts with an empty cache and gets the env clean on exit."""
    _clear_index_cache()
    original_env = os.environ.pop("DRAWING_COMPARE_INDEX_CACHE_SIZE", None)
    try:
        yield
    finally:
        _clear_index_cache()
        if original_env is None:
            os.environ.pop("DRAWING_COMPARE_INDEX_CACHE_SIZE", None)
        else:
            os.environ["DRAWING_COMPARE_INDEX_CACHE_SIZE"] = original_env


def _index(handle: str, *, entity_count: int = 100, render_time_ms: float = 50.0) -> DrawingRenderIndex:
    """Build a minimal DrawingRenderIndex for cache tests."""
    from pathlib import Path

    return DrawingRenderIndex(
        dxf_path=Path(f"/synthetic/{handle}.dxf"),
        source_signature={"handle": handle},
        render_environment_hash="test_env",
        doc=None,
        modelspace=None,
        bbox_cache=None,
        envelopes=[],
        entity_count=entity_count,
        render_time_ms=render_time_ms,
    )


class TestResolveMaxCacheEntries:
    def test_default_returns_legacy_value_when_no_env_no_psutil(self):
        """No env override + psutil unavailable → original 4."""
        with patch.dict("sys.modules", {"psutil": None}):
            # The import inside the helper will raise; helper falls back.
            assert _resolve_max_cache_entries() == _MAX_INDEX_CACHE_ENTRIES

    def test_env_override_takes_priority(self):
        os.environ["DRAWING_COMPARE_INDEX_CACHE_SIZE"] = "12"
        assert _resolve_max_cache_entries() == 12

    def test_env_override_clamped_to_64(self):
        os.environ["DRAWING_COMPARE_INDEX_CACHE_SIZE"] = "999"
        assert _resolve_max_cache_entries() == 64

    def test_env_invalid_value_falls_back(self):
        os.environ["DRAWING_COMPARE_INDEX_CACHE_SIZE"] = "not_a_number"
        # Falls through to psutil / default. We can't assume psutil's value,
        # but we CAN assert it returned a positive int (no exception).
        result = _resolve_max_cache_entries()
        assert isinstance(result, int)
        assert result >= _MAX_INDEX_CACHE_ENTRIES

    def test_env_zero_falls_through(self):
        os.environ["DRAWING_COMPARE_INDEX_CACHE_SIZE"] = "0"
        # 0 is not >= 1, so the override is rejected.
        result = _resolve_max_cache_entries()
        assert result >= _MAX_INDEX_CACHE_ENTRIES

    def test_psutil_8gb_returns_8(self):
        class _StubMem:
            available = 9 * (1024 ** 3)  # 9 GiB

        with patch("psutil.virtual_memory", return_value=_StubMem()):
            assert _resolve_max_cache_entries() == 8

    def test_psutil_16gb_returns_16(self):
        class _StubMem:
            available = 20 * (1024 ** 3)  # 20 GiB

        with patch("psutil.virtual_memory", return_value=_StubMem()):
            assert _resolve_max_cache_entries() == 16


class TestDrawingRenderIndexMetadata:
    def test_dataclass_carries_entity_count_and_render_time_ms(self):
        idx = _index("h1", entity_count=350, render_time_ms=120.5)
        assert idx.entity_count == 350
        assert idx.render_time_ms == 120.5

    def test_dataclass_defaults_zero_when_unset(self):
        # Legacy callers that build the index without the new fields still
        # work; the new fields default to 0 / 0.0.
        from pathlib import Path

        legacy = DrawingRenderIndex(
            dxf_path=Path("/x.dxf"),
            source_signature={},
            render_environment_hash="",
            doc=None,
            modelspace=None,
            bbox_cache=None,
            envelopes=[],
        )
        assert legacy.entity_count == 0
        assert legacy.render_time_ms == 0.0


class TestEvictToCapacity:
    def test_no_op_when_below_capacity(self):
        _INDEX_CACHE["a"] = _index("a")
        _INDEX_CACHE_ORDER.append("a")
        _evict_to_capacity(4)
        assert "a" in _INDEX_CACHE

    def test_evicts_cheapest_among_oldest_three(self):
        # 5 entries, capacity = 3. Oldest 3 are a/b/c.
        # Costs:  a=100*50=5000  b=10*5=50 (cheapest)  c=200*100=20000
        # Then 4 entries, capacity = 3. Oldest 3 are a/c/d.
        # Costs:  a=5000  c=20000  d=300*200=60000 → evict a (cheapest)
        # Then 3 entries, no further eviction.
        _INDEX_CACHE.update({
            "a": _index("a", entity_count=100, render_time_ms=50.0),
            "b": _index("b", entity_count=10, render_time_ms=5.0),
            "c": _index("c", entity_count=200, render_time_ms=100.0),
            "d": _index("d", entity_count=300, render_time_ms=200.0),
            "e": _index("e", entity_count=400, render_time_ms=400.0),
        })
        _INDEX_CACHE_ORDER.extend(["a", "b", "c", "d", "e"])

        _evict_to_capacity(3)

        # b (cheapest of {a,b,c}) and a (cheapest of {a,c,d}) both gone.
        assert "b" not in _INDEX_CACHE
        assert "a" not in _INDEX_CACHE
        # The 3 most-expensive survive.
        assert {"c", "d", "e"} == set(_INDEX_CACHE.keys())
        assert _INDEX_CACHE_ORDER == ["c", "d", "e"]

    def test_evicts_legacy_entry_first_when_cost_zero(self):
        # Legacy entries (entity_count=0, render_time_ms=0) cost evaluates
        # to 0 by default, so the eviction sweep takes them first.
        _INDEX_CACHE.update({
            "legacy": _index("legacy", entity_count=0, render_time_ms=0.0),
            "newish": _index("newish", entity_count=500, render_time_ms=300.0),
        })
        _INDEX_CACHE_ORDER.extend(["legacy", "newish"])

        _evict_to_capacity(1)

        assert "legacy" not in _INDEX_CACHE
        assert "newish" in _INDEX_CACHE
