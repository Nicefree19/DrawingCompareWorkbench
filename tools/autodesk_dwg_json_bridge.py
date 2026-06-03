"""Autodesk managed-runtime DWG-to-adapter-JSON bridge.

This wrapper is for explicit local/internal commercial-native validation only.
It compiles and runs ``tools/autodesk_dwg_json_extractor.cs`` against an
installed Autodesk DWG runtime such as DWG TrueView or AutoCAD.  The extractor
opens the original DWG through Autodesk's managed DatabaseServices API and
emits the ``DwgAdapterDrawing`` JSON contract expected by
``src.services.comparison.commercial_dwg_json_adapter``.

It does not convert DWG to DXF and does not run in the default customer path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence


SCHEMA_VERSION = "dwg-adapter-drawing-json/v1"
BRIDGE_NAME = "autodesk-managed-standalone-dwg-json-bridge"
BRIDGE_VERSION = "1"
AUTODESK_ROOT_ENV = "DRAWING_COMPARE_AUTODESK_DWG_RUNTIME_ROOT"
CSC_ENV = "DRAWING_COMPARE_CSC_PATH"
MAX_ENTITIES_ENV = "DRAWING_COMPARE_AUTODESK_BRIDGE_MAX_ENTITIES"
DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_MAX_ENTITIES = 200_000
MANAGED_DLLS = ("accoremgd.dll", "acdbmgd.dll", "acmgd.dll")
DEFAULT_AUTODESK_ROOT_CANDIDATES = (
    r"C:\Program Files\Autodesk\DWG TrueView 2020 - English",
    r"C:\Program Files\Autodesk\AutoCAD 2017",
)
SOURCE_PATH = Path(__file__).resolve().with_name("autodesk_dwg_json_extractor.cs")
DEFAULT_BUILD_ROOT = Path("build/autodesk-dwg-json-bridge")


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
    parser.add_argument("--autodesk-root", type=Path)
    parser.add_argument("--csc", type=Path)
    parser.add_argument("--build-root", type=Path, default=DEFAULT_BUILD_ROOT)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--max-entities", type=int)
    return parser.parse_args(argv)


class BridgeError(RuntimeError):
    """User-facing bridge failure."""


def run_bridge(args: argparse.Namespace) -> dict[str, Any]:
    input_path = Path(args.input).resolve()
    if not input_path.exists() or not input_path.is_file():
        raise BridgeError(f"input DWG does not exist: {input_path}")
    autodesk_root = resolve_autodesk_root(args.autodesk_root)
    if not autodesk_root:
        raise BridgeError(
            "Autodesk DWG runtime root was not found. Set "
            f"{AUTODESK_ROOT_ENV} or pass --autodesk-root."
        )
    csc = resolve_csc(args.csc)
    if not csc:
        raise BridgeError(f"C# compiler was not found. Set {CSC_ENV} or pass --csc.")

    build_root = Path(args.build_root).resolve()
    extractor = build_extractor(autodesk_root=autodesk_root, csc=csc, build_root=build_root)
    max_entities = _max_entities(args.max_entities)
    timeout_seconds = max(1.0, float(args.timeout_seconds or DEFAULT_TIMEOUT_SECONDS))

    with tempfile.TemporaryDirectory(prefix="dcw-autodesk-bridge-") as raw:
        output_path = Path(raw) / "drawing.json"
        command = [
            str(extractor),
            str(input_path),
            str(args.acadver).upper(),
            str(output_path),
            str(max_entities),
        ]
        env = os.environ.copy()
        env["PATH"] = str(autodesk_root) + os.pathsep + env.get("PATH", "")
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise BridgeError(f"Autodesk DWG JSON extractor timed out after {timeout_seconds:g}s: {command}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "no extractor output")[:1200]
            raise BridgeError(
                "Autodesk DWG JSON extractor failed "
                f"(exit_code={completed.returncode}): {detail}"
            )
        if not output_path.exists():
            detail = (completed.stderr or completed.stdout or "no extractor output")[:1200]
            raise BridgeError("Autodesk DWG JSON extractor did not produce JSON: " + detail)
        drawing = _load_json_with_fallback(output_path)

    if not isinstance(drawing, dict):
        raise BridgeError("Autodesk DWG JSON extractor output must be a JSON object.")
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
        "autodesk_root": str(autodesk_root),
        "extractor_exe": str(extractor),
        "extractor_exit_code": completed.returncode,
        "csc_path": str(csc),
        "max_entities": max_entities,
    }
    metadata.setdefault("autodesk_dwg_json_bridge", {})
    if isinstance(metadata["autodesk_dwg_json_bridge"], dict):
        metadata["autodesk_dwg_json_bridge"].update(
            {
                "bridge": BRIDGE_NAME,
                "bridge_version": BRIDGE_VERSION,
                "acadver": str(args.acadver).upper(),
                "autodesk_root": str(autodesk_root),
                "extractor_exe": str(extractor),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "drawing": drawing,
    }


def build_extractor(*, autodesk_root: Path, csc: Path, build_root: Path) -> Path:
    if not SOURCE_PATH.exists():
        raise BridgeError(f"extractor source is missing: {SOURCE_PATH}")
    source = SOURCE_PATH.read_bytes()
    fingerprint = hashlib.sha256()
    fingerprint.update(source)
    fingerprint.update(str(autodesk_root).lower().encode("utf-8"))
    for dll in MANAGED_DLLS:
        path = autodesk_root / dll
        if not path.exists():
            raise BridgeError(f"Autodesk managed DLL is missing: {path}")
        stat = path.stat()
        fingerprint.update(dll.encode("ascii"))
        fingerprint.update(str(stat.st_size).encode("ascii"))
        fingerprint.update(str(int(stat.st_mtime)).encode("ascii"))
    build_dir = build_root / (_slug(autodesk_root.name) + "-" + fingerprint.hexdigest()[:16])
    extractor = build_dir / "DcwAutodeskDwgJsonExtractor.exe"
    if extractor.exists() and all((build_dir / dll).exists() for dll in MANAGED_DLLS):
        return extractor

    build_dir.mkdir(parents=True, exist_ok=True)
    for dll in MANAGED_DLLS:
        shutil.copy2(autodesk_root / dll, build_dir / dll)
    command = [
        str(csc),
        "/nologo",
        "/target:exe",
        "/platform:x64",
        f"/out:{extractor}",
        *[f"/reference:{autodesk_root / dll}" for dll in MANAGED_DLLS],
        str(SOURCE_PATH),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "no compiler output")[:2000]
        raise BridgeError(f"Autodesk DWG extractor compile failed: {detail}")
    return extractor


def resolve_autodesk_root(explicit: Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit)
    env_path = os.environ.get(AUTODESK_ROOT_ENV)
    if env_path:
        candidates.append(Path(env_path))
    candidates.extend(Path(item) for item in DEFAULT_AUTODESK_ROOT_CANDIDATES)
    candidates.extend(_autodesk_runtime_candidates())
    return _first_runtime_root(candidates)


def resolve_csc(explicit: Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit)
    env_path = os.environ.get(CSC_ENV)
    if env_path:
        candidates.append(Path(env_path))
    which = shutil.which("csc.exe") or shutil.which("csc")
    if which:
        candidates.append(Path(which))
    windir = Path(os.environ.get("WINDIR") or r"C:\Windows")
    candidates.extend(
        [
            windir / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe",
            windir / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe",
        ]
    )
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


def _autodesk_runtime_candidates() -> list[Path]:
    roots = (Path(r"C:\Program Files\Autodesk"), Path(r"C:\Program Files (x86)\Autodesk"))
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        try:
            found.extend(path.parent for path in root.glob("*/acdbmgd.dll"))
        except OSError:
            continue
    return sorted(found, key=lambda item: _runtime_sort_key(item))


def _runtime_sort_key(path: Path) -> tuple[int, str]:
    text = str(path).lower()
    trueview_rank = 0 if "trueview" in text else 1
    year_match = re.search(r"(20\d{2})", text)
    year_rank = -int(year_match.group(1)) if year_match else 0
    return (trueview_rank, year_rank, text)


def _first_runtime_root(candidates: Sequence[Path]) -> Path | None:
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if not candidate.exists() or not candidate.is_dir():
            continue
        if all((candidate / dll).exists() for dll in MANAGED_DLLS):
            return candidate.resolve()
    return None


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
    raise BridgeError("failed to decode Autodesk DWG JSON extractor output: " + "; ".join(errors[:3]))


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-").lower()
    return slug or "autodesk-runtime"


def _emit_json(payload: dict[str, Any]) -> None:
    data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(data)
        return
    sys.stdout.write(data.decode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
