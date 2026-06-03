"""Emit DwgAdapterDrawing JSON from a DWG through an explicit DXF converter.

This is a bridge helper for approved external wrappers. It is intentionally
fallback-marked: output from this tool must not be counted as native DWG
evidence because it reads the converter-produced DXF, not DWG sections.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.comparison.dxf_importer import DxfImporter  # noqa: E402
from src.services.comparison.user_dwg_converter import UserDwgConverter  # noqa: E402


SUPPORTED_RAW_TYPES = {
    "arc": "ARC",
    "block_reference": "INSERT",
    "circle": "CIRCLE",
    "line": "LINE",
    "mtext": "MTEXT",
    "polyline": "LWPOLYLINE",
    "text": "TEXT",
}

_CONVERTER_ARGS_HELP = (
    "JSON array of converter argument templates. Templates may use "
    "{input}, {output_dir}, {output}, and {stem}."
)


def convert_dwg_to_adapter_json(
    dwg_path: Path,
    acadver: str,
    *,
    converter_command: str,
    converter_args_json: str = "[]",
    timeout_seconds: float = 120.0,
    max_entities: int = 500_000,
    max_dxf_tokens: int = 12_000_000,
    keep_converted_dxf: bool = False,
) -> dict[str, Any]:
    args_template = _parse_args_template(converter_args_json)
    converter = UserDwgConverter(converter_command, args_template=args_template)
    converted_dxf = converter.convert(dwg_path, timeout=int(timeout_seconds))
    try:
        canonical = DxfImporter(max_entities=max_entities, max_tokens=max_dxf_tokens).import_file(converted_dxf)
        return _adapter_payload(
            dwg_path,
            acadver,
            canonical,
            converted_dxf=converted_dxf,
            converter_command=converter_command,
            converter_args_template=args_template,
            keep_converted_dxf=keep_converted_dxf,
        )
    finally:
        if not keep_converted_dxf:
            converter.cleanup_converted_output(converted_dxf)


def _adapter_payload(
    dwg_path: Path,
    acadver: str,
    canonical: dict[str, Any],
    *,
    converted_dxf: Path,
    converter_command: str,
    converter_args_template: Sequence[str],
    keep_converted_dxf: bool,
) -> dict[str, Any]:
    layers = canonical.get("layers") or []
    layer_names_by_id = {
        str(layer.get("id") or ""): str(layer.get("name") or "0")
        for layer in layers
        if isinstance(layer, dict)
    }
    bridge_metadata = {
        "evidence_scope": "converted_dxf_bridge",
        "uses_converted_dxf": True,
        "source_dwg_path": str(dwg_path),
        "converted_dxf_path": str(converted_dxf),
        "converted_dxf_retained": bool(keep_converted_dxf),
        "converter_command": str(converter_command),
        "converter_args_template": list(converter_args_template),
        "acadver": acadver,
    }
    return {
        "schema_version": "dwg-adapter-drawing-json/v1",
        "drawing": {
            "header": {"$ACADVER": acadver},
            "layers": [_adapter_layer(layer) for layer in layers if isinstance(layer, dict)],
            "entities": [
                _adapter_entity(entity, layer_names_by_id)
                for entity in canonical.get("entities") or []
                if isinstance(entity, dict)
            ],
            "metadata": {
                "source_path": str(dwg_path),
                "commercial_dwg_json_bridge": bridge_metadata,
                "source_import_report": _compact_import_report(canonical.get("import_report") or {}),
            },
        },
    }


def _adapter_layer(layer: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(layer.get("name") or "0"),
        "color": layer.get("color"),
        "linetype": layer.get("linetype"),
        "lineweight": layer.get("lineweight"),
    }


def _adapter_entity(entity: dict[str, Any], layer_names_by_id: dict[str, str]) -> dict[str, Any]:
    canonical_type = str(entity.get("type") or "").strip().lower()
    source = entity.get("source") or {}
    layer_name = layer_names_by_id.get(str(entity.get("layer_id") or ""), "0")
    raw_type = SUPPORTED_RAW_TYPES.get(canonical_type, str(source.get("raw_type") or canonical_type).upper())
    return {
        "type": raw_type,
        "geometry": dict(entity.get("geometry") or {}),
        "layer": layer_name,
        "handle": source.get("handle"),
        "owner_handle": source.get("owner_handle"),
        "style": dict(entity.get("style") or {}),
        "layout_name": entity.get("layout_name") or source.get("layout_name"),
        "attributes": list((entity.get("geometry") or {}).get("attributes") or []),
    }


def _compact_import_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report.get("status"),
        "warning_count": len(report.get("warnings") or []),
        "unsupported_entities": report.get("unsupported_entities") or [],
        "stats": report.get("stats") or {},
    }


def _parse_args_template(raw: str) -> tuple[str, ...]:
    if not raw:
        return ()
    parsed = json.loads(raw)
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError("--converter-args-json must be a JSON array of strings.")
    return tuple(parsed)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dwg", type=Path)
    parser.add_argument("acadver")
    parser.add_argument("--converter-command", required=True)
    parser.add_argument("--converter-args-json", default="[]", help=_CONVERTER_ARGS_HELP)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--max-entities", type=int, default=500_000)
    parser.add_argument("--max-dxf-tokens", type=int, default=12_000_000)
    parser.add_argument("--keep-converted-dxf", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    payload = convert_dwg_to_adapter_json(
        args.dwg,
        args.acadver,
        converter_command=args.converter_command,
        converter_args_json=args.converter_args_json,
        timeout_seconds=args.timeout_seconds,
        max_entities=args.max_entities,
        max_dxf_tokens=args.max_dxf_tokens,
        keep_converted_dxf=args.keep_converted_dxf,
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
