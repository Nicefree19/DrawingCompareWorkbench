"""Validate an ADR-004 version-stratified DWG/DXF sample pack.

This script validates local evidence generated outside the product runtime. It
does not invoke a DWG converter. It reads an existing sample pack manifest,
checks DWG/DXF headers, then optionally runs import and compare smoke checks in
subprocesses so large samples can time out without leaving the parent process
stuck.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMPORT_TIMEOUT_SECONDS = 120.0
DEFAULT_COMPARE_TIMEOUT_SECONDS = 180.0
DEFAULT_MAX_ENTITIES = 500_000
DEFAULT_MAX_DXF_TOKENS = 25_000_000
DEFAULT_SKIP_COMPARE_OVER_DXF_MB = 50.0
EXPECTED_SIDES = ("before", "after")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_manifest(sample_pack: Path, *, root: Path = ROOT) -> tuple[Path, dict[str, Any]]:
    manifest_path = _manifest_path(_resolve(root, sample_pack))
    return manifest_path, json.loads(manifest_path.read_text(encoding="utf-8"))


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != 1:
        errors.append("manifest.schema_version must be 1")
    versions = manifest.get("versions")
    if not isinstance(versions, dict) or not versions:
        errors.append("manifest.versions must be a non-empty object")
        return errors

    for code, item in sorted(versions.items()):
        prefix = f"versions.{code}"
        if item.get("dwg_code") != code:
            errors.append(f"{prefix}.dwg_code must match its version key")
        for key in ("sample_before_dwg", "sample_after_dwg", "outputs"):
            if key not in item:
                errors.append(f"{prefix}.{key} is required")
        outputs = item.get("outputs") or {}
        for side in EXPECTED_SIDES:
            side_outputs = outputs.get(side)
            if not isinstance(side_outputs, list) or not side_outputs:
                errors.append(f"{prefix}.outputs.{side} must be a non-empty list")
                continue
            for index, output in enumerate(side_outputs):
                for key in ("path", "size", "sha256", "acadver"):
                    if key not in output:
                        errors.append(f"{prefix}.outputs.{side}[{index}].{key} is required")
    return errors


def build_report(
    sample_pack: Path,
    *,
    root: Path = ROOT,
    run_import: bool = True,
    run_compare: bool = True,
    max_entities: int = DEFAULT_MAX_ENTITIES,
    max_dxf_tokens: int = DEFAULT_MAX_DXF_TOKENS,
    import_timeout_seconds: float = DEFAULT_IMPORT_TIMEOUT_SECONDS,
    compare_timeout_seconds: float = DEFAULT_COMPARE_TIMEOUT_SECONDS,
    skip_compare_over_dxf_mb: float = DEFAULT_SKIP_COMPARE_OVER_DXF_MB,
) -> dict[str, Any]:
    sample_pack = _resolve(root, sample_pack)
    manifest_path, manifest = load_manifest(sample_pack, root=root)
    manifest_errors = validate_manifest(manifest)

    version_records = []
    for code, item in sorted((manifest.get("versions") or {}).items()):
        version_records.append(
            _version_record(
                code,
                item,
                run_import=run_import,
                run_compare=run_compare,
                max_entities=max_entities,
                max_dxf_tokens=max_dxf_tokens,
                import_timeout_seconds=import_timeout_seconds,
                compare_timeout_seconds=compare_timeout_seconds,
                skip_compare_over_dxf_mb=skip_compare_over_dxf_mb,
            )
        )

    validation_errors = [
        error
        for record in version_records
        for error in record.get("validation_errors", [])
    ]
    import_status_counts = Counter(
        str(result.get("status") or "not_run")
        for record in version_records
        for result in (record.get("imports") or {}).values()
    )
    compare_status_counts = Counter(
        str((record.get("compare") or {}).get("status") or "not_run")
        for record in version_records
    )
    header_mismatch_count = sum(
        1
        for record in version_records
        for side in EXPECTED_SIDES
        for output in ((record.get("outputs") or {}).get(side) or [])
        if output.get("exists") and not output.get("header_matches_expected")
    )

    report = {
        "schema_version": "adr004-version-sample-pack-validation/v1",
        "generated_at": datetime.now().isoformat(),
        "sample_pack": str(sample_pack),
        "manifest_path": str(manifest_path),
        "status": _overall_status(
            manifest_errors=manifest_errors,
            validation_errors=validation_errors,
            version_records=version_records,
        ),
        "limits": {
            "max_entities": max_entities,
            "max_dxf_tokens": max_dxf_tokens,
            "import_timeout_seconds": import_timeout_seconds,
            "compare_timeout_seconds": compare_timeout_seconds,
            "skip_compare_over_dxf_mb": skip_compare_over_dxf_mb,
        },
        "summary": {
            "version_count": len(version_records),
            "manifest_error_count": len(manifest_errors),
            "validation_error_count": len(validation_errors),
            "header_mismatch_count": header_mismatch_count,
            "import_status_counts": dict(sorted(import_status_counts.items())),
            "compare_status_counts": dict(sorted(compare_status_counts.items())),
        },
        "manifest_errors": manifest_errors,
        "validation_errors": validation_errors,
        "versions": version_records,
    }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    limits = report.get("limits") or {}
    lines = [
        "# ADR-004 Version Sample Pack Validation",
        "",
        f"Status: **{report.get('status')}**",
        "",
        "## Scope",
        "",
        f"- Sample pack: `{report.get('sample_pack')}`",
        f"- Manifest: `{report.get('manifest_path')}`",
        f"- Versions: `{report.get('summary', {}).get('version_count')}`",
        f"- Import limits: max_entities `{limits.get('max_entities')}`, "
        f"max_dxf_tokens `{limits.get('max_dxf_tokens')}`, "
        f"timeout `{limits.get('import_timeout_seconds')}s`",
        f"- Compare timeout: `{limits.get('compare_timeout_seconds')}s`",
        f"- Compare size skip: `{limits.get('skip_compare_over_dxf_mb')} MiB`",
        "",
        "## Header Checks",
        "",
        "| version | side | DXF `$ACADVER` | expected | size | status |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for record in report.get("versions") or []:
        for side in EXPECTED_SIDES:
            outputs = (record.get("outputs") or {}).get(side) or []
            for output in outputs:
                lines.append(
                    "| {version} | {side} | {detected} | {expected} | {size} | {status} |".format(
                        version=_md_cell(str(record.get("version") or "")),
                        side=side,
                        detected=_md_cell(str(output.get("detected_acadver") or "")),
                        expected=_md_cell(str(output.get("expected_acadver") or "")),
                        size=output.get("actual_size") or "",
                        status=_header_status(output),
                    )
                )

    lines.extend(
        [
            "",
            "## Import Smoke",
            "",
            "| version | side | status | entities | warnings | error |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for record in report.get("versions") or []:
        for side in EXPECTED_SIDES:
            result = (record.get("imports") or {}).get(side) or {"status": "not_run"}
            lines.append(
                "| {version} | {side} | {status} | {entities} | {warnings} | {error} |".format(
                    version=_md_cell(str(record.get("version") or "")),
                    side=side,
                    status=_md_cell(str(result.get("status") or "")),
                    entities=result.get("entity_count") or "",
                    warnings=_md_cell(", ".join(result.get("warning_codes") or [])),
                    error=_md_cell(str(result.get("error_code") or result.get("reason") or "")),
                )
            )

    lines.extend(
        [
            "",
            "## Compare Smoke",
            "",
            "| version | status | summary | note |",
            "| --- | --- | --- | --- |",
        ]
    )
    for record in report.get("versions") or []:
        result = record.get("compare") or {"status": "not_run"}
        lines.append(
            "| {version} | {status} | {summary} | {note} |".format(
                version=_md_cell(str(record.get("version") or "")),
                status=_md_cell(str(result.get("status") or "")),
                summary=_md_cell(_diff_summary(result.get("summary"))),
                note=_md_cell(str(result.get("reason") or result.get("error_code") or "")),
            )
        )

    if report.get("manifest_errors"):
        lines.extend(["", "## Manifest Errors", ""])
        lines.extend(f"- {_md_cell(error)}" for error in report["manifest_errors"])
    if report.get("validation_errors"):
        lines.extend(["", "## Validation Errors", ""])
        lines.extend(f"- {_md_cell(error)}" for error in report["validation_errors"])
    lines.append("")
    return "\n".join(lines)


def detect_dxf_acadver(path: Path, *, max_scan_bytes: int = 1_048_576) -> str | None:
    try:
        data = path.read_bytes()[:max_scan_bytes]
    except OSError:
        return None
    for encoding in ("utf-8", "cp949"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip().upper() == "$ACADVER" and index + 2 < len(lines):
            return lines[index + 2].strip()
    return None


def detect_dwg_header(path: Path) -> str | None:
    try:
        header = path.read_bytes()[:6]
    except OSError:
        return None
    try:
        value = header.decode("ascii")
    except UnicodeDecodeError:
        return None
    return value if value.startswith("AC") else None


def _version_record(
    code: str,
    item: dict[str, Any],
    *,
    run_import: bool,
    run_compare: bool,
    max_entities: int,
    max_dxf_tokens: int,
    import_timeout_seconds: float,
    compare_timeout_seconds: float,
    skip_compare_over_dxf_mb: float,
) -> dict[str, Any]:
    errors: list[str] = []
    outputs = {
        side: _output_records(code, side, (item.get("outputs") or {}).get(side) or [], errors)
        for side in EXPECTED_SIDES
    }
    dwg_inputs = {
        "before": _dwg_record(code, item.get("sample_before_dwg"), "sample_before_dwg", errors),
        "after": _dwg_record(code, item.get("sample_after_dwg"), "sample_after_dwg", errors),
    }
    imports = {
        side: _run_import_for_side(
            outputs[side],
            run_import=run_import,
            max_entities=max_entities,
            max_dxf_tokens=max_dxf_tokens,
            import_timeout_seconds=import_timeout_seconds,
        )
        for side in EXPECTED_SIDES
    }
    compare = _run_compare_for_version(
        outputs,
        dwg_inputs,
        run_compare=run_compare,
        max_entities=max_entities,
        max_dxf_tokens=max_dxf_tokens,
        import_timeout_seconds=import_timeout_seconds,
        compare_timeout_seconds=compare_timeout_seconds,
        skip_compare_over_dxf_mb=skip_compare_over_dxf_mb,
    )
    return {
        "version": code,
        "pair_kind": item.get("pair_kind"),
        "dxf_output_version": item.get("dxf_output_version"),
        "outputs": outputs,
        "dwg_inputs": dwg_inputs,
        "imports": imports,
        "compare": compare,
        "validation_errors": errors,
    }


def _output_records(
    code: str,
    side: str,
    outputs: list[dict[str, Any]],
    errors: list[str],
) -> list[dict[str, Any]]:
    records = []
    for index, output in enumerate(outputs):
        path = Path(str(output.get("path") or ""))
        exists = path.exists()
        actual_size = path.stat().st_size if exists else None
        detected = detect_dxf_acadver(path) if exists else None
        expected = str(output.get("acadver") or code)
        record = {
            "path": str(path),
            "exists": exists,
            "manifest_size": output.get("size"),
            "actual_size": actual_size,
            "size_matches_manifest": actual_size == output.get("size") if exists else False,
            "expected_acadver": expected,
            "detected_acadver": detected,
            "header_matches_manifest": detected == output.get("acadver"),
            "header_matches_version": detected == code,
            "header_matches_expected": detected == expected == code,
            "sha256": output.get("sha256"),
        }
        if not exists:
            errors.append(f"{code}.{side}[{index}] DXF missing: {path}")
        elif not record["size_matches_manifest"]:
            errors.append(f"{code}.{side}[{index}] DXF size mismatch: {path}")
        if exists and not record["header_matches_expected"]:
            errors.append(
                f"{code}.{side}[{index}] DXF $ACADVER mismatch: "
                f"expected {expected}, detected {detected or '(missing)'}"
            )
        records.append(record)
    return records


def _dwg_record(code: str, raw_path: Any, field_name: str, errors: list[str]) -> dict[str, Any]:
    path = Path(str(raw_path or ""))
    exists = path.exists()
    header = detect_dwg_header(path) if exists else None
    record = {
        "path": str(path),
        "exists": exists,
        "detected_header": header,
        "header_matches_version": header == code,
    }
    if not exists:
        errors.append(f"{code}.{field_name} missing: {path}")
    elif header != code:
        errors.append(f"{code}.{field_name} DWG header mismatch: expected {code}, detected {header or '(missing)'}")
    return record


def _run_import_for_side(
    outputs: list[dict[str, Any]],
    *,
    run_import: bool,
    max_entities: int,
    max_dxf_tokens: int,
    import_timeout_seconds: float,
) -> dict[str, Any]:
    if not run_import:
        return {"status": "skipped", "reason": "import_disabled"}
    first = _first_existing_output(outputs)
    if first is None:
        return {"status": "skipped", "reason": "no_existing_dxf"}
    return _run_import_worker(
        Path(str(first["path"])),
        max_entities=max_entities,
        max_dxf_tokens=max_dxf_tokens,
        import_timeout_seconds=import_timeout_seconds,
    )


def _run_compare_for_version(
    outputs: dict[str, list[dict[str, Any]]],
    dwg_inputs: dict[str, dict[str, Any]],
    *,
    run_compare: bool,
    max_entities: int,
    max_dxf_tokens: int,
    import_timeout_seconds: float,
    compare_timeout_seconds: float,
    skip_compare_over_dxf_mb: float,
) -> dict[str, Any]:
    if not run_compare:
        return {"status": "skipped", "reason": "compare_disabled"}
    before_output = _first_existing_output(outputs["before"])
    after_output = _first_existing_output(outputs["after"])
    if before_output is None or after_output is None:
        return {"status": "skipped", "reason": "missing_dxf_pair"}
    total_bytes = int(before_output.get("actual_size") or 0) + int(after_output.get("actual_size") or 0)
    threshold_bytes = int(skip_compare_over_dxf_mb * 1024 * 1024)
    if threshold_bytes > 0 and total_bytes > threshold_bytes:
        return {
            "status": "skipped",
            "reason": "dxf_size_over_limit",
            "total_dxf_bytes": total_bytes,
            "skip_compare_over_dxf_mb": skip_compare_over_dxf_mb,
        }
    before_dwg = dwg_inputs["before"]
    after_dwg = dwg_inputs["after"]
    source_a = Path(str(before_dwg["path"])) if before_dwg.get("exists") else Path(str(before_output["path"]))
    source_b = Path(str(after_dwg["path"])) if after_dwg.get("exists") else Path(str(after_output["path"]))
    return _run_compare_worker(
        source_a,
        source_b,
        max_entities=max_entities,
        max_dxf_tokens=max_dxf_tokens,
        import_timeout_seconds=import_timeout_seconds,
        compare_timeout_seconds=compare_timeout_seconds,
    )


def _run_import_worker(
    path: Path,
    *,
    max_entities: int,
    max_dxf_tokens: int,
    import_timeout_seconds: float,
) -> dict[str, Any]:
    timeout = max(import_timeout_seconds + 15.0, 30.0)
    return _run_worker(
        [
            "_import_worker",
            "--path",
            str(path),
            "--max-entities",
            str(max_entities),
            "--max-dxf-tokens",
            str(max_dxf_tokens),
            "--import-timeout-seconds",
            str(import_timeout_seconds),
        ],
        timeout_seconds=timeout,
    )


def _run_compare_worker(
    source_a: Path,
    source_b: Path,
    *,
    max_entities: int,
    max_dxf_tokens: int,
    import_timeout_seconds: float,
    compare_timeout_seconds: float,
) -> dict[str, Any]:
    return _run_worker(
        [
            "_compare_worker",
            "--source-a",
            str(source_a),
            "--source-b",
            str(source_b),
            "--max-entities",
            str(max_entities),
            "--max-dxf-tokens",
            str(max_dxf_tokens),
            "--import-timeout-seconds",
            str(import_timeout_seconds),
        ],
        timeout_seconds=compare_timeout_seconds,
    )


def _run_worker(args: list[str], *, timeout_seconds: float) -> dict[str, Any]:
    cmd = [sys.executable, str(Path(__file__).resolve()), *args]
    try:
        completed = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "error_code": "ADR004_WORKER_TIMEOUT",
            "message": f"Worker timed out after {timeout_seconds:.1f}s.",
            "stdout_tail": _tail(exc.stdout),
            "stderr_tail": _tail(exc.stderr),
        }
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    payload = _parse_worker_json(stdout)
    if payload is None:
        return {
            "status": "failed",
            "error_code": "ADR004_WORKER_OUTPUT_INVALID",
            "message": "Worker did not emit valid JSON.",
            "returncode": completed.returncode,
            "stdout_tail": _tail(stdout),
            "stderr_tail": _tail(stderr),
        }
    payload["returncode"] = completed.returncode
    if completed.returncode != 0 and payload.get("status") not in {"failed", "timeout"}:
        payload["status"] = "failed"
        payload["error_code"] = payload.get("error_code") or "ADR004_WORKER_FAILED"
    if stderr.strip():
        payload["stderr_tail"] = _tail(stderr)
    return payload


def _import_worker_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description="Import one DXF sample and emit compact JSON.")
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--max-entities", type=int, required=True)
    parser.add_argument("--max-dxf-tokens", type=int, required=True)
    parser.add_argument("--import-timeout-seconds", type=float, required=True)
    args = parser.parse_args(argv)

    from src.services.comparison.cad_stability import CadStabilityLimits
    from src.services.comparison.import_pipeline import ImportPipeline, ImportPipelineOptions

    result = ImportPipeline(
        ImportPipelineOptions(
            stability_limits=CadStabilityLimits(
                import_timeout_seconds=args.import_timeout_seconds,
                max_entities=args.max_entities,
                max_dxf_tokens=args.max_dxf_tokens,
            )
        )
    ).import_file(args.path)
    print(json.dumps(_compact_import_result(result.to_dict()), ensure_ascii=False))
    return 0 if result.status in {"ok", "partial"} else 1


def _compare_worker_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description="Compare one sample pair and emit compact JSON.")
    parser.add_argument("--source-a", type=Path, required=True)
    parser.add_argument("--source-b", type=Path, required=True)
    parser.add_argument("--max-entities", type=int, required=True)
    parser.add_argument("--max-dxf-tokens", type=int, required=True)
    parser.add_argument("--import-timeout-seconds", type=float, required=True)
    args = parser.parse_args(argv)

    from src.services.comparison.cad_stability import CadStabilityLimits
    from src.services.comparison.import_pipeline import (
        ComparePipeline,
        ComparePipelineOptions,
        ImportPipelineOptions,
    )

    result = ComparePipeline(
        ComparePipelineOptions(
            import_options=ImportPipelineOptions(
                dwg_backend_mode="user_converter",
                stability_limits=CadStabilityLimits(
                    import_timeout_seconds=args.import_timeout_seconds,
                    max_entities=args.max_entities,
                    max_dxf_tokens=args.max_dxf_tokens,
                ),
            )
        )
    ).compare(args.source_a, args.source_b)
    print(json.dumps(_compact_compare_result(result.to_dict()), ensure_ascii=False))
    return 0 if result.status in {"ok", "partial"} else 1


def _compact_import_result(data: dict[str, Any]) -> dict[str, Any]:
    import_report = data.get("import_report") or {}
    return {
        "status": data.get("status"),
        "error_code": data.get("error_code"),
        "message": data.get("message"),
        "version": data.get("version"),
        "entity_count": data.get("entity_count"),
        "layer_count": data.get("layer_count"),
        "bbox": data.get("bbox"),
        "elapsed_ms": data.get("elapsed_ms"),
        "warning_codes": _warning_codes(data.get("warnings") or []),
        "warning_count": len(data.get("warnings") or []),
        "import_stats": import_report.get("stats") or {},
    }


def _compact_compare_result(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": data.get("status"),
        "error_code": data.get("error_code"),
        "message": data.get("message"),
        "summary": data.get("summary"),
        "elapsed_ms": data.get("elapsed_ms"),
        "partial_imports": data.get("partial_imports") or [],
        "input_resolution": data.get("input_resolution") or {},
        "warning_codes": _warning_codes(data.get("warnings") or []),
        "warning_count": len(data.get("warnings") or []),
        "imports": {
            side: _compact_import_result(result)
            for side, result in (data.get("imports") or {}).items()
        },
    }


def _warning_codes(warnings: list[dict[str, Any]]) -> list[str]:
    codes = []
    for warning in warnings:
        code = str(warning.get("code") or "")
        if code and code not in codes:
            codes.append(code)
    return codes


def _first_existing_output(outputs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for output in outputs:
        if output.get("exists"):
            return output
    return None


def _overall_status(
    *,
    manifest_errors: list[str],
    validation_errors: list[str],
    version_records: list[dict[str, Any]],
) -> str:
    if manifest_errors or validation_errors:
        return "failed"
    blocking_statuses = {"failed", "timeout"}
    for record in version_records:
        for result in (record.get("imports") or {}).values():
            if result.get("status") in blocking_statuses:
                return "partial"
        compare = record.get("compare") or {}
        if compare.get("status") in blocking_statuses:
            return "partial"
    return "ok"


def _header_status(output: dict[str, Any]) -> str:
    if not output.get("exists"):
        return "missing"
    if not output.get("size_matches_manifest"):
        return "size_mismatch"
    if not output.get("header_matches_expected"):
        return "acadver_mismatch"
    return "ok"


def _diff_summary(summary: Any) -> str:
    if not isinstance(summary, dict):
        return ""
    keys = ("added", "removed", "modified", "unchanged", "total_changes")
    return ", ".join(f"{key} {summary.get(key)}" for key in keys if key in summary)


def _parse_worker_json(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        return payload if isinstance(payload, dict) else None
    return None


def _tail(value: str | bytes | None, *, limit: int = 2000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value[-limit:]


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _manifest_path(sample_pack: Path) -> Path:
    if sample_pack.name.lower() == "manifest.json":
        return sample_pack
    return sample_pack / "manifest.json"


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if argv and argv[0] == "_import_worker":
        return _import_worker_main(argv[1:])
    if argv and argv[0] == "_compare_worker":
        return _compare_worker_main(argv[1:])

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sample_pack", type=Path)
    parser.add_argument("--json-report", "--out", dest="json_report", type=Path)
    parser.add_argument("--md-report", type=Path)
    parser.add_argument("--max-entities", type=int, default=DEFAULT_MAX_ENTITIES)
    parser.add_argument("--max-dxf-tokens", type=int, default=DEFAULT_MAX_DXF_TOKENS)
    parser.add_argument("--import-timeout-seconds", type=float, default=DEFAULT_IMPORT_TIMEOUT_SECONDS)
    parser.add_argument("--compare-timeout-seconds", type=float, default=DEFAULT_COMPARE_TIMEOUT_SECONDS)
    parser.add_argument("--skip-compare-over-dxf-mb", type=float, default=DEFAULT_SKIP_COMPARE_OVER_DXF_MB)
    parser.add_argument("--no-import", action="store_true")
    parser.add_argument("--no-compare", action="store_true")
    args = parser.parse_args(argv)

    sample_pack = _resolve(ROOT, args.sample_pack)
    json_report = _resolve(ROOT, args.json_report) if args.json_report else sample_pack / "validation_summary.json"
    md_report = _resolve(ROOT, args.md_report) if args.md_report else sample_pack / "validation_summary.md"
    report = build_report(
        sample_pack,
        run_import=not args.no_import,
        run_compare=not args.no_compare,
        max_entities=args.max_entities,
        max_dxf_tokens=args.max_dxf_tokens,
        import_timeout_seconds=args.import_timeout_seconds,
        compare_timeout_seconds=args.compare_timeout_seconds,
        skip_compare_over_dxf_mb=args.skip_compare_over_dxf_mb,
    )
    _write_json(json_report, report)
    _write_text(md_report, render_markdown(report))
    print(
        "adr004 sample pack validation: "
        f"status={report['status']} "
        f"versions={report['summary']['version_count']} "
        f"imports={report['summary']['import_status_counts']} "
        f"compares={report['summary']['compare_status_counts']} "
        f"json={json_report} md={md_report}"
    )
    return 0 if report["status"] in {"ok", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
