# -*- coding: utf-8 -*-
"""Inventory DWG native-support Phase 0 corpus readiness.

This script does not import or compare DWG geometry. It records cheap version
signals plus converted-DXF fallback readiness so AC1032/native reader planning
can start from measured corpus facts.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.comparison.dwg_dxf_fallback import resolve_dwg_dxf_fallback_pair
from src.services.comparison.dwg_importer import DwgImportError, DwgVersionDetector


SCHEMA_VERSION = 1


def inventory_dwg_native_phase0(
    roots: Sequence[Path],
    *,
    out: Path | None = None,
    max_dwg_samples: int = 200,
) -> dict[str, Any]:
    resolved_roots = [Path(root).resolve() for root in roots]
    dwg_files = list(_iter_dwg_files(resolved_roots, limit=max(1, int(max_dwg_samples))))
    version_items = [_version_item(path) for path in dwg_files]
    fallback_items = [_folder_fallback_item(root) for root in resolved_roots if root.exists() and root.is_dir()]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(),
        "roots": [str(root) for root in resolved_roots],
        "dwg_sample_limit": max(1, int(max_dwg_samples)),
        "dwg_count": len(version_items),
        "version_counts": _version_counts(version_items),
        "unsupported_count": sum(1 for item in version_items if not item.get("supported")),
        "converted_dxf_fallback_ready_count": sum(1 for item in fallback_items if item.get("fallback_used")),
        "dwg_files": version_items,
        "folder_fallbacks": fallback_items,
    }
    if out is not None:
        out = Path(out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _iter_dwg_files(roots: Iterable[Path], *, limit: int) -> Iterable[Path]:
    emitted = 0
    seen: set[Path] = set()
    for root in roots:
        if root.is_file() and root.suffix.lower() == ".dwg":
            iterator: Iterable[Path] = [root]
        elif root.is_dir():
            iterator = root.rglob("*.dwg")
        else:
            continue
        for item in iterator:
            resolved = item.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield resolved
            emitted += 1
            if emitted >= limit:
                return


def _version_item(path: Path) -> dict[str, Any]:
    try:
        version = DwgVersionDetector.detect_file(path).to_dict()
    except DwgImportError as exc:
        version = {
            "code": "",
            "family": "",
            "release": "",
            "supported": False,
            "error_code": exc.code,
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        version = {
            "code": "",
            "family": "",
            "release": "",
            "supported": False,
            "error": str(exc),
        }
    return {"path": str(path), **version}


def _version_counts(items: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        code = str(item.get("code") or item.get("error_code") or "unknown")
        counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items()))


def _folder_fallback_item(root: Path) -> dict[str, Any]:
    resolution = resolve_dwg_dxf_fallback_pair(root, root)
    payload = resolution.to_dict()
    diagnostics = payload.get("diagnostics")
    if isinstance(diagnostics, dict):
        payload["fallback_kind"] = diagnostics.get("fallback_kind", "")
        payload["fallback_counts"] = diagnostics.get("fallback_counts", {})
    payload["root"] = str(root.resolve())
    payload["fallback_used"] = bool(payload.pop("used", False))
    return payload


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path, help="DWG corpus file/folder roots")
    parser.add_argument("--out", type=Path, help="Optional JSON output path")
    parser.add_argument("--max-dwg-samples", type=int, default=200)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    payload = inventory_dwg_native_phase0(
        args.roots,
        out=args.out,
        max_dwg_samples=args.max_dwg_samples,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
