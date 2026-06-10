"""Tolerant DXF readers for ezdxf-based comparison and render paths."""

from __future__ import annotations

import io
import logging
import threading
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Run-scoped parsed-document cache (issue-1 lever #2, 2026-06-11)
#
# cProfile of one real-pair pipeline run showed the SAME 71.9 MB converted DXF
# parsed SEVEN times (80.7 s cumulative, >50% of the run): descriptor scan x2,
# compare x2, cloud-marker x1, sheet-region detect x2 — every one of them
# funnels through read_dxf_document_result below, so a cache HERE covers all
# consumers without threading new parameters through their call chains.
#
# Safety model:
# - The cache only exists inside an explicit ``dxf_document_cache_scope()``
#   (the pipeline run opens one). No scope → behavior identical to before.
# - Consumers that MUTATE the document (dxf_cloud_marker adds cloud entities
#   and saveas-es a copy) must pass ``mutable=True`` to get a private fresh
#   parse; the cached pristine doc is never handed to them. All other in-repo
#   consumers are read-only (verified: no saveas/add_entity/layer-state writes
#   in drawing_batch descriptor, dwg_differ extraction, sheet_region_detector).
# - Keyed by (resolved path, mtime_ns, size) so an on-disk change invalidates.
# - Tiny LRU (default 4 docs): one pair's two sides + headroom — a folder run
#   with many pairs evicts old pairs instead of pinning gigabytes.
# ---------------------------------------------------------------------------

_DOC_CACHE_LOCK = threading.Lock()
_DOC_CACHE_SCOPES: list[dict[str, Any]] = []


@contextmanager
def dxf_document_cache_scope(maxsize: int = 4) -> Iterator[dict[str, Any]]:
    """Enable parsed-DXF reuse for read_dxf_document_result within this scope."""

    scope: dict[str, Any] = {
        "entries": OrderedDict(),
        "maxsize": max(1, int(maxsize)),
        "hits": 0,
        "misses": 0,
    }
    with _DOC_CACHE_LOCK:
        _DOC_CACHE_SCOPES.append(scope)
    try:
        yield scope
    finally:
        with _DOC_CACHE_LOCK:
            try:
                _DOC_CACHE_SCOPES.remove(scope)
            except ValueError:
                pass
        scope["entries"].clear()


def _document_cache_key(path: Path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
        return (str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))
    except OSError:
        return None

_LWPOLYLINE_REPAIR_REASON = "missing AcDbPolyline subclass in LWPOLYLINE"


@dataclass(frozen=True)
class DxfReadDiagnostics:
    sanitized: bool = False
    repair_count: int = 0
    reason: str = ""
    primary_error: str = ""

    def warning(self) -> str:
        if not self.sanitized:
            return ""
        return (
            "DXF sanitized in memory: "
            f"{self.reason}; repaired_lwpolyline_count={self.repair_count}; "
            f"primary_error={self.primary_error}"
        )


@dataclass(frozen=True)
class DxfReadResult:
    doc: Any
    diagnostics: DxfReadDiagnostics


def read_dxf_document(
    path: str | Path,
    *,
    ezdxf_module: Any | None = None,
    mutable: bool = False,
) -> Any:
    """Read a DXF document, applying narrowly-scoped in-memory repairs if needed."""

    return read_dxf_document_result(path, ezdxf_module=ezdxf_module, mutable=mutable).doc


def read_dxf_document_result(
    path: str | Path,
    *,
    ezdxf_module: Any | None = None,
    mutable: bool = False,
) -> DxfReadResult:
    """Read a DXF document and return diagnostics about any repair fallback.

    Some customer/exporter DXFs omit the mandatory ``100 / AcDbPolyline``
    marker on ``LWPOLYLINE`` entities.  ezdxf refuses to load those files, and
    ``ezdxf.recover`` does not repair this specific defect.  The fallback below
    leaves the source file untouched, injects only the missing LWPOLYLINE
    structural tags in memory, and then re-runs ezdxf on that sanitized stream.

    ``mutable=True`` requests a PRIVATE fresh parse that bypasses any active
    ``dxf_document_cache_scope`` — required by consumers that modify the
    document (e.g. the cloud marker). Read-only consumers keep the default and
    may receive a shared document inside a scope.
    """

    dxf_path = Path(path)
    scope: dict[str, Any] | None = None
    key: tuple[str, int, int] | None = None
    if not mutable:
        with _DOC_CACHE_LOCK:
            scope = _DOC_CACHE_SCOPES[-1] if _DOC_CACHE_SCOPES else None
        if scope is not None:
            key = _document_cache_key(dxf_path)
            if key is not None:
                with _DOC_CACHE_LOCK:
                    cached = scope["entries"].get(key)
                    if cached is not None:
                        scope["entries"].move_to_end(key)
                        scope["hits"] += 1
                        return cached
                    scope["misses"] += 1

    result = _read_dxf_document_uncached(dxf_path, ezdxf_module)
    if scope is not None and key is not None:
        with _DOC_CACHE_LOCK:
            entries = scope["entries"]
            entries[key] = result
            entries.move_to_end(key)
            while len(entries) > scope["maxsize"]:
                entries.popitem(last=False)
    return result


def _read_dxf_document_uncached(
    dxf_path: Path,
    ezdxf_module: Any | None,
) -> DxfReadResult:
    if ezdxf_module is None:
        import ezdxf as ezdxf_module  # type: ignore[no-redef]

    primary_exc: BaseException | None = None
    try:
        doc = ezdxf_module.readfile(str(dxf_path))
        return DxfReadResult(doc=doc, diagnostics=DxfReadDiagnostics())
    except Exception as exc:
        if not _should_try_lwpolyline_repair(exc):
            raise
        primary_exc = exc
        primary_error = f"{exc.__class__.__name__}: {exc}"

    text = _read_dxf_text(dxf_path, ezdxf_module=ezdxf_module)
    sanitized_text, repair_count = _repair_lwpolyline_subclasses(text)
    assert primary_exc is not None
    if repair_count <= 0:
        raise primary_exc

    try:
        doc = ezdxf_module.read(io.StringIO(sanitized_text))
    except Exception:
        logger.debug("In-memory DXF sanitize failed for %s", dxf_path, exc_info=True)
        raise primary_exc
    try:
        doc.filename = str(dxf_path)
    except Exception:
        pass

    diagnostics = DxfReadDiagnostics(
        sanitized=True,
        repair_count=repair_count,
        reason=_LWPOLYLINE_REPAIR_REASON,
        primary_error=primary_error,
    )
    logger.warning("%s: %s", dxf_path, diagnostics.warning())
    return DxfReadResult(doc=doc, diagnostics=diagnostics)


def _should_try_lwpolyline_repair(exc: BaseException) -> bool:
    message = str(exc)
    return (
        "AcDbPolyline" in message
        and "LWPOLYLINE" in message.upper()
        and "missing" in message.lower()
    )


def _read_dxf_text(path: Path, *, ezdxf_module: Any) -> str:
    encoding = "utf-8"
    try:
        info = ezdxf_module.dxf_file_info(str(path))
        encoding = str(getattr(info, "encoding", None) or encoding)
    except Exception:
        pass
    return path.read_text(encoding=encoding, errors="surrogateescape")


def _repair_lwpolyline_subclasses(text: str) -> tuple[str, int]:
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    repair_count = 0
    index = 0

    while index + 1 < len(lines):
        code = lines[index].strip()
        value = lines[index + 1].strip()
        if code == "0" and value.upper() == "LWPOLYLINE":
            entity_end = _find_entity_end(lines, index + 2)
            repaired = _repair_lwpolyline_entity(
                lines,
                start=index,
                end=entity_end,
                newline=newline,
            )
            output.extend(repaired.lines)
            if repaired.repaired:
                repair_count += 1
            index = entity_end
            continue
        output.extend(lines[index : index + 2])
        index += 2

    if index < len(lines):
        output.extend(lines[index:])
    return "".join(output), repair_count


@dataclass(frozen=True)
class _EntityRepair:
    lines: list[str]
    repaired: bool


def _repair_lwpolyline_entity(
    lines: list[str],
    *,
    start: int,
    end: int,
    newline: str,
) -> _EntityRepair:
    has_entity_subclass = False
    has_polyline_subclass = False
    has_vertex_count = False
    vertex_count = 0
    entity_subclass_at: int | None = None

    for cursor in range(start + 2, end - 1, 2):
        code = lines[cursor].strip()
        value = lines[cursor + 1].strip()
        if code == "100" and value == "AcDbEntity":
            has_entity_subclass = True
            entity_subclass_at = cursor
        elif code == "100" and value == "AcDbPolyline":
            has_polyline_subclass = True
        elif code == "90":
            has_vertex_count = True
        elif code == "10":
            vertex_count += 1

    if has_entity_subclass and has_polyline_subclass and has_vertex_count:
        return _EntityRepair(lines=list(lines[start:end]), repaired=False)

    insert_at = start + 2
    if entity_subclass_at is not None:
        insert_at = entity_subclass_at + 2
    elif insert_at + 1 < end and lines[insert_at].strip() == "5":
        insert_at += 2
        if insert_at + 1 < end and lines[insert_at].strip() == "330":
            insert_at += 2

    repaired_lines = list(lines[start:insert_at])
    if not has_entity_subclass:
        repaired_lines.extend(("100" + newline, "AcDbEntity" + newline))
    if not has_polyline_subclass:
        repaired_lines.extend(("100" + newline, "AcDbPolyline" + newline))
    if not has_vertex_count:
        repaired_lines.extend(("90" + newline, str(vertex_count) + newline))
    repaired_lines.extend(lines[insert_at:end])
    return _EntityRepair(lines=repaired_lines, repaired=True)


def _find_entity_end(lines: list[str], start: int) -> int:
    cursor = start
    while cursor + 1 < len(lines):
        if lines[cursor].strip() == "0":
            return cursor
        cursor += 2
    return len(lines)
