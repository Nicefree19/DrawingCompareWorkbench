"""Build a local-only real-world DWG manifest from a sample folder.

The generated manifest is intentionally source-control safe: it references
local DWG paths and file facts, but it does not copy customer/project drawings
into the repository.  The output is compatible with
``scripts/validate_real_world_dwg_samples.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path("build/reports/local-dwg-samples.manifest.json")
SUPPORTED_NATIVE_CODES = {"AC1015"}
REVISION_SUFFIX_RE = re.compile(r"(?P<base>.+?)(?:[\s_.-]*(?:r|rev|revision)[\s_.-]*(?P<rev>\d+))$", re.IGNORECASE)
BEFORE_DIR_NAMES = {"before", "old", "base", "source_a", "a"}
AFTER_DIR_NAMES = {"after", "new", "revised", "revision", "source_b", "b"}
DEFAULT_EXCLUDED_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "out",
}

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.comparison.dwg_importer import DwgImportError, DwgVersionDetector  # noqa: E402


def build_manifest(
    source_root: Path,
    *,
    cache_dir: Path | None = None,
    include_pairs: bool = True,
    max_samples: int | None = None,
    include_generated: bool = False,
) -> dict[str, Any]:
    source_root = Path(source_root).resolve()
    samples = _samples(source_root, max_samples=max_samples, include_generated=include_generated)
    manifest: dict[str, Any] = {
        "schema_version": "cad-real-world-local/v1",
        "generated_at": datetime.now().isoformat(),
        "source_policy": "local DWG files are referenced, not copied",
        "source_root": str(source_root),
        "cache_dir": str(Path(cache_dir).resolve()) if cache_dir else "",
        "include_generated": bool(include_generated),
        "excluded_dir_names": [] if include_generated else sorted(DEFAULT_EXCLUDED_DIR_NAMES),
        "samples": samples,
        "pairs": _pairs(samples) if include_pairs else [],
    }
    return manifest


def _samples(source_root: Path, *, max_samples: int | None, include_generated: bool) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, path in enumerate(_iter_dwg_files(source_root, include_generated=include_generated), start=1):
        if max_samples is not None and len(output) >= max(0, int(max_samples)):
            break
        relative_path = path.relative_to(source_root) if source_root.is_dir() else Path(path.name)
        output.append(_sample(path, relative_path=relative_path, index=index))
    return output


def _iter_dwg_files(source_root: Path, *, include_generated: bool) -> Iterable[Path]:
    if source_root.is_file() and source_root.suffix.lower() == ".dwg":
        yield source_root.resolve()
        return
    if not source_root.is_dir():
        return
    seen: set[Path] = set()
    for dirpath, dirnames, filenames in os.walk(source_root):
        current_dir = Path(dirpath)
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if include_generated or not _is_excluded_generated_dir(current_dir / name, source_root=source_root)
        ]
        for filename in sorted(filenames):
            path = current_dir / filename
            if path.suffix.lower() != ".dwg":
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield resolved


def _is_excluded_generated_dir(path: Path, *, source_root: Path) -> bool:
    try:
        if path.resolve() == source_root.resolve():
            return False
    except OSError:
        pass
    return path.name.casefold() in DEFAULT_EXCLUDED_DIR_NAMES


def _sample(path: Path, *, relative_path: Path, index: int) -> dict[str, Any]:
    size = path.stat().st_size
    detected: dict[str, Any]
    try:
        version = DwgVersionDetector.detect_file(path)
        detected = version.to_dict()
    except DwgImportError as exc:
        detected = {
            "code": "unknown",
            "family": "",
            "release": "",
            "supported": False,
            "error_code": exc.code,
            "error": str(exc),
        }
    except OSError as exc:
        detected = {
            "code": "unknown",
            "family": "",
            "release": "",
            "supported": False,
            "error": str(exc),
        }
    return {
        "id": f"dwg-{index:04d}",
        "path": relative_path.as_posix(),
        "format": "dwg",
        "expected_version": str(detected.get("code") or "unknown"),
        "expected_size_bytes": size,
        "detected_family": str(detected.get("family") or ""),
        "detected_release": str(detected.get("release") or ""),
        "detected_supported": bool(detected.get("supported")),
        "pair_key": _pair_key(relative_path),
        "revision_index": _revision_index(relative_path),
    }


def _pairs(samples: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        grouped.setdefault(str(sample.get("pair_key") or ""), []).append(sample)

    pairs: list[dict[str, Any]] = []
    for key in sorted(grouped):
        items = sorted(grouped[key], key=lambda item: (int(item.get("revision_index") or 0), str(item.get("path"))))
        base = [item for item in items if int(item.get("revision_index") or 0) == 0]
        revisions = [item for item in items if int(item.get("revision_index") or 0) > 0]
        if len(base) != 1 or len(revisions) != 1:
            continue
        old = base[0]
        new = revisions[0]
        pairs.append(
            {
                "id": f"pair-{len(pairs) + 1:04d}",
                "old_sample": old["id"],
                "new_sample": new["id"],
                "pair_key": key,
                "current_import_expectation": _import_expectation(old, new),
            }
        )
    return pairs


def _pair_key(relative_path: Path) -> str:
    stem = _base_stem(relative_path.stem)
    parent = _pair_parent(relative_path)
    return f"{parent}/{stem}".strip("/")


def _revision_index(relative_path: Path) -> int:
    match = REVISION_SUFFIX_RE.match(relative_path.stem)
    if match:
        try:
            return int(match.group("rev") or "0")
        except ValueError:
            return 0
    return _side_dir_revision_index(relative_path)


def _base_stem(stem: str) -> str:
    match = REVISION_SUFFIX_RE.match(stem)
    value = match.group("base") if match else stem
    return re.sub(r"\s+", " ", value).strip().casefold()


def _pair_parent(relative_path: Path) -> str:
    parts = []
    for part in relative_path.parent.parts:
        if part == ".":
            continue
        if _side_dir_revision_index(Path(part) / relative_path.name) in {0, 1} and _is_side_dir(part):
            continue
        parts.append(part)
    return Path(*parts).as_posix() if parts else ""


def _side_dir_revision_index(relative_path: Path) -> int:
    for part in reversed(relative_path.parent.parts):
        normalized = _normalize_side_dir(part)
        if normalized in BEFORE_DIR_NAMES:
            return 0
        if normalized in AFTER_DIR_NAMES:
            return 1
    return 0


def _is_side_dir(value: str) -> bool:
    normalized = _normalize_side_dir(value)
    return normalized in BEFORE_DIR_NAMES or normalized in AFTER_DIR_NAMES


def _normalize_side_dir(value: str) -> str:
    return re.sub(r"[\s_.-]+", "_", value.strip().casefold())


def _import_expectation(old: dict[str, Any], new: dict[str, Any]) -> str:
    versions = {str(old.get("expected_version") or ""), str(new.get("expected_version") or "")}
    if versions and versions <= SUPPORTED_NATIVE_CODES:
        return "native_cleanroom_ac1015_preview"
    return "unsupported_version_until_native_reader_expands_beyond_AC1015"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path, help="Local DWG file or folder")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--no-pairs", action="store_true")
    parser.add_argument(
        "--include-generated",
        action="store_true",
        help="Include generated repository output directories such as build/ and out/.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = build_manifest(
        args.source_root,
        cache_dir=args.cache_dir,
        include_pairs=not args.no_pairs,
        max_samples=args.max_samples,
        include_generated=bool(args.include_generated),
    )
    _write_json(args.manifest, manifest)
    print(
        "real-world DWG manifest: "
        f"samples={len(manifest['samples'])} pairs={len(manifest['pairs'])} "
        f"path={Path(args.manifest).resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
