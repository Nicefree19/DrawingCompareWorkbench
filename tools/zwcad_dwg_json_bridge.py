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
import csv
import ctypes
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
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


class BridgeTimeoutError(BridgeError):
    """Bridge operation exceeded its local timeout."""


class ZwcadProcessWatchdog:
    """Kill spawned ZWCAD instances if a blocking COM call exceeds the timeout."""

    def __init__(self, existing_pids: set[int], *, timeout_seconds: float, only_pids: set[int] | None = None):
        self.existing_pids = set(existing_pids)
        # Only ever terminate the specific PIDs we spawned (finding 7); never a
        # ZWCAD instance the user launched independently after our snapshot.
        self.only_pids = set(only_pids) if only_pids is not None else None
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.stage = "initializing"
        self.fired = False
        self.killed_pids: list[int] = []
        self._started = False
        self._timer = threading.Timer(self.timeout_seconds, self._fire)
        self._timer.daemon = True

    def set_stage(self, stage: str) -> None:
        self.stage = str(stage or "unknown")

    def start(self) -> None:
        self._started = True
        self._timer.start()

    def cancel(self) -> None:
        self._timer.cancel()
        # Join so a _fire() already in progress finishes writing killed_pids before
        # the caller reads it; otherwise the reported cleanup PIDs race (finding 11).
        if self._started:
            self._timer.join(timeout=10.0)

    def _fire(self) -> None:
        self.fired = True
        with contextlib.suppress(Exception):
            self.killed_pids = _cleanup_spawned_zwcad(
                self.existing_pids, grace_seconds=0.0, only_pids=self.only_pids
            )


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
    parser.add_argument(
        "--roi-json",
        help=(
            "Optional ROI JSON object or file path. Shape: "
            "{\"bbox\":[minx,miny,maxx,maxy],\"margin\":100}."
        ),
    )
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
    roi = _roi_from_arg(getattr(args, "roi_json", None))
    mode = getattr(args, "mode", "com")
    if mode == "script":
        return _run_script_bridge(args, input_path=input_path, max_entities=max_entities, roi=roi)
    if mode == "lisp-com":
        return _run_lisp_com_bridge(args, input_path=input_path, max_entities=max_entities, roi=roi)
    existing_zwcad_pids = _zwcad_process_ids()
    session = _dispatch_zwcad(args.prog_id)
    app = session.app
    spawned_zwcad_pids = _pinned_zwcad_pids(app, existing_zwcad_pids, created_new=session.created_new)
    doc = None
    close_errors: list[str] = []
    forced_cleanup_pids: list[int] = []
    try:
        _safe_set(app, "Visible", bool(args.visible))
        documents = _get(app, "Documents")
        if documents is None:
            raise BridgeError("ZWCAD COM application does not expose Documents.")
        _suppress_open_dialogs(app)
        doc = _open_document_readonly(documents, input_path)
        if doc is None:
            raise BridgeError("ZWCAD COM Documents.Open returned no document.")
        drawing = _drawing_from_document(
            doc,
            input_path=input_path,
            acadver=str(args.acadver).upper(),
            max_entities=max_entities,
            roi=roi,
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
            # Happy path: the app was just Quit() synchronously, so the spawned
            # ZWCAD is already present/exiting -- no need to sleep a grace window
            # waiting for a late spawn (finding 8).
            forced_cleanup_pids = _cleanup_spawned_zwcad(
                existing_zwcad_pids, grace_seconds=0.0, only_pids=spawned_zwcad_pids
            )

    metadata = drawing.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        drawing["metadata"] = metadata
    metadata["source_path"] = str(input_path)
    entity_count = _drawing_entity_count(drawing)
    truncated = _extraction_truncated(drawing)
    metadata["commercial_dwg_json_bridge"] = {
        "adapter": BRIDGE_NAME,
        "adapter_version": BRIDGE_VERSION,
        "evidence_scope": "native_dwg_bridge",
        "uses_native_dwg": True,
        "uses_converted_dxf": False,
        "prog_id": session.prog_id,
        "created_new_com_application": session.created_new,
        "max_entities": max_entities,
        "entity_count": entity_count,
        "truncated": truncated,
        "possibly_truncated": truncated,
        "roi": roi,
        "close_errors": close_errors,
        "forced_zwcad_process_cleanup_pids": forced_cleanup_pids,
    }
    metadata.setdefault("zwcad_dwg_json_bridge", {})
    if isinstance(metadata["zwcad_dwg_json_bridge"], dict):
        metadata["zwcad_dwg_json_bridge"].update(
            {
                "bridge": BRIDGE_NAME,
                "bridge_version": BRIDGE_VERSION,
                "acadver": str(args.acadver).upper(),
                "prog_id": session.prog_id,
                "entity_count": entity_count,
                "max_entities": max_entities,
                "truncated": truncated,
                "possibly_truncated": truncated,
                "roi": roi,
            }
        )
    return {"schema_version": SCHEMA_VERSION, "drawing": drawing}


def _run_lisp_com_bridge(
    args: argparse.Namespace,
    *,
    input_path: Path,
    max_entities: int,
    roi: dict[str, float] | None,
) -> dict[str, Any]:
    timeout_seconds = max(1.0, float(getattr(args, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS) or DEFAULT_TIMEOUT_SECONDS))
    existing_zwcad_pids = _zwcad_process_ids()
    session = _dispatch_zwcad(getattr(args, "prog_id", None))
    app = session.app
    spawned_zwcad_pids = _pinned_zwcad_pids(app, existing_zwcad_pids, created_new=session.created_new)
    doc = None
    close_errors: list[str] = []
    forced_cleanup_pids: list[int] = []
    extraction_timed_out = False
    watchdog = _zwcad_process_watchdog(
        existing_zwcad_pids, timeout_seconds=timeout_seconds, only_pids=spawned_zwcad_pids
    )
    with _bridge_workspace(keep=bool(getattr(args, "keep_temp", False))) as work_dir:
        lisp_path = work_dir / "extract_dwg_json.lsp"
        output_path = work_dir / "drawing.json"
        lisp_path.write_text(_lisp_extractor().strip() + "\n", encoding="ascii")
        try:
            watchdog.start()
            watchdog.set_stage("set_visible")
            _safe_set(app, "Visible", bool(getattr(args, "visible", False)))
            watchdog.set_stage("get_documents")
            documents = _get(app, "Documents")
            if documents is None:
                raise BridgeError("ZWCAD COM application does not expose Documents.")
            watchdog.set_stage("suppress_dialogs")
            _suppress_open_dialogs(app)
            watchdog.set_stage("open_document")
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
                    *_roi_lisp_lines(roi),
                    "(DCW_EXPORT)",
                    "",
                ]
            )
            try:
                watchdog.set_stage("send_command")
                _call(doc, "SendCommand", command_text)
            except Exception as exc:
                if watchdog.fired:
                    extraction_timed_out = True
                    raise BridgeTimeoutError(_lisp_com_timeout_message(watchdog)) from exc
                raise
            try:
                watchdog.set_stage("wait_for_output")
                _wait_for_output(output_path, timeout_seconds=timeout_seconds)
            except BridgeTimeoutError:
                extraction_timed_out = True
                raise
            watchdog.set_stage("load_output")
            drawing = _load_json_with_fallback(output_path)
            watchdog.set_stage("complete")
        except Exception as exc:
            if watchdog.fired and not isinstance(exc, BridgeTimeoutError):
                extraction_timed_out = True
                raise BridgeTimeoutError(_lisp_com_timeout_message(watchdog)) from exc
            raise
        finally:
            watchdog.cancel()
            forced_cleanup_pids = _merge_pids(forced_cleanup_pids, watchdog.killed_pids)
            if extraction_timed_out:
                with contextlib.suppress(Exception):
                    forced_cleanup_pids = _merge_pids(
                        forced_cleanup_pids,
                        _cleanup_spawned_zwcad(existing_zwcad_pids, grace_seconds=0.0, only_pids=spawned_zwcad_pids),
                    )
            elif doc is not None:
                try:
                    _close_document(doc)
                except Exception as exc:  # pragma: no cover - defensive COM cleanup.
                    close_errors.append(f"document_close_failed: {type(exc).__name__}: {exc}")
            if (
                not extraction_timed_out
                and session.created_new
                and not bool(getattr(args, "keep_open", False))
            ):
                try:
                    _call(app, "Quit")
                except Exception as exc:  # pragma: no cover - defensive COM cleanup.
                    close_errors.append(f"application_quit_failed: {type(exc).__name__}: {exc}")
                forced_cleanup_pids = _merge_pids(
                    forced_cleanup_pids,
                    _cleanup_spawned_zwcad(existing_zwcad_pids, grace_seconds=0.0, only_pids=spawned_zwcad_pids),
                )

    if not isinstance(drawing, dict):
        raise BridgeError("ZWCAD LISP COM extractor output must be a JSON object.")
    metadata = drawing.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        drawing["metadata"] = metadata
    metadata["source_path"] = str(input_path)
    entity_count = _drawing_entity_count(drawing)
    truncated = _extraction_truncated(drawing)
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
        "entity_count": entity_count,
        "truncated": truncated,
        "possibly_truncated": truncated,
        "roi": roi,
        "close_errors": close_errors,
        "forced_zwcad_process_cleanup_pids": forced_cleanup_pids,
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
                "entity_count": entity_count,
                "max_entities": max_entities,
                "truncated": truncated,
                "possibly_truncated": truncated,
                "roi": roi,
            }
        )
    return {"schema_version": SCHEMA_VERSION, "drawing": drawing}


def _run_script_bridge(
    args: argparse.Namespace,
    *,
    input_path: Path,
    max_entities: int,
    roi: dict[str, float] | None,
) -> dict[str, Any]:
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
                    *_roi_lisp_lines(roi),
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
    entity_count = _drawing_entity_count(drawing)
    truncated = _extraction_truncated(drawing)
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
        "entity_count": entity_count,
        "truncated": truncated,
        "possibly_truncated": truncated,
        "roi": roi,
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
                "entity_count": entity_count,
                "max_entities": max_entities,
                "truncated": truncated,
                "possibly_truncated": truncated,
                "roi": roi,
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
    roi: dict[str, float] | None,
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
        if roi is not None and not _payload_in_roi(payload, roi):
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
                "roi": roi,
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


def _roi_from_arg(value: str | None) -> dict[str, float] | None:
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip()
    try:
        if raw[0] in "{[":
            data = json.loads(raw)
        else:
            path = Path(raw)
            if path.exists() and path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
            else:
                data = json.loads(raw)
    except OSError as exc:
        raise BridgeError(f"--roi-json could not be read: {raw}") from exc
    except json.JSONDecodeError as exc:
        raise BridgeError("--roi-json must be a JSON object string or an existing JSON file.") from exc

    if not isinstance(data, dict):
        raise BridgeError("--roi-json must be a JSON object.")
    bbox = data.get("bbox")
    if bbox is None or isinstance(bbox, (str, bytes)):
        raise BridgeError('--roi-json requires "bbox": [minx, miny, maxx, maxy].')
    try:
        bbox_values = list(bbox)
    except TypeError as exc:
        raise BridgeError('--roi-json requires "bbox": [minx, miny, maxx, maxy].') from exc
    if len(bbox_values) != 4:
        raise BridgeError('--roi-json requires "bbox": [minx, miny, maxx, maxy].')

    minx, miny, maxx, maxy = [_finite_float(item, "--roi-json bbox values") for item in bbox_values]
    if minx > maxx or miny > maxy:
        raise BridgeError("--roi-json bbox minimum values must be <= maximum values.")
    margin = _finite_float(data.get("margin", 0.0), "--roi-json margin")
    if margin < 0:
        raise BridgeError("--roi-json margin must be >= 0.")
    return {
        "minx": minx - margin,
        "miny": miny - margin,
        "maxx": maxx + margin,
        "maxy": maxy + margin,
    }


def _roi_lisp_lines(roi: dict[str, float] | None) -> list[str]:
    if roi is None:
        return []
    return [
        "(setq DCW_ROI_ENABLED T)",
        f"(setq DCW_ROI_MINX {_lisp_number(roi['minx'])})",
        f"(setq DCW_ROI_MINY {_lisp_number(roi['miny'])})",
        f"(setq DCW_ROI_MAXX {_lisp_number(roi['maxx'])})",
        f"(setq DCW_ROI_MAXY {_lisp_number(roi['maxy'])})",
    ]


def _payload_in_roi(payload: dict[str, Any], roi: dict[str, float]) -> bool:
    bbox = _payload_bbox(payload)
    if bbox is None:
        return True
    minx, miny, maxx, maxy = bbox
    return not (
        maxx < roi["minx"]
        or minx > roi["maxx"]
        or maxy < roi["miny"]
        or miny > roi["maxy"]
    )


def _payload_bbox(payload: dict[str, Any]) -> tuple[float, float, float, float] | None:
    points = list(_payload_points(payload))
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    minx = min(xs)
    miny = min(ys)
    maxx = max(xs)
    maxy = max(ys)
    geometry = payload.get("geometry") if isinstance(payload.get("geometry"), dict) else {}
    if str(payload.get("type") or "").upper() in {"CIRCLE", "ARC"}:
        center = _roi_point_xy(geometry.get("center"))
        radius = _optional_finite_float(geometry.get("radius"))
        if center is not None and radius is not None and radius > 0.0:
            minx = min(minx, center[0] - radius)
            miny = min(miny, center[1] - radius)
            maxx = max(maxx, center[0] + radius)
            maxy = max(maxy, center[1] + radius)
    return (minx, miny, maxx, maxy)


def _payload_points(payload: dict[str, Any]) -> Iterable[tuple[float, float]]:
    geometry = payload.get("geometry") if isinstance(payload.get("geometry"), dict) else {}
    raw_type = str(payload.get("type") or "").upper()
    candidate_points: list[Any] = []
    if raw_type == "LINE":
        candidate_points.extend([geometry.get("start"), geometry.get("end")])
    elif raw_type in {"CIRCLE", "ARC"}:
        candidate_points.append(geometry.get("center"))
    elif raw_type in {"LWPOLYLINE", "POLYLINE"}:
        for vertex in geometry.get("vertices") or []:
            candidate_points.append(vertex.get("point") if isinstance(vertex, dict) else vertex)
    elif raw_type in {"TEXT", "MTEXT", "INSERT"}:
        candidate_points.append(geometry.get("insert"))
    for attr in payload.get("attributes") or []:
        if isinstance(attr, dict):
            candidate_points.append(attr.get("insert"))
    for attr in geometry.get("attributes") or []:
        if isinstance(attr, dict):
            candidate_points.append(attr.get("insert"))

    for point in candidate_points:
        xy = _roi_point_xy(point)
        if xy is not None:
            yield xy


def _roi_point_xy(point: Any) -> tuple[float, float] | None:
    values = _sequence(point)
    if len(values) < 2:
        return None
    x = _optional_finite_float(values[0])
    y = _optional_finite_float(values[1])
    if x is None or y is None:
        return None
    return (x, y)


def _finite_float(value: Any, label: str) -> float:
    result = _optional_finite_float(value)
    if result is None:
        raise BridgeError(f"{label} must be finite numbers.")
    return result


def _optional_finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _lisp_number(value: float) -> str:
    return format(float(value), ".12g")


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
    raise BridgeTimeoutError(f"ZWCAD LISP COM extractor did not produce JSON within {timeout_seconds:g}s: {path}")


def _zwcad_process_ids() -> set[int]:
    native = _process_ids_for_image_toolhelp("ZWCAD.exe")
    if native is not None:
        return native
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ZWCAD.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if completed.returncode != 0:
        return set()
    pids: set[int] = set()
    for row in csv.reader(line for line in completed.stdout.splitlines() if line.strip()):
        if len(row) < 2:
            continue
        if row[0].strip('"').lower() != "zwcad.exe":
            continue
        try:
            pids.add(int(row[1]))
        except ValueError:
            continue
    return pids


def _process_ids_for_image_toolhelp(image_name: str) -> set[int] | None:
    if os.name != "nt":
        return set()
    expected = Path(image_name).name.casefold()
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except Exception:
        return None

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_uint32),
            ("cntUsage", ctypes.c_uint32),
            ("th32ProcessID", ctypes.c_uint32),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", ctypes.c_uint32),
            ("cntThreads", ctypes.c_uint32),
            ("th32ParentProcessID", ctypes.c_uint32),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_uint32),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    create_snapshot.restype = ctypes.c_void_p
    process_first = kernel32.Process32FirstW
    process_first.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry32W)]
    process_first.restype = ctypes.c_int
    process_next = kernel32.Process32NextW
    process_next.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry32W)]
    process_next.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int

    snapshot = create_snapshot(0x00000002, 0)
    if snapshot in (None, ctypes.c_void_p(-1).value):
        return None
    pids: set[int] = set()
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(ProcessEntry32W)
        if not process_first(snapshot, ctypes.byref(entry)):
            return pids
        while True:
            if str(entry.szExeFile).casefold() == expected:
                pids.add(int(entry.th32ProcessID))
            if not process_next(snapshot, ctypes.byref(entry)):
                break
    finally:
        close_handle(snapshot)
    return pids


def _spawned_zwcad_pids(existing_pids: set[int]) -> set[int]:
    """PIDs that appeared since ``existing_pids`` was snapshotted -- i.e. the ZWCAD
    instance(s) this bridge spawned. Pin this set right after dispatch so cleanup
    never targets a ZWCAD the user opens later (finding 7)."""
    return _zwcad_process_ids() - set(existing_pids)


def _zwcad_pid_from_app(app: Any) -> int | None:
    """Resolve the exact PID backing a COM app via its main window handle
    (AcadApplication.HWND -> GetWindowThreadProcessId). This pins the instance we
    drive regardless of the process image name and even when a blocked COM call
    keeps image enumeration from seeing it, so a hung open can still be killed."""
    if os.name != "nt":
        return None
    hwnd = _get(app, "HWND")
    try:
        hwnd_int = int(hwnd)
    except (TypeError, ValueError):
        return None
    if not hwnd_int:
        return None
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
    except Exception:
        return None
    get_pid = user32.GetWindowThreadProcessId
    get_pid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    get_pid.restype = ctypes.c_uint32
    pid = ctypes.c_uint32(0)
    get_pid(ctypes.c_void_p(hwnd_int), ctypes.byref(pid))
    return int(pid.value) or None


def _pinned_zwcad_pids(app: Any, existing_pids: set[int], *, created_new: bool) -> set[int]:
    """The precise set of PIDs cleanup/watchdog may terminate. Prefer the HWND-derived
    PID; fall back to the post-dispatch image-name diff. Empty when we attached to an
    instance the user already had open (created_new False), so it is never killed."""
    if not created_new:
        return set()
    pid = _zwcad_pid_from_app(app)
    if pid:
        return {pid}
    return _spawned_zwcad_pids(existing_pids)


_OPEN_DIALOG_SUPPRESSION = (
    '(setvar "FILEDIA" 0)'
    '(setvar "CMDDIA" 0)'
    '(setvar "SECURELOAD" 0)'
    '(setvar "PROXYNOTICE" 0)'
    '(setvar "XLOADCTL" 0)'
    '(setvar "FONTALT" "simplex")'
)


def _suppress_open_dialogs(app: Any) -> None:
    """Disable modal dialogs that block Documents.Open under COM automation (proxy
    notice, missing-font/xref prompts, security/file dialogs) BEFORE opening the
    target. These system variables are registry/profile-scoped, so setting them on
    the app's initial document carries into the subsequent open. Best-effort."""
    doc = _get(app, "ActiveDocument")
    if doc is None:
        return
    with contextlib.suppress(Exception):
        _call(doc, "SendCommand", _OPEN_DIALOG_SUPPRESSION + "\n")


def _cleanup_spawned_zwcad(
    existing_pids: set[int],
    *,
    grace_seconds: float = 0.0,
    only_pids: set[int] | None = None,
) -> list[int]:
    if grace_seconds > 0.0:
        time.sleep(grace_seconds)
    if only_pids is not None:
        # Precisely pinned PIDs (e.g. resolved from the COM app window via
        # app.HWND): terminate them directly so a HUNG ZWCAD is killed even when
        # image-name enumeration misses it (the open_document hang showed an empty
        # ZWCAD.exe enumeration while the process was alive). Never touch a PID that
        # pre-existed our dispatch.
        targets = {int(pid) for pid in only_pids} - set(existing_pids)
        killed: list[int] = []
        for pid in sorted(targets):
            if _kill_process_tree(pid):
                killed.append(pid)
        return killed
    spawned = sorted(_zwcad_process_ids() - set(existing_pids))
    killed = []
    for pid in spawned:
        if _kill_process_tree(pid):
            killed.append(pid)
    return killed


def _zwcad_process_watchdog(
    existing_pids: set[int], *, timeout_seconds: float, only_pids: set[int] | None = None
) -> ZwcadProcessWatchdog:
    return ZwcadProcessWatchdog(existing_pids, timeout_seconds=timeout_seconds, only_pids=only_pids)


def _lisp_com_timeout_message(watchdog: ZwcadProcessWatchdog) -> str:
    return (
        f"ZWCAD LISP COM extractor exceeded wall timeout after {watchdog.timeout_seconds:g}s "
        f"during {watchdog.stage}."
    )


def _merge_pids(left: Sequence[int], right: Sequence[int]) -> list[int]:
    merged: list[int] = []
    seen: set[int] = set()
    for value in (*left, *right):
        pid = int(value)
        if pid in seen:
            continue
        seen.add(pid)
        merged.append(pid)
    return merged


def _drawing_entity_count(drawing: dict[str, Any]) -> int:
    entities = drawing.get("entities")
    if isinstance(entities, list):
        return len(entities)
    model_space = drawing.get("model_space")
    if isinstance(model_space, list):
        return len(model_space)
    metadata = drawing.get("metadata")
    if isinstance(metadata, dict):
        bridge_meta = metadata.get("zwcad_dwg_json_bridge")
        if isinstance(bridge_meta, dict):
            try:
                return int(bridge_meta.get("entity_count") or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def _extraction_truncated(drawing: dict[str, Any]) -> bool:
    """Authoritative truncation signal: True only when the extractor stopped early
    with more entities remaining (LISP/COM ``truncated`` flag), never inferred from
    ``entity_count >= max_entities`` which false-positives at exactly the cap."""
    metadata = drawing.get("metadata")
    if not isinstance(metadata, dict):
        return False
    for key in (
        "zwcad_dwg_json_bridge",
        "autocad_dwg_json_bridge",
        "commercial_dwg_json_bridge",
    ):
        section = metadata.get(key)
        if isinstance(section, dict) and section.get("truncated") is True:
            return True
    return False


def _kill_process_tree(pid: int) -> bool:
    try:
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F", "/T"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _terminate_process(pid)
    return completed.returncode == 0 or _terminate_process(pid)


def _terminate_process(pid: int) -> bool:
    if os.name != "nt":
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except Exception:
        return False
    open_process = kernel32.OpenProcess
    open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    open_process.restype = ctypes.c_void_p
    terminate_process = kernel32.TerminateProcess
    terminate_process.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    terminate_process.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int

    handle = open_process(0x0001, 0, int(pid))
    if not handle:
        return False
    try:
        return bool(terminate_process(handle, 1))
    finally:
        close_handle(handle)


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
