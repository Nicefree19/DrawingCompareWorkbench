"""LRU + byte-bounded cache of per-pair viewer overlays.

MONO-4 #5 extraction: the V2 workbench god-object previously held this cache as
five instance fields plus touch/put/evict/estimate methods. It is now an
isolated collaborator so the eviction policy and byte accounting can be reasoned
about and unit-tested on their own (see
``tests/unit/services/comparison/test_overlay_cache_characterization.py``).

The owner (``DrawingCompareWorkbenchV2``) keeps thin delegators + read-only
properties so every existing call site and test reads the same interface; it
supplies ``active_pair`` / ``viewer_root`` / the limit constants per call so the
cache stays decoupled from widget state and from the module-level limits the
tests monkeypatch.
"""

from __future__ import annotations

from typing import Optional

from src.services.comparison.viewer_tile_cache import append_viewer_perf_event


class OverlayCache:
    """Bounded LRU cache: pair_id -> overlay rows, evicting by pair count and
    by estimated byte size while never evicting the active pair."""

    def __init__(self) -> None:
        self.cache: dict[str, list[dict]] = {}
        self.order: list[str] = []
        self.bytes_by_pair: dict[str, int] = {}
        self.total_bytes: int = 0
        self.evictions: int = 0

    def clear(self) -> None:
        self.cache = {}
        self.order = []
        self.bytes_by_pair = {}
        self.total_bytes = 0
        self.evictions = 0

    def get(self, pair_id: str) -> Optional[list[dict]]:
        cached = self.cache.get(pair_id)
        if cached is not None:
            self.touch(pair_id)
        return cached

    def touch(self, pair_id: str) -> None:
        if not pair_id:
            return
        try:
            self.order.remove(pair_id)
        except ValueError:
            pass
        self.order.append(pair_id)

    @staticmethod
    def estimate_value_bytes(value: object) -> int:
        if value is None:
            return 0
        if isinstance(value, bool):
            return 1
        if isinstance(value, (int, float)):
            return 8
        if isinstance(value, str):
            return len(value.encode("utf-8", errors="ignore"))
        if isinstance(value, dict):
            total = 256
            for key, item in value.items():
                total += len(str(key).encode("utf-8", errors="ignore"))
                total += OverlayCache.estimate_value_bytes(item)
            return total
        if isinstance(value, (list, tuple)):
            return 64 + sum(OverlayCache.estimate_value_bytes(item) for item in value)
        return len(str(value).encode("utf-8", errors="ignore"))

    @staticmethod
    def estimate_bytes(overlays: list[dict]) -> int:
        total = 0
        for overlay in overlays:
            total += OverlayCache.estimate_value_bytes(overlay)
        return total

    def put(
        self,
        pair_id: str,
        overlays: list[dict],
        *,
        active_pair: str,
        viewer_root,
        pair_limit: int,
        byte_limit: int,
    ) -> None:
        if not pair_id:
            return
        previous_bytes = int(self.bytes_by_pair.get(pair_id, 0))
        overlay_bytes = self.estimate_bytes(overlays)
        self.cache[pair_id] = overlays
        self.bytes_by_pair[pair_id] = overlay_bytes
        self.total_bytes = max(
            0,
            int(self.total_bytes) - previous_bytes + overlay_bytes,
        )
        self.touch(pair_id)
        self.evict_if_needed(
            active_pair=active_pair,
            viewer_root=viewer_root,
            pair_limit=pair_limit,
            byte_limit=byte_limit,
        )

    def evict_if_needed(
        self,
        *,
        active_pair: str,
        viewer_root,
        pair_limit: int,
        byte_limit: int,
    ) -> None:
        pair_limit = max(1, int(pair_limit))
        byte_limit = max(1, int(byte_limit))
        while self.order and (
            len(self.order) > pair_limit
            or self.total_bytes > byte_limit
        ):
            reason = (
                "pair_limit"
                if len(self.order) > pair_limit
                else "byte_limit"
            )
            evict_pair = self.order.pop(0)
            if evict_pair == active_pair and self.order:
                self.order.append(evict_pair)
                continue
            if evict_pair in self.cache:
                evicted_bytes = int(self.bytes_by_pair.pop(evict_pair, 0))
                self.cache.pop(evict_pair, None)
                self.total_bytes = max(
                    0,
                    self.total_bytes - evicted_bytes,
                )
                self.evictions += 1
                if viewer_root:
                    append_viewer_perf_event(
                        viewer_root,
                        "viewer_overlay_cache_evict",
                        pair_uuid=evict_pair,
                        overlay_cache_pair_limit=pair_limit,
                        overlay_cache_byte_limit=byte_limit,
                        overlay_cache_evicted_bytes=evicted_bytes,
                        overlay_cache_total_bytes=self.total_bytes,
                        overlay_cache_pair_count=len(self.cache),
                        overlay_cache_eviction_reason=reason,
                        overlay_cache_eviction_count=self.evictions,
                    )
            if (
                len(self.order) == 1
                and self.order[0] == active_pair
                and self.total_bytes > byte_limit
            ):
                break
