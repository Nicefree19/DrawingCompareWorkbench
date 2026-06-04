"""Evaluate local DWG accuracy evidence against compare results.

This runner consumes normalized local evidence, executes pairs that can be
decoded by the MIT-safe fixture adapter, and reports structural TP/FP/FN
metrics. Pairs that require a commercial bridge are skipped rather than being
silently counted as pass/fail.
"""

from __future__ import annotations

import argparse
import os
import json
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
        pair_report = _evaluate_pair(pair, files_by_id, backend_selection, import_cache)
        pair_reports.append(pair_report)
        if progress:
            _print_progress(index, len(active_pairs), pair_report)
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)

    evaluated = [item for item in pair_reports if item["status"] == "evaluated"]
    skipped = [item for item in pair_reports if item["status"] == "skipped"]
    counts = Counter(item.get("classification") for item in evaluated)
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
    try:
        import_start = time.perf_counter()
        importer = DwgImporter(adapter=_adapter_for_pair(backend_selection, before_is_fixture and after_is_fixture))
        old_doc, before_cache_hit = _import_with_cache(
            importer,
            before_path,
            before,
            import_cache,
        )
        new_doc, after_cache_hit = _import_with_cache(
            importer,
            after_path,
            after,
            import_cache,
        )
        import_ms = round((time.perf_counter() - import_start) * 1000.0, 3)
        for label, doc in (("before", old_doc), ("after", new_doc)):
            report = doc.get("import_report") or {}
            if report.get("status") not in {"ok", "partial"}:
                return {
                    **base,
                    "status": "skipped",
                    "skip_reason": f"{label}_import_{report.get('error_code') or report.get('status')}",
                    "import_report": {"before": old_doc.get("import_report"), "after": new_doc.get("import_report")},
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
    return evaluated


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
) -> tuple[dict[str, Any], bool]:
    if import_cache is None:
        return importer.import_file(path), False
    key = _import_cache_key(record, path)
    if key in import_cache:
        return import_cache[key], True
    doc = importer.import_file(path)
    import_cache[key] = doc
    return doc, False


def _import_cache_key(record: dict[str, Any], path: Path) -> str:
    return "|".join(
        [
            str(record.get("file_id") or path),
            str(record.get("sha256") or ""),
            str(record.get("file_size_bytes") or ""),
            str(record.get("dwg_version") or ""),
        ]
    )


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
    print(
        "[{}/{}] pair_id={} status={} detail={} elapsed_ms={}".format(
            index,
            total,
            pair_report.get("pair_id"),
            pair_report.get("status"),
            detail,
            elapsed if elapsed is not None else "",
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
