# -*- coding: utf-8 -*-
"""Primitive-bbox spatial index for the diff-steered viewer engine.

Phase G uses this on the **flattened primitive stream** produced by
``scene_pack_builder``. Unlike the existing ``spatial_index.SpatialIndex``
(which accepts whole DXF entities and computes per-entity bboxes), this
module accepts ``(primitive_id, bbox)`` tuples directly so it can index
the post-flatten output of ``ezdxf`` Recorder / CustomJSONBackend.

Two implementations behind one interface:

* :class:`RTreePrimitiveIndex` — wraps the ``rtree`` library when available.
  Bulk-load via stream constructor, intersection query, disk persistence.
* :class:`GridPrimitiveIndex` — pure-Python fallback. Splits the world into
  uniform cells and returns all primitives whose bbox intersects any cell
  the query bbox touches. O(k) where k is the number of overlapped cells.

The factory :func:`build_primitive_index` picks the best available backend
at runtime so callers don't have to handle the rtree-missing case.

The module is **side-effect-free at import time** — rtree is imported
lazily inside the rtree class so unit tests can run on machines without
the binary wheel.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Protocol, Tuple

logger = logging.getLogger(__name__)

#: World-coords bbox: ``(min_x, min_y, max_x, max_y)``.
Bbox = Tuple[float, float, float, float]

#: One indexed primitive — id is the position in the scene pack.
PrimitiveBbox = Tuple[int, Bbox]

#: Index format version — bumped whenever on-disk layout changes so old
#: indexes are auto-rejected by ``load_primitive_index``.
INDEX_FORMAT_VERSION: int = 1

# ---------------------------------------------------------------------------
# Protocol — what every backend must support
# ---------------------------------------------------------------------------


class PrimitiveIndex(Protocol):
    """Common interface for both rtree and grid indices."""

    backend: str
    primitive_count: int

    def query_overlap(self, bbox: Bbox) -> List[int]: ...
    def save_to_disk(self, path: Path) -> None: ...


# ---------------------------------------------------------------------------
# Grid fallback — pure Python, no native deps
# ---------------------------------------------------------------------------


@dataclass
class GridPrimitiveIndex:
    """Uniform-grid primitive index. Always available (no native deps).

    Trades query speed for portability. Each cell is the same size in world
    units; primitives are bucketed into all cells their bbox touches.
    Query enumerates only the cells the query bbox overlaps and dedupes.

    Attributes:
        backend: always ``"grid"``.
        cell_size: world units per cell (auto-tuned by build helper).
        world_bbox: enclosing bbox of all indexed primitives.
        primitive_count: number of (id, bbox) pairs indexed.
        _cells: mapping ``(ix, iy) -> list[int]``.
        _bboxes: mapping ``primitive_id -> Bbox`` (kept for save/load).
    """

    backend: str = field(default="grid", init=False)
    cell_size: float = 1.0
    world_bbox: Bbox = (0.0, 0.0, 0.0, 0.0)
    primitive_count: int = 0
    _cells: dict[Tuple[int, int], List[int]] = field(default_factory=dict)
    _bboxes: dict[int, Bbox] = field(default_factory=dict)

    def _cell_index(self, x: float, y: float) -> Tuple[int, int]:
        return (int(math.floor(x / self.cell_size)),
                int(math.floor(y / self.cell_size)))

    def insert(self, primitive_id: int, bbox: Bbox) -> None:
        x0, y0, x1, y1 = bbox
        # Normalise (caller may have inverted coords)
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        ix0, iy0 = self._cell_index(x0, y0)
        ix1, iy1 = self._cell_index(x1, y1)
        for ix in range(ix0, ix1 + 1):
            for iy in range(iy0, iy1 + 1):
                self._cells.setdefault((ix, iy), []).append(primitive_id)
        self._bboxes[primitive_id] = (x0, y0, x1, y1)
        self.primitive_count += 1

    def query_overlap(self, bbox: Bbox) -> List[int]:
        """Return primitive ids whose bbox intersects ``bbox``.

        Overselects (returns all primitives in any cell touched by the
        query bbox). The caller should filter against the real bbox if
        precision matters. For Phase G's purpose (zone vector micro-pack)
        overselection is harmless — the renderer will simply draw a few
        extra primitives that were close to the query window.
        """

        x0, y0, x1, y1 = bbox
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        ix0, iy0 = self._cell_index(x0, y0)
        ix1, iy1 = self._cell_index(x1, y1)
        seen: set[int] = set()
        out: List[int] = []
        for ix in range(ix0, ix1 + 1):
            for iy in range(iy0, iy1 + 1):
                bucket = self._cells.get((ix, iy))
                if not bucket:
                    continue
                for pid in bucket:
                    if pid in seen:
                        continue
                    seen.add(pid)
                    out.append(pid)
        return out

    def save_to_disk(self, path: Path) -> None:
        """Persist as JSON so the format is human-inspectable + version-portable.

        Phase G2.4 fix — stream-write to avoid the ``json.dumps`` MemoryError
        seen on real DWG inputs (200K+ primitives).
        """

        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format_version": INDEX_FORMAT_VERSION,
            "backend": "grid",
            "cell_size": self.cell_size,
            "world_bbox": list(self.world_bbox),
            "primitive_count": self.primitive_count,
            "bboxes": {str(pid): list(bbox) for pid, bbox in self._bboxes.items()},
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, separators=(",", ":"))
        tmp.replace(path)

    @classmethod
    def load_from_disk(cls, path: Path) -> "GridPrimitiveIndex":
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format_version") != INDEX_FORMAT_VERSION:
            raise ValueError(
                f"Index format version mismatch: {data.get('format_version')!r} "
                f"!= {INDEX_FORMAT_VERSION}"
            )
        if data.get("backend") != "grid":
            raise ValueError(f"Not a grid index: {data.get('backend')!r}")
        cell = float(data["cell_size"])
        idx = cls(cell_size=cell, world_bbox=tuple(data["world_bbox"]))  # type: ignore[arg-type]
        for pid_str, bbox in data["bboxes"].items():
            idx.insert(int(pid_str), tuple(bbox))  # type: ignore[arg-type]
        return idx


# ---------------------------------------------------------------------------
# R-tree backend — preferred when ``rtree`` package is installed
# ---------------------------------------------------------------------------


@dataclass
class RTreePrimitiveIndex:
    """Bulk-loaded R-tree wrapper. Faster than grid for large indices.

    Builds via the rtree stream constructor (one-shot) so we don't pay
    insertion overhead per primitive. On disk, rtree writes its own .idx +
    .dat files; we wrap with a sidecar JSON carrying world_bbox + count
    so the loader can validate without parsing the binary index.
    """

    backend: str = field(default="rtree", init=False)
    world_bbox: Bbox = (0.0, 0.0, 0.0, 0.0)
    primitive_count: int = 0
    _idx: Optional[object] = field(default=None, repr=False)

    @classmethod
    def build_from_iterable(cls, primitives: Iterable[PrimitiveBbox]) -> "RTreePrimitiveIndex":
        from rtree import index as rtree_index

        items = list(primitives)
        if not items:
            return cls()

        # Compute world bbox from input.
        xs0 = min(b[1][0] for b in items)
        ys0 = min(b[1][1] for b in items)
        xs1 = max(b[1][2] for b in items)
        ys1 = max(b[1][3] for b in items)

        # Stream constructor — bulk load is much faster than insert() loop.
        # Each tuple: (id, (xmin, ymin, xmax, ymax), obj). obj=None -> save bytes.
        def _stream() -> Iterator[Tuple[int, Bbox, None]]:
            for pid, bbox in items:
                # Normalise inverted coords defensively.
                x0, y0, x1, y1 = bbox
                if x1 < x0:
                    x0, x1 = x1, x0
                if y1 < y0:
                    y0, y1 = y1, y0
                yield (pid, (x0, y0, x1, y1), None)

        props = rtree_index.Property()
        props.dimension = 2
        idx = rtree_index.Index(_stream(), properties=props, interleaved=True)

        return cls(
            world_bbox=(xs0, ys0, xs1, ys1),
            primitive_count=len(items),
            _idx=idx,
        )

    def query_overlap(self, bbox: Bbox) -> List[int]:
        if self._idx is None:
            return []
        x0, y0, x1, y1 = bbox
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        # rtree.intersection() returns an iterator of int ids when objects=False
        return list(self._idx.intersection((x0, y0, x1, y1)))  # type: ignore[union-attr]

    def save_to_disk(self, path: Path) -> None:
        """Write the rtree .idx + .dat files plus a JSON sidecar at ``path``.

        ``path`` is treated as the base name (no extension) — rtree appends
        ``.idx`` and ``.dat``. The JSON sidecar (``path.with_suffix('.meta.json')``)
        carries ``primitive_count`` + ``world_bbox`` + format version so the
        loader can validate before opening the binary index.
        """

        from rtree import index as rtree_index

        if self._idx is None:
            raise ValueError("Cannot save an empty rtree index")

        path.parent.mkdir(parents=True, exist_ok=True)

        # rtree saves to {path}.idx and {path}.dat. Strip any suffix we got.
        base = path.with_suffix("")  # remove e.g. ".rtree"
        # Wipe stale sibling files from a previous build — rtree refuses to
        # overwrite when the storage already exists.
        for suffix in (".idx", ".dat"):
            stale = base.with_suffix(suffix)
            if stale.exists():
                stale.unlink()

        props = rtree_index.Property()
        props.dimension = 2
        # Persist a *new* on-disk index, populated from the in-memory one.
        # rtree doesn't expose an export-existing-index API, so we re-stream.
        def _stream() -> Iterator[Tuple[int, Bbox, None]]:
            # Walk every primitive id; query a bounding box covering the world
            # to enumerate everything stored in the in-memory index.
            for hit in self._idx.intersection(  # type: ignore[union-attr]
                (-math.inf, -math.inf, math.inf, math.inf), objects=True
            ):
                yield (hit.id, tuple(hit.bbox), None)  # type: ignore[misc]

        rtree_index.Index(str(base), _stream(), properties=props, interleaved=True)

        meta = {
            "format_version": INDEX_FORMAT_VERSION,
            "backend": "rtree",
            "world_bbox": list(self.world_bbox),
            "primitive_count": self.primitive_count,
            "rtree_base": str(base.name),
        }
        meta_path = path.with_suffix(".meta.json")
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def load_from_disk(cls, path: Path) -> "RTreePrimitiveIndex":
        from rtree import index as rtree_index

        meta_path = path.with_suffix(".meta.json")
        if not meta_path.exists():
            raise FileNotFoundError(
                f"R-tree sidecar metadata missing: {meta_path}"
            )
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("format_version") != INDEX_FORMAT_VERSION:
            raise ValueError(
                f"Index format version mismatch: {meta.get('format_version')!r}"
            )
        if meta.get("backend") != "rtree":
            raise ValueError(f"Not an rtree index: {meta.get('backend')!r}")

        base = path.with_suffix("")
        props = rtree_index.Property()
        props.dimension = 2
        idx = rtree_index.Index(str(base), properties=props, interleaved=True)
        bbox_raw = meta.get("world_bbox") or [0.0, 0.0, 0.0, 0.0]
        return cls(
            world_bbox=(float(bbox_raw[0]), float(bbox_raw[1]),
                        float(bbox_raw[2]), float(bbox_raw[3])),
            primitive_count=int(meta.get("primitive_count", 0) or 0),
            _idx=idx,
        )


# ---------------------------------------------------------------------------
# Factory + auto-tuning
# ---------------------------------------------------------------------------


def _is_rtree_available() -> bool:
    try:
        import rtree  # noqa: F401
        return True
    except ImportError:
        return False


def _suggest_grid_cell_size(
    world_bbox: Bbox,
    primitive_count: int,
    *,
    primitives_for_avg: Optional[List[PrimitiveBbox]] = None,
) -> float:
    """Pick a reasonable cell size so queries actually discriminate.

    The original rule "32 primitives per cell" works for large inputs but
    collapses to a single giant cell for small ones (fewer than 32
    primitives) — every query then returns the entire scene. We fix this
    with a two-part rule:

    1. **Coarse rule**: ``target_cells = max(16, primitive_count // 32)``.
       Forces at least a 4x4 grid even when there are few primitives.
    2. **Fine rule**: if the resulting cell side is much larger than the
       average primitive size, shrink it to ~2x average primitive size
       so most queries can prune most primitives.

    Returns 1.0 for degenerate inputs.
    """

    x0, y0, x1, y1 = world_bbox
    width = max(x1 - x0, 1.0)
    height = max(y1 - y0, 1.0)
    area = width * height
    if primitive_count <= 0 or area <= 0:
        return 1.0

    # Coarse rule — at least a 4x4 grid so small inputs still get cell
    # discrimination.
    target_cells = max(16, primitive_count // 32)
    cell_area = area / target_cells
    cell_from_coarse = math.sqrt(cell_area)

    # Fine rule — if we have the actual primitives, prefer ~2x average
    # primitive size so queries scoped to one primitive's neighbourhood
    # don't overshoot into unrelated regions.
    if primitives_for_avg:
        sizes: List[float] = []
        for _, bbox in primitives_for_avg:
            sizes.append(max(bbox[2] - bbox[0], bbox[3] - bbox[1], 0.0))
        if sizes:
            avg = sum(sizes) / len(sizes)
            if avg > 0:
                # Don't drop below 2x avg primitive size — and don't grow
                # past the coarse-rule cell either (keeps cell count
                # bounded for huge inputs).
                cell_from_fine = max(avg * 2.0, 1.0)
                return min(cell_from_coarse, cell_from_fine)

    return cell_from_coarse


def build_primitive_index(
    primitives: Iterable[PrimitiveBbox],
    *,
    prefer_backend: Optional[str] = None,
) -> PrimitiveIndex:
    """Build the best available primitive index for ``primitives``.

    Picks rtree when ``rtree`` is installed, else grid fallback. Use
    ``prefer_backend="grid"`` to force the fallback in tests.
    """

    items = list(primitives)
    if prefer_backend == "grid" or not _is_rtree_available():
        if not _is_rtree_available() and prefer_backend != "grid":
            logger.info(
                "rtree unavailable, falling back to grid primitive index"
            )
        # Compute world bbox up front for cell-size tuning.
        if items:
            xs0 = min(b[1][0] for b in items)
            ys0 = min(b[1][1] for b in items)
            xs1 = max(b[1][2] for b in items)
            ys1 = max(b[1][3] for b in items)
            world_bbox: Bbox = (xs0, ys0, xs1, ys1)
        else:
            world_bbox = (0.0, 0.0, 0.0, 0.0)
        cell = _suggest_grid_cell_size(
            world_bbox, len(items), primitives_for_avg=items,
        )
        idx = GridPrimitiveIndex(cell_size=cell, world_bbox=world_bbox)
        for pid, bbox in items:
            idx.insert(pid, bbox)
        return idx
    return RTreePrimitiveIndex.build_from_iterable(items)


def load_primitive_index(path: Path) -> PrimitiveIndex:
    """Auto-detect on-disk format and load. Looks for ``{path}.meta.json``
    first (rtree case); falls back to plain JSON read (grid case).
    """

    meta_path = path.with_suffix(".meta.json")
    if meta_path.exists():
        return RTreePrimitiveIndex.load_from_disk(path)
    return GridPrimitiveIndex.load_from_disk(path)


__all__ = [
    "Bbox",
    "PrimitiveBbox",
    "INDEX_FORMAT_VERSION",
    "PrimitiveIndex",
    "GridPrimitiveIndex",
    "RTreePrimitiveIndex",
    "build_primitive_index",
    "load_primitive_index",
]
