"""ZWCAD COM DWG-to-adapter-JSON bridge.

This wrapper is for explicit local/internal commercial-native validation only.
It drives an installed ZWCAD COM server, opens the original DWG, and emits the
``DwgAdapterDrawing`` JSON contract expected by
``src.services.comparison.commercial_dwg_json_adapter``.

It does not save or read an intermediate DXF and does not run in the default
customer path.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


SCHEMA_VERSION = "dwg-adapter-drawing-json/v1"
BRIDGE_NAME = "zwcad-com-dwg-json-bridge"
BRIDGE_VERSION = "1"
PROG_ID_ENV = "DRAWING_COMPARE_ZWCAD_PROG_ID"
EXE_ENV = "DRAWING_COMPARE_ZWCAD_EXE"
MAX_ENTITIES_ENV = "DRAWING_COMPARE_ZWCAD_BRIDGE_MAX_ENTITIES"
DEFAULT_PROG_IDS = ("ZWCAD.Application.2025", "ZWCAD.Application")
DEFAULT_ZWCAD_EXE_CANDIDATES = (r"C:\Program Files\ZWSOFT\ZWCAD 2025\ZWCAD.exe",)
DEFAULT_MAX_ENTITIES = 200_000
DEFAULT_TIMEOUT_SECONDS = 180.0


@dataclass(frozen=True)
class ZwcadSession:
    app: Any
    created_new: bool
    prog_id: str


class BridgeError(RuntimeError):
    """User-facing bridge failure."""


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = run_bridge(args)
    except BridgeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _emit_json(payload)
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("acadver")
    parser.add_argument("--mode", choices=("lisp-com", "script", "com"), default="lisp-com")
    parser.add_argument("--zwcad-exe", type=Path)
    parser.add_argument("--prog-id")
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--max-entities", type=int)
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="Leave a newly created ZWCAD COM application open after extraction.",
    )
    return parser.parse_args(argv)


def run_bridge(args: argparse.Namespace) -> dict[str, Any]:
    input_path = Path(args.input).resolve()
    if not input_path.exists() or not input_path.is_file():
        raise BridgeError(f"input DWG does not exist: {input_path}")

    max_entities = _max_entities(args.max_entities)
    mode = getattr(args, "mode", "com")
    if mode == "script":
        return _run_script_bridge(args, input_path=input_path, max_entities=max_entities)
    if mode == "lisp-com":
        return _run_lisp_com_bridge(args, input_path=input_path, max_entities=max_entities)
    session = _dispatch_zwcad(args.prog_id)
    app = session.app
    doc = None
    close_errors: list[str] = []
    try:
        _safe_set(app, "Visible", bool(args.visible))
        documents = _get(app, "Documents")
        if documents is None:
            raise BridgeError("ZWCAD COM application does not expose Documents.")
        doc = _open_document_readonly(documents, input_path)
        if doc is None:
            raise BridgeError("ZWCAD COM Documents.Open returned no document.")
        drawing = _drawing_from_document(
            doc,
            input_path=input_path,
            acadver=str(args.acadver).upper(),
            max_entities=max_entities,
        )
    finally:
        if doc is not None:
            try:
                _close_document(doc)
            except Exception as exc:  # pragma: no cover - defensive COM cleanup.
                close_errors.append(f"document_close_failed: {type(exc).__name__}: {exc}")
        if session.created_new and not bool(args.keep_open):
            try:
                _call(app, "Quit")
            except Exception as exc:  # pragma: no cover - defensive COM cleanup.
                close_errors.append(f"application_quit_failed: {type(exc).__name__}: {exc}")

    metadata = drawing.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        drawing["metadata"] = metadata
    metadata["source_path"] = str(input_path)
    metadata["commercial_dwg_json_bridge"] = {
        "adapter": BRIDGE_NAME,
        "adapter_version": BRIDGE_VERSION,
        "evidence_scope": "native_dwg_bridge",
        "uses_native_dwg": True,
        "uses_converted_dxf": False,
        "prog_id": session.prog_id,
        "created_new_com_application": session.created_new,
        "max_entities": max_entities,
        "close_errors": close_errors,
    }
    metadata.setdefault("zwcad_dwg_json_bridge", {})
    if isinstance(metadata["zwcad_dwg_json_bridge"], dict):
        metadata["zwcad_dwg_json_bridge"].update(
            {
                "bridge": BRIDGE_NAME,
                "bridge_version": BRIDGE_VERSION,
                "acadver": str(args.acadver).upper(),
                "prog_id": session.prog_id,
                "entity_count": len(drawing.get("entities") or []),
            }
        )
    return {"schema_version": SCHEMA_VERSION, "drawing": drawing}


def _run_lisp_com_bridge(args: argparse.Namespace, *, input_path: Path, max_entities: int) -> dict[str, Any]:
    timeout_seconds = max(1.0, float(getattr(args, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS) or DEFAULT_TIMEOUT_SECONDS))
    session = _dispatch_zwcad(getattr(args, "prog_id", None))
    app = session.app
    doc = None
    close_errors: list[str] = []
    with _bridge_workspace(keep=bool(getattr(args, "keep_temp", False))) as work_dir:
        lisp_path = work_dir / "extract_dwg_json.lsp"
        output_path = work_dir / "drawing.json"
        lisp_path.write_text(_lisp_extractor().strip() + "\n", encoding="ascii")
        try:
            _safe_set(app, "Visible", bool(getattr(args, "visible", False)))
            documents = _get(app, "Documents")
            if documents is None:
                raise BridgeError("ZWCAD COM application does not expose Documents.")
            doc = _open_document_readonly(documents, input_path)
            if doc is None:
                raise BridgeError("ZWCAD COM Documents.Open returned no document.")
            command_text = "\n".join(
                [
                    '(setvar "SECURELOAD" 0)',
                    '(setvar "FILEDIA" 0)',
                    '(setvar "CMDDIA" 0)',
                    f'(load "{_lisp_path(lisp_path)}")',
                    f'(setq DCW_OUT "{_lisp_path(output_path)}")',
                    f'(setq DCW_ACADVER "{_escape_lisp_string(str(args.acadver).upper())}")',
                    f"(setq DCW_MAX_ENTITIES {max_entities})",
                    "(DCW_EXPORT)",
                    "",
                ]
            )
            _call(doc, "SendCommand", command_text)
            _wait_for_output(output_path, timeout_seconds=timeout_seconds)
            drawing = _load_json_with_fallback(output_path)
        finally:
            if doc is not None:
                try:
                    _close_document(doc)
                except Exception as exc:  # pragma: no cover - defensive COM cleanup.
                    close_errors.append(f"document_close_failed: {type(exc).__name__}: {exc}")
            if session.created_new and not bool(getattr(args, "keep_open", False)):
                try:
                    _call(app, "Quit")
                except Exception as exc:  # pragma: no cover - defensive COM cleanup.
                    close_errors.append(f"application_quit_failed: {type(exc).__name__}: {exc}")

    if not isinstance(drawing, dict):
        raise BridgeError("ZWCAD LISP COM extractor output must be a JSON object.")
    metadata = drawing.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        drawing["metadata"] = metadata
    metadata["source_path"] = str(input_path)
    metadata["commercial_dwg_json_bridge"] = {
        "adapter": BRIDGE_NAME,
        "adapter_version": BRIDGE_VERSION,
        "evidence_scope": "native_dwg_bridge",
        "uses_native_dwg": True,
        "uses_converted_dxf": False,
        "prog_id": session.prog_id,
        "created_new_com_application": session.created_new,
        "lisp_com_mode": True,
        "max_entities": max_entities,
        "close_errors": close_errors,
    }
    metadata.setdefault("zwcad_dwg_json_bridge", {})
    if isinstance(metadata["zwcad_dwg_json_bridge"], dict):
        metadata["zwcad_dwg_json_bridge"].update(
            {
                "bridge": BRIDGE_NAME,
                "bridge_version": BRIDGE_VERSION,
                "acadver": str(args.acadver).upper(),
                "prog_id": session.prog_id,
                "lisp_com_mode": True,
            }
        )
    return {"schema_version": SCHEMA_VERSION, "drawing": drawing}


def _run_script_bridge(args: argparse.Namespace, *, input_path: Path, max_entities: int) -> dict[str, Any]:
    zwcad_exe = resolve_zwcad_exe(getattr(args, "zwcad_exe", None))
    if not zwcad_exe:
        raise BridgeError(f"ZWCAD.exe was not found. Set {EXE_ENV} or pass --zwcad-exe.")
    timeout_seconds = max(1.0, float(getattr(args, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS) or DEFAULT_TIMEOUT_SECONDS))
    with _bridge_workspace(keep=bool(getattr(args, "keep_temp", False))) as work_dir:
        lisp_path = work_dir / "extract_dwg_json.lsp"
        script_path = work_dir / "extract_dwg_json.scr"
        output_path = work_dir / "drawing.json"
        lisp_path.write_text(_lisp_extractor().strip() + "\n", encoding="ascii")
        script_path.write_text(
            "\n".join(
                [
                    '(setvar "SECURELOAD" 0)',
                    f'(load "{_lisp_path(lisp_path)}")',
                    f'(setq DCW_OUT "{_lisp_path(output_path)}")',
                    f'(setq DCW_ACADVER "{_escape_lisp_string(str(args.acadver).upper())}")',
                    f"(setq DCW_MAX_ENTITIES {max_entities})",
                    "(DCW_EXPORT)",
                    "_.QUIT",
                    "",
                ]
            ),
            encoding="ascii",
        )
        command = [str(zwcad_exe), str(input_path), "/b", str(script_path)]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
                cwd=str(work_dir),
            )
        except subprocess.TimeoutExpired as exc:
            raise BridgeError(f"ZWCAD script extractor timed out after {timeout_seconds:g}s: {command}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "no ZWCAD output")[:1200]
            raise BridgeError(f"ZWCAD script extractor failed (exit_code={completed.returncode}): {detail}")
        if not output_path.exists():
            detail = (completed.stderr or completed.stdout or "no ZWCAD output")[:1200]
            raise BridgeError("ZWCAD script extractor did not produce JSON: " + detail)
        drawing = _load_json_with_fallback(output_path)

    if not isinstance(drawing, dict):
        raise BridgeError("ZWCAD script extractor output must be a JSON object.")
    metadata = drawing.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        drawing["metadata"] = metadata
    metadata["source_path"] = str(input_path)
    metadata["commercial_dwg_json_bridge"] = {
        "adapter": BRIDGE_NAME,
        "adapter_version": BRIDGE_VERSION,
        "evidence_scope": "native_dwg_bridge",
        "uses_native_dwg": True,
        "uses_converted_dxf": False,
        "zwcad_exe": str(zwcad_exe),
        "zwcad_exit_code": completed.returncode,
        "script_mode": True,
        "max_entities": max_entities,
    }
    metadata.setdefault("zwcad_dwg_json_bridge", {})
    if isinstance(metadata["zwcad_dwg_json_bridge"], dict):
        metadata["zwcad_dwg_json_bridge"].update(
            {
                "bridge": BRIDGE_NAME,
                "bridge_version": BRIDGE_VERSION,
                "acadver": str(args.acadver).upper(),
                "zwcad_exe": str(zwcad_exe),
                "script_mode": True,
            }
        )
    return {"schema_version": SCHEMA_VERSION, "drawing": drawing}


def resolve_zwcad_exe(explicit: Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit)
    env_path = os.environ.get(EXE_ENV)
    if env_path:
        candidates.append(Path(env_path))
    candidates.extend(Path(item) for item in DEFAULT_ZWCAD_EXE_CANDIDATES)
    candidates.extend(_zwcad_exe_candidates())
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


def _zwcad_exe_candidates() -> list[Path]:
    roots = (Path(r"C:\Program Files\ZWSOFT"), Path(r"C:\Program Files (x86)\ZWSOFT"))
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        try:
            found.extend(root.glob("**/ZWCAD.exe"))
        except OSError:
            continue
    return sorted(found, key=lambda path: str(path).lower())


def _dispatch_zwcad(explicit_prog_id: str | None = None) -> ZwcadSession:
    prog_ids = _candidate_prog_ids(explicit_prog_id)
    try:
        import win32com.client  # type: ignore[import-not-found]
    except ImportError as exc:
        raise BridgeError("pywin32 is required for ZWCAD COM bridge automation.") from exc

    errors: list[str] = []
    for prog_id in prog_ids:
        dispatch_ex = getattr(win32com.client, "DispatchEx", None)
        if callable(dispatch_ex):
            try:
                return ZwcadSession(dispatch_ex(prog_id), True, prog_id)
            except Exception as exc:
                errors.append(f"{prog_id} DispatchEx: {type(exc).__name__}: {exc}")
        try:
            return ZwcadSession(win32com.client.Dispatch(prog_id), False, prog_id)
        except Exception as exc:
            errors.append(f"{prog_id} Dispatch: {type(exc).__name__}: {exc}")
    raise BridgeError("ZWCAD COM application could not be created: " + "; ".join(errors[:4]))


def _candidate_prog_ids(explicit_prog_id: str | None = None) -> tuple[str, ...]:
    values = [explicit_prog_id, os.environ.get(PROG_ID_ENV), *DEFAULT_PROG_IDS]
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in result:
            result.append(item)
    return tuple(result)


def _drawing_from_document(
    doc: Any,
    *,
    input_path: Path,
    acadver: str,
    max_entities: int,
) -> dict[str, Any]:
    layers = _layers(doc)
    entities: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}
    truncated = False
    for entity in _iter_collection(_get(doc, "ModelSpace")):
        payload = _entity_payload(entity)
        if payload is None:
            raw_type = _object_name(entity) or "UNKNOWN"
            skipped[raw_type] = skipped.get(raw_type, 0) + 1
            continue
        if len(entities) >= max_entities:
            truncated = True
            break
        entities.append(payload)

    return {
        "header": {"$ACADVER": acadver},
        "layers": layers or [{"name": "0"}],
        "entities": entities,
        "metadata": {
            "source_path": str(input_path),
            "zwcad_dwg_json_bridge": {
                "bridge": BRIDGE_NAME,
                "bridge_version": BRIDGE_VERSION,
                "acadver": acadver,
                "entity_count": len(entities),
                "skipped_entity_counts": skipped,
                "truncated": truncated,
            },
        },
    }


def _layers(doc: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for layer in _iter_collection(_get(doc, "Layers")):
        name = str(_get(layer, "Name", "0") or "0")
        result.append(
            {
                "name": name,
                "color": _json_safe(_get(layer, "Color")),
                "linetype": _json_safe(_get(layer, "Linetype")),
                "lineweight": _json_safe(_get(layer, "Lineweight")),
            }
        )
    return result


def _entity_payload(entity: Any) -> dict[str, Any] | None:
    raw_type = _raw_type(entity)
    if not raw_type:
        return None

    if raw_type == "LINE":
        geometry = {
            "start": _point(_get(entity, "StartPoint")),
            "end": _point(_get(entity, "EndPoint")),
        }
    elif raw_type == "CIRCLE":
        geometry = {
            "center": _point(_get(entity, "Center")),
            "radius": _float(_get(entity, "Radius")),
            "normal": _point(_get(entity, "Normal"), default=(0.0, 0.0, 1.0)),
        }
    elif raw_type == "ARC":
        geometry = {
            "center": _point(_get(entity, "Center")),
            "radius": _float(_get(entity, "Radius")),
            "start_angle_deg": _angle_deg(_get(entity, "StartAngle")),
            "end_angle_deg": _angle_deg(_get(entity, "EndAngle")),
            "normal": _point(_get(entity, "Normal"), default=(0.0, 0.0, 1.0)),
        }
    elif raw_type in {"LWPOLYLINE", "POLYLINE"}:
        geometry = {
            "vertices": _vertices(_get(entity, "Coordinates"), raw_type=raw_type),
            "closed": bool(_get(entity, "Closed", False)),
        }
    elif raw_type == "TEXT":
        geometry = {
            "text": str(_get(entity, "TextString", "") or ""),
            "insert": _point(_get(entity, "InsertionPoint")),
            "height": _float(_get(entity, "Height")),
            "rotation_deg": _angle_deg(_get(entity, "Rotation")),
            "alignment": f"{_json_safe(_get(entity, 'HorizontalAlignment'))}:{_json_safe(_get(entity, 'VerticalAlignment'))}",
        }
    elif raw_type == "MTEXT":
        geometry = {
            "raw_content": str(_get(entity, "TextString", "") or ""),
            "plain_text": str(_get(entity, "TextString", "") or ""),
            "insert": _point(_get(entity, "InsertionPoint")),
            "height": _float(_get(entity, "Height")),
            "box_width": _json_safe(_get(entity, "Width")),
            "rotation_deg": _angle_deg(_get(entity, "Rotation")),
        }
    elif raw_type == "INSERT":
        geometry = {
            "block_name": str(_get(entity, "EffectiveName", None) or _get(entity, "Name", "") or ""),
            "insert": _point(_get(entity, "InsertionPoint")),
            "scale": [
                _float(_get(entity, "XScaleFactor"), 1.0),
                _float(_get(entity, "YScaleFactor"), 1.0),
                _float(_get(entity, "ZScaleFactor"), 1.0),
            ],
            "rotation_deg": _angle_deg(_get(entity, "Rotation")),
            "attributes": _attributes(entity),
        }
    else:
        return None

    return {
        "type": raw_type,
        "geometry": geometry,
        "layer": str(_get(entity, "Layer", "0") or "0"),
        "handle": _json_safe(_get(entity, "Handle")),
        "owner_handle": _json_safe(_get(entity, "OwnerID")),
        "style": _style(entity),
        "layout_name": "Model",
        "attributes": geometry.get("attributes", []),
    }


def _raw_type(entity: Any) -> str | None:
    name = _object_name(entity).upper()
    if "LWPOLYLINE" in name:
        return "LWPOLYLINE"
    if "POLYLINE" in name:
        return "POLYLINE" if "3D" in name else "LWPOLYLINE"
    if "BLOCKREFERENCE" in name or "INSERT" in name:
        return "INSERT"
    if "MTEXT" in name:
        return "MTEXT"
    if "TEXT" in name:
        return "TEXT"
    if "CIRCLE" in name:
        return "CIRCLE"
    if "ARC" in name:
        return "ARC"
    if "LINE" in name:
        return "LINE"
    return None


def _object_name(entity: Any) -> str:
    return str(_get(entity, "ObjectName", None) or _get(entity, "EntityName", None) or type(entity).__name__)


def _style(entity: Any) -> dict[str, Any]:
    return {
        "color": _json_safe(_get(entity, "Color")),
        "linetype": _json_safe(_get(entity, "Linetype")),
        "lineweight": _json_safe(_get(entity, "Lineweight")),
        "text_style": _json_safe(_get(entity, "StyleName")),
        "dimension_style": _json_safe(_get(entity, "DimensionStyle")),
    }


def _attributes(entity: Any) -> list[dict[str, Any]]:
    try:
        attrs = _call(entity, "GetAttributes")
    except Exception:
        return []
    result: list[dict[str, Any]] = []
    for attr in _iter_collection(attrs):
        result.append(
            {
                "tag": str(_get(attr, "TagString", "") or ""),
                "text": str(_get(attr, "TextString", "") or ""),
                "insert": _point(_get(attr, "InsertionPoint")),
                "source_handle": _json_safe(_get(attr, "Handle")),
            }
        )
    return result


def _vertices(value: Any, *, raw_type: str) -> list[dict[str, Any]]:
    coords = [_float(item) for item in _sequence(value)]
    if not coords:
        return []
    step = 3 if raw_type == "POLYLINE" and len(coords) % 3 == 0 else 2
    vertices: list[dict[str, Any]] = []
    for index in range(0, len(coords) - (step - 1), step):
        point = coords[index : index + step]
        if len(point) == 2:
            point.append(0.0)
        vertices.append({"point": point, "bulge": 0.0})
    return vertices


def _lisp_extractor() -> str:
    try:
        from tools.autocad_dwg_json_bridge import LISP_EXTRACTOR
    except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback.
        from autocad_dwg_json_bridge import LISP_EXTRACTOR  # type: ignore[no-redef]
    return LISP_EXTRACTOR


@contextlib.contextmanager
def _bridge_workspace(*, keep: bool):
    raw = tempfile.mkdtemp(prefix="dcw-zwcad-bridge-")
    path = Path(raw).resolve()
    try:
        yield path
    finally:
        if not keep:
            shutil.rmtree(path, ignore_errors=True)


def _lisp_path(path: Path) -> str:
    return str(path).replace("\\", "/")


def _escape_lisp_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _load_json_with_fallback(path: Path) -> Any:
    data = path.read_bytes()
    errors: list[str] = []
    for encoding in ("utf-8-sig", "mbcs", "cp949", "latin-1"):
        try:
            return json.loads(data.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"{encoding}: {exc}")
        except LookupError:
            continue
    raise BridgeError("failed to decode ZWCAD script extractor output: " + "; ".join(errors[:3]))


def _wait_for_output(path: Path, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    stable_size: int | None = None
    stable_seen_at = 0.0
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size > 0:
            size = path.stat().st_size
            now = time.monotonic()
            if size == stable_size and now - stable_seen_at >= 0.5:
                return
            if size != stable_size:
                stable_size = size
                stable_seen_at = now
        time.sleep(0.2)
    raise BridgeError(f"ZWCAD LISP COM extractor did not produce JSON within {timeout_seconds:g}s: {path}")


def _close_document(doc: Any) -> None:
    _safe_set(doc, "Saved", True)
    try:
        _call(doc, "Close", False)
    except TypeError:
        _call(doc, "Close")


def _open_document_readonly(documents: Any, path: Path) -> Any:
    try:
        return _call(documents, "Open", str(path), True)
    except TypeError:
        return _call(documents, "Open", str(path))
    except Exception:
        return _call(documents, "Open", str(path))


def _call(obj: Any, name: str, *args: Any) -> Any:
    method = getattr(obj, name)
    return method(*args)


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    try:
        return getattr(obj, name)
    except Exception:
        return default


def _safe_set(obj: Any, name: str, value: Any) -> None:
    try:
        setattr(obj, name, value)
    except Exception:
        return


def _iter_collection(collection: Any) -> Iterable[Any]:
    if collection is None:
        return ()
    try:
        return iter(collection)
    except TypeError:
        pass
    count = int(_get(collection, "Count", 0) or 0)

    def generator() -> Iterable[Any]:
        for index in range(count):
            try:
                yield _call(collection, "Item", index)
            except Exception:
                yield _call(collection, "Item", index + 1)

    return generator()


def _sequence(value: Any) -> list[Any]:
    if value is None or isinstance(value, (str, bytes)):
        return []
    if isinstance(value, dict):
        return [value.get("x", 0.0), value.get("y", 0.0), value.get("z", 0.0)]
    try:
        return list(value)
    except TypeError:
        return []


def _point(value: Any, *, default: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> list[float]:
    values = _sequence(value)
    if len(values) >= 2:
        return [
            _float(values[0], default[0]),
            _float(values[1], default[1]),
            _float(values[2], default[2]) if len(values) > 2 else default[2],
        ]
    return [default[0], default[1], default[2]]


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _angle_deg(value: Any) -> float:
    raw = _float(value)
    if abs(raw) <= math.tau + 1e-9:
        return math.degrees(raw)
    return raw


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _max_entities(value: int | None) -> int:
    if value is not None:
        return max(1, int(value))
    raw = os.environ.get(MAX_ENTITIES_ENV)
    if raw:
        try:
            return max(1, int(raw))
        except ValueError as exc:
            raise BridgeError(f"{MAX_ENTITIES_ENV} must be an integer.") from exc
    return DEFAULT_MAX_ENTITIES


def _emit_json(payload: dict[str, Any]) -> None:
    data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(data)
        return
    sys.stdout.write(data.decode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
