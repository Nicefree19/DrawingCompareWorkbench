"""AutoCAD/AcCoreConsole DWG-to-adapter-JSON bridge.

This wrapper is for explicit local/internal commercial-native validation only.
It drives an installed Autodesk AcCoreConsole executable, loads a generated
AutoLISP extractor, and emits the ``DwgAdapterDrawing`` JSON contract expected
by ``src.services.comparison.commercial_dwg_json_adapter``.

It does not convert DWG to DXF and does not run in the default customer path.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence


SCHEMA_VERSION = "dwg-adapter-drawing-json/v1"
BRIDGE_NAME = "autocad-accoreconsole-lisp-json-bridge"
BRIDGE_VERSION = "1"
ACCORECONSOLE_ENV = "DRAWING_COMPARE_ACCORECONSOLE_PATH"
MAX_ENTITIES_ENV = "DRAWING_COMPARE_AUTOCAD_BRIDGE_MAX_ENTITIES"
DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_MAX_ENTITIES = 200_000
DEFAULT_ACCORECONSOLE_CANDIDATES = (
    r"C:\Program Files\Autodesk\AutoCAD 2017\accoreconsole.exe",
    r"C:\Program Files\Autodesk\DWG TrueView 2020 - English\accoreconsole.exe",
)


LISP_EXTRACTOR = r"""
(vl-load-com)

(defun dcw-json-string (value / s i ch out)
  (setq s (vl-princ-to-string (if value value "")))
  (setq i 1)
  (setq out "\"")
  (while (<= i (strlen s))
    (setq ch (substr s i 1))
    (cond
      ((= ch "\\") (setq out (strcat out "\\\\")))
      ((= ch "\"") (setq out (strcat out "\\\"")))
      (T (setq out (strcat out ch)))
    )
    (setq i (1+ i))
  )
  (strcat out "\"")
)

(defun dcw-num (value)
  (cond
    ((numberp value) (rtos value 2 12))
    (T "0.0")
  )
)

(defun dcw-point-json (pt / x y z)
  (setq x (if (and (listp pt) (numberp (car pt))) (car pt) 0.0))
  (setq y (if (and (listp pt) (numberp (cadr pt))) (cadr pt) 0.0))
  (setq z (if (and (listp pt) (numberp (caddr pt))) (caddr pt) 0.0))
  (strcat "[" (dcw-num x) "," (dcw-num y) "," (dcw-num z) "]")
)

(defun dcw-angle-deg (value)
  (cond
    ((numberp value) (* 180.0 (/ value pi)))
    (T 0.0)
  )
)

(defun dcw-layer (data / layer)
  (setq layer (cdr (assoc 8 data)))
  (if layer layer "0")
)

(defun dcw-handle (data / handle)
  (setq handle (cdr (assoc 5 data)))
  (if handle handle "")
)

(defun dcw-modelspace-p (data / space layout etype)
  (setq etype (strcase (cdr (assoc 0 data))))
  (setq space (cdr (assoc 67 data)))
  (setq layout (cdr (assoc 410 data)))
  (and
    (not (member etype '("VERTEX" "SEQEND" "ATTRIB")))
    (or (null space) (= space 0))
    (or (null layout) (= (strcase layout) "MODEL"))
  )
)

(defun dcw-roi-active-p ()
  (and
    (boundp 'DCW_ROI_ENABLED)
    DCW_ROI_ENABLED
    (boundp 'DCW_ROI_MINX)
    (boundp 'DCW_ROI_MINY)
    (boundp 'DCW_ROI_MAXX)
    (boundp 'DCW_ROI_MAXY)
    (numberp DCW_ROI_MINX)
    (numberp DCW_ROI_MINY)
    (numberp DCW_ROI_MAXX)
    (numberp DCW_ROI_MAXY)
  )
)

(defun dcw-point-x (pt)
  (if (and (listp pt) (numberp (car pt))) (car pt) nil)
)

(defun dcw-point-y (pt)
  (if (and (listp pt) (numberp (cadr pt))) (cadr pt) nil)
)

(defun dcw-bbox-in-roi-p (minx miny maxx maxy)
  (if (not (dcw-roi-active-p))
    T
    (not
      (or
        (< maxx DCW_ROI_MINX)
        (> minx DCW_ROI_MAXX)
        (< maxy DCW_ROI_MINY)
        (> miny DCW_ROI_MAXY)
      )
    )
  )
)

(defun dcw-point-in-roi-p (pt / x y)
  (if (not (dcw-roi-active-p))
    T
    (progn
      (setq x (dcw-point-x pt))
      (setq y (dcw-point-y pt))
      (and
        x
        y
        (>= x DCW_ROI_MINX)
        (<= x DCW_ROI_MAXX)
        (>= y DCW_ROI_MINY)
        (<= y DCW_ROI_MAXY)
      )
    )
  )
)

(defun dcw-line-in-roi-p (data / p1 p2 x1 y1 x2 y2)
  (setq p1 (cdr (assoc 10 data)))
  (setq p2 (cdr (assoc 11 data)))
  (setq x1 (dcw-point-x p1))
  (setq y1 (dcw-point-y p1))
  (setq x2 (dcw-point-x p2))
  (setq y2 (dcw-point-y p2))
  (or
    (dcw-point-in-roi-p p1)
    (dcw-point-in-roi-p p2)
    (and
      x1 y1 x2 y2
      (dcw-bbox-in-roi-p (min x1 x2) (min y1 y2) (max x1 x2) (max y1 y2))
    )
  )
)

(defun dcw-circle-in-roi-p (data / center radius x y)
  (setq center (cdr (assoc 10 data)))
  (setq radius (cdr (assoc 40 data)))
  (setq x (dcw-point-x center))
  (setq y (dcw-point-y center))
  (or
    (dcw-point-in-roi-p center)
    (and
      x y (numberp radius)
      (dcw-bbox-in-roi-p (- x radius) (- y radius) (+ x radius) (+ y radius))
    )
  )
)

(defun dcw-lwvertices-in-roi-p (data / hit seen item pt x y minx miny maxx maxy)
  (setq hit nil)
  (setq seen nil)
  (foreach item data
    (if (= (car item) 10)
      (progn
        (setq pt (cdr item))
        (if (dcw-point-in-roi-p pt) (setq hit T))
        (setq x (dcw-point-x pt))
        (setq y (dcw-point-y pt))
        (if (and x y)
          (progn
            (if seen
              (progn
                (setq minx (min minx x))
                (setq miny (min miny y))
                (setq maxx (max maxx x))
                (setq maxy (max maxy y))
              )
              (progn
                (setq minx x)
                (setq miny y)
                (setq maxx x)
                (setq maxy y)
                (setq seen T)
              )
            )
          )
        )
      )
    )
  )
  (or hit (and seen (dcw-bbox-in-roi-p minx miny maxx maxy)))
)

(defun dcw-polyvertices-in-roi-p (entity / hit seen cursor data etype pt x y minx miny maxx maxy)
  (setq hit nil)
  (setq seen nil)
  (setq cursor (entnext entity))
  (while cursor
    (setq data (entget cursor))
    (setq etype (strcase (cdr (assoc 0 data))))
    (cond
      ((= etype "VERTEX")
        (progn
          (setq pt (cdr (assoc 10 data)))
          (if (dcw-point-in-roi-p pt) (setq hit T))
          (setq x (dcw-point-x pt))
          (setq y (dcw-point-y pt))
          (if (and x y)
            (progn
              (if seen
                (progn
                  (setq minx (min minx x))
                  (setq miny (min miny y))
                  (setq maxx (max maxx x))
                  (setq maxy (max maxy y))
                )
                (progn
                  (setq minx x)
                  (setq miny y)
                  (setq maxx x)
                  (setq maxy y)
                  (setq seen T)
                )
              )
            )
          )
        )
      )
      ((= etype "SEQEND") (setq cursor nil))
    )
    (if cursor (setq cursor (entnext cursor)))
  )
  (or hit (and seen (dcw-bbox-in-roi-p minx miny maxx maxy)))
)

(defun dcw-block-in-roi-p (entity data / res mn mx)
  ;; INSERT/TEXT/MTEXT bodies extend well beyond their assoc-10 insertion point, so
  ;; test the entity's true geometric extents (vla-getboundingbox). On any failure
  ;; fall back to the insertion-point test so extraction never breaks.
  (setq res
    (vl-catch-all-apply
      (function
        (lambda ( / obj minp maxp)
          (setq obj (vlax-ename->vla-object entity))
          (vla-getboundingbox obj 'minp 'maxp)
          (cons
            (vlax-safearray->list (vlax-variant-value minp))
            (vlax-safearray->list (vlax-variant-value maxp))
          )
        )
      )
      nil
    )
  )
  (if (or (vl-catch-all-error-p res) (null res))
    (dcw-point-in-roi-p (cdr (assoc 10 data)))
    (progn
      (setq mn (car res))
      (setq mx (cdr res))
      (if (and mn mx (numberp (car mn)) (numberp (cadr mn)) (numberp (car mx)) (numberp (cadr mx)))
        (dcw-bbox-in-roi-p (car mn) (cadr mn) (car mx) (cadr mx))
        (dcw-point-in-roi-p (cdr (assoc 10 data)))
      )
    )
  )
)

(defun dcw-entity-in-roi-p (entity data etype)
  (if (not (dcw-roi-active-p))
    T
    (cond
      ((= etype "LINE") (dcw-line-in-roi-p data))
      ((or (= etype "CIRCLE") (= etype "ARC")) (dcw-circle-in-roi-p data))
      ((= etype "LWPOLYLINE") (dcw-lwvertices-in-roi-p data))
      ((= etype "POLYLINE") (dcw-polyvertices-in-roi-p entity))
      ((or (= etype "TEXT") (= etype "MTEXT") (= etype "INSERT"))
        (dcw-block-in-roi-p entity data)
      )
      (T T)
    )
  )
)

(defun dcw-common-prefix (etype data)
  (strcat
    "{\"type\":" (dcw-json-string etype)
    ",\"layer\":" (dcw-json-string (dcw-layer data))
    ",\"handle\":" (dcw-json-string (dcw-handle data))
    ",\"geometry\":"
  )
)

(defun dcw-lwvertices-json (data / out first item)
  (setq out "[")
  (setq first T)
  (foreach item data
    (if (= (car item) 10)
      (progn
        (if first (setq first nil) (setq out (strcat out ",")))
        (setq out (strcat out "{\"point\":" (dcw-point-json (cdr item)) ",\"bulge\":0.0}"))
      )
    )
  )
  (strcat out "]")
)

(defun dcw-polyvertices-json (entity / out first cursor data etype)
  (setq out "[")
  (setq first T)
  (setq cursor (entnext entity))
  (while cursor
    (setq data (entget cursor))
    (setq etype (strcase (cdr (assoc 0 data))))
    (cond
      ((= etype "VERTEX")
        (progn
          (if first (setq first nil) (setq out (strcat out ",")))
          (setq out (strcat out "{\"point\":" (dcw-point-json (cdr (assoc 10 data))) ",\"bulge\":0.0}"))
        )
      )
      ((= etype "SEQEND") (setq cursor nil))
    )
    (if cursor (setq cursor (entnext cursor)))
  )
  (strcat out "]")
)

(defun dcw-mtext-content (data / out item code)
  (setq out "")
  (foreach item data
    (setq code (car item))
    (if (or (= code 1) (= code 3))
      (setq out (strcat out (cdr item)))
    )
  )
  out
)

(defun dcw-closed-p (data / flags)
  (setq flags (cdr (assoc 70 data)))
  (if (and (numberp flags) (= (logand flags 1) 1)) "true" "false")
)

(defun dcw-entity-json (entity / data etype geom blockname)
  (setq data (entget entity))
  (if (not (dcw-modelspace-p data))
    nil
    (progn
      (setq etype (strcase (cdr (assoc 0 data))))
      (if (not (dcw-entity-in-roi-p entity data etype))
        nil
        (cond
          ((= etype "LINE")
            (strcat
              (dcw-common-prefix "LINE" data)
              "{\"start\":" (dcw-point-json (cdr (assoc 10 data)))
              ",\"end\":" (dcw-point-json (cdr (assoc 11 data))) "}}"
            )
          )
          ((= etype "CIRCLE")
            (strcat
              (dcw-common-prefix "CIRCLE" data)
              "{\"center\":" (dcw-point-json (cdr (assoc 10 data)))
              ",\"radius\":" (dcw-num (cdr (assoc 40 data))) "}}"
            )
          )
          ((= etype "ARC")
            (strcat
              (dcw-common-prefix "ARC" data)
              "{\"center\":" (dcw-point-json (cdr (assoc 10 data)))
              ",\"radius\":" (dcw-num (cdr (assoc 40 data)))
              ",\"start_angle_deg\":" (dcw-num (dcw-angle-deg (cdr (assoc 50 data))))
              ",\"end_angle_deg\":" (dcw-num (dcw-angle-deg (cdr (assoc 51 data))))
              ",\"normal\":[0,0,1]}}"
            )
          )
          ((= etype "LWPOLYLINE")
            (strcat
              (dcw-common-prefix "LWPOLYLINE" data)
              "{\"vertices\":" (dcw-lwvertices-json data)
              ",\"closed\":" (dcw-closed-p data) "}}"
            )
          )
          ((= etype "POLYLINE")
            (strcat
              (dcw-common-prefix "POLYLINE" data)
              "{\"vertices\":" (dcw-polyvertices-json entity)
              ",\"closed\":" (dcw-closed-p data) "}}"
            )
          )
          ((= etype "TEXT")
            (strcat
              (dcw-common-prefix "TEXT" data)
              "{\"insert\":" (dcw-point-json (cdr (assoc 10 data)))
              ",\"height\":" (dcw-num (cdr (assoc 40 data)))
              ",\"text\":" (dcw-json-string (cdr (assoc 1 data)))
              ",\"rotation_deg\":" (dcw-num (dcw-angle-deg (cdr (assoc 50 data))))
              ",\"alignment\":\"0:0\"}}"
            )
          )
          ((= etype "MTEXT")
            (strcat
              (dcw-common-prefix "MTEXT" data)
              "{\"insert\":" (dcw-point-json (cdr (assoc 10 data)))
              ",\"height\":" (dcw-num (cdr (assoc 40 data)))
              ",\"raw_content\":" (dcw-json-string (dcw-mtext-content data))
              ",\"plain_text\":" (dcw-json-string (dcw-mtext-content data))
              ",\"rotation_deg\":" (dcw-num (dcw-angle-deg (cdr (assoc 50 data))))
              "}}"
            )
          )
          ((= etype "INSERT")
            (progn
              (setq blockname (cdr (assoc 2 data)))
              (strcat
                (dcw-common-prefix "INSERT" data)
                "{\"insert\":" (dcw-point-json (cdr (assoc 10 data)))
                ",\"scale\":[" (dcw-num (cdr (assoc 41 data))) "," (dcw-num (cdr (assoc 42 data))) "," (dcw-num (cdr (assoc 43 data))) "]"
                ",\"rotation_deg\":" (dcw-num (dcw-angle-deg (cdr (assoc 50 data))))
                ",\"block_name\":" (dcw-json-string blockname)
                ",\"attributes\":[]}}"
              )
            )
          )
          (T nil)
        )
      )
    )
  )
)

(defun dcw-write-layers (f / rec first)
  (write-line "\"layers\":[" f)
  (setq first T)
  (setq rec (tblnext "LAYER" T))
  (while rec
    (if first (setq first nil) (princ "," f))
    (write-line (strcat "{\"name\":" (dcw-json-string (cdr (assoc 2 rec))) "}") f)
    (setq rec (tblnext "LAYER"))
  )
  (write-line "]," f)
)

(defun dcw-write-entities (f / entity json first count maxcount truncated)
  (write-line "\"entities\":[" f)
  (setq first T)
  (setq count 0)
  (setq truncated nil)
  (setq maxcount (if (and (boundp 'DCW_MAX_ENTITIES) (numberp DCW_MAX_ENTITIES)) DCW_MAX_ENTITIES 200000))
  ;; Always walk the full model-space table via entnext and let dcw-entity-json's
  ;; per-entity ROI test decide membership. Using ssget "_C" here skipped entities
  ;; on frozen/off layers and outside the current space, and could return nil
  ;; (zero entities) in a headless session -- diverging from this walk.
  (setq entity (entnext))
  (while (and entity (< count maxcount))
    (setq json (dcw-entity-json entity))
    (if json
      (progn
        (if first (setq first nil) (princ "," f))
        (write-line json f)
        (setq count (1+ count))
      )
    )
    (setq entity (entnext entity))
  )
  (if (and entity (>= count maxcount)) (setq truncated T))
  (write-line "]," f)
  (write-line (strcat "\"metadata\":{\"autocad_dwg_json_bridge\":{\"entity_count\":" (itoa count) ",\"truncated\":" (if truncated "true" "false") "}}") f)
)

(defun DCW_EXPORT (/ f acadver)
  (setq acadver (if (and (boundp 'DCW_ACADVER) DCW_ACADVER) DCW_ACADVER ""))
  (setq f (open DCW_OUT "w"))
  (write-line "{" f)
  (write-line (strcat "\"header\":{\"$ACADVER\":" (dcw-json-string acadver) "},") f)
  (dcw-write-layers f)
  (dcw-write-entities f)
  (write-line "}" f)
  (close f)
  (princ)
)
"""


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
    parser.add_argument("--accoreconsole", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--max-entities", type=int)
    parser.add_argument("--keep-temp", action="store_true")
    return parser.parse_args(argv)


class BridgeError(RuntimeError):
    """User-facing bridge failure."""


def run_bridge(args: argparse.Namespace) -> dict[str, Any]:
    input_path = Path(args.input).resolve()
    if not input_path.exists() or not input_path.is_file():
        raise BridgeError(f"input DWG does not exist: {input_path}")
    accoreconsole = resolve_accoreconsole(args.accoreconsole)
    if not accoreconsole:
        raise BridgeError(
            "AcCoreConsole.exe was not found. Set "
            f"{ACCORECONSOLE_ENV} or pass --accoreconsole."
        )
    max_entities = _max_entities(args.max_entities)
    timeout_seconds = max(1.0, float(args.timeout_seconds or DEFAULT_TIMEOUT_SECONDS))

    with _bridge_workspace(keep=bool(args.keep_temp)) as work_dir:
        lisp_path = work_dir / "extract_dwg_json.lsp"
        script_path = work_dir / "extract_dwg_json.scr"
        output_path = work_dir / "drawing.json"
        lisp_path.write_text(LISP_EXTRACTOR.strip() + "\n", encoding="ascii")
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
        command = [
            str(accoreconsole),
            "/i",
            str(input_path),
            "/s",
            str(script_path),
            "/l",
            "en-US",
            "/readonly",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise BridgeError(f"AcCoreConsole timed out after {timeout_seconds:g}s: {command}") from exc

        if not output_path.exists():
            stdout = _decode_console_bytes(completed.stdout)
            stderr = _decode_console_bytes(completed.stderr)
            detail = (stderr or stdout or "no console output")[:1200]
            raise BridgeError(
                "AcCoreConsole did not produce DWG JSON "
                f"(exit_code={completed.returncode}): {detail}"
            )
        drawing = _load_json_with_fallback(output_path)

    if not isinstance(drawing, dict):
        raise BridgeError("AcCoreConsole extractor output must be a JSON object.")
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
        "accoreconsole_path": str(accoreconsole),
        "accoreconsole_exit_code": completed.returncode,
        "max_entities": max_entities,
    }
    metadata.setdefault("autocad_dwg_json_bridge", {})
    if isinstance(metadata["autocad_dwg_json_bridge"], dict):
        metadata["autocad_dwg_json_bridge"].update(
            {
                "bridge": BRIDGE_NAME,
                "bridge_version": BRIDGE_VERSION,
                "acadver": str(args.acadver).upper(),
                "accoreconsole_path": str(accoreconsole),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "drawing": drawing,
    }


def resolve_accoreconsole(explicit: Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit)
    env_path = os.environ.get(ACCORECONSOLE_ENV)
    if env_path:
        candidates.append(Path(env_path))
    which = shutil.which("accoreconsole.exe") or shutil.which("AcCoreConsole.exe")
    if which:
        candidates.append(Path(which))
    candidates.extend(Path(item) for item in DEFAULT_ACCORECONSOLE_CANDIDATES)
    candidates.extend(_autodesk_accoreconsole_candidates())
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


def _autodesk_accoreconsole_candidates() -> list[Path]:
    roots = (Path(r"C:\Program Files\Autodesk"), Path(r"C:\Program Files (x86)\Autodesk"))
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        try:
            found.extend(root.glob("*/accoreconsole.exe"))
        except OSError:
            continue
    return sorted(found, key=lambda item: str(item), reverse=True)


@contextmanager
def _bridge_workspace(*, keep: bool) -> Iterator[Path]:
    if keep:
        path = Path(tempfile.mkdtemp(prefix="dcw-autocad-bridge-"))
        yield path
        return
    with tempfile.TemporaryDirectory(prefix="dcw-autocad-bridge-") as raw:
        yield Path(raw)


def _lisp_path(path: Path) -> str:
    return _escape_lisp_string(str(path.resolve()).replace("\\", "/"))


def _escape_lisp_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _decode_console_bytes(data: bytes) -> str:
    if not data:
        return ""
    encodings = ("utf-16le", "utf-8-sig", "mbcs", "cp949", "latin-1") if b"\x00" in data[:80] else ("utf-8-sig", "mbcs", "cp949", "latin-1")
    for encoding in encodings:
        try:
            return data.decode(encoding, errors="replace")
        except LookupError:
            continue
    return data.decode(errors="replace")


def _load_json_with_fallback(path: Path) -> Any:
    data = path.read_bytes()
    errors: list[str] = []
    for encoding in ("utf-8-sig", "mbcs", "cp949", "latin-1"):
        try:
            return json.loads(data.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError, LookupError) as exc:
            errors.append(f"{encoding}: {exc}")
    raise BridgeError("extractor output is not valid JSON: " + "; ".join(errors))


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
