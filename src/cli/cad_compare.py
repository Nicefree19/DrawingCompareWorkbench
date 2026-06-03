"""CLI for direct CAD drawing comparison.

The GUI workbench is still the primary reviewer surface.  This module provides
scriptable entry points for automation, regression checks, and MCP wrappers:

    cad-compare file old.dxf new.dxf --output result.json
    cad-compare folder old_dir new_dir --output-dir build/compare-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cad-compare",
        description="Compare two CAD drawings or two drawing folders.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full JSON payload to stdout instead of a compact summary.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    file_parser = subparsers.add_parser("file", help="Compare two DWG/DXF files.")
    file_parser.add_argument("source_a", type=Path, help="Old/base drawing path.")
    file_parser.add_argument("source_b", type=Path, help="New/revised drawing path.")
    file_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Optional JSON output path. Parent directories are created.",
    )
    file_parser.add_argument(
        "--include-layer",
        action="append",
        default=None,
        help="Layer to include. May be supplied multiple times.",
    )
    file_parser.add_argument(
        "--exclude-layer",
        action="append",
        default=None,
        help="Layer to exclude. May be supplied multiple times.",
    )
    file_parser.add_argument(
        "--dwg-backend",
        default=None,
        help=(
            "DWG backend mode for file compare. Use 'user_converter' for nearby "
            "converted DXFs or 'oda_converter' for explicit local legacy fallback."
        ),
    )
    file_parser.add_argument(
        "--dwg-allowed-license-id",
        action="append",
        default=None,
        help=(
            "Explicit DWG adapter license id allowlist entry. Repeat for approved "
            "commercial SDK adapters; default is MIT and INTERNAL only."
        ),
    )
    _add_commercial_bridge_options(file_parser)
    file_parser.add_argument(
        "--oda-converter-path",
        type=Path,
        default=None,
        help="Optional local converter executable path for the 'oda_converter' DWG backend mode.",
    )
    file_parser.add_argument(
        "--user-converter-path",
        type=Path,
        default=None,
        help="Optional customer-provided converter executable path for the 'user_converter' DWG backend mode.",
    )
    file_parser.add_argument(
        "--user-converter-arg",
        action="append",
        default=None,
        help=(
            "Argument template for --user-converter-path. May be repeated; "
            "supports {input}, {output_dir}, {output}, and {stem}."
        ),
    )
    file_parser.add_argument(
        "--user-conversion-timeout",
        type=float,
        default=None,
        help="Optional user converter timeout in seconds for the 'user_converter' DWG backend mode.",
    )
    file_parser.add_argument(
        "--oda-conversion-timeout",
        type=float,
        default=None,
        help="Optional ODA conversion timeout in seconds for the 'oda_converter' DWG backend mode.",
    )
    file_parser.add_argument(
        "--dwg-conversion-cache-dir",
        type=Path,
        default=None,
        help="Optional cache directory for ODA-converted DXF files.",
    )
    file_parser.add_argument(
        "--import-timeout",
        type=float,
        default=None,
        help="Optional CAD import timeout in seconds.",
    )
    file_parser.add_argument(
        "--max-dxf-tokens",
        type=int,
        default=None,
        help="Optional maximum DXF group-code token budget.",
    )
    file_parser.add_argument(
        "--max-entities",
        type=int,
        default=None,
        help="Optional maximum imported CAD entity budget.",
    )
    file_parser.add_argument(
        "--fail-on-change",
        action="store_true",
        help="Return exit code 1 when differences are detected.",
    )
    file_parser.set_defaults(func=_run_file_compare)

    folder_parser = subparsers.add_parser(
        "folder",
        help="Scan, match, and compare two drawing folders.",
    )
    folder_parser.add_argument("source_a", type=Path, help="Old/base drawing folder or file.")
    folder_parser.add_argument("source_b", type=Path, help="New/revised drawing folder or file.")
    folder_parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="Run output directory. Defaults to build/cad-compare/<timestamp>.",
    )
    folder_parser.add_argument("--recursive", action="store_true", help="Scan folders recursively.")
    folder_parser.add_argument("--use-ocr", action="store_true", help="Enable OCR fallback.")
    folder_parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable descriptor cache for this run.",
    )
    folder_parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Maximum parallel comparison workers.",
    )
    folder_parser.add_argument(
        "--fast-first-review",
        action="store_true",
        help="Prioritize first reviewable result over full heavy artifact export.",
    )
    folder_parser.add_argument(
        "--dwg-backend",
        default=None,
        help="DWG backend mode for folder compare. Use 'user_converter' with a customer-provided converter.",
    )
    folder_parser.add_argument(
        "--dwg-allowed-license-id",
        action="append",
        default=None,
        help=(
            "Explicit DWG adapter license id allowlist entry for folder compare. "
            "Repeat for approved commercial SDK adapters; default is MIT and INTERNAL only."
        ),
    )
    _add_commercial_bridge_options(folder_parser)
    folder_parser.add_argument(
        "--user-converter-path",
        type=Path,
        default=None,
        help="Optional customer-provided converter executable path for the 'user_converter' DWG backend mode.",
    )
    folder_parser.add_argument(
        "--user-converter-arg",
        action="append",
        default=None,
        help=(
            "Argument template for --user-converter-path. May be repeated; "
            "supports {input}, {output_dir}, {output}, and {stem}."
        ),
    )
    folder_parser.add_argument(
        "--user-conversion-timeout",
        type=float,
        default=None,
        help="Optional user converter timeout in seconds for the 'user_converter' DWG backend mode.",
    )
    folder_parser.add_argument(
        "--dwg-conversion-cache-dir",
        type=Path,
        default=None,
        help="Optional cache directory for user-converted DXF files.",
    )
    folder_parser.add_argument(
        "--summary-json",
        type=Path,
        help="Optional compact run summary JSON path.",
    )
    folder_parser.set_defaults(func=_run_folder_compare)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("cad-compare cancelled.", file=sys.stderr)
        return 130


def _add_commercial_bridge_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dwg-commercial-adapter-spec",
        default=None,
        help=(
            "Explicit commercial DWG adapter factory spec, for example "
            "src.services.comparison.commercial_dwg_json_adapter:create_adapter. "
            "Only meaningful with --dwg-backend commercial_sdk."
        ),
    )
    parser.add_argument(
        "--dwg-bridge-command",
        default=None,
        help="Commercial DWG JSON bridge command. Only meaningful with --dwg-backend commercial_sdk.",
    )
    parser.add_argument(
        "--dwg-bridge-args-json",
        default=None,
        help=(
            "JSON array of commercial DWG bridge argument templates; supports "
            "{input}, {acadver}, {version}, {stem}, {family}, and {release}."
        ),
    )
    parser.add_argument(
        "--dwg-bridge-license-id",
        default=None,
        help="Explicit approved license id reported by the commercial DWG JSON bridge.",
    )
    parser.add_argument(
        "--dwg-bridge-supported-versions",
        default=None,
        help="Comma-separated ACxxxx versions supported by the commercial DWG JSON bridge.",
    )
    parser.add_argument(
        "--dwg-bridge-timeout-seconds",
        type=float,
        default=None,
        help="Timeout in seconds for each commercial DWG JSON bridge import.",
    )


def _run_file_compare(args: argparse.Namespace) -> int:
    _require_existing_path(args.source_a, "source_a")
    _require_existing_path(args.source_b, "source_b")

    from src.services.comparison.dwg_differ import DwgDiffer
    from src.services.comparison.dwg_backend import (
        DWG_BACKEND_ODA_CONVERTER,
        DWG_BACKEND_USER_CONVERTER,
        normalize_dwg_backend_mode,
    )

    differ_config = {"use_canonical_pipeline": True, "allow_oda_fallback": False}
    if args.dwg_backend:
        backend_mode = normalize_dwg_backend_mode(args.dwg_backend)
        differ_config["dwg_backend_mode"] = backend_mode
        if backend_mode == DWG_BACKEND_ODA_CONVERTER:
            differ_config["allow_oda_fallback"] = True
        if backend_mode == DWG_BACKEND_USER_CONVERTER and args.user_converter_path:
            differ_config["user_converter_path"] = str(args.user_converter_path)
    if args.dwg_allowed_license_id:
        differ_config["allowed_dwg_license_ids"] = ["MIT", "INTERNAL", *args.dwg_allowed_license_id]
    if args.oda_converter_path:
        differ_config["oda_converter_path"] = str(args.oda_converter_path)
    if args.user_converter_arg:
        differ_config["user_conversion_args"] = list(args.user_converter_arg)
    if args.user_conversion_timeout is not None:
        differ_config["user_conversion_timeout_seconds"] = args.user_conversion_timeout
    if args.oda_conversion_timeout is not None:
        differ_config["oda_conversion_timeout_seconds"] = args.oda_conversion_timeout
    if args.dwg_conversion_cache_dir:
        differ_config["dwg_conversion_cache_dir"] = str(args.dwg_conversion_cache_dir)
    if args.import_timeout is not None:
        differ_config["import_timeout_seconds"] = args.import_timeout
    if args.max_dxf_tokens is not None:
        differ_config["max_dxf_tokens"] = args.max_dxf_tokens
    if args.max_entities is not None:
        differ_config["max_entities"] = args.max_entities
    with _temporary_env(_dwg_commercial_env_updates(args)):
        differ = DwgDiffer(config=differ_config)
        result = differ.compare(
            args.source_a,
            args.source_b,
            include_layers=args.include_layer,
            exclude_layers=args.exclude_layer,
        )
        result = _maybe_legacy_fallback(
            result,
            args,
            include_layers=args.include_layer,
            exclude_layers=args.exclude_layer,
        )
    payload = {
        "mode": "file",
        "status": _comparison_status(result.metadata),
        "source_a": str(args.source_a.resolve()),
        "source_b": str(args.source_b.resolve()),
        "result": result.to_dict(),
    }
    _write_json_if_requested(payload, args.output)
    _emit_payload(payload, full_json=args.json)
    if payload["status"] == "failed":
        return 2
    if args.fail_on_change and result.has_changes:
        return 1
    return 0


def _maybe_legacy_fallback(
    result: Any,
    args: argparse.Namespace,
    *,
    include_layers: Sequence[str] | None,
    exclude_layers: Sequence[str] | None,
) -> Any:
    from src.services.comparison.dwg_differ import DwgDiffer

    canonical_status = _comparison_status(result.metadata)
    if canonical_status == "failed":
        canonical_error_code = result.metadata.get("error_code")
        canonical_message = result.metadata.get("message") or "CAD comparison failed"
        try:
            fallback_result = DwgDiffer(
                config={
                    "use_canonical_pipeline": False,
                    "use_legacy_ezdxf_pipeline": True,
                    "allow_oda_fallback": False,
                }
            ).compare(
                args.source_a,
                args.source_b,
                include_layers=include_layers,
                exclude_layers=exclude_layers,
            )
        except Exception:
            fallback_result = None
        if fallback_result is not None and _comparison_status(fallback_result.metadata) != "failed":
            fallback_result.metadata.update(
                {
                    "canonical_fallback_used": True,
                    "canonical_fallback_reason": f"{canonical_error_code}: {canonical_message}",
                    "canonical_error_code": canonical_error_code,
                    "canonical_pipeline_status": result.metadata.get("pipeline_status"),
                }
            )
            fallback_result.warnings.append(
                f"Canonical CAD compare failed; used ezdxf fallback: {canonical_error_code}: {canonical_message}"
            )
            result = fallback_result
    return result


def _run_folder_compare(args: argparse.Namespace) -> int:
    _require_existing_path(args.source_a, "source_a")
    _require_existing_path(args.source_b, "source_b")

    from src.services.comparison.folder_compare_pipeline import (
        FolderComparePipeline,
        FolderCompareRunRequest,
    )

    output_dir = args.output_dir or _default_output_dir()
    request = FolderCompareRunRequest(
        source_a=args.source_a,
        source_b=args.source_b,
        output_dir=output_dir,
        recursive=bool(args.recursive),
        use_ocr=bool(args.use_ocr),
        enable_descriptor_cache=not bool(args.no_cache),
        max_workers=args.max_workers,
        fast_first_review=bool(args.fast_first_review),
        dwg_backend_mode=args.dwg_backend,
        allowed_dwg_license_ids=tuple(["MIT", "INTERNAL", *(args.dwg_allowed_license_id or [])]),
        user_converter_path=args.user_converter_path,
        user_conversion_args=tuple(args.user_converter_arg or ()),
        user_conversion_timeout_seconds=args.user_conversion_timeout,
        dwg_conversion_cache_dir=args.dwg_conversion_cache_dir,
    )
    with _temporary_env(_dwg_commercial_env_updates(args)):
        result = FolderComparePipeline(request).run(progress_callback=_print_progress)
    payload = {
        "mode": "folder",
        "status": "ok",
        "source_a": str(Path(args.source_a).resolve()),
        "source_b": str(Path(args.source_b).resolve()),
        "output_dir": result.output_dir,
        "artifact_dir": result.artifact_dir,
        "review_project_path": result.review_project_path,
        "review_state_path": result.review_state_path,
        "run_manifest_path": result.run_manifest_path,
        "preflight_report_path": result.preflight_report_path,
        "confirmed_pairs": result.confirmed_pairs,
        "review_required_pairs": result.review_required_pairs,
        "unmatched_a": result.unmatched_a,
        "unmatched_b": result.unmatched_b,
        "compare_summary": result.compare_summary.to_dict(),
    }
    _write_json_if_requested(payload, args.summary_json)
    _emit_payload(payload, full_json=args.json)
    return 0


def _comparison_status(metadata: dict[str, Any]) -> str:
    status = metadata.get("pipeline_status")
    if status in {"ok", "partial", "failed"}:
        return str(status)
    if metadata.get("error_code"):
        return "failed"
    return "ok"


def _dwg_commercial_env_updates(args: argparse.Namespace) -> dict[str, str | None]:
    from src.services.comparison.commercial_dwg_json_adapter import (
        ARGS_JSON_ENV,
        COMMAND_ENV,
        LICENSE_ID_ENV,
        SUPPORTED_VERSIONS_ENV,
        TIMEOUT_SECONDS_ENV,
    )
    from src.services.comparison.dwg_backend import COMMERCIAL_SDK_ADAPTER_ENV

    timeout = getattr(args, "dwg_bridge_timeout_seconds", None)
    return {
        COMMERCIAL_SDK_ADAPTER_ENV: getattr(args, "dwg_commercial_adapter_spec", None),
        COMMAND_ENV: getattr(args, "dwg_bridge_command", None),
        ARGS_JSON_ENV: getattr(args, "dwg_bridge_args_json", None),
        LICENSE_ID_ENV: getattr(args, "dwg_bridge_license_id", None),
        SUPPORTED_VERSIONS_ENV: getattr(args, "dwg_bridge_supported_versions", None),
        TIMEOUT_SECONDS_ENV: str(timeout) if timeout is not None else None,
    }


@contextmanager
def _temporary_env(updates: dict[str, str | None]) -> Iterator[None]:
    old_values = {key: os.environ.get(key) for key in updates}
    try:
        for key, value in updates.items():
            if value is not None:
                os.environ[key] = value
        yield
    finally:
        for key, old_value in old_values.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


def _default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("build") / "cad-compare" / stamp


def _emit_payload(payload: dict[str, Any], *, full_json: bool) -> None:
    if full_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return
    mode = payload.get("mode")
    if mode == "file":
        summary = payload.get("result", {}).get("summary", {})
        metadata = payload.get("result", {}).get("metadata", {})
        if payload.get("status") == "failed":
            print(
                "file compare: "
                f"status=failed "
                f"error={metadata.get('error_code') or 'unknown'} "
                f"message={metadata.get('message') or 'comparison failed'}"
            )
        else:
            print(
                "file compare: "
                f"status={payload.get('status')} "
                f"added={summary.get('added', 0)} "
                f"deleted={summary.get('deleted', 0)} "
                f"modified={summary.get('modified', 0)} "
                f"unchanged={summary.get('unchanged', 0)}"
            )
    elif mode == "folder":
        summary = payload.get("compare_summary", {})
        print(
            "folder compare: "
            f"status={payload.get('status')} "
            f"confirmed={payload.get('confirmed_pairs', 0)} "
            f"review_required={payload.get('review_required_pairs', 0)} "
            f"completed={summary.get('completed_pairs', 0)} "
            f"failed={summary.get('failed_pairs', 0)} "
            f"output={payload.get('output_dir')}"
        )
    else:
        print(json.dumps(payload, ensure_ascii=False, default=str))


def _print_progress(stage: str, percent: float, message: str) -> None:
    print(f"[{stage:>10}] {percent:5.1f}% {message}", file=sys.stderr)


def _require_existing_path(path: Path, label: str) -> None:
    if not path.exists():
        raise SystemExit(f"{label} does not exist: {path}")


def _write_json_if_requested(payload: dict[str, Any], output: Path | None) -> None:
    if output is None:
        return
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
