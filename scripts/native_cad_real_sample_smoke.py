"""Smoke-check the local public DWG corpus for native CAD evidence.

The collected files stay under ``.local`` and are not product fixtures.  This
script verifies the local manifest, DWG header coverage, and a bounded importer
probe so version identification and real parsing outcomes stay separate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.native_cad_version_matrix import DEFAULT_MATRIX_PATH, load_matrix  # noqa: E402
from src.services.comparison.dwg_importer import DwgImportError, DwgImporter, DwgVersionDetector  # noqa: E402


MANIFEST_SCHEMA = "native-cad-real-sample-manifest/v1"
REPORT_SCHEMA = "native-cad-real-sample-smoke/v1"
DEFAULT_MANIFEST_PATH = Path(".local/native_cad_real_samples/manifest.json")
DEFAULT_JSON_REPORT = Path(".local/native_cad_real_samples/smoke_report.json")
DEFAULT_MD_REPORT = Path(".local/native_cad_real_samples/smoke_report.md")


def load_sample_manifest(path: Path, *, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    return json.loads(_resolve(repo_root, path).read_text(encoding="utf-8-sig"))


def build_report(
    manifest: Mapping[str, Any],
    *,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    matrix_path: Path = DEFAULT_MATRIX_PATH,
    repo_root: Path = REPO_ROOT,
    import_samples_per_code: int = 1,
    run_import: bool = True,
) -> dict[str, Any]:
    matrix = load_matrix(matrix_path, repo_root=repo_root)
    matrix_rows = _matrix_rows_by_code(matrix)
    matrix_codes = set(matrix_rows)
    manifest_errors = validate_manifest_shape(manifest)
    item_results = [
        _item_result(item, repo_root=repo_root, matrix_codes=matrix_codes)
        for item in manifest.get("items") or []
        if isinstance(item, Mapping)
    ]
    integrity_failures = [
        item["path"]
        for item in item_results
        if not item["exists"]
        or not item["size_matches_manifest"]
        or not item["sha256_matches_manifest"]
        or not item["header_matches_manifest"]
    ]
    header_counts = Counter(str(item.get("actual_header") or "") for item in item_results if item.get("actual_header"))
    target_coverage = [
        {
            "code": code,
            "sample_count": int(header_counts.get(code, 0)),
            "covered": header_counts.get(code, 0) > 0,
            "matrix_real_sample_corpus": bool((matrix_rows[code].get("evidence") or {}).get("real_sample_corpus")),
        }
        for code in sorted(matrix_codes)
    ]
    missing_target_codes = [row["code"] for row in target_coverage if not row["covered"]]
    extra_headers = sorted(code for code in header_counts if code not in matrix_codes)
    selected_imports = _select_import_probe_items(
        item_results,
        matrix_codes=matrix_codes,
        import_samples_per_code=import_samples_per_code,
    )
    import_results = [_import_probe(item, repo_root=repo_root) for item in selected_imports] if run_import else []
    import_status_counts = Counter(str(item.get("status") or "not_run") for item in import_results)
    parsing_success_codes = sorted(
        {
            str(item.get("code"))
            for item in import_results
            if item.get("status") in {"ok", "partial"}
        }
    )
    blocked_parse_codes = sorted(
        {
            str(item.get("code"))
            for item in import_results
            if item.get("status") == "failed"
        }
    )
    source_license_warnings = _source_license_warnings(manifest.get("source_summary") or [], item_results)
    status = "PASS"
    if manifest_errors or integrity_failures or missing_target_codes:
        status = "FAIL"
    return {
        "schema": REPORT_SCHEMA,
        "generated_at_utc": _utc_now(),
        "status": status,
        "manifest_path": str(_resolve(repo_root, manifest_path)),
        "matrix_path": str(_resolve(repo_root, matrix_path)),
        "manifest_errors": manifest_errors,
        "summary": {
            "manifest_total_files": manifest.get("total_files"),
            "validated_files": len(item_results),
            "integrity_failure_count": len(integrity_failures),
            "target_code_count": len(matrix_codes),
            "target_codes_covered": len(matrix_codes) - len(missing_target_codes),
            "import_probe_count": len(import_results),
            "parsing_success_codes": parsing_success_codes,
            "blocked_parse_codes": blocked_parse_codes,
        },
        "header_summary": [
            {"header_code": code, "count": int(count)} for code, count in sorted(header_counts.items())
        ],
        "target_coverage": target_coverage,
        "missing_target_codes": missing_target_codes,
        "inventory_only_headers": extra_headers,
        "integrity_failures": integrity_failures,
        "source_license_warnings": source_license_warnings,
        "import_probe": {
            "run": bool(run_import),
            "samples_per_code": max(0, int(import_samples_per_code)),
            "status_counts": dict(sorted(import_status_counts.items())),
            "results": import_results,
        },
        "items": item_results,
    }


def validate_manifest_shape(manifest: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema") != MANIFEST_SCHEMA:
        errors.append(f"manifest.schema must be {MANIFEST_SCHEMA}")
    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        errors.append("manifest.items must be a non-empty list")
        return errors
    seen_paths: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            errors.append(f"items[{index}] must be an object")
            continue
        prefix = f"items[{index}]"
        for key in ("path", "size_bytes", "header_code", "sha256", "source", "source_url", "license_note"):
            if key not in item:
                errors.append(f"{prefix}.{key} is required")
        path = str(item.get("path") or "")
        if path in seen_paths:
            errors.append(f"{prefix}.path duplicates {path}")
        seen_paths.add(path)
    return errors


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Native CAD Real Sample Smoke",
        "",
        f"Status: **{report.get('status')}**",
        "",
        "## Summary",
        "",
    ]
    summary = report.get("summary") or {}
    for key in (
        "manifest_total_files",
        "validated_files",
        "integrity_failure_count",
        "target_code_count",
        "target_codes_covered",
        "import_probe_count",
    ):
        lines.append(f"- {key}: `{summary.get(key)}`")
    lines.append(f"- parsing_success_codes: `{', '.join(summary.get('parsing_success_codes') or []) or '-'}`")
    lines.append(f"- blocked_parse_codes: `{', '.join(summary.get('blocked_parse_codes') or []) or '-'}`")
    lines.extend(["", "## Target Coverage", "", "| code | samples | matrix corpus flag |", "| --- | ---: | --- |"])
    for row in report.get("target_coverage") or []:
        lines.append(f"| {row.get('code')} | {row.get('sample_count')} | {row.get('matrix_real_sample_corpus')} |")
    lines.extend(
        [
            "",
            "## Import Probe",
            "",
            "| code | path | status | error | stage | object | entities |",
            "| --- | --- | --- | --- | --- | --- | ---: |",
        ]
    )
    for result in (report.get("import_probe") or {}).get("results") or []:
        lines.append(
            "| {code} | `{path}` | {status} | {error} | {stage} | {object_ref} | {entities} |".format(
                code=result.get("code"),
                path=result.get("path"),
                status=result.get("status"),
                error=result.get("error_code") or "",
                stage=result.get("failure_stage") or "",
                object_ref=_object_ref(result),
                entities=result.get("canonical_entity_count"),
            )
        )
    if not (report.get("import_probe") or {}).get("results"):
        lines.append("| _not run_ |  |  |  |  |  |")
    if report.get("missing_target_codes"):
        lines.extend(["", "## Missing Target Codes", ""])
        lines.extend(f"- {code}" for code in report["missing_target_codes"])
    if report.get("integrity_failures"):
        lines.extend(["", "## Integrity Failures", ""])
        lines.extend(f"- `{path}`" for path in report["integrity_failures"])
    if report.get("source_license_warnings"):
        lines.extend(["", "## Source Use Warnings", ""])
        lines.extend(f"- {warning}" for warning in report["source_license_warnings"])
    lines.append("")
    return "\n".join(lines)


def _item_result(item: Mapping[str, Any], *, repo_root: Path, matrix_codes: set[str]) -> dict[str, Any]:
    path_text = str(item.get("path") or "")
    path = _resolve(repo_root, Path(path_text))
    exists = path.is_file()
    actual_size = path.stat().st_size if exists else None
    actual_sha = _sha256(path) if exists else ""
    actual_header = _header(path) if exists else ""
    detection = _detect(path) if exists else {"status": "missing"}
    return {
        "path": path_text,
        "exists": exists,
        "source": item.get("source"),
        "source_url": item.get("source_url"),
        "license_note": item.get("license_note"),
        "expected_size_bytes": item.get("size_bytes"),
        "actual_size_bytes": actual_size,
        "size_matches_manifest": actual_size == int(item.get("size_bytes") or -1) if exists else False,
        "expected_sha256": item.get("sha256"),
        "actual_sha256": actual_sha,
        "sha256_matches_manifest": actual_sha == str(item.get("sha256") or "").lower() if exists else False,
        "expected_header": item.get("header_code"),
        "actual_header": actual_header,
        "header_matches_manifest": actual_header == str(item.get("header_code") or "") if exists else False,
        "matrix_target": actual_header in matrix_codes,
        "version_detection": detection,
    }


def _object_ref(result: Mapping[str, Any]) -> str:
    handle = result.get("object_handle")
    offset = result.get("object_offset")
    if handle and offset is not None:
        return f"{handle}@{offset}"
    if handle:
        return str(handle)
    return ""


def _select_import_probe_items(
    items: Sequence[Mapping[str, Any]],
    *,
    matrix_codes: set[str],
    import_samples_per_code: int,
) -> list[Mapping[str, Any]]:
    limit = max(0, int(import_samples_per_code))
    if limit == 0:
        return []
    selected: list[Mapping[str, Any]] = []
    per_code: dict[str, int] = defaultdict(int)
    for item in items:
        code = str(item.get("actual_header") or "")
        if code not in matrix_codes or per_code[code] >= limit:
            continue
        if not item.get("exists") or not item.get("header_matches_manifest") or not item.get("sha256_matches_manifest"):
            continue
        selected.append(item)
        per_code[code] += 1
    return selected


def _import_probe(item: Mapping[str, Any], *, repo_root: Path) -> dict[str, Any]:
    path = _resolve(repo_root, Path(str(item.get("path") or "")))
    doc = DwgImporter().import_file(path)
    report = doc.get("import_report") or {}
    stats = report.get("stats") or {}
    warning = (report.get("warnings") or [{}])[0]
    details = warning.get("details") or {} if isinstance(warning, Mapping) else {}
    return {
        "code": item.get("actual_header"),
        "path": item.get("path"),
        "status": report.get("status"),
        "error_code": report.get("error_code"),
        "failure_stage": details.get("failure_stage"),
        "reader_error_type": details.get("reader_error_type"),
        "reader_error": details.get("reader_error"),
        "object_handle": details.get("object_handle"),
        "object_offset": details.get("object_offset"),
        "object_payload_prefix_hex": details.get("object_payload_prefix_hex"),
        "adapter": (report.get("adapter") or {}).get("name"),
        "backend_mode": (report.get("adapter") or {}).get("backend_mode"),
        "implementation_status": (report.get("adapter") or {}).get("implementation_status"),
        "canonical_entity_count": stats.get("canonical_entity_count"),
        "raw_entity_count": stats.get("raw_entity_count"),
        "elapsed_ms": stats.get("elapsed_ms"),
    }


def _matrix_rows_by_code(matrix: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = matrix.get("rows") or []
    return {
        str(row.get("code")): row
        for row in rows
        if isinstance(row, Mapping) and row.get("code")
    }


def _source_license_warnings(source_summary: Sequence[Any], items: Sequence[Mapping[str, Any]]) -> list[str]:
    source_counts = {
        str(entry.get("source")): int(entry.get("count") or 0)
        for entry in source_summary
        if isinstance(entry, Mapping)
    }
    notes_by_source: dict[str, str] = {}
    for item in items:
        source = str(item.get("source") or "")
        if source and source not in notes_by_source:
            notes_by_source[source] = str(item.get("license_note") or "")
    warnings = []
    for source in sorted(source_counts):
        note = notes_by_source.get(source, "")
        lowered = note.casefold()
        if "gpl" in lowered or "no open-source license" in lowered or "explicit license not found" in lowered:
            warnings.append(f"{source}: local evidence only; {note}")
    return warnings


def _detect(path: Path) -> dict[str, Any]:
    try:
        return {"status": "ok", **DwgVersionDetector.detect_file(path).to_dict()}
    except DwgImportError as exc:
        return {
            "status": "failed",
            "error_code": exc.code,
            "message": str(exc),
        }


def _header(path: Path) -> str:
    try:
        return path.read_bytes()[:6].decode("ascii", errors="replace").replace("\x00", "").strip()
    except OSError:
        return ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX_PATH)
    parser.add_argument("--json-report", type=Path, default=DEFAULT_JSON_REPORT)
    parser.add_argument("--md-report", type=Path, default=DEFAULT_MD_REPORT)
    parser.add_argument("--import-samples-per-code", type=int, default=1)
    parser.add_argument("--no-import", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = load_sample_manifest(args.manifest)
    report = build_report(
        manifest,
        manifest_path=args.manifest,
        matrix_path=args.matrix,
        import_samples_per_code=args.import_samples_per_code,
        run_import=not args.no_import,
    )
    _write_json(_resolve(REPO_ROOT, args.json_report), report)
    _write_text(_resolve(REPO_ROOT, args.md_report), render_markdown(report))
    print(
        "native CAD real sample smoke: "
        f"status={report['status']} "
        f"covered={report['summary']['target_codes_covered']}/{report['summary']['target_code_count']} "
        f"integrity_failures={report['summary']['integrity_failure_count']} "
        f"import_probes={report['summary']['import_probe_count']}"
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
