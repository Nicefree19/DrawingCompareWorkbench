"""Validate a native CAD bridge command against the scene-pack contract."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.comparison.dwg_importer import DwgVersionDetector, DwgVersionInfo  # noqa: E402
from src.services.comparison.native_cad_bridge import NativeCadBridgeRunner  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--bridge-command", required=True)
    parser.add_argument("--bridge-args-json", default="")
    parser.add_argument("--acadver", default="")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    args = parser.parse_args(argv)

    version = _version(args.input, args.acadver)
    runner = NativeCadBridgeRunner(
        command=args.bridge_command,
        args_template=_args_template(args.bridge_args_json),
        timeout_seconds=args.timeout_seconds,
        adapter_id="native-cad-contract-validator",
    )
    result = runner.run(args.input, version)
    if not result.ok or result.scene_pack is None:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "failure": result.failure.to_dict() if result.failure else None,
                    "runner": runner.diagnostics(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    overview = result.scene_pack.overview_lod0_payload()
    print(
        json.dumps(
            {
                "status": "PASS",
                "schema_version": result.scene_pack.schema_version,
                "primitive_count": overview["primitive_count"],
                "world_bbox": overview["world_bbox"],
                "drawing_entity_count": len((result.drawing or {}).get("model_space") or []),
                "runner": runner.diagnostics(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _version(path: Path, override: str) -> DwgVersionInfo:
    if override:
        detected = DwgVersionDetector.detect_bytes(override.encode("ascii")[:6].ljust(6, b"0"))
        return detected
    return DwgVersionDetector.detect_file(path)


def _args_template(raw: str) -> tuple[str, ...]:
    if not raw:
        return ("{input}", "{acadver}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--bridge-args-json must be a JSON string array: {exc}") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise SystemExit("--bridge-args-json must be a JSON string array")
    return tuple(parsed)


if __name__ == "__main__":
    raise SystemExit(main())
