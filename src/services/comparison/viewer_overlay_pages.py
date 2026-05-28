"""Paged overlay store for drawing viewer packages.

The legacy viewer package writes one large ``overlays/{pair}.json`` payload
per pair. That shape is convenient but expensive to decode when a drawing has
tens of thousands of change zones. This module keeps the compatible JSON file
in place while adding a page manifest + small page files that GUI code can read
incrementally.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any, Iterator, Sequence

from .safe_unicode import safe_unicode


OVERLAY_PAGE_SCHEMA_VERSION = 1
DEFAULT_OVERLAY_PAGE_SIZE = 512
OVERLAY_PAGES_DIRNAME = "overlay_pages"


@dataclass(frozen=True)
class OverlayPageStoreSummary:
    manifest_path: Path
    overlay_count: int
    page_count: int
    page_size: int
    total_bytes: int

    def to_manifest_fields(self) -> dict[str, Any]:
        return {
            "overlay_pages_manifest": str(self.manifest_path),
            "overlay_page_count": int(self.page_count),
            "overlay_page_size": int(self.page_size),
            "overlay_pages_total_bytes": int(self.total_bytes),
        }


def write_overlay_page_store(
    *,
    pair_id: str,
    overlays: Sequence[dict[str, Any]],
    output_root: Path,
    page_size: int = DEFAULT_OVERLAY_PAGE_SIZE,
) -> OverlayPageStoreSummary:
    """Write chunked overlay page files and return a compact summary."""

    normalized_page_size = max(1, int(page_size or DEFAULT_OVERLAY_PAGE_SIZE))
    root = Path(output_root) / _safe_name(pair_id)
    root.mkdir(parents=True, exist_ok=True)
    for stale in root.glob("page_*.json"):
        try:
            stale.unlink()
        except OSError:
            pass

    overlay_list = [item for item in overlays if isinstance(item, dict)]
    pages: list[dict[str, Any]] = []
    page_pair_counts: dict[str, int] = {}
    total_bytes = 0
    for page_index, start in enumerate(range(0, len(overlay_list), normalized_page_size)):
        chunk = overlay_list[start : start + normalized_page_size]
        chunk_page_pair_counts: dict[str, int] = {}
        for overlay in chunk:
            key = overlay_page_pair_key(overlay)
            chunk_page_pair_counts[key] = int(chunk_page_pair_counts.get(key, 0)) + 1
            page_pair_counts[key] = int(page_pair_counts.get(key, 0)) + 1
        page_path = root / f"page_{page_index:05d}.json"
        payload = {
            "schema_version": OVERLAY_PAGE_SCHEMA_VERSION,
            "pair_id": str(pair_id),
            "page_index": int(page_index),
            "start": int(start),
            "record_count": len(chunk),
            "page_pair_counts": chunk_page_pair_counts,
            "overlays": chunk,
        }
        _write_json(page_path, payload)
        try:
            page_bytes = int(page_path.stat().st_size)
        except OSError:
            page_bytes = 0
        total_bytes += page_bytes
        pages.append(
            {
                "page_index": int(page_index),
                "start": int(start),
                "record_count": len(chunk),
                "path": str(page_path),
                "bytes": int(page_bytes),
                "page_pairs": sorted(chunk_page_pair_counts),
                "page_pair_counts": chunk_page_pair_counts,
            }
        )

    manifest_path = root / "manifest.json"
    manifest = {
        "schema_version": OVERLAY_PAGE_SCHEMA_VERSION,
        "pair_id": str(pair_id),
        "page_size": int(normalized_page_size),
        "overlay_count": len(overlay_list),
        "page_count": len(pages),
        "total_bytes": int(total_bytes),
        "page_pair_counts": page_pair_counts,
        "pages": pages,
    }
    _write_json(manifest_path, manifest)
    try:
        total_bytes += int(manifest_path.stat().st_size)
    except OSError:
        pass
    return OverlayPageStoreSummary(
        manifest_path=manifest_path,
        overlay_count=len(overlay_list),
        page_count=len(pages),
        page_size=normalized_page_size,
        total_bytes=total_bytes,
    )


def read_overlay_page_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


class OverlayPageStore:
    """Small query facade over paged overlay files.

    The facade deliberately exposes iterators instead of a default list-returning
    API. Callers that need all records must make that choice explicit.
    """

    def __init__(self, manifest_path: Path):
        self.manifest_path = Path(manifest_path)
        self.manifest = read_overlay_page_manifest(self.manifest_path)
        self.last_page_files_read = 0
        self.last_page_files_skipped = 0

    @property
    def overlay_count(self) -> int:
        return _int_value(self.manifest.get("overlay_count"))

    @property
    def page_count(self) -> int:
        return _int_value(self.manifest.get("page_count"))

    @property
    def total_bytes(self) -> int:
        return _int_value(self.manifest.get("total_bytes"))

    @property
    def page_size(self) -> int:
        return _int_value(self.manifest.get("page_size"), DEFAULT_OVERLAY_PAGE_SIZE)

    def summary(self) -> dict[str, Any]:
        return {
            "overlay_count": self.overlay_count,
            "page_count": self.page_count,
            "page_size": self.page_size,
            "total_bytes": self.total_bytes,
            "page_pair_counts": dict(self.manifest.get("page_pair_counts") or {}),
        }

    def iter_overlays(self) -> Iterator[dict[str, Any]]:
        yield from self._iter_page_overlays()

    def iter_visible_pdf_pages(self, page_a: int, page_b: int) -> Iterator[dict[str, Any]]:
        target = page_pair_key(page_a, page_b)
        for overlay in self._iter_page_overlays(page_pair_key_filter=target):
            overlay_key = overlay_page_pair_key(overlay)
            if overlay_key == "__global__" or overlay_key == target:
                yield overlay

    def iter_initial(self, limit: int) -> Iterator[dict[str, Any]]:
        yield from islice(self.iter_overlays(), max(0, int(limit)))

    def get_zone(self, zone_id: str) -> dict[str, Any] | None:
        target = str(zone_id or "")
        if not target:
            return None
        for overlay in self.iter_overlays():
            if str(overlay.get("zone_id") or "") == target:
                return dict(overlay)
        return None

    def _iter_page_overlays(self, *, page_pair_key_filter: str | None = None) -> Iterator[dict[str, Any]]:
        self.last_page_files_read = 0
        self.last_page_files_skipped = 0
        pages = self.manifest.get("pages") if isinstance(self.manifest, dict) else None
        if not isinstance(pages, list):
            return
        for page in pages:
            if not isinstance(page, dict):
                continue
            if page_pair_key_filter and not page_may_contain_pair(page, page_pair_key_filter):
                self.last_page_files_skipped += 1
                continue
            page_path = _resolve_page_path(page.get("path"), self.manifest_path.parent)
            if page_path is None or not page_path.exists():
                self.last_page_files_skipped += 1
                continue
            try:
                payload = json.loads(page_path.read_text(encoding="utf-8"))
            except Exception:
                self.last_page_files_skipped += 1
                continue
            self.last_page_files_read += 1
            overlays = payload.get("overlays") if isinstance(payload, dict) else None
            if not isinstance(overlays, list):
                continue
            for overlay in overlays:
                if isinstance(overlay, dict):
                    yield overlay


def iter_overlay_page_store(manifest_path: Path) -> Iterator[dict[str, Any]]:
    """Yield overlay records page by page from a paged overlay manifest."""

    yield from OverlayPageStore(manifest_path).iter_overlays()


def load_overlay_page_store(manifest_path: Path) -> list[dict[str, Any]]:
    return list(iter_overlay_page_store(manifest_path))


def overlay_page_store_is_available(path: Path | str | None) -> bool:
    if not path:
        return False
    manifest_path = Path(path)
    if not manifest_path.exists():
        return False
    manifest = read_overlay_page_manifest(manifest_path)
    return bool(manifest.get("page_count") or manifest.get("overlay_count"))


def page_pair_key(page_a: int, page_b: int) -> str:
    return f"{int(page_a)}:{int(page_b)}"


def overlay_page_pair_key(overlay: dict[str, Any]) -> str:
    for source in (
        overlay,
        overlay.get("metadata") if isinstance(overlay.get("metadata"), dict) else None,
    ):
        if not isinstance(source, dict):
            continue
        if "page_a" in source or "page_b" in source:
            try:
                return page_pair_key(int(source.get("page_a", 0) or 0), int(source.get("page_b", 0) or 0))
            except (TypeError, ValueError):
                continue
    return "__global__"


def page_may_contain_pair(page_record: dict[str, Any], pair_key: str) -> bool:
    page_pairs = page_record.get("page_pairs")
    if isinstance(page_pairs, list):
        values = {str(value) for value in page_pairs}
        return "__global__" in values or str(pair_key) in values
    page_pair_counts = page_record.get("page_pair_counts")
    if isinstance(page_pair_counts, dict):
        return "__global__" in page_pair_counts or str(pair_key) in page_pair_counts
    return True


def _int_value(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _resolve_page_path(value: object, manifest_dir: Path) -> Path | None:
    text = str(value or "")
    if not text:
        return None
    path = Path(text)
    if path.is_absolute():
        return path
    return manifest_dir / path


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return safe or "pair"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(safe_unicode(payload), handle, ensure_ascii=False, indent=2)
    tmp.replace(target)


__all__ = [
    "DEFAULT_OVERLAY_PAGE_SIZE",
    "OVERLAY_PAGE_SCHEMA_VERSION",
    "OVERLAY_PAGES_DIRNAME",
    "OverlayPageStore",
    "OverlayPageStoreSummary",
    "iter_overlay_page_store",
    "load_overlay_page_store",
    "overlay_page_pair_key",
    "overlay_page_store_is_available",
    "page_may_contain_pair",
    "page_pair_key",
    "read_overlay_page_manifest",
    "write_overlay_page_store",
]
