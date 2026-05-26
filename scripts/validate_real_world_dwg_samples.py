"""Validate local real-world DWG sample readiness.

The repository must not copy customer/project DWGs into source control.  This
script reads a local-only manifest, verifies file headers and sizes when the
source folder is available, runs the ODA-free import boundary, and summarizes
descriptor-cache deltas for known legacy pairs.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = Path("tests/data/comparison/real_world/local-dwg-samples.manifest.json")
DEFAULT_GOLDEN = Path("tests/data/comparison/real_world/golden-results.json")
DEFAULT_JSON_REPORT = Path("build/reports/real-world-dwg-validation.json")
DEFAULT_MD_REPORT = Path("build/reports/real-world-dwg-validation.md")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.comparison.dwg_importer import DwgVersionDetector  # noqa: E402
from src.services.comparison.import_pipeline import ImportPipeline, ImportPipelineOptions  # noqa: E402


def load_manifest(path: Path = DEFAULT_MANIFEST, *, root: Path = ROOT) -> dict[str, Any]:
    return json.loads(_resolve(root, path).read_text(encoding="utf-8"))


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != "cad-real-world-local/v1":
        errors.append("manifest.schema_version must be cad-real-world-local/v1")
    if not manifest.get("source_root"):
        errors.append("manifest.source_root is required")
    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        errors.append("manifest.samples must be a non-empty list")
        return errors
    ids = [str(sample.get("id") or "") for sample in samples]
    if len(ids) != len(set(ids)):
        errors.append("sample ids must be unique")
    for index, sample in enumerate(samples):
        prefix = f"samples[{index}]"
        for key in ("id", "path", "format", "expected_version", "expected_size_bytes"):
            if key not in sample:
                errors.append(f"{prefix}.{key} is required")
        if sample.get("format") != "dwg":
            errors.append(f"{prefix}.format must be dwg")
    known = set(ids)
    for index, pair in enumerate(manifest.get("pairs") or []):
        prefix = f"pairs[{index}]"
        if pair.get("old_sample") not in known:
            errors.append(f"{prefix}.old_sample references an unknown sample")
        if pair.get("new_sample") not in known:
            errors.append(f"{prefix}.new_sample references an unknown sample")
    return errors


def build_report(manifest: dict[str, Any], *, root: Path = ROOT, require_local: bool = False) -> dict[str, Any]:
    manifest_errors = validate_manifest(manifest)
    source_root = Path(str(manifest.get("source_root") or ""))
    cache_dir = Path(str(manifest.get("cache_dir") or ""))
    local_available = source_root.exists()
    if require_local and not local_available:
        manifest_errors.append(f"source_root does not exist: {source_root}")

    samples_by_id = {sample["id"]: sample for sample in manifest.get("samples") or [] if "id" in sample}
    sample_results = []
    if local_available:
        pipeline = ImportPipeline(ImportPipelineOptions(allow_oda_fallback=False))
        for sample in manifest.get("samples") or []:
            sample_results.append(_sample_result(sample, source_root=source_root, pipeline=pipeline))

    pair_results = []
    for pair in manifest.get("pairs") or []:
        pair_results.append(
            _pair_result(
                pair,
                samples_by_id=samples_by_id,
                source_root=source_root,
                cache_dir=cache_dir,
                local_available=local_available,
            )
        )

    import_status_counts = Counter(str(item.get("import_status") or "not_run") for item in sample_results)
    unsupported_versions = sorted(
        {
            str(item.get("detected_version"))
            for item in sample_results
            if item.get("import_error_code") == "DWG_UNSUPPORTED_VERSION"
        }
    )
    report = {
        "schema_version": "cad-real-world-dwg-validation/v1",
        "generated_at": datetime.now().isoformat(),
        "manifest_path": str(_resolve(root, DEFAULT_MANIFEST)),
        "source_root": str(source_root),
        "source_root_available": local_available,
        "cache_dir": str(cache_dir),
        "cache_dir_available": cache_dir.exists(),
        "status": "failed" if manifest_errors else ("ok" if local_available else "skipped"),
        "manifest_errors": manifest_errors,
        "summary": {
            "sample_count": len(manifest.get("samples") or []),
            "validated_sample_count": len(sample_results),
            "pair_count": len(manifest.get("pairs") or []),
            "import_status_counts": dict(sorted(import_status_counts.items())),
            "unsupported_versions": unsupported_versions,
        },
        "samples": sample_results,
        "pairs": pair_results,
    }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Real-World DWG Validation Report",
        "",
        f"Status: **{report['status']}**",
        "",
        "## Scope",
        "",
        f"- Source root: `{report['source_root']}`",
        f"- Source available: `{report['source_root_available']}`",
        f"- Cache available: `{report['cache_dir_available']}`",
        f"- Samples: `{report['summary']['sample_count']}`",
        f"- Pairs: `{report['summary']['pair_count']}`",
        "",
        "## Samples",
        "",
        "| id | version | supported | import status | error | size ok |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for sample in report.get("samples") or []:
        lines.append(
            "| {id} | {version} | {supported} | {status} | {error} | {size_ok} |".format(
                id=sample.get("id"),
                version=sample.get("detected_version"),
                supported=sample.get("detected_supported"),
                status=sample.get("import_status"),
                error=sample.get("import_error_code") or "",
                size_ok=sample.get("size_matches_manifest"),
            )
        )
    if not report.get("samples"):
        lines.append("| _not run_ |  |  |  |  |  |")
    lines.extend(["", "## Pairs", "", "| id | descriptor cache | entity delta | fingerprint changed |", "| --- | --- | --- | --- |"])
    for pair in report.get("pairs") or []:
        delta = pair.get("descriptor_delta") or {}
        lines.append(
            "| {id} | {cache} | {delta} | {changed} |".format(
                id=pair.get("id"),
                cache=pair.get("descriptor_cache_status"),
                delta=delta.get("entity_count_delta", ""),
                changed=delta.get("content_fingerprint_changed", ""),
            )
        )
    if report.get("manifest_errors"):
        lines.extend(["", "## Manifest Errors", ""])
        lines.extend(f"- {error}" for error in report["manifest_errors"])
    if report.get("golden_errors"):
        lines.extend(["", "## Golden Errors", ""])
        lines.extend(f"- {error}" for error in report["golden_errors"])
    lines.append("")
    return "\n".join(lines)


def build_golden_results(report: dict[str, Any]) -> dict[str, Any]:
    """Build a compact, source-control-safe golden result snapshot."""

    samples = []
    for sample in report.get("samples") or []:
        samples.append(
            {
                "id": sample.get("id"),
                "expected_version": sample.get("expected_version"),
                "expected_size_bytes": sample.get("expected_size_bytes"),
                "detected_version": sample.get("detected_version"),
                "detected_supported": sample.get("detected_supported"),
                "expected_import_status": sample.get("import_status"),
                "expected_import_error_code": sample.get("import_error_code"),
            }
        )

    pairs = []
    for pair in report.get("pairs") or []:
        delta = pair.get("descriptor_delta") or {}
        pairs.append(
            {
                "id": pair.get("id"),
                "current_import_expectation": pair.get("current_import_expectation"),
                "descriptor_cache_status": pair.get("descriptor_cache_status"),
                "old_entity_count": delta.get("old_entity_count"),
                "new_entity_count": delta.get("new_entity_count"),
                "entity_count_delta": delta.get("entity_count_delta"),
                "entity_type_delta": delta.get("entity_type_delta") or {},
                "old_layer_count": delta.get("old_layer_count"),
                "new_layer_count": delta.get("new_layer_count"),
                "content_fingerprint_changed": delta.get("content_fingerprint_changed"),
            }
        )

    return {
        "schema_version": "cad-real-world-golden/v1",
        "source_policy": "local DWG files are referenced, not copied",
        "native_dwg_supported_versions": ["AC1015"],
        "expected_unsupported_versions": report.get("summary", {}).get("unsupported_versions") or [],
        "samples": samples,
        "pairs": pairs,
    }


def compare_golden(report: dict[str, Any], golden: dict[str, Any]) -> list[str]:
    actual = build_golden_results(report)
    errors: list[str] = []
    if golden.get("schema_version") != actual["schema_version"]:
        errors.append("golden.schema_version mismatch")
    if golden.get("native_dwg_supported_versions") != actual["native_dwg_supported_versions"]:
        errors.append("golden.native_dwg_supported_versions mismatch")
    if golden.get("expected_unsupported_versions") != actual["expected_unsupported_versions"]:
        errors.append("golden.expected_unsupported_versions mismatch")
    errors.extend(_compare_named_items("samples", golden.get("samples") or [], actual.get("samples") or []))
    errors.extend(_compare_named_items("pairs", golden.get("pairs") or [], actual.get("pairs") or []))
    return errors


def _sample_result(sample: dict[str, Any], *, source_root: Path, pipeline: ImportPipeline) -> dict[str, Any]:
    path = source_root / str(sample["path"])
    result: dict[str, Any] = {
        "id": sample.get("id"),
        "path": str(path),
        "exists": path.is_file(),
        "expected_version": sample.get("expected_version"),
        "expected_size_bytes": sample.get("expected_size_bytes"),
    }
    if not path.is_file():
        result.update({"status": "missing"})
        return result
    size = path.stat().st_size
    result["actual_size_bytes"] = size
    result["size_matches_manifest"] = size == int(sample.get("expected_size_bytes") or -1)
    try:
        version = DwgVersionDetector.detect_file(path)
        result["detected_version"] = version.code
        result["detected_family"] = version.family
        result["detected_release"] = version.release
        result["detected_supported"] = version.supported
    except Exception as exc:  # noqa: BLE001
        result["detected_error"] = str(exc)
    import_result = pipeline.import_file(path)
    result["import_status"] = import_result.status
    result["import_error_code"] = import_result.error_code
    result["import_message"] = import_result.user_message
    result["importer"] = import_result.importer
    result["elapsed_ms"] = import_result.elapsed_ms
    return result


def _pair_result(
    pair: dict[str, Any],
    *,
    samples_by_id: dict[str, dict[str, Any]],
    source_root: Path,
    cache_dir: Path,
    local_available: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": pair.get("id"),
        "old_sample": pair.get("old_sample"),
        "new_sample": pair.get("new_sample"),
        "current_import_expectation": pair.get("current_import_expectation"),
    }
    cache_files = pair.get("descriptor_cache_files") or []
    if cache_files:
        descriptors = []
        missing = []
        for name in cache_files:
            path = cache_dir / str(name)
            if not path.exists():
                missing.append(str(name))
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            descriptors.append(payload.get("descriptor") or {})
        if missing:
            result["descriptor_cache_status"] = "missing"
            result["missing_descriptor_cache_files"] = missing
        else:
            result["descriptor_cache_status"] = "ok"
            result["descriptor_delta"] = _descriptor_delta(descriptors)
    else:
        result["descriptor_cache_status"] = "not_configured"
    if local_available:
        old = samples_by_id.get(str(pair.get("old_sample"))) or {}
        new = samples_by_id.get(str(pair.get("new_sample"))) or {}
        result["old_path"] = str(source_root / str(old.get("path") or ""))
        result["new_path"] = str(source_root / str(new.get("path") or ""))
    return result


def _descriptor_delta(descriptors: list[dict[str, Any]]) -> dict[str, Any]:
    if len(descriptors) < 2:
        return {}
    a, b = descriptors[0], descriptors[1]
    count_a = sum(int(value) for value in (a.get("entity_counts") or {}).values())
    count_b = sum(int(value) for value in (b.get("entity_counts") or {}).values())
    keys = sorted(set((a.get("entity_counts") or {})) | set((b.get("entity_counts") or {})))
    per_type = {
        key: int((b.get("entity_counts") or {}).get(key, 0)) - int((a.get("entity_counts") or {}).get(key, 0))
        for key in keys
    }
    return {
        "old_entity_count": count_a,
        "new_entity_count": count_b,
        "entity_count_delta": count_b - count_a,
        "entity_type_delta": per_type,
        "old_layer_count": len(a.get("layers") or []),
        "new_layer_count": len(b.get("layers") or []),
        "content_fingerprint_changed": a.get("content_fingerprint") != b.get("content_fingerprint"),
    }


def _compare_named_items(name: str, expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    expected_by_id = {str(item.get("id")): item for item in expected}
    actual_by_id = {str(item.get("id")): item for item in actual}
    if sorted(expected_by_id) != sorted(actual_by_id):
        errors.append(f"golden.{name} ids mismatch: expected={sorted(expected_by_id)} actual={sorted(actual_by_id)}")
        return errors
    for item_id in sorted(expected_by_id):
        if expected_by_id[item_id] != actual_by_id[item_id]:
            errors.append(f"golden.{name}.{item_id} mismatch")
    return errors


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--write-golden", action="store_true")
    parser.add_argument("--json-report", type=Path, default=DEFAULT_JSON_REPORT)
    parser.add_argument("--md-report", type=Path, default=DEFAULT_MD_REPORT)
    parser.add_argument("--require-local", action="store_true")
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)
    report = build_report(manifest, require_local=args.require_local)
    golden_path = _resolve(ROOT, args.golden)
    if args.write_golden:
        _write_json(golden_path, build_golden_results(report))
    elif golden_path.exists() and report.get("source_root_available"):
        golden = json.loads(golden_path.read_text(encoding="utf-8"))
        golden_errors = compare_golden(report, golden)
        report["golden_errors"] = golden_errors
        if golden_errors:
            report["status"] = "failed"
    _write_json(_resolve(ROOT, args.json_report), report)
    _write_text(_resolve(ROOT, args.md_report), render_markdown(report))
    print(
        "real-world DWG validation: "
        f"status={report['status']} "
        f"samples={report['summary']['validated_sample_count']}/{report['summary']['sample_count']} "
        f"unsupported={','.join(report['summary']['unsupported_versions']) or '-'}"
    )
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
