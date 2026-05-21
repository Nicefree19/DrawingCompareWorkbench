# -*- coding: utf-8 -*-
"""Lightweight grid index for DXF change near matching.

This is a dependency-free fallback for large drawing comparisons when the
optional rtree package is not installed. It indexes changes by entity type,
layer, and coarse grid cell, then searches only the neighboring cells.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Tuple


GridKey = Tuple[str, str, int, int]


@dataclass
class GridSpatialIndex:
    """Dependency-free spatial index for point-like DXF changes."""

    tolerance: float
    _buckets: DefaultDict[GridKey, List[Tuple[int, Any]]] = field(
        default_factory=lambda: defaultdict(list),
        init=False,
        repr=False,
    )
    _items: Dict[int, Any] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.tolerance = max(float(self.tolerance or 0.0), 1e-9)

    def insert(self, item_id: int, change: Any) -> bool:
        """Insert one change. Returns False when it has no usable location."""

        location = getattr(change, "location", None)
        if location is None:
            return False
        key = self._key_for(change, location)
        if key is None:
            return False
        self._buckets[key].append((item_id, change))
        self._items[item_id] = change
        return True

    def bulk_insert(self, changes: Iterable[Any]) -> int:
        """Insert changes using their enumeration index as the item id."""

        inserted = 0
        for item_id, change in enumerate(changes):
            if self.insert(item_id, change):
                inserted += 1
        return inserted

    def query(self, probe: Any) -> List[Tuple[float, int, Any]]:
        """Return candidate changes sorted by distance and item id."""

        location = getattr(probe, "location", None)
        if location is None:
            return []
        key = self._key_for(probe, location)
        if key is None:
            return []

        entity_type, layer, gx, gy = key
        candidates: List[Tuple[float, int, Any]] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                bucket = self._buckets.get((entity_type, layer, gx + dx, gy + dy), [])
                for item_id, change in bucket:
                    distance = self._distance(location, getattr(change, "location", None))
                    if distance is not None and distance <= self.tolerance:
                        candidates.append((distance, item_id, change))
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates

    def __len__(self) -> int:
        return len(self._items)

    def _key_for(self, change: Any, location: Any) -> Optional[GridKey]:
        try:
            x = float(location[0])
            y = float(location[1])
        except Exception:
            return None

        entity_type = str(getattr(change, "entity_type", ""))
        layer = str(getattr(change, "layer", ""))
        gx = math.floor(x / self.tolerance)
        gy = math.floor(y / self.tolerance)
        return (entity_type, layer, gx, gy)

    @staticmethod
    def _distance(loc_a: Any, loc_b: Any) -> Optional[float]:
        if loc_a is None or loc_b is None:
            return None
        try:
            return math.hypot(float(loc_a[0]) - float(loc_b[0]), float(loc_a[1]) - float(loc_b[1]))
        except Exception:
            return None
