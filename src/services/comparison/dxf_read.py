"""Tolerant DXF readers for ezdxf-based comparison and render paths."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

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


def read_dxf_document(path: str | Path, *, ezdxf_module: Any | None = None) -> Any:
    """Read a DXF document, applying narrowly-scoped in-memory repairs if needed."""

    return read_dxf_document_result(path, ezdxf_module=ezdxf_module).doc


def read_dxf_document_result(
    path: str | Path,
    *,
    ezdxf_module: Any | None = None,
) -> DxfReadResult:
    """Read a DXF document and return diagnostics about any repair fallback.

    Some customer/exporter DXFs omit the mandatory ``100 / AcDbPolyline``
    marker on ``LWPOLYLINE`` entities.  ezdxf refuses to load those files, and
    ``ezdxf.recover`` does not repair this specific defect.  The fallback below
    leaves the source file untouched, injects only the missing LWPOLYLINE
    structural tags in memory, and then re-runs ezdxf on that sanitized stream.
    """

    if ezdxf_module is None:
        import ezdxf as ezdxf_module  # type: ignore[no-redef]

    dxf_path = Path(path)
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
