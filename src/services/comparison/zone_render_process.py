"""JSONL subprocess entrypoint for selected-zone crop rendering."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .zone_render_service import RenderJob, WorldWindow, render_zone_pair


def _world_window_from_payload(payload: dict[str, Any]) -> WorldWindow:
    window = payload.get("world_window") or {}
    return WorldWindow(
        xmin=float(window["xmin"]),
        ymin=float(window["ymin"]),
        xmax=float(window["xmax"]),
        ymax=float(window["ymax"]),
    )


def _job_from_request(request: dict[str, Any]) -> RenderJob:
    return RenderJob(
        pair_uuid=str(request.get("pair_uuid") or ""),
        zone_id=str(request.get("zone_id") or ""),
        request_id=str(request.get("request_id") or ""),
        source_before=Path(str(request["source_before"])),
        source_after=Path(str(request["source_after"])),
        world_window=_world_window_from_payload(request),
        cache_root=Path(str(request["cache_root"])),
        dxf_cache_dir=Path(str(request["dxf_cache_dir"])),
        output_width=int(request.get("output_width") or 1600),
        output_height=int(request.get("output_height") or 900),
        renderer_backend=str(request.get("renderer_backend") or "ezdxf-matplotlib-zone"),
        font_manifest_hash=str(request.get("font_manifest_hash") or "unknown"),
        render_environment_hash=str(request.get("render_environment_hash") or ""),
        before_background_image=str(request.get("before_background_image") or ""),
        after_background_image=str(request.get("after_background_image") or ""),
        before_background_transform=request.get("before_background_transform") if isinstance(request.get("before_background_transform"), dict) else None,
        after_background_transform=request.get("after_background_transform") if isinstance(request.get("after_background_transform"), dict) else None,
    )


def _write_response(payload: dict[str, Any]) -> None:
    # Sanitize lone surrogate codepoints from Korean Windows paths before
    # JSON serialization. Without this, the parent workbench surfaces
    # "'utf-8' codec can't encode character ... surrogates not allowed"
    # as "선택 구역 렌더 실패 - 상대 위치 표시를 유지합니다".
    from .safe_unicode import safe_unicode

    sys.stdout.write(json.dumps(safe_unicode(payload), ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    _write_response({"ok": True, "event": "ready"})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            if request.get("command") == "shutdown":
                _write_response({"ok": True, "command": "shutdown"})
                return 0
            job = _job_from_request(request)
            result = render_zone_pair(job).to_dict()
            _write_response(
                {
                    "ok": True,
                    "request_id": job.request_id,
                    "pair_uuid": job.pair_uuid,
                    "zone_id": job.zone_id,
                    "result": result,
                }
            )
        except Exception as exc:
            error_type = type(exc).__name__
            _write_response(
                {
                    "ok": False,
                    "request_id": str(locals().get("request", {}).get("request_id", "")),
                    "pair_uuid": str(locals().get("request", {}).get("pair_uuid", "")),
                    "zone_id": str(locals().get("request", {}).get("zone_id", "")),
                    "error": str(exc),
                    "error_type": error_type,
                    "visual_fidelity": "relative_overlay",
                    "render_lifecycle": "failed",
                    "reason_code": error_type,
                    "fallback_reason_code": error_type,
                }
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
