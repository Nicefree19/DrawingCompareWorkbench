"""Build release-readiness baseline metrics from current compare evidence.

The output is intended for ``audit_drawing_compare_release_readiness.py`` as a
baseline metrics input.  It only records metrics that are derived from actual
artifacts or command runs.  Missing evidence is reported in
``known_missing_metrics`` instead of being filled with placeholder values.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.comparison.accuracy_metrics import (  # noqa: E402
    ExpectedChange,
    compute_metrics,
    expected_change_from_dict,
    match_changes_to_truth,
)


SCHEMA_VERSION = "dwg-release-baseline-metrics/v1"
DEFAULT_GOLDEN_MANIFEST = Path("tests/data/comparison/golden/manifest.yaml")
DEFAULT_RESULT_DIR = Path("build/reports/dwg-release-baseline-metrics")
DEFAULT_OUTPUT = Path("build/reports/dwg-release-baseline-metrics.json")
RELEASE_METRIC_KEYS = (
    "recall",
    "precision",
    "false_positive_zone_rate",
    "duplicate_zone_rate",
    "overlay_error_px_150dpi",
    "small_drawing_seconds",
    "medium_drawing_seconds",
    "large_drawing_seconds",
    "progress_max_gap_s",
    "cancel_response_s",
    "orphan_processes",
    "customer_path_oda_calls",
    "exported_sensitive_path_leaks",
)


@dataclass(frozen=True)
class CompareExecution:
    exit_code: int | None
    elapsed_s: float
    stdout_tail: str = ""
    stderr_tail: str = ""
    timed_out: bool = False


@dataclass(frozen=True)
class NormalizedChange:
    location: tuple[float, float] | None
    change_type: str
    layer: str | None = None
    entity_type: str | None = None
    change_category: str | None = None


CompareRunner = Callable[[Sequence[str], float], CompareExecution]


def build_metrics(
    *,
    golden_manifest: Path = DEFAULT_GOLDEN_MANIFEST,
    result_dir: Path = DEFAULT_RESULT_DIR,
    output_json: Path = DEFAULT_OUTPUT,
    product_evidence_json: Path | Sequence[Path] | None = None,
    fallback_audit_json: Path | None = None,
    sharable_path_audits: Sequence[Path] = (),
    large_dwg_probe: Path | None = None,
    supplemental_probe: Path | None = None,
    python_executable: str = sys.executable,
    pair_timeout_seconds: float = 120.0,
    max_entities: int = 200_000,
    max_dxf_tokens: int = 30_000_000,
    reuse_existing_results: bool = False,
    compare_runner: CompareRunner | None = None,
) -> dict[str, Any]:
    golden_manifest = _resolve(golden_manifest)
    result_dir = _resolve(result_dir)
    output_json = _resolve(output_json)
    result_dir.mkdir(parents=True, exist_ok=True)
    manifest = _load_structured(golden_manifest)
    pairs = _manifest_pairs(manifest)

    pair_reports: list[dict[str, Any]] = []
    aggregate_tp = aggregate_fp = aggregate_fn = aggregate_duplicate_fp = 0
    small_seconds: list[float] = []

    for pair in pairs:
        pair_report = _run_golden_pair(
            pair,
            manifest_dir=golden_manifest.parent,
            result_dir=result_dir,
            python_executable=python_executable,
            pair_timeout_seconds=pair_timeout_seconds,
            max_entities=max_entities,
            max_dxf_tokens=max_dxf_tokens,
            reuse_existing_results=reuse_existing_results,
            compare_runner=compare_runner or _run_compare_command,
        )
        pair_reports.append(pair_report)
        metrics = pair_report.get("metrics") if isinstance(pair_report.get("metrics"), dict) else {}
        aggregate_tp += int(metrics.get("tp_count") or 0)
        aggregate_fp += int(metrics.get("fp_count") or 0)
        aggregate_fn += int(metrics.get("fn_count") or 0)
        aggregate_duplicate_fp += int(metrics.get("duplicate_fp_count") or 0)
        elapsed = _as_float(pair_report.get("elapsed_s"))
        if elapsed is not None:
            small_seconds.append(elapsed)

    metrics_block = _aggregate_quality_metrics(
        tp=aggregate_tp,
        fp=aggregate_fp,
        fn=aggregate_fn,
        duplicate_fp=aggregate_duplicate_fp,
    )
    if small_seconds:
        metrics_block["small_drawing_seconds"] = round(max(small_seconds), 6)

    _add_process_policy_metrics(
        metrics_block,
        product_evidence_json=product_evidence_json,
        fallback_audit_json=fallback_audit_json,
        sharable_path_audits=sharable_path_audits,
        large_dwg_probe=large_dwg_probe,
        supplemental_probe=supplemental_probe,
    )
    known_missing = [key for key in RELEASE_METRIC_KEYS if key not in metrics_block]

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(),
        "source_policy": "repo regression and supplied release artifacts only; missing metrics are not synthesized",
        "golden_manifest": str(golden_manifest),
        "result_dir": str(result_dir),
        "evidence_counts": _evidence_counts(
            pair_reports,
            product_evidence_json=product_evidence_json,
            fallback_audit_json=fallback_audit_json,
            large_dwg_probe=large_dwg_probe,
            supplemental_probe=supplemental_probe,
        ),
        "metrics": metrics_block,
        "known_missing_metrics": known_missing,
        "summary": {
            "golden_pair_count": len(pair_reports),
            "golden_pair_passed_count": sum(1 for item in pair_reports if item.get("status") == "passed"),
            "tp_count": aggregate_tp,
            "fp_count": aggregate_fp,
            "fn_count": aggregate_fn,
            "duplicate_fp_count": aggregate_duplicate_fp,
        },
        "inputs": {
            "product_evidence_json": [str(path) for path in _path_list(product_evidence_json)],
            "fallback_audit_json": str(fallback_audit_json) if fallback_audit_json else "",
            "sharable_path_audits": [str(path) for path in sharable_path_audits],
            "large_dwg_probe": str(large_dwg_probe) if large_dwg_probe else "",
            "supplemental_probe": str(supplemental_probe) if supplemental_probe else "",
        },
        "pairs": pair_reports,
    }
    _write_json(output_json, report)
    return report


def _run_golden_pair(
    pair: dict[str, Any],
    *,
    manifest_dir: Path,
    result_dir: Path,
    python_executable: str,
    pair_timeout_seconds: float,
    max_entities: int,
    max_dxf_tokens: int,
    reuse_existing_results: bool,
    compare_runner: CompareRunner,
) -> dict[str, Any]:
    pair_id = str(pair.get("pair_id") or pair.get("id") or f"pair_{len(str(pair))}")
    before = _resolve_manifest_path(manifest_dir, pair.get("before_path"))
    after = _resolve_manifest_path(manifest_dir, pair.get("after_path"))
    truth_path = _resolve_manifest_path(manifest_dir, pair.get("expected_changes_path"))
    pair_result_dir = result_dir / pair_id
    pair_result_dir.mkdir(parents=True, exist_ok=True)
    result_json = pair_result_dir / "cad_compare_result.json"

    execution = CompareExecution(exit_code=0, elapsed_s=0.0)
    if not reuse_existing_results or not result_json.exists():
        command = [
            python_executable,
            "-m",
            "src.cli.cad_compare",
            "file",
            str(before),
            str(after),
            "--output",
            str(result_json),
            "--max-entities",
            str(max_entities),
            "--max-dxf-tokens",
            str(max_dxf_tokens),
        ]
        execution = compare_runner(command, pair_timeout_seconds)

    payload = _load_json(result_json)
    truth_payload = _load_json(truth_path)
    if not isinstance(payload, dict):
        return _pair_error(pair_id, before, after, truth_path, result_json, execution, "result_json_missing")
    if not isinstance(truth_payload, dict):
        return _pair_error(pair_id, before, after, truth_path, result_json, execution, "truth_json_missing")

    truth = [
        expected_change_from_dict(item)
        for item in truth_payload.get("expected_changes") or []
        if isinstance(item, dict)
    ]
    truth_zones = _collapse_truth_zones(truth)
    predicted = _normalized_prediction_zones(_result_changes(payload))
    match_report = match_changes_to_truth(predicted, truth_zones, location_tol=1.0, strict_type=False, require_layer_match=False)
    metrics = compute_metrics(match_report).to_dict()
    duplicate_fp_count = _duplicate_fp_count(match_report.false_positives, truth_zones)
    metrics["duplicate_fp_count"] = duplicate_fp_count
    summary = _result_summary(payload)
    return {
        "pair_id": pair_id,
        "status": "passed" if execution.exit_code in (0, None) and not execution.timed_out else "failed",
        "before_path": str(before),
        "after_path": str(after),
        "truth_path": str(truth_path),
        "result_json": str(result_json),
        "exit_code": execution.exit_code,
        "timed_out": execution.timed_out,
        "elapsed_s": round(float(execution.elapsed_s), 6),
        "compare_status": str(payload.get("status") or ""),
        "result_status": str((payload.get("result") or {}).get("status") or payload.get("status") or ""),
        "summary": summary,
        "truth_count": len(truth_zones),
        "raw_truth_count": len(truth),
        "predicted_zone_count": len(predicted),
        "raw_change_count": len(_result_changes(payload)),
        "metrics": metrics,
        "stdout_tail": execution.stdout_tail,
        "stderr_tail": execution.stderr_tail,
    }


def _pair_error(
    pair_id: str,
    before: Path,
    after: Path,
    truth_path: Path,
    result_json: Path,
    execution: CompareExecution,
    reason: str,
) -> dict[str, Any]:
    return {
        "pair_id": pair_id,
        "status": "failed",
        "before_path": str(before),
        "after_path": str(after),
        "truth_path": str(truth_path),
        "result_json": str(result_json),
        "exit_code": execution.exit_code,
        "timed_out": execution.timed_out,
        "elapsed_s": round(float(execution.elapsed_s), 6),
        "reason": reason,
        "metrics": {"tp_count": 0, "fp_count": 0, "fn_count": 0, "duplicate_fp_count": 0},
        "stdout_tail": execution.stdout_tail,
        "stderr_tail": execution.stderr_tail,
    }


def _aggregate_quality_metrics(*, tp: int, fp: int, fn: int, duplicate_fp: int) -> dict[str, Any]:
    total_predicted = tp + fp
    total_truth = tp + fn
    metrics: dict[str, Any] = {
        "tp_count": tp,
        "fp_count": fp,
        "fn_count": fn,
        "duplicate_fp_count": duplicate_fp,
    }
    if total_predicted:
        metrics["precision"] = round(tp / total_predicted, 6)
        metrics["false_positive_zone_rate"] = round(fp / total_predicted, 6)
        metrics["duplicate_zone_rate"] = round(duplicate_fp / total_predicted, 6)
    if total_truth:
        metrics["recall"] = round(tp / total_truth, 6)
    return metrics


def _add_process_policy_metrics(
    metrics: dict[str, Any],
    *,
    product_evidence_json: Path | Sequence[Path] | None,
    fallback_audit_json: Path | None,
    sharable_path_audits: Sequence[Path],
    large_dwg_probe: Path | None,
    supplemental_probe: Path | None,
) -> None:
    orphan_values: list[int] = []
    for product_path in _path_list(product_evidence_json):
        product = _load_json(product_path)
        if not isinstance(product, dict):
            continue
        cleanup = product.get("process_cleanup") if isinstance(product.get("process_cleanup"), dict) else {}
        if "orphan_processes" in cleanup:
            orphan_values.append(_as_int(cleanup.get("orphan_processes")))
    if orphan_values:
        metrics["orphan_processes"] = max(orphan_values)

    fallback = _load_json(fallback_audit_json) if fallback_audit_json else None
    if isinstance(fallback, dict):
        calls = 0
        seen = False
        for item in fallback.get("versions") or []:
            if isinstance(item, dict) and "default_customer_oda_calls" in item:
                calls += _as_int(item.get("default_customer_oda_calls"))
                seen = True
        if seen:
            metrics["customer_path_oda_calls"] = calls

    leak_count = 0
    leak_seen = False
    for path in sharable_path_audits:
        payload = _load_json(path)
        if isinstance(payload, dict) and "leak_count" in payload:
            leak_count += _as_int(payload.get("leak_count"))
            leak_seen = True
    if leak_seen:
        metrics["exported_sensitive_path_leaks"] = leak_count

    probe = _load_json(large_dwg_probe) if large_dwg_probe else None
    if isinstance(probe, dict) and probe:
        medium = _as_float(probe.get("medium_drawing_seconds"))
        if medium is not None:
            metrics["medium_drawing_seconds"] = round(medium, 6)
        large = _as_float(probe.get("large_drawing_seconds"))
        if large is None:
            large = _as_float(probe.get("elapsed_s"))
        if large is not None:
            metrics["large_drawing_seconds"] = round(large, 6)
        progress_gap = _as_float(probe.get("progress_max_gap_s"))
        if progress_gap is not None:
            metrics["progress_max_gap_s"] = round(progress_gap, 6)
        cancel_probe = probe.get("cancel_probe") if isinstance(probe.get("cancel_probe"), dict) else {}
        cancel = _as_float(cancel_probe.get("cancel_to_idle_s"))
        if cancel is not None:
            metrics["cancel_response_s"] = round(cancel, 6)

    supplemental = _load_json(supplemental_probe) if supplemental_probe else None
    supplemental_metrics = supplemental.get("metrics") if isinstance(supplemental, dict) else {}
    if isinstance(supplemental_metrics, dict):
        overlay_error = _as_float(supplemental_metrics.get("overlay_error_px_150dpi"))
        if overlay_error is not None:
            metrics["overlay_error_px_150dpi"] = round(overlay_error, 6)


def _evidence_counts(
    pair_reports: Sequence[dict[str, Any]],
    *,
    product_evidence_json: Path | Sequence[Path] | None,
    fallback_audit_json: Path | None,
    large_dwg_probe: Path | None,
    supplemental_probe: Path | None,
) -> dict[str, Any]:
    product_pairs: list[dict[str, Any]] = []
    for product_path in _path_list(product_evidence_json):
        product = _load_json(product_path)
        if isinstance(product, dict):
            product_pairs.extend(item for item in product.get("pairs") or [] if isinstance(item, dict))

    converted_by_version = _converted_fallback_counts(fallback_audit_json)
    product_converted_by_version = {code: 0 for code in converted_by_version}
    for pair in product_pairs:
        version = str(pair.get("version") or pair.get("version_code") or "").upper()
        provenance = pair.get("provenance") if isinstance(pair.get("provenance"), dict) else {}
        backend = str(
            provenance.get("selected_dwg_backend_mode")
            or pair.get("dwg_backend_mode")
            or pair.get("backend")
            or ""
        ).lower()
        if version in converted_by_version and ("converted" in backend or "fallback" in backend or "oda" in backend):
            product_converted_by_version[version] += 1
    for version, count in product_converted_by_version.items():
        converted_by_version[version] = max(converted_by_version[version], count)

    probe = _load_json(large_dwg_probe) if large_dwg_probe else None
    large_cad_dxf_pairs = 0
    if isinstance(probe, dict):
        large_cad_dxf_pairs = _as_int(probe.get("large_cad_dxf_pairs"))
        if not large_cad_dxf_pairs and isinstance(probe.get("large_pairs"), list):
            large_cad_dxf_pairs = sum(
                1 for item in probe.get("large_pairs") or [] if isinstance(item, dict) and item.get("status") == "passed"
            )

    counts = {
        "pdf_pairs": 0,
        "dxf_pairs": len(pair_reports),
        "large_cad_dxf_pairs": large_cad_dxf_pairs,
        "ac1015_native_baselines": sum(
            1 for pair in product_pairs if str(pair.get("version") or "").upper() == "AC1015" and pair.get("status") == "passed"
        ),
        "ac1024_converted_dxf_fallback_pairs": converted_by_version["AC1024"],
        "ac1027_converted_dxf_fallback_pairs": converted_by_version["AC1027"],
        "ac1032_converted_dxf_fallback_pairs": converted_by_version["AC1032"],
        "negative_failure_samples": sum(1 for pair in pair_reports if _as_int(pair.get("raw_truth_count")) == 0),
        "partial_import_samples": sum(1 for pair in pair_reports if str(pair.get("compare_status") or "").lower() == "partial"),
        "block_text_dimension_pairs": sum(1 for pair in pair_reports if _is_block_text_dimension_pair(str(pair.get("pair_id") or ""))),
        "converted_dxf_fallback_pairs_by_version": converted_by_version,
    }
    supplemental = _load_json(supplemental_probe) if supplemental_probe else None
    supplemental_counts = supplemental.get("evidence_counts") if isinstance(supplemental, dict) else {}
    if isinstance(supplemental_counts, dict):
        for key in ("pdf_pairs", "negative_failure_samples", "block_text_dimension_pairs"):
            counts[key] = _as_int(counts.get(key)) + _as_int(supplemental_counts.get(key))
    return counts


def _converted_fallback_counts(fallback_audit_json: Path | None) -> dict[str, int]:
    converted_by_version = {"AC1024": 0, "AC1027": 0, "AC1032": 0}
    fallback = _load_json(fallback_audit_json) if fallback_audit_json else None
    if not isinstance(fallback, dict):
        return converted_by_version
    versions = fallback.get("versions")
    if isinstance(versions, dict):
        items = ({"code": code, **value} for code, value in versions.items() if isinstance(value, dict))
    elif isinstance(versions, list):
        items = (item for item in versions if isinstance(item, dict))
    else:
        return converted_by_version
    for item in items:
        code = str(item.get("code") or "").upper()
        if code in converted_by_version:
            converted_by_version[code] = max(
                converted_by_version[code],
                _as_int(item.get("converted_dxf_baseline_count")),
            )
    return converted_by_version


def _is_block_text_dimension_pair(pair_id: str) -> bool:
    lowered = pair_id.lower()
    return any(token in lowered for token in ("block", "attribute", "attrib", "text", "dimension", "dim"))


def _normalized_prediction_zones(changes: Sequence[dict[str, Any]]) -> list[NormalizedChange]:
    normalized = [_normalize_change(item) for item in changes]
    normalized = [item for item in normalized if item.location is not None]
    return _collapse_added_deleted_pairs(normalized)


def _collapse_truth_zones(truth: Sequence[ExpectedChange], *, max_distance: float = 60.0) -> list[ExpectedChange]:
    output: list[ExpectedChange] = []
    used: set[int] = set()
    for index, expected in enumerate(truth):
        if index in used:
            continue
        if expected.change_type.lower() not in {"added", "deleted"} or expected.location is None:
            output.append(expected)
            used.add(index)
            continue
        match_index = None
        for candidate_index, candidate in enumerate(truth):
            if candidate_index == index or candidate_index in used:
                continue
            if candidate.change_type.lower() == expected.change_type.lower() or candidate.change_type.lower() not in {"added", "deleted"}:
                continue
            if candidate.location is None:
                continue
            if not _entity_types_compatible(expected.entity_type, candidate.entity_type):
                continue
            tolerance = max(
                max_distance,
                float(expected.tolerance_mm or 0.0),
                float(candidate.tolerance_mm or 0.0),
            )
            if _distance(expected.location, candidate.location) <= tolerance:
                match_index = candidate_index
                break
        if match_index is None:
            output.append(expected)
            used.add(index)
            continue
        other = truth[match_index]
        output.append(
            ExpectedChange(
                location=(
                    (expected.location[0] + other.location[0]) / 2.0,
                    (expected.location[1] + other.location[1]) / 2.0,
                ),
                change_type="modified",
                layer=expected.layer if expected.layer == other.layer else None,
                entity_type=_merged_entity_type(expected.entity_type, other.entity_type),
                tolerance_mm=max(float(expected.tolerance_mm or 1.0), float(other.tolerance_mm or 1.0)),
                notes="; ".join(part for part in (expected.notes, other.notes) if part),
            )
        )
        used.add(index)
        used.add(match_index)
    return output


def _collapse_added_deleted_pairs(changes: Sequence[NormalizedChange], *, max_distance: float = 60.0) -> list[NormalizedChange]:
    output: list[NormalizedChange] = []
    used: set[int] = set()
    for index, change in enumerate(changes):
        if index in used:
            continue
        if change.change_type not in {"added", "deleted"} or change.location is None:
            output.append(change)
            used.add(index)
            continue
        match_index = None
        for candidate_index, candidate in enumerate(changes):
            if candidate_index == index or candidate_index in used:
                continue
            if candidate.change_type == change.change_type or candidate.change_type not in {"added", "deleted"}:
                continue
            if candidate.layer != change.layer or candidate.entity_type != change.entity_type or candidate.location is None:
                continue
            if _distance(change.location, candidate.location) <= max_distance:
                match_index = candidate_index
                break
        if match_index is None:
            output.append(change)
            used.add(index)
            continue
        other = changes[match_index]
        output.append(
            NormalizedChange(
                location=(
                    (change.location[0] + other.location[0]) / 2.0,
                    (change.location[1] + other.location[1]) / 2.0,
                ),
                change_type="modified",
                layer=change.layer,
                entity_type=change.entity_type,
                change_category=change.change_category or other.change_category,
            )
        )
        used.add(index)
        used.add(match_index)
    return output


def _normalize_change(change: dict[str, Any]) -> NormalizedChange:
    metadata = change.get("metadata") if isinstance(change.get("metadata"), dict) else {}
    entity_type = _entity_type(metadata.get("entity_type") or change.get("field_name"))
    if entity_type == "BLOCK_REFERENCE" and _has_attribute_text_delta(change):
        entity_type = "ATTRIB"
    location = _block_attribute_location(change) if entity_type == "ATTRIB" else None
    if location is None:
        location = _text_insert_location(change, entity_type)
    if location is None:
        location = _location_from_metadata(metadata)
    if location is None:
        location = _location_from_text(change.get("location"))
    return NormalizedChange(
        location=location,
        change_type=str(change.get("change_type") or "").lower(),
        layer=_optional_str(metadata.get("layer")),
        entity_type=entity_type,
        change_category=_optional_str(metadata.get("change_category")),
    )


def _has_attribute_text_delta(change: dict[str, Any]) -> bool:
    old_attrs = _attribute_text_key(change.get("old_value"))
    new_attrs = _attribute_text_key(change.get("new_value"))
    return bool(old_attrs or new_attrs) and old_attrs != new_attrs


def _attribute_text_key(value: Any) -> tuple[tuple[str, str], ...]:
    entity = value if isinstance(value, dict) else {}
    geometry = entity.get("geometry") if isinstance(entity.get("geometry"), dict) else {}
    attrs = geometry.get("attributes") if isinstance(geometry.get("attributes"), list) else []
    key: list[tuple[str, str]] = []
    for attr in attrs:
        if not isinstance(attr, dict):
            continue
        tag = str(attr.get("tag") or "")
        text = str(attr.get("canonical_text") or attr.get("text") or "")
        key.append((tag, text))
    return tuple(sorted(key))


def _block_attribute_location(change: dict[str, Any]) -> tuple[float, float] | None:
    for key in ("new_value", "old_value"):
        entity = change.get(key) if isinstance(change.get(key), dict) else {}
        geometry = entity.get("geometry") if isinstance(entity.get("geometry"), dict) else {}
        insert = geometry.get("insert") if isinstance(geometry.get("insert"), dict) else {}
        base_x = _as_float(insert.get("x"))
        base_y = _as_float(insert.get("y"))
        if base_x is None or base_y is None:
            continue
        attrs = geometry.get("attributes") if isinstance(geometry.get("attributes"), list) else []
        for attr in attrs:
            if not isinstance(attr, dict):
                continue
            attr_insert = attr.get("insert") if isinstance(attr.get("insert"), dict) else {}
            attr_x = _as_float(attr_insert.get("x")) or 0.0
            attr_y = _as_float(attr_insert.get("y")) or 0.0
            return (base_x + attr_x, base_y + attr_y)
        return (base_x, base_y)
    return None


def _duplicate_fp_count(false_positives: Sequence[Any], truth: Sequence[ExpectedChange]) -> int:
    count = 0
    for item in false_positives:
        location = getattr(item, "location", None)
        if location is None:
            continue
        for expected in truth:
            if expected.location is None:
                continue
            tolerance = expected.tolerance_mm if expected.tolerance_mm is not None else 1.0
            if _distance(location, expected.location) <= tolerance:
                count += 1
                break
    return count


def _result_changes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    changes = result.get("changes") if isinstance(result, dict) else None
    return [item for item in changes or [] if isinstance(item, dict)]


def _result_summary(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    summary = result.get("summary") if isinstance(result, dict) else None
    return dict(summary) if isinstance(summary, dict) else {}


def _manifest_pairs(manifest: Any) -> list[dict[str, Any]]:
    if isinstance(manifest, dict) and isinstance(manifest.get("pairs"), list):
        return [item for item in manifest["pairs"] if isinstance(item, dict)]
    if isinstance(manifest, list):
        return [item for item in manifest if isinstance(item, dict)]
    raise ValueError("Manifest must contain a pairs list")


def _path_list(value: Path | Sequence[Path] | None) -> list[Path]:
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        return [Path(value)]
    return [Path(item) for item in value]


def _load_structured(path: Path) -> Any:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImportError("PyYAML is required for YAML manifests; use JSON instead") from exc
        return yaml.safe_load(text)
    return json.loads(text)


def _run_compare_command(command: Sequence[str], timeout_seconds: float) -> CompareExecution:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            list(command),
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        return CompareExecution(
            exit_code=completed.returncode,
            elapsed_s=time.perf_counter() - started,
            stdout_tail=_tail(completed.stdout),
            stderr_tail=_tail(completed.stderr),
        )
    except subprocess.TimeoutExpired as exc:
        return CompareExecution(
            exit_code=None,
            elapsed_s=time.perf_counter() - started,
            stdout_tail=_tail(exc.stdout),
            stderr_tail=_tail(exc.stderr),
            timed_out=True,
        )


def _load_json(path: Path | None) -> Any:
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def _resolve(path: Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else (ROOT / path).resolve()


def _resolve_manifest_path(manifest_dir: Path, value: Any) -> Path:
    if value is None:
        return manifest_dir
    path = Path(str(value))
    return path if path.is_absolute() else (manifest_dir / path).resolve()


def _location_from_metadata(metadata: dict[str, Any]) -> tuple[float, float] | None:
    x = _as_float(metadata.get("x"))
    y = _as_float(metadata.get("y"))
    if x is None or y is None:
        return None
    return (x, y)


def _location_from_text(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().replace("(", "").replace(")", "")
    if "," not in cleaned:
        return None
    left, right = cleaned.split(",", 1)
    x = _as_float(left)
    y = _as_float(right.split()[0])
    if x is None or y is None:
        return None
    return (x, y)


def _text_insert_location(change: dict[str, Any], entity_type: str | None) -> tuple[float, float] | None:
    if entity_type not in {"TEXT", "MTEXT"}:
        return None
    points: list[tuple[float, float]] = []
    for key in ("old_value", "new_value"):
        entity = change.get(key) if isinstance(change.get(key), dict) else {}
        geometry = entity.get("geometry") if isinstance(entity.get("geometry"), dict) else {}
        insert = geometry.get("insert") if isinstance(geometry.get("insert"), dict) else {}
        x = _as_float(insert.get("x"))
        y = _as_float(insert.get("y"))
        if x is not None and y is not None:
            points.append((x, y))
    if not points:
        return None
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _entity_type(value: Any) -> str | None:
    text = _optional_str(value)
    return text.upper() if text else None


def _entity_types_compatible(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return True
    return _entity_type(left) == _entity_type(right)


def _merged_entity_type(left: str | None, right: str | None) -> str | None:
    left_type = _entity_type(left)
    right_type = _entity_type(right)
    if left_type and right_type and left_type != right_type:
        return None
    return left_type or right_type


def _tail(value: Any, *, max_chars: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = str(value)
    return text[-max_chars:]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden-manifest", type=Path, default=DEFAULT_GOLDEN_MANIFEST)
    parser.add_argument("--result-dir", type=Path, default=DEFAULT_RESULT_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--product-evidence-json", type=Path, action="append", default=[])
    parser.add_argument("--fallback-audit-json", type=Path)
    parser.add_argument("--sharable-path-audit", type=Path, action="append", default=[])
    parser.add_argument("--large-dwg-probe", type=Path)
    parser.add_argument("--supplemental-probe", type=Path)
    parser.add_argument("--pair-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--max-entities", type=int, default=200_000)
    parser.add_argument("--max-dxf-tokens", type=int, default=30_000_000)
    parser.add_argument("--reuse-existing-results", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_metrics(
        golden_manifest=args.golden_manifest,
        result_dir=args.result_dir,
        output_json=args.out,
        product_evidence_json=args.product_evidence_json,
        fallback_audit_json=args.fallback_audit_json,
        sharable_path_audits=args.sharable_path_audit,
        large_dwg_probe=args.large_dwg_probe,
        supplemental_probe=args.supplemental_probe,
        pair_timeout_seconds=args.pair_timeout_seconds,
        max_entities=args.max_entities,
        max_dxf_tokens=args.max_dxf_tokens,
        reuse_existing_results=args.reuse_existing_results,
    )
    print(json.dumps({"status": "written", "out": str(args.out), "known_missing_metrics": report["known_missing_metrics"]}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
