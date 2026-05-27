"""CLI for direct CAD drawing comparison.

The GUI workbench is still the primary reviewer surface.  This module provides
scriptable entry points for automation, regression checks, and MCP wrappers:

    cad-compare file old.dxf new.dxf --output result.json
    cad-compare folder old_dir new_dir --output-dir build/compare-run
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


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


def _run_file_compare(args: argparse.Namespace) -> int:
    _require_existing_path(args.source_a, "source_a")
    _require_existing_path(args.source_b, "source_b")

    from src.services.comparison.dwg_differ import DwgDiffer

    differ = DwgDiffer(config={"use_canonical_pipeline": True, "allow_oda_fallback": False})
    result = differ.compare(
        args.source_a,
        args.source_b,
        include_layers=args.include_layer,
        exclude_layers=args.exclude_layer,
    )
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
                include_layers=args.include_layer,
                exclude_layers=args.exclude_layer,
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
    )
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
