"""Evaluate local DWG accuracy evidence against compare results.

This runner consumes normalized local evidence, executes pairs that can be
decoded by the MIT-safe fixture adapter, and reports structural TP/FP/FN
metrics. Pairs that require a commercial bridge are skipped rather than being
silently counted as pass/fail.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = Path(".local/accuracy-evidence/corpus_manifest_normalized.json")
DEFAULT_TRUTH = Path(".local/accuracy-evidence/truth_normalized.json")
DEFAULT_REPORT_JSON = Path(".local/accuracy-evidence/accuracy_metric_report.json")
DEFAULT_REPORT_MD = Path(".local/accuracy-evidence/accuracy_metric_report.md")
REPORT_SCHEMA_VERSION = "dwg-accuracy-metric-report/v1"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_accuracy_evidence import load_manifest, load_truth, sha256_file  # noqa: E402
from src.services.comparison.commercial_dwg_json_adapter import (  # noqa: E402
    ARGS_JSON_ENV,
    COMMAND_ENV,
    LICENSE_ID_ENV,
    SUPPORTED_VERSIONS_ENV,
    TIMEOUT_SECONDS_ENV,
)
from src.services.comparison.dwg_backend import (  # noqa: E402
    COMMERCIAL_SDK_ADAPTER_ENV,
    DWG_BACKEND_COMMERCIAL_SDK,
    create_dwg_backend_selection,
    normalize_dwg_backend_mode,
)
from src.services.comparison.dwg_importer import DwgImporter, DwgJsonFixtureAdapter  # noqa: E402
from src.services.comparison.drawing_compare_engine import (  # noqa: E402
    CompareTolerance,
    DrawingCompareEngine,
    DrawingCompareOptions,
)


STRUCTURAL_ENTITY_TYPES = {
    "line",
    "circle",
    "arc",
    "polyline",
    "block_reference",
}
STRUCTURAL_CATEGORIES = {"geometry", "block"}
EVALUATION_BACKEND_FIXTURE_ONLY = "fixture_only"


def evaluate_evidence(
    manifest_path: Path = DEFAULT_MANIFEST,
    truth_path: Path = DEFAULT_TRUTH,
    *,
    pair_ids: set[str] | None = None,
    max_pairs: int | None = None,
    dwg_backend: str = EVALUATION_BACKEND_FIXTURE_ONLY,
    roi_retry_margin: float | None = None,
    roi_retry_margins: Sequence[float] | None = None,
    roi_attempt_retries: int = 1,
    roi_first: bool = False,
    roi_max_attempts: int | None = None,
    progress: bool = False,
) -> dict[str, Any]:
    manifest_path = _resolve(manifest_path)
    truth_path = _resolve(truth_path)
    backend_selection = _create_evaluation_backend(dwg_backend)
    files = load_manifest(manifest_path)
    pairs = load_truth(truth_path)
    files_by_id = {str(item.get("file_id") or ""): item for item in files}
    active_pairs = [pair for pair in pairs if pair.get("accuracy_status", "active") == "active"]
    if pair_ids is not None:
        active_pairs = [pair for pair in active_pairs if str(pair.get("pair_id") or "") in pair_ids]
    if max_pairs is not None:
        active_pairs = active_pairs[:max_pairs]

    pair_reports: list[dict[str, Any]] = []
    import_cache: dict[str, dict[str, Any]] = {}
    started = time.perf_counter()
    for index, pair in enumerate(active_pairs, start=1):
        pair_report = _evaluate_pair(
            pair,
            files_by_id,
            backend_selection,
            import_cache,
            roi_retry_margin=roi_retry_margin,
            roi_retry_margins=roi_retry_margins,
            roi_attempt_retries=roi_attempt_retries,
            roi_first=roi_first,
            roi_max_attempts=roi_max_attempts,
        )
        pair_reports.append(pair_report)
        if progress:
            _print_progress(index, len(active_pairs), pair_report)
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)

    evaluated = [item for item in pair_reports if item["status"] == "evaluated"]
    skipped = [item for item in pair_reports if item["status"] == "skipped"]
    counts = Counter(item.get("classification") for item in evaluated)
    skip_reason_counts = Counter(item.get("skip_reason") for item in skipped)
    bucket_counts = Counter(
        item.get("failure_bucket")
        for item in evaluated
        if item.get("failure_bucket")
    )
    by_pair_type = _metrics_by_dimension(evaluated, "pair_type")
    by_version = _metrics_by_dimension(evaluated, "dwg_version")
    metrics = _metrics(counts)
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(),
        "status": _report_status(evaluated, len(active_pairs), metrics),
        "dwg_backend": _backend_report(backend_selection),
        "manifest_path": str(manifest_path),
        "truth_path": str(truth_path),
        "manifest_sha256": sha256_file(manifest_path),
        "truth_sha256": sha256_file(truth_path),
        "summary": {
            "active_pair_count": len(active_pairs),
            "evaluated_pair_count": len(evaluated),
            "skipped_pair_count": len(skipped),
            "elapsed_ms": elapsed_ms,
            **metrics,
            "classification_counts": dict(sorted((str(k), v) for k, v in counts.items() if k)),
            "skip_reason_counts": dict(sorted((str(k), v) for k, v in skip_reason_counts.items() if k)),
            "failure_bucket_counts": dict(sorted((str(k), v) for k, v in bucket_counts.items() if k)),
        },
        "target_assessment": _target_assessment(len(active_pairs), len(evaluated), metrics),
        "by_pair_type": by_pair_type,
        "by_version": by_version,
        "pairs": pair_reports,
    }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Accuracy Metric Report",
        "",
        f"Status: **{report['status']}**",
        "",
        "## Summary",
        "",
        f"- Active pairs selected: `{summary['active_pair_count']}`",
        f"- Evaluated pairs: `{summary['evaluated_pair_count']}`",
        f"- Skipped pairs: `{summary['skipped_pair_count']}`",
        f"- Precision: `{summary['precision']}`",
        f"- Recall: `{summary['recall']}`",
        f"- F1: `{summary['f1']}`",
        f"- Structural FN: `{summary['fn_count']}`",
        "",
        "## Target Assessment",
        "",
        "| profile | status | blockers |",
        "| --- | --- | --- |",
    ]
    for name, assessment in report.get("target_assessment", {}).items():
        blockers = ", ".join(assessment.get("blockers") or [])
        lines.append(f"| `{name}` | `{assessment.get('status')}` | {blockers} |")
    lines.extend(["", "## Pair Type Metrics", "", "| pair type | evaluated | precision | recall | TP | FP | FN | TN |"])
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for pair_type, item in (report.get("by_pair_type") or {}).items():
        lines.append(
            "| `{}` | {} | {} | {} | {} | {} | {} | {} |".format(
                pair_type,
                item["evaluated_pair_count"],
                item["precision"],
                item["recall"],
                item["tp_count"],
                item["fp_count"],
                item["fn_count"],
                item["tn_count"],
            )
        )
    if summary.get("failure_bucket_counts"):
        lines.extend(["", "## Failure Buckets", "", "| bucket | count |", "| --- | ---: |"])
        for bucket, count in summary["failure_bucket_counts"].items():
            lines.append(f"| `{bucket}` | {count} |")
    if summary.get("skip_reason_counts"):
        lines.extend(["", "## Skip Reasons", "", "| reason | count |", "| --- | ---: |"])
        for reason, count in summary["skip_reason_counts"].items():
            lines.append(f"| `{reason}` | {count} |")
    lines.append("")
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--truth", type=Path, default=DEFAULT_TRUTH)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    parser.add_argument("--pair-id", action="append", default=None)
    parser.add_argument("--max-pairs", type=int, default=None)
    parser.add_argument(
        "--dwg-backend",
        default=EVALUATION_BACKEND_FIXTURE_ONLY,
        help=(
            "DWG backend for non-fixture pairs. Defaults to fixture_only. "
            "Use commercial_sdk with an approved JSON bridge to evaluate real DWGs."
        ),
    )
    parser.add_argument(
        "--dwg-commercial-adapter-spec",
        default=None,
        help=(
            "Explicit commercial DWG adapter factory spec. For the built-in JSON bridge use "
            "src.services.comparison.commercial_dwg_json_adapter:create_adapter."
        ),
    )
    parser.add_argument("--dwg-bridge-command", default=None, help="Commercial DWG JSON bridge command.")
    parser.add_argument(
        "--dwg-bridge-args-json",
        default=None,
        help="JSON array of bridge argument templates; supports {input}, {acadver}, and related placeholders.",
    )
    parser.add_argument("--dwg-bridge-license-id", default=None, help="Approved bridge license id.")
    parser.add_argument(
        "--dwg-bridge-supported-versions",
        default=None,
        help="Comma-separated ACxxxx versions supported by the bridge, or *.",
    )
    parser.add_argument("--dwg-bridge-timeout-seconds", type=float, default=None)
    parser.add_argument(
        "--dwg-bridge-roi-retry-margin",
        type=float,
        default=None,
        help=(
            "When a changed pair produces no structural changes and bridge output appears capped, "
            "retry the pair once with --roi-json derived from expected_changes approx_bbox plus this margin."
        ),
    )
    parser.add_argument(
        "--dwg-bridge-roi-retry-margins",
        default=None,
        help=(
            "Comma-separated ROI margins to try in order, for example 250,1000,5000. "
            "Useful when reviewed approx_bbox is only a rough locator."
        ),
    )
    parser.add_argument(
        "--dwg-bridge-roi-attempt-retries",
        type=int,
        default=1,
        help="Retry the same ROI margin this many times when ZWCAD import times out.",
    )
    parser.add_argument(
        "--dwg-bridge-roi-first",
        action="store_true",
        help=(
            "For changed commercial-DWG evidence pairs with expected_changes approx_bbox, "
            "start with ROI extraction instead of waiting for a capped full extraction."
        ),
    )
    parser.add_argument(
        "--dwg-bridge-roi-max-attempts",
        type=int,
        default=None,
        help=(
            "Maximum ROI extraction attempts per pair across all margins/retries "
            "(each attempt launches CAD for before+after). Prevents silent unbounded "
            "CAD-launch escalation; the per-pair launch count is always reported."
        ),
    )
    parser.add_argument(
        "--allow-blocked",
        action="store_true",
        help="Write reports and return zero even when the target assessment remains blocked.",
    )
    parser.add_argument("--progress", action="store_true", help="Print one progress line per evaluated pair.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    with _temporary_env(_dwg_commercial_env_updates(args)):
        report = evaluate_evidence(
            args.manifest,
            args.truth,
            pair_ids=set(args.pair_id) if args.pair_id else None,
            max_pairs=args.max_pairs,
            dwg_backend=args.dwg_backend,
            roi_retry_margin=args.dwg_bridge_roi_retry_margin,
            roi_retry_margins=_parse_roi_margins_arg(args.dwg_bridge_roi_retry_margins),
            roi_attempt_retries=args.dwg_bridge_roi_attempt_retries,
            roi_first=args.dwg_bridge_roi_first,
            roi_max_attempts=args.dwg_bridge_roi_max_attempts,
            progress=args.progress,
        )
    _write_json(args.report_json, report)
    _write_text(args.report_md, render_markdown(report))
    print(f"status={report['status']}")
    print(f"evaluated_pairs={report['summary']['evaluated_pair_count']}")
    print(f"skipped_pairs={report['summary']['skipped_pair_count']}")
    print(f"precision={report['summary']['precision']}")
    print(f"recall={report['summary']['recall']}")
    return 0 if report["status"] in {"passed", "skipped"} or args.allow_blocked else 1


def _evaluate_pair(
    pair: dict[str, Any],
    files_by_id: dict[str, dict[str, Any]],
    backend_selection: dict[str, Any],
    import_cache: dict[str, dict[str, Any]] | None = None,
    *,
    roi_retry_margin: float | None = None,
    roi_retry_margins: Sequence[float] | None = None,
    roi_attempt_retries: int = 1,
    roi_first: bool = False,
    roi_max_attempts: int | None = None,
) -> dict[str, Any]:
    before = files_by_id.get(str(pair.get("before_file_id") or ""))
    after = files_by_id.get(str(pair.get("after_file_id") or ""))
    base = {
        "pair_id": pair.get("pair_id"),
        "pair_type": pair.get("pair_type"),
        "dwg_version": pair.get("dwg_version"),
        "expected_changed": bool(pair.get("expected_changed")),
        "expected_change_count": int(pair.get("expected_change_count") or 0),
    }
    if before is None or after is None:
        return {**base, "status": "skipped", "skip_reason": "missing_manifest_file"}
    before_is_fixture = _is_fixture_file(before)
    after_is_fixture = _is_fixture_file(after)
    if not before_is_fixture or not after_is_fixture:
        if backend_selection["mode"] == EVALUATION_BACKEND_FIXTURE_ONLY:
            return {**base, "status": "skipped", "skip_reason": "requires_non_fixture_dwg_backend"}
        if not backend_selection["selection"].adapter.is_available():
            return {
                **base,
                "status": "skipped",
                "skip_reason": "dwg_backend_unavailable",
                "dwg_backend": backend_selection["selection"].to_dict(),
            }

    before_path = Path(str(before.get("absolute_path") or ""))
    after_path = Path(str(after.get("absolute_path") or ""))
    started = time.perf_counter()
    roi_first_attempt: dict[str, Any] | None = None
    if roi_first and bool(pair.get("expected_changed")):
        roi_first_report, roi_first_info = _roi_retry_pair(
            pair,
            before_path,
            after_path,
            backend_selection,
            before_is_fixture and after_is_fixture,
            roi_retry_margin=roi_retry_margin,
            roi_retry_margins=roi_retry_margins,
            roi_attempt_retries=roi_attempt_retries,
            initial_import_ms=0.0,
            initial_compare_ms=0.0,
            initial_cap_truncation={"possibly_truncated": None, "sides": {}},
            pair_started=started,
            roi_mode="first",
            import_cache=import_cache,
            before_record=before,
            after_record=after,
            roi_max_attempts=roi_max_attempts,
        )
        # ROI-first is only a reliable VERDICT when it detected a change. Trust it for
        # a positive detection (fast path) or an honest skip (e.g. empty ROI ->
        # bbox recalibration, which is transparently excluded rather than scored).
        # But an evaluated "no change" may simply mean the change fell outside the
        # ROI, so fall through to the full extraction instead of recording a false
        # negative.
        if roi_first_report is not None:
            is_evaluated = roi_first_report.get("status") == "evaluated"
            if not is_evaluated or roi_first_report.get("predicted_changed"):
                return {**roi_first_report, "roi_retry": roi_first_info}
            roi_first_attempt = {**roi_first_info, "fell_through_to_full": True}
    try:
        import_start = time.perf_counter()
        importer = DwgImporter(adapter=_adapter_for_pair(backend_selection, before_is_fixture and after_is_fixture))
        old_doc, before_cache_hit = _import_with_cache(
            importer,
            before_path,
            before,
            import_cache,
        )
        before_report = old_doc.get("import_report") or {}
        if before_report.get("status") not in {"ok", "partial"}:
            import_ms = round((time.perf_counter() - import_start) * 1000.0, 3)
            return {
                **base,
                "status": "skipped",
                "skip_reason": f"before_import_{before_report.get('error_code') or before_report.get('status')}",
                "import_report": {"before": before_report, "after": None},
                "import_cache": {"before_hit": before_cache_hit, "after_hit": False},
                "timing_ms": {"import": import_ms, "total": round((time.perf_counter() - started) * 1000.0, 3)},
            }
        new_doc, after_cache_hit = _import_with_cache(
            importer,
            after_path,
            after,
            import_cache,
        )
        import_ms = round((time.perf_counter() - import_start) * 1000.0, 3)
        after_report = new_doc.get("import_report") or {}
        if after_report.get("status") not in {"ok", "partial"}:
            return {
                **base,
                "status": "skipped",
                "skip_reason": f"after_import_{after_report.get('error_code') or after_report.get('status')}",
                "import_report": {"before": before_report, "after": after_report},
                "import_cache": {"before_hit": before_cache_hit, "after_hit": after_cache_hit},
                "timing_ms": {"import": import_ms, "total": round((time.perf_counter() - started) * 1000.0, 3)},
            }
        compare_start = time.perf_counter()
        result = DrawingCompareEngine(
            DrawingCompareOptions(
                tolerance=CompareTolerance(position_tolerance_mm=1.0, bbox_tolerance_mm=1.0),
                structural_position_tolerance_mm=0.1,
                include_unchanged=False,
                include_entity_snapshots=False,
            )
        ).compare(old_doc, new_doc)
        compare_ms = round((time.perf_counter() - compare_start) * 1000.0, 3)
    except Exception as exc:
        return {**base, "status": "skipped", "skip_reason": "compare_exception", "message": str(exc)}

    payload = result.to_dict()
    structural_changes = [_change_summary(change) for change in payload.get("changes") or [] if _is_structural_change(change)]
    predicted_changed = bool(structural_changes)
    expected_changed = bool(pair.get("expected_changed"))
    cap_truncation = _cap_truncation_assessment(old_doc, new_doc)
    if expected_changed and not predicted_changed and cap_truncation["possibly_truncated"]:
        roi_retry_report, roi_retry = _roi_retry_pair(
            pair,
            before_path,
            after_path,
            backend_selection,
            before_is_fixture and after_is_fixture,
            roi_retry_margin=roi_retry_margin,
            roi_retry_margins=roi_retry_margins,
            roi_attempt_retries=roi_attempt_retries,
            initial_import_ms=import_ms,
            initial_compare_ms=compare_ms,
            initial_cap_truncation=cap_truncation,
            pair_started=started,
            import_cache=import_cache,
            before_record=before,
            after_record=after,
            roi_max_attempts=roi_max_attempts,
        )
        if roi_retry_report is not None:
            return {**roi_retry_report, "roi_retry": roi_retry}
        total_ms = round((time.perf_counter() - started) * 1000.0, 3)
        return {
            **base,
            "status": "skipped",
            "skip_reason": "cap_truncated_requires_roi_extraction",
            "dwg_backend": _pair_backend_report(backend_selection, before_is_fixture and after_is_fixture),
            "predicted_changed": predicted_changed,
            "predicted_total_change_count": int(payload.get("summary", {}).get("total_changes") or 0),
            "predicted_structural_change_count": len(structural_changes),
            "structural_changes": structural_changes,
            "summary": payload.get("summary") or {},
            "cap_truncation": cap_truncation,
            "roi_retry": roi_retry,
            "roi_first_attempt": roi_first_attempt,
            "import_report": {"before": before_report, "after": after_report},
            "timing_ms": {"import": import_ms, "compare": compare_ms, "total": total_ms},
            "import_cache": {"before_hit": before_cache_hit, "after_hit": after_cache_hit},
        }
    classification = _classification(expected_changed, predicted_changed)
    total_ms = round((time.perf_counter() - started) * 1000.0, 3)
    evaluated = {
        **base,
        "status": "evaluated",
        "dwg_backend": _pair_backend_report(backend_selection, before_is_fixture and after_is_fixture),
        "classification": classification,
        "predicted_changed": predicted_changed,
        "predicted_total_change_count": int(payload.get("summary", {}).get("total_changes") or 0),
        "predicted_structural_change_count": len(structural_changes),
        "structural_changes": structural_changes,
        "summary": payload.get("summary") or {},
        "timing_ms": {"import": import_ms, "compare": compare_ms, "total": total_ms},
        "import_cache": {"before_hit": before_cache_hit, "after_hit": after_cache_hit},
    }
    bucket = _failure_bucket(pair, classification, structural_changes)
    if bucket:
        evaluated["failure_bucket"] = bucket
    if roi_first_attempt is not None:
        evaluated["roi_first_attempt"] = roi_first_attempt
    return evaluated


def _roi_retry_pair(
    pair: dict[str, Any],
    before_path: Path,
    after_path: Path,
    backend_selection: dict[str, Any],
    fixture_pair: bool,
    *,
    roi_retry_margin: float | None,
    roi_retry_margins: Sequence[float] | None = None,
    roi_attempt_retries: int = 1,
    initial_import_ms: float,
    initial_compare_ms: float,
    initial_cap_truncation: dict[str, Any],
    pair_started: float,
    roi_mode: str = "retry",
    import_cache: dict[str, dict[str, Any]] | None = None,
    before_record: dict[str, Any] | None = None,
    after_record: dict[str, Any] | None = None,
    roi_max_attempts: int | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    margins = _roi_margin_candidates(roi_retry_margin, roi_retry_margins)
    if not margins:
        return None, {"attempted": False, "reason": "disabled"}
    attempts: list[dict[str, Any]] = []
    last_report: dict[str, Any] | None = None
    last_info: dict[str, Any] = {"attempted": False, "reason": "not_attempted"}
    retries = max(1, int(roi_attempt_retries or 1))
    max_attempts = roi_max_attempts if (roi_max_attempts and roi_max_attempts > 0) else None
    launched = 0
    capped = False
    stop_sweep = False
    for margin in margins:
        for retry_index in range(retries):
            report, info = _roi_attempt_pair(
                pair,
                before_path,
                after_path,
                backend_selection,
                fixture_pair,
                roi_margin=margin,
                roi_attempt_index=retry_index + 1,
                initial_import_ms=initial_import_ms,
                initial_compare_ms=initial_compare_ms,
                initial_cap_truncation=initial_cap_truncation,
                pair_started=pair_started,
                roi_mode=roi_mode,
                import_cache=import_cache,
                before_record=before_record,
                after_record=after_record,
            )
            last_report = report
            last_info = info
            attempts.append(_roi_attempt_summary(report, info))
            if isinstance(info, dict) and info.get("attempted"):
                launched += 1
            if report is None:
                stop_sweep = True
                break
            if report.get("status") == "evaluated":
                stop_sweep = True
                break
            # Bound the per-pair CAD-launch escalation (margins x retries x before/after)
            # explicitly rather than letting it grow silently (finding 15).
            if max_attempts is not None and launched >= max_attempts:
                capped = True
                stop_sweep = True
                break
            skip_reason = str(report.get("skip_reason") or "")
            if _roi_timeout_skip_reason(skip_reason) and retry_index + 1 < retries:
                continue
            if skip_reason == "roi_empty_requires_bbox_recalibration":
                break
            stop_sweep = True
            break
        if stop_sweep:
            break
    last_info["attempts"] = attempts
    last_info["attempt_count"] = len(attempts)
    last_info["launched_attempts"] = launched
    last_info["max_attempts"] = max_attempts
    last_info["capped_at_max_attempts"] = capped
    return last_report, last_info


def _roi_attempt_pair(
    pair: dict[str, Any],
    before_path: Path,
    after_path: Path,
    backend_selection: dict[str, Any],
    fixture_pair: bool,
    *,
    roi_margin: float,
    roi_attempt_index: int,
    initial_import_ms: float,
    initial_compare_ms: float,
    initial_cap_truncation: dict[str, Any],
    pair_started: float,
    roi_mode: str,
    import_cache: dict[str, dict[str, Any]] | None = None,
    before_record: dict[str, Any] | None = None,
    after_record: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if fixture_pair or backend_selection["mode"] == EVALUATION_BACKEND_FIXTURE_ONLY:
        return None, {"attempted": False, "reason": "requires_non_fixture_commercial_backend"}
    roi_request = _roi_request_from_expected_changes(pair, roi_margin)
    if roi_request is None:
        return None, {"attempted": False, "reason": "missing_expected_change_bbox"}

    adapter = _adapter_for_pair(backend_selection, fixture_pair)
    args_template = getattr(adapter, "args_template", None)
    if args_template is None:
        return None, {"attempted": False, "reason": "adapter_does_not_support_roi_args"}
    # ROI imports are cached/quarantined under an ROI-aware key so they never collide
    # with the full-extraction docs and are reused across attempts/pairs sharing the
    # same file + ROI (finding 4).
    roi_cache_suffix = "|roi=" + json.dumps(roi_request, sort_keys=True, separators=(",", ":"))
    before_hit = False
    after_hit = False

    retry_info = {
        "attempted": True,
        "mode": roi_mode,
        "attempt_index": roi_attempt_index,
        "roi_request": roi_request,
        "initial_cap_truncation": initial_cap_truncation,
    }
    base = {
        "pair_id": pair.get("pair_id"),
        "pair_type": pair.get("pair_type"),
        "dwg_version": pair.get("dwg_version"),
        "expected_changed": bool(pair.get("expected_changed")),
        "expected_change_count": int(pair.get("expected_change_count") or 0),
    }
    try:
        with _temporary_bridge_roi_args(adapter, roi_request):
            importer = DwgImporter(adapter=adapter)
            import_start = time.perf_counter()
            old_doc, before_hit = _import_with_cache(
                importer,
                before_path,
                before_record or {},
                import_cache,
                cache_key_suffix=roi_cache_suffix,
                cache_failures=False,
            )
            before_report = old_doc.get("import_report") or {}
            if before_report.get("status") not in {"ok", "partial"}:
                import_ms = round((time.perf_counter() - import_start) * 1000.0, 3)
                return (
                    {
                        **base,
                        "status": "skipped",
                        "skip_reason": f"roi_retry_before_import_{before_report.get('error_code') or before_report.get('status')}",
                        "dwg_backend": _pair_backend_report(backend_selection, fixture_pair),
                        "import_report": {"before": before_report, "after": None},
                        "timing_ms": {
                            "initial_import": initial_import_ms,
                            "initial_compare": initial_compare_ms,
                            "roi_import": import_ms,
                            "total": round((time.perf_counter() - pair_started) * 1000.0, 3),
                        },
                        "import_cache": {"before_hit": False, "after_hit": False},
                    },
                    retry_info,
                )
            new_doc, after_hit = _import_with_cache(
                importer,
                after_path,
                after_record or {},
                import_cache,
                cache_key_suffix=roi_cache_suffix,
                cache_failures=False,
            )
            import_ms = round((time.perf_counter() - import_start) * 1000.0, 3)
            after_report = new_doc.get("import_report") or {}
            if after_report.get("status") not in {"ok", "partial"}:
                return (
                    {
                        **base,
                        "status": "skipped",
                        "skip_reason": f"roi_retry_after_import_{after_report.get('error_code') or after_report.get('status')}",
                        "dwg_backend": _pair_backend_report(backend_selection, fixture_pair),
                        "import_report": {"before": before_report, "after": after_report},
                        "timing_ms": {
                            "initial_import": initial_import_ms,
                            "initial_compare": initial_compare_ms,
                            "roi_import": import_ms,
                            "total": round((time.perf_counter() - pair_started) * 1000.0, 3),
                        },
                        "import_cache": {"before_hit": False, "after_hit": False},
                    },
                    retry_info,
                )
            compare_start = time.perf_counter()
            result = DrawingCompareEngine(
                DrawingCompareOptions(
                    tolerance=CompareTolerance(position_tolerance_mm=1.0, bbox_tolerance_mm=1.0),
                    structural_position_tolerance_mm=0.1,
                    include_unchanged=False,
                    include_entity_snapshots=False,
                )
            ).compare(old_doc, new_doc)
            compare_ms = round((time.perf_counter() - compare_start) * 1000.0, 3)
    except Exception as exc:
        return (
            {
                **base,
                "status": "skipped",
                "skip_reason": "roi_retry_exception",
                "message": str(exc),
                "dwg_backend": _pair_backend_report(backend_selection, fixture_pair),
                "timing_ms": {
                    "initial_import": initial_import_ms,
                    "initial_compare": initial_compare_ms,
                    "total": round((time.perf_counter() - pair_started) * 1000.0, 3),
                },
                "import_cache": {"before_hit": before_hit, "after_hit": after_hit},
            },
            retry_info,
        )

    payload = result.to_dict()
    structural_changes = [_change_summary(change) for change in payload.get("changes") or [] if _is_structural_change(change)]
    predicted_changed = bool(structural_changes)
    expected_changed = bool(pair.get("expected_changed"))
    cap_truncation = _cap_truncation_assessment(old_doc, new_doc)
    if expected_changed and not predicted_changed and cap_truncation["possibly_truncated"]:
        return (
            {
                **base,
                "status": "skipped",
                "skip_reason": "roi_retry_cap_truncated",
                "dwg_backend": _pair_backend_report(backend_selection, fixture_pair),
                "predicted_changed": predicted_changed,
                "predicted_total_change_count": int(payload.get("summary", {}).get("total_changes") or 0),
                "predicted_structural_change_count": len(structural_changes),
                "structural_changes": structural_changes,
                "summary": payload.get("summary") or {},
                "cap_truncation": cap_truncation,
                "import_report": {"before": before_report, "after": after_report},
                "timing_ms": {
                    "initial_import": initial_import_ms,
                    "initial_compare": initial_compare_ms,
                    "roi_import": import_ms,
                    "roi_compare": compare_ms,
                    "total": round((time.perf_counter() - pair_started) * 1000.0, 3),
                },
                "import_cache": {"before_hit": before_hit, "after_hit": after_hit},
            },
            retry_info,
        )
    if expected_changed and not predicted_changed and _both_docs_have_no_imported_entities(old_doc, new_doc):
        return (
            {
                **base,
                "status": "skipped",
                "skip_reason": "roi_empty_requires_bbox_recalibration",
                "dwg_backend": _pair_backend_report(backend_selection, fixture_pair),
                "predicted_changed": predicted_changed,
                "predicted_total_change_count": int(payload.get("summary", {}).get("total_changes") or 0),
                "predicted_structural_change_count": len(structural_changes),
                "structural_changes": structural_changes,
                "summary": payload.get("summary") or {},
                "cap_truncation": cap_truncation,
                "import_report": {"before": before_report, "after": after_report},
                "timing_ms": {
                    "initial_import": initial_import_ms,
                    "initial_compare": initial_compare_ms,
                    "roi_import": import_ms,
                    "roi_compare": compare_ms,
                    "total": round((time.perf_counter() - pair_started) * 1000.0, 3),
                },
                "import_cache": {"before_hit": before_hit, "after_hit": after_hit},
            },
            retry_info,
        )

    classification = _classification(expected_changed, predicted_changed)
    evaluated = {
        **base,
        "status": "evaluated",
        "dwg_backend": _pair_backend_report(backend_selection, fixture_pair),
        "classification": classification,
        "predicted_changed": predicted_changed,
        "predicted_total_change_count": int(payload.get("summary", {}).get("total_changes") or 0),
        "predicted_structural_change_count": len(structural_changes),
        "structural_changes": structural_changes,
        "summary": payload.get("summary") or {},
        "cap_truncation": cap_truncation,
        "import_report": {"before": before_report, "after": after_report},
        "timing_ms": {
            "initial_import": initial_import_ms,
            "initial_compare": initial_compare_ms,
            "roi_import": import_ms,
            "roi_compare": compare_ms,
            "total": round((time.perf_counter() - pair_started) * 1000.0, 3),
        },
        "import_cache": {"before_hit": before_hit, "after_hit": after_hit},
    }
    bucket = _failure_bucket(pair, classification, structural_changes)
    if bucket:
        evaluated["failure_bucket"] = bucket
    return evaluated, retry_info


def _roi_margin_candidates(
    roi_retry_margin: float | None,
    roi_retry_margins: Sequence[float] | None,
) -> list[float]:
    raw_values: list[Any] = []
    if roi_retry_margins is not None:
        raw_values.extend(roi_retry_margins)
    elif roi_retry_margin is not None:
        raw_values.append(roi_retry_margin)

    margins: list[float] = []
    seen: set[float] = set()
    for raw in raw_values:
        value = _safe_float(raw)
        if value is None or value < 0:
            continue
        key = round(value, 9)
        if key in seen:
            continue
        seen.add(key)
        margins.append(value)
    return margins


def _roi_attempt_summary(report: dict[str, Any] | None, info: dict[str, Any]) -> dict[str, Any]:
    roi_request = info.get("roi_request") if isinstance(info, dict) else None
    summary = {
        "attempted": bool(info.get("attempted")) if isinstance(info, dict) else False,
        "mode": info.get("mode") if isinstance(info, dict) else None,
        "attempt_index": info.get("attempt_index") if isinstance(info, dict) else None,
        "roi_request": roi_request,
    }
    if report is not None:
        summary.update(
            {
                "status": report.get("status"),
                "detail": report.get("classification") or report.get("skip_reason"),
                "timing_ms": report.get("timing_ms"),
                "predicted_structural_change_count": report.get("predicted_structural_change_count"),
            }
        )
    elif isinstance(info, dict):
        summary["reason"] = info.get("reason")
    return summary


def _roi_timeout_skip_reason(skip_reason: str) -> bool:
    return skip_reason in {
        "roi_retry_before_import_DWG_IMPORT_TIMEOUT",
        "roi_retry_after_import_DWG_IMPORT_TIMEOUT",
    }


def _roi_request_from_expected_changes(pair: dict[str, Any], margin: float) -> dict[str, Any] | None:
    margin_value = _safe_float(margin)
    if margin_value is None or margin_value < 0:
        return None
    boxes: list[tuple[float, float, float, float]] = []
    for change in pair.get("expected_changes") or []:
        if not isinstance(change, dict):
            continue
        bbox = change.get("approx_bbox") or change.get("bbox")
        parsed = _bbox_tuple(bbox)
        if parsed is not None:
            boxes.append(parsed)
    if not boxes:
        return None
    minx = min(box[0] for box in boxes)
    miny = min(box[1] for box in boxes)
    maxx = max(box[2] for box in boxes)
    maxy = max(box[3] for box in boxes)
    return {
        "bbox": [minx, miny, maxx, maxy],
        "margin": margin_value,
    }


def _bbox_tuple(value: Any) -> tuple[float, float, float, float] | None:
    if value is None or isinstance(value, (str, bytes)):
        return None
    try:
        raw = list(value)
    except TypeError:
        return None
    if len(raw) != 4:
        return None
    values = [_safe_float(item) for item in raw]
    if any(item is None for item in values):
        return None
    minx, miny, maxx, maxy = [float(item) for item in values]
    if minx > maxx or miny > maxy:
        return None
    return (minx, miny, maxx, maxy)


def _both_docs_have_no_imported_entities(before_doc: dict[str, Any], after_doc: dict[str, Any]) -> bool:
    return _doc_imported_entity_count(before_doc) == 0 and _doc_imported_entity_count(after_doc) == 0


def _doc_imported_entity_count(doc: dict[str, Any]) -> int | None:
    report = doc.get("import_report") or {}
    stats = report.get("stats") or {}
    raw_count = _safe_int(stats.get("raw_entity_count"))
    if raw_count is not None:
        return raw_count
    canonical_count = _safe_int(stats.get("canonical_entity_count"))
    if canonical_count is not None:
        return canonical_count
    entities = doc.get("entities")
    if isinstance(entities, list):
        return len(entities)
    return None


@contextmanager
def _temporary_bridge_roi_args(adapter: Any, roi_request: dict[str, Any]):
    previous = tuple(getattr(adapter, "args_template"))
    roi_json = json.dumps(roi_request, ensure_ascii=False, separators=(",", ":"))
    roi_json_template = roi_json.replace("{", "{{").replace("}", "}}")
    setattr(adapter, "args_template", (*previous, "--roi-json", roi_json_template))
    try:
        yield
    finally:
        setattr(adapter, "args_template", previous)


def _cap_truncation_assessment(before_doc: dict[str, Any], after_doc: dict[str, Any]) -> dict[str, Any]:
    sides = {
        "before": _cap_truncation_side(before_doc),
        "after": _cap_truncation_side(after_doc),
    }
    return {
        "possibly_truncated": any(item.get("possibly_truncated") for item in sides.values()),
        "sides": sides,
    }


def _cap_truncation_side(doc: dict[str, Any]) -> dict[str, Any]:
    report = doc.get("import_report") or {}
    stats = report.get("stats") or {}
    max_entities = _doc_bridge_max_entities(doc)
    # Trust the bridge's authoritative truncation flag only. The previous
    # ``raw_count >= max_entities`` heuristic false-positived whenever a drawing
    # legitimately held exactly ``max_entities`` entities, which silently
    # reclassified genuine false negatives as cap-truncated skips.
    explicit_truncated = _doc_bridge_possibly_truncated(doc)
    raw_count = _safe_int(stats.get("raw_entity_count"))
    canonical_count = _safe_int(stats.get("canonical_entity_count"))
    possibly_truncated = bool(explicit_truncated)
    return {
        "possibly_truncated": possibly_truncated,
        "raw_entity_count": raw_count,
        "canonical_entity_count": canonical_count,
        "max_entities": max_entities,
    }


def _doc_bridge_max_entities(doc: dict[str, Any]) -> int | None:
    metadata = doc.get("metadata") or {}
    adapter_metadata = metadata.get("adapter_metadata") if isinstance(metadata, dict) else None
    if not isinstance(adapter_metadata, dict):
        return None
    for key in ("commercial_dwg_json_bridge", "zwcad_dwg_json_bridge"):
        section = adapter_metadata.get(key)
        if isinstance(section, dict):
            value = _safe_int(section.get("max_entities"))
            if value:
                return value
    return None


def _doc_bridge_possibly_truncated(doc: dict[str, Any]) -> bool:
    metadata = doc.get("metadata") or {}
    adapter_metadata = metadata.get("adapter_metadata") if isinstance(metadata, dict) else None
    if not isinstance(adapter_metadata, dict):
        return False
    for key in ("commercial_dwg_json_bridge", "zwcad_dwg_json_bridge", "autocad_dwg_json_bridge"):
        section = adapter_metadata.get(key)
        if isinstance(section, dict) and (
            section.get("truncated") is True or section.get("possibly_truncated") is True
        ):
            return True
    return False


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _is_fixture_file(record: dict[str, Any]) -> bool:
    if record.get("json_fixture") is True:
        return True
    path = Path(str(record.get("absolute_path") or ""))
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return DwgJsonFixtureAdapter.MARKER in data


def _import_with_cache(
    importer: DwgImporter,
    path: Path,
    record: dict[str, Any],
    import_cache: dict[str, dict[str, Any]] | None,
    *,
    cache_key_suffix: str = "",
    cache_failures: bool = True,
) -> tuple[dict[str, Any], bool]:
    if import_cache is None:
        return importer.import_file(path), False
    key = _import_cache_key(record, path) + cache_key_suffix
    if key in import_cache:
        return import_cache[key], True
    doc = importer.import_file(path)
    # ROI imports pass cache_failures=False so a transient ROI timeout is retried
    # rather than permanently quarantined, while successful ROI extractions are
    # still reused across attempts/pairs that share the same file + ROI.
    report = doc.get("import_report") or {}
    if cache_failures or report.get("status") in {"ok", "partial"}:
        import_cache[key] = doc
    return doc, False


def _import_cache_key(record: dict[str, Any], path: Path) -> str:
    version = str(record.get("dwg_version") or "")
    size = str(record.get("file_size_bytes") or "")
    digest = str(record.get("sha256") or "").strip().lower()
    if digest and set(digest) != {"0"}:
        return "|".join(["sha256", digest, size, version])
    try:
        resolved = str(path.resolve())
    except OSError:
        resolved = str(path)
    return "|".join(["path", resolved.casefold(), size, version])


def _create_evaluation_backend(dwg_backend: str) -> dict[str, Any]:
    raw = str(dwg_backend or EVALUATION_BACKEND_FIXTURE_ONLY).strip()
    normalized = raw.casefold().replace("-", "_")
    if normalized in {"", EVALUATION_BACKEND_FIXTURE_ONLY, "fixture", "fixtures"}:
        return {"mode": EVALUATION_BACKEND_FIXTURE_ONLY, "selection": None}
    selection = create_dwg_backend_selection(normalize_dwg_backend_mode(raw))
    return {"mode": selection.mode, "selection": selection}


def _adapter_for_pair(backend_selection: dict[str, Any], fixture_pair: bool):
    if fixture_pair:
        return DwgJsonFixtureAdapter()
    selection = backend_selection.get("selection")
    if selection is None:
        return DwgJsonFixtureAdapter()
    return selection.adapter


def _backend_report(backend_selection: dict[str, Any]) -> dict[str, Any]:
    if backend_selection["mode"] == EVALUATION_BACKEND_FIXTURE_ONLY:
        return {"mode": EVALUATION_BACKEND_FIXTURE_ONLY, "source": "default", "adapter": "dwg-json-fixture"}
    return backend_selection["selection"].to_dict()


def _pair_backend_report(backend_selection: dict[str, Any], fixture_pair: bool) -> dict[str, Any]:
    if fixture_pair:
        return {"mode": EVALUATION_BACKEND_FIXTURE_ONLY, "source": "fixture", "adapter": "dwg-json-fixture"}
    return _backend_report(backend_selection)


def _print_progress(index: int, total: int, pair_report: dict[str, Any]) -> None:
    elapsed = (pair_report.get("timing_ms") or {}).get("total")
    detail = pair_report.get("classification") or pair_report.get("skip_reason") or ""
    roi_suffix = ""
    roi_info = pair_report.get("roi_retry") or pair_report.get("roi_first_attempt")
    if isinstance(roi_info, dict) and roi_info.get("attempted"):
        launched = roi_info.get("launched_attempts", roi_info.get("attempt_count"))
        roi_suffix = f" roi_cad_launches={launched}"
        if roi_info.get("capped_at_max_attempts"):
            roi_suffix += " roi_capped=1"
    print(
        "[{}/{}] pair_id={} status={} detail={} elapsed_ms={}{}".format(
            index,
            total,
            pair_report.get("pair_id"),
            pair_report.get("status"),
            detail,
            elapsed if elapsed is not None else "",
            roi_suffix,
        ),
        file=sys.stderr,
        flush=True,
    )


def _is_structural_change(change: dict[str, Any]) -> bool:
    entity_type = str(change.get("entity_type") or "")
    if entity_type not in STRUCTURAL_ENTITY_TYPES:
        return False
    change_type = str(change.get("change_type") or "")
    if change_type in {"added", "removed"}:
        return True
    categories = set((change.get("geometry_diff") or {}).get("categories") or [])
    categories.update(str(diff.get("path") or "").split(".", 1)[0] for diff in change.get("attribute_diffs") or [])
    return bool(categories & STRUCTURAL_CATEGORIES)


def _change_summary(change: dict[str, Any]) -> dict[str, Any]:
    return {
        "change_id": change.get("change_id"),
        "change_type": change.get("change_type"),
        "entity_type": change.get("entity_type"),
        "layer_name": change.get("layer_name"),
        "location": change.get("location"),
        "categories": (change.get("geometry_diff") or {}).get("categories") or [],
        "field_paths": [
            field.get("path")
            for field in ((change.get("geometry_diff") or {}).get("fields") or [])
        ],
    }


def _classification(expected_changed: bool, predicted_changed: bool) -> str:
    if expected_changed and predicted_changed:
        return "TP"
    if expected_changed and not predicted_changed:
        return "FN"
    if not expected_changed and predicted_changed:
        return "FP"
    return "TN"


def _failure_bucket(pair: dict[str, Any], classification: str, structural_changes: Sequence[dict[str, Any]]) -> str | None:
    if classification not in {"FP", "FN"}:
        return None
    if classification == "FN":
        for change in pair.get("expected_changes") or []:
            if change.get("failure_bucket_hint"):
                return str(change["failure_bucket_hint"])
    pair_type = str(pair.get("pair_type") or "")
    if pair_type == "block_transform_case":
        return "block_transform_noise"
    if pair_type == "import_edge_case":
        return _import_edge_bucket(pair)
    if pair_type == "non_structural_noise":
        text = f"{pair.get('pair_id') or ''} {pair.get('notes') or ''}".lower()
        return "title_block_noise" if "title" in text or "revision" in text else "text_dimension_noise"
    if pair_type == "version_resave":
        return "unit_scale_mismatch"
    if pair_type == "identical":
        return "unknown"
    if structural_changes:
        categories = {category for change in structural_changes for category in change.get("categories") or []}
        if "block" in categories:
            return "block_transform_noise"
        if "geometry" in categories:
            return "curve_approximation_noise"
    return "unknown"


def _import_edge_bucket(pair: dict[str, Any]) -> str:
    expected = json.dumps(pair.get("expected_changes") or [], ensure_ascii=False).lower()
    if "ocs" in expected or "normal" in expected:
        return "ocs_normal_mismatch"
    if "arc" in expected or "curve" in expected:
        return "curve_approximation_noise"
    return "bridge_import_loss"


def _metrics_by_dimension(evaluated: Sequence[dict[str, Any]], dimension: str) -> dict[str, Any]:
    groups: dict[str, Counter[str]] = defaultdict(Counter)
    for item in evaluated:
        groups[str(item.get(dimension) or "unknown")][str(item.get("classification") or "")] += 1
    return {
        key: {"evaluated_pair_count": sum(counts.values()), **_metrics(counts)}
        for key, counts in sorted(groups.items())
    }


def _metrics(counts: Counter[str]) -> dict[str, Any]:
    tp = int(counts.get("TP", 0))
    fp = int(counts.get("FP", 0))
    fn = int(counts.get("FN", 0))
    tn = int(counts.get("TN", 0))
    precision = _round_metric(tp / (tp + fp)) if tp + fp else None
    recall = _round_metric(tp / (tp + fn)) if tp + fn else None
    f1 = _round_metric((2 * precision * recall) / (precision + recall)) if precision is not None and recall is not None and precision + recall else None
    negative_fp_rate = _round_metric(fp / (fp + tn)) if fp + tn else None
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp_count": tp,
        "fp_count": fp,
        "fn_count": fn,
        "tn_count": tn,
        "negative_fp_rate": negative_fp_rate,
    }


def _target_assessment(active_pair_count: int, evaluated_pair_count: int, metrics: dict[str, Any]) -> dict[str, Any]:
    blockers = []
    if active_pair_count < 50:
        blockers.append(f"active_pair_count={active_pair_count}/50")
    if evaluated_pair_count < active_pair_count:
        blockers.append(f"evaluated_pair_count={evaluated_pair_count}/{active_pair_count}")
    if metrics.get("fn_count") != 0:
        blockers.append(f"structural_fn={metrics.get('fn_count')}/0")
    precision = metrics.get("precision")
    recall = metrics.get("recall")
    if precision is not None and precision < 0.95:
        blockers.append(f"precision={precision}/0.95")
    if recall is not None and recall < 0.98:
        blockers.append(f"recall={recall}/0.98")
    return {
        "internal_pilot_accuracy": {
            "status": "passed" if not blockers else "blocked",
            "blockers": blockers,
        }
    }


def _report_status(evaluated: Sequence[dict[str, Any]], active_pair_count: int, metrics: dict[str, Any]) -> str:
    if not evaluated:
        return "skipped"
    assessment = _target_assessment(active_pair_count, len(evaluated), metrics)
    if any(item.get("status") == "blocked" for item in assessment.values()):
        return "blocked"
    return "passed"


def _dwg_commercial_env_updates(args: argparse.Namespace) -> dict[str, str | None]:
    updates = {
        COMMERCIAL_SDK_ADAPTER_ENV: getattr(args, "dwg_commercial_adapter_spec", None),
        COMMAND_ENV: getattr(args, "dwg_bridge_command", None),
        ARGS_JSON_ENV: getattr(args, "dwg_bridge_args_json", None),
        LICENSE_ID_ENV: getattr(args, "dwg_bridge_license_id", None),
        SUPPORTED_VERSIONS_ENV: getattr(args, "dwg_bridge_supported_versions", None),
        TIMEOUT_SECONDS_ENV: (
            str(args.dwg_bridge_timeout_seconds)
            if getattr(args, "dwg_bridge_timeout_seconds", None) is not None
            else None
        ),
    }
    try:
        normalized_backend = normalize_dwg_backend_mode(getattr(args, "dwg_backend", ""))
    except ValueError:
        normalized_backend = ""
    if normalized_backend == DWG_BACKEND_COMMERCIAL_SDK and not updates[COMMERCIAL_SDK_ADAPTER_ENV]:
        updates[
            COMMERCIAL_SDK_ADAPTER_ENV
        ] = "src.services.comparison.commercial_dwg_json_adapter:create_adapter"
    return updates


@contextmanager
def _temporary_env(updates: dict[str, str | None]):
    previous: dict[str, str | None] = {}
    try:
        for key, value in updates.items():
            if value is None:
                continue
            previous[key] = os.environ.get(key)
            os.environ[key] = str(value)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _parse_roi_margins_arg(value: str | None) -> tuple[float, ...] | None:
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip()
    try:
        if raw.startswith("["):
            parsed = json.loads(raw)
            items = parsed if isinstance(parsed, list) else None
        else:
            items = [item.strip() for item in raw.replace(";", ",").split(",")]
    except json.JSONDecodeError as exc:
        raise ValueError("--dwg-bridge-roi-retry-margins must be a JSON array or comma-separated numbers.") from exc
    if items is None:
        raise ValueError("--dwg-bridge-roi-retry-margins JSON value must be an array.")
    margins: list[float] = []
    for item in items:
        if item in (None, ""):
            continue
        margin = _safe_float(item)
        if margin is None or margin < 0:
            raise ValueError("--dwg-bridge-roi-retry-margins values must be finite non-negative numbers.")
        margins.append(margin)
    return tuple(margins) if margins else None


def _round_metric(value: float) -> float:
    return round(float(value), 6)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = _resolve(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    resolved = _resolve(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(text, encoding="utf-8")


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
