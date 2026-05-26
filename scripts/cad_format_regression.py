"""CAD sample regression snapshot and report builder.

This script is intentionally deterministic: it imports the cad-samples
manifest, normalizes every sample, compares the current result with
golden-results.json, and writes a Markdown report suitable for CI artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = Path("tests/data/comparison/cad_samples/manifest.yaml")
DEFAULT_GOLDEN_PATH = Path("tests/data/comparison/cad_samples/golden-results.json")
DEFAULT_REPORT_PATH = Path("build/reports/cad-format-regression-report.md")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.comparison.drawing_compare_engine import (  # noqa: E402
    CompareTolerance,
    DrawingCompareEngine,
    DrawingCompareOptions,
    result_fingerprint,
)
from src.services.comparison.drawing_normalizer import (  # noqa: E402
    DrawingNormalizer,
    NormalizationOptions,
)
from src.services.comparison.dxf_importer import DxfImporter, DxfParseError  # noqa: E402


NORMALIZATION_OPTIONS = NormalizationOptions(
    flatten_curves=True,
    flatten_tolerance_mm=0.1,
    resolve_bylayer_byblock=True,
    normalize_text=True,
    remove_near_zero_geometry=False,
)


def load_manifest(path: Path = DEFAULT_MANIFEST_PATH, *, root: Path = ROOT) -> dict[str, Any]:
    return yaml.safe_load(_resolve(root, path).read_text(encoding="utf-8"))


def load_golden(path: Path = DEFAULT_GOLDEN_PATH, *, root: Path = ROOT) -> dict[str, Any]:
    return json.loads(_resolve(root, path).read_text(encoding="utf-8"))


def expanded_samples(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    samples = [dict(sample) for sample in manifest.get("samples") or []]
    for sample_set in manifest.get("generated_sample_sets") or []:
        count = int(sample_set.get("count") or 0)
        for index in range(count):
            source = {
                "kind": "generated",
                "generator": sample_set["generator"],
                "profile": sample_set.get("profile"),
                "variant_index": index,
                "line_count": int(sample_set.get("line_count") or 0) + index,
                "layer": sample_set.get("layer") or "GEN",
                "spacing_mm": float(sample_set.get("spacing_mm") or 10.0),
            }
            samples.append(
                {
                    "id": f"{sample_set['id_prefix']}_{index + 1:03d}",
                    "category": sample_set["category"],
                    "format": sample_set.get("format", "dxf"),
                    "source": source,
                    "tags": list(sample_set.get("tags") or []),
                }
            )
    return samples


def validate_manifest(manifest: dict[str, Any], *, root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != "cad-samples/v1":
        errors.append("manifest.schema_version must be cad-samples/v1")

    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        errors.append("manifest.samples must be a non-empty list")
        return errors

    base_sample_ids: set[str] = set()
    for index, sample in enumerate(samples):
        prefix = f"samples[{index}]"
        sample_id = sample.get("id")
        if not sample_id:
            errors.append(f"{prefix}.id is required")
            continue
        if sample_id in base_sample_ids:
            errors.append(f"sample id {sample_id!r} is duplicated")
        base_sample_ids.add(str(sample_id))

        if sample.get("format") not in {"dxf", "dwg"}:
            errors.append(f"{prefix}.format must be dxf or dwg")
        if not sample.get("category"):
            errors.append(f"{prefix}.category is required")
        if not isinstance(sample.get("tags"), list) or not sample.get("tags"):
            errors.append(f"{prefix}.tags must be a non-empty list")

        source = sample.get("source")
        if not isinstance(source, dict):
            errors.append(f"{prefix}.source must be an object")
            continue
        if source.get("kind") == "file":
            source_path = source.get("path")
            if not source_path:
                errors.append(f"{prefix}.source.path is required for file samples")
            elif not _resolve(root, Path(source_path)).is_file():
                errors.append(f"{prefix}.source.path does not exist: {source_path}")
        elif source.get("kind") == "generated":
            required = {"generator", "line_count", "layer", "spacing_mm"}
            missing = sorted(required - set(source))
            if missing:
                errors.append(f"{prefix}.source is missing generated fields: {', '.join(missing)}")
        else:
            errors.append(f"{prefix}.source.kind must be file or generated")

    for index, sample_set in enumerate(manifest.get("generated_sample_sets") or []):
        prefix = f"generated_sample_sets[{index}]"
        for key in ("id_prefix", "category", "format", "generator", "count", "profile"):
            if not sample_set.get(key):
                errors.append(f"{prefix}.{key} is required")
        if sample_set.get("format") != "dxf":
            errors.append(f"{prefix}.format must be dxf")
        if sample_set.get("generator") not in {"parametric_entities", "grid_lines"}:
            errors.append(f"{prefix}.generator is not supported")
        if int(sample_set.get("count") or 0) <= 0:
            errors.append(f"{prefix}.count must be positive")
        if not isinstance(sample_set.get("tags"), list) or not sample_set.get("tags"):
            errors.append(f"{prefix}.tags must be a non-empty list")

    expanded = expanded_samples(manifest)
    expanded_ids = [str(sample.get("id") or "") for sample in expanded]
    if len(expanded_ids) != len(set(expanded_ids)):
        errors.append("expanded sample ids must be unique")
    if len(expanded) < 100:
        errors.append(f"expanded sample coverage must include at least 100 samples, got {len(expanded)}")
    sample_ids = set(expanded_ids)

    for index, case in enumerate(manifest.get("diff_cases") or []):
        prefix = f"diff_cases[{index}]"
        for key in ("old_sample", "new_sample"):
            if case.get(key) not in sample_ids:
                errors.append(f"{prefix}.{key} references unknown sample: {case.get(key)!r}")
        options = case.get("options") or {}
        for key in ("position_tolerance_mm", "search_radius_mm"):
            if key not in options:
                errors.append(f"{prefix}.options.{key} is required")

    for index, case in enumerate(manifest.get("fuzz_cases") or []):
        prefix = f"fuzz_cases[{index}]"
        if case.get("sample") not in sample_ids:
            errors.append(f"{prefix}.sample references unknown sample: {case.get('sample')!r}")
        if not case.get("expected_error"):
            errors.append(f"{prefix}.expected_error is required")

    return errors


def build_current_results(manifest: dict[str, Any], *, root: Path = ROOT) -> dict[str, Any]:
    samples = sample_by_id(manifest)
    return {
        "schema_version": "cad-golden-results/v1",
        "description": "Golden importer, normalizer, and diff snapshots for CAD format regression tests.",
        "normalization_options": NORMALIZATION_OPTIONS.to_dict(),
        "samples": {
            sample["id"]: sample_snapshot(sample, root=root)
            for sample in expanded_samples(manifest)
        },
        "diff_cases": {
            case["id"]: diff_snapshot(case, samples, root=root)
            for case in manifest.get("diff_cases") or []
        },
    }


def compare_results(actual: dict[str, Any], golden: dict[str, Any]) -> list[str]:
    if actual == golden:
        return []
    mismatches: list[str] = []
    if actual.get("schema_version") != golden.get("schema_version"):
        mismatches.append("schema_version differs")
    if actual.get("normalization_options") != golden.get("normalization_options"):
        mismatches.append("normalization_options differs")

    actual_samples = actual.get("samples") or {}
    golden_samples = golden.get("samples") or {}
    for sample_id in sorted(set(actual_samples) | set(golden_samples)):
        if sample_id not in actual_samples:
            mismatches.append(f"sample {sample_id} missing from actual results")
            continue
        if sample_id not in golden_samples:
            mismatches.append(f"sample {sample_id} missing from golden-results.json")
            continue
        if actual_samples[sample_id] != golden_samples[sample_id]:
            mismatches.append(f"sample {sample_id} snapshot differs")

    actual_cases = actual.get("diff_cases") or {}
    golden_cases = golden.get("diff_cases") or {}
    for case_id in sorted(set(actual_cases) | set(golden_cases)):
        if case_id not in actual_cases:
            mismatches.append(f"diff case {case_id} missing from actual results")
            continue
        if case_id not in golden_cases:
            mismatches.append(f"diff case {case_id} missing from golden-results.json")
            continue
        if actual_cases[case_id] != golden_cases[case_id]:
            mismatches.append(f"diff case {case_id} snapshot differs")
    return mismatches


def build_regression_report(
    *,
    manifest: dict[str, Any],
    golden: dict[str, Any],
    actual: dict[str, Any],
    mismatches: list[str],
    manifest_errors: list[str],
) -> str:
    sample_rows = []
    for sample_id, snapshot in sorted((actual.get("samples") or {}).items()):
        sample_rows.append(
            "| {id} | {category} | {format} | {status} | {entities} | {layers} | {warnings} | {hash} |".format(
                id=sample_id,
                category=snapshot["category"],
                format=snapshot["format"],
                status=snapshot["status"],
                entities=snapshot["entity_count"],
                layers=snapshot["layer_count"],
                warnings=snapshot["warning_count"],
                hash=_short_hash(snapshot["geometry_hash"]),
            )
        )

    diff_rows = []
    for case_id, snapshot in sorted((actual.get("diff_cases") or {}).items()):
        summary = snapshot["summary"]
        diff_rows.append(
            "| {id} | {changes} | +{added} / -{removed} / ~{modified} / ={unchanged} | {fingerprint} |".format(
                id=case_id,
                changes=summary["total_changes"],
                added=summary["added"],
                removed=summary["removed"],
                modified=summary["modified"],
                unchanged=summary["unchanged"],
                fingerprint=_short_hash(snapshot["fingerprint"]),
            )
        )

    status = "PASS" if not mismatches and not manifest_errors else "FAIL"
    lines = [
        "# CAD Format Regression Report",
        "",
        f"Status: **{status}**",
        "",
        "## Scope",
        "",
        "- Manifest: `tests/data/comparison/cad_samples/manifest.yaml`",
        "- Golden results: `tests/data/comparison/cad_samples/golden-results.json`",
        "- CI workflow: `.github/workflows/cad-format-regression.yml`",
        "- Test entrypoint: `tests/unit/services/comparison/test_cad_format_regression.py`",
        "",
        "## Coverage Summary",
        "",
        f"- Samples: {len(expanded_samples(manifest))}",
        f"- Manifest sample definitions: {len(manifest.get('samples') or [])}",
        f"- Generated sample definitions: {len(manifest.get('generated_sample_sets') or [])}",
        f"- Diff cases: {len(manifest.get('diff_cases') or [])}",
        f"- Fuzz cases: {len(manifest.get('fuzz_cases') or [])}",
        f"- Golden sample snapshots: {len(golden.get('samples') or {})}",
        f"- Current sample snapshots: {len(actual.get('samples') or {})}",
        "",
        "## Sample Snapshots",
        "",
        "| Sample | Category | Format | Status | Entities | Layers | Warnings | Geometry Hash |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | --- |",
        *sample_rows,
        "",
        "## Diff Snapshots",
        "",
        "| Case | Total Changes | Added / Removed / Modified / Unchanged | Fingerprint |",
        "| --- | ---: | --- | --- |",
        *(diff_rows or ["| none | 0 | +0 / -0 / ~0 / =0 | n/a |"]),
        "",
        "## Normalization Policy",
        "",
        "```json",
        json.dumps(actual.get("normalization_options") or {}, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Validation Findings",
        "",
    ]
    if manifest_errors:
        lines.extend(f"- MANIFEST: {error}" for error in manifest_errors)
    if mismatches:
        lines.extend(f"- GOLDEN: {mismatch}" for mismatch in mismatches)
    if not manifest_errors and not mismatches:
        lines.append("- No manifest or golden snapshot mismatches detected.")
    lines.extend(
        [
            "",
            "## Maintenance",
            "",
            "Run `python scripts/cad_format_regression.py --check --report build/reports/cad-format-regression-report.md` before merging importer, normalizer, writer, or compare-engine changes.",
            "When an intentional behavior change is reviewed, run `python scripts/cad_format_regression.py --update-golden --report docs/CAD_FORMAT_REGRESSION_REPORT.md` and review the JSON and report diff together.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(path: Path, report: str, *, root: Path = ROOT) -> None:
    resolved = _resolve(root, path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(report, encoding="utf-8")


def write_golden(path: Path, results: dict[str, Any], *, root: Path = ROOT) -> None:
    resolved = _resolve(root, path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sample_by_id(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {sample["id"]: sample for sample in expanded_samples(manifest)}


def sample_snapshot(sample: dict[str, Any], *, root: Path = ROOT) -> dict[str, Any]:
    imported, normalized, report = normalized_sample(sample, root=root)
    warnings = list(imported["import_report"]["warnings"])
    stats = imported["import_report"]["stats"]
    return {
        "category": sample["category"],
        "format": sample["format"],
        "status": imported["import_report"]["status"],
        "entity_count": len(normalized["entities"]),
        "layer_count": len(normalized["layers"]),
        "bbox": stable_bbox(normalized.get("extents")),
        "geometry_hash": aggregate_geometry_hash(normalized["entities"]),
        "warning_count": len(warnings),
        "warning_codes": dict(sorted(Counter(warning["code"] for warning in warnings).items())),
        "unsupported_entity_count": stats["unsupported_entity_count"],
        "entity_types": dict(sorted(Counter(entity["type"] for entity in normalized["entities"]).items())),
        "normalization": {
            "changed_entity_count": report["changed_entity_count"],
            "removed_entity_count": report["removed_entity_count"],
            "rounded_coordinate_count": report["rounded_coordinate_count"],
            "normalized_polyline_count": report["normalized_polyline_count"],
            "flattened_curve_count": report["flattened_curve_count"],
            "normalized_text_count": report["normalized_text_count"],
            "recomputed_hash_count": report["recomputed_hash_count"],
        },
    }


def normalized_sample(
    sample: dict[str, Any],
    *,
    root: Path = ROOT,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    imported = import_sample(sample, root=root)
    normalized, report = DrawingNormalizer(NORMALIZATION_OPTIONS).normalize(imported)
    return imported, normalized, report.to_dict()


def import_sample(sample: dict[str, Any], *, root: Path = ROOT) -> dict[str, Any]:
    source = sample["source"]
    if source["kind"] == "file":
        source_path = Path(source["path"])
        path = _resolve(root, source_path)
        text = path.read_text(encoding="utf-8")
        return DxfImporter(expand_blocks=True).import_text(
            text,
            source_path=source_path,
            file_name=path.name,
        )
    if source["kind"] == "generated" and source["generator"] == "grid_lines":
        text = generate_grid_dxf(
            line_count=int(source["line_count"]),
            spacing_mm=float(source["spacing_mm"]),
            layer=str(source["layer"]),
        )
        return DxfImporter(expand_blocks=True).import_text(
            text,
            file_name=f"{sample['id']}.dxf",
        )
    if source["kind"] == "generated" and source["generator"] == "parametric_entities":
        text = generate_parametric_dxf(
            profile=str(source.get("profile") or "simple"),
            variant_index=int(source.get("variant_index") or 0),
            line_count=int(source.get("line_count") or 0),
            layer=str(source.get("layer") or "GEN"),
            spacing_mm=float(source.get("spacing_mm") or 10.0),
        )
        return DxfImporter(expand_blocks=True).import_text(
            text,
            file_name=f"{sample['id']}.dxf",
        )
    raise AssertionError(f"Unsupported cad-sample source: {source}")


def diff_snapshot(
    case: dict[str, Any],
    samples: dict[str, dict[str, Any]],
    *,
    root: Path = ROOT,
) -> dict[str, Any]:
    _, old_normalized, _ = normalized_sample(samples[case["old_sample"]], root=root)
    _, new_normalized, _ = normalized_sample(samples[case["new_sample"]], root=root)
    options = DrawingCompareOptions(
        tolerance=CompareTolerance(
            position_tolerance_mm=float(case["options"]["position_tolerance_mm"]),
            bbox_tolerance_mm=float(case["options"]["position_tolerance_mm"]),
        ),
        search_radius_mm=float(case["options"]["search_radius_mm"]),
        include_entity_snapshots=False,
        include_match_candidates=False,
    )
    result = DrawingCompareEngine(options).compare(old_normalized, new_normalized)
    payload = result.to_dict()
    return {
        "summary": payload["summary"],
        "change_types": [change["change_type"] for change in payload["changes"]],
        "entity_types": [change["entity_type"] for change in payload["changes"]],
        "fingerprint": result_fingerprint(result),
    }


def mutate_for_fuzz(
    case: dict[str, Any],
    samples: dict[str, dict[str, Any]],
    *,
    root: Path = ROOT,
) -> str:
    source = samples[case["sample"]]["source"]
    if source["kind"] == "file":
        text = _resolve(root, Path(source["path"])).read_text(encoding="utf-8")
    elif source["kind"] == "generated" and source["generator"] == "parametric_entities":
        text = generate_parametric_dxf(
            profile=str(source.get("profile") or "simple"),
            variant_index=int(source.get("variant_index") or 0),
            line_count=int(source.get("line_count") or 0),
            layer=str(source.get("layer") or "GEN"),
            spacing_mm=float(source.get("spacing_mm") or 10.0),
        )
    elif source["kind"] == "generated" and source["generator"] == "grid_lines":
        text = generate_grid_dxf(
            line_count=int(source["line_count"]),
            spacing_mm=float(source["spacing_mm"]),
            layer=str(source["layer"]),
        )
    else:
        raise AssertionError(f"Unsupported fuzz sample source: {source}")
    if case["mutation"] == "truncate_bytes":
        return text[: int(case["byte_count"])]
    if case["mutation"] == "replace_first_group_code":
        lines = text.splitlines()
        lines[0] = str(case["replacement"])
        return "\n".join(lines) + "\n"
    raise AssertionError(f"Unsupported fuzz mutation: {case['mutation']}")


def generate_grid_dxf(*, line_count: int, spacing_mm: float, layer: str) -> str:
    lines: list[str] = [
        "0", "SECTION", "2", "HEADER",
        "9", "$ACADVER", "1", "AC1032",
        "9", "$INSUNITS", "70", "4",
        "0", "ENDSEC",
        "0", "SECTION", "2", "TABLES",
        "0", "TABLE", "2", "LAYER",
        "0", "LAYER", "2", "0", "70", "0", "62", "7", "6", "Continuous",
        "0", "LAYER", "2", layer, "70", "0", "62", "8", "6", "Continuous",
        "0", "ENDTAB",
        "0", "ENDSEC",
        "0", "SECTION", "2", "BLOCKS",
        "0", "ENDSEC",
        "0", "SECTION", "2", "ENTITIES",
    ]
    for index in range(line_count):
        y = index * spacing_mm
        lines.extend(
            [
                "0", "LINE",
                "5", f"G{index:04d}",
                "8", layer,
                "10", "0",
                "20", _num(y),
                "11", "1000",
                "21", _num(y),
            ]
        )
    lines.extend(["0", "ENDSEC", "0", "EOF"])
    return "\n".join(lines) + "\n"


def generate_parametric_dxf(
    *,
    profile: str,
    variant_index: int,
    line_count: int,
    layer: str,
    spacing_mm: float,
) -> str:
    scale = max(1, variant_index + 1)
    line_total = max(1, line_count or 1)
    layers = ["0", layer, "ANNO", "DETAIL", "IGNORE"]
    lines: list[str] = [
        "0", "SECTION", "2", "HEADER",
        "9", "$ACADVER", "1", "AC1032",
        "9", "$INSUNITS", "70", "4",
        "0", "ENDSEC",
        "0", "SECTION", "2", "TABLES",
        "0", "TABLE", "2", "LAYER",
    ]
    for idx, name in enumerate(layers):
        lines.extend(["0", "LAYER", "2", name, "70", "0", "62", str(7 + idx), "6", "Continuous"])
    lines.extend(["0", "ENDTAB", "0", "ENDSEC"])

    if profile == "block_centered":
        lines.extend(
            [
                "0", "SECTION", "2", "BLOCKS",
                "0", "BLOCK", "5", f"BA{scale:03X}", "2", f"BLK_{scale:03d}", "70", "0",
                "10", "0", "20", "0", "30", "0",
                *_line_entity(f"BB{scale:03X}", "DETAIL", 0, 0, 10 * scale, 0),
                *_circle_entity(f"BC{scale:03X}", "DETAIL", 5 * scale, 5, 2 + scale % 4),
                "0", "ENDBLK",
                "0", "ENDSEC",
            ]
        )
    else:
        lines.extend(["0", "SECTION", "2", "BLOCKS", "0", "ENDSEC"])

    lines.extend(["0", "SECTION", "2", "ENTITIES"])
    if profile == "simple":
        for idx in range(line_total):
            y = idx * spacing_mm
            lines.extend(_line_entity(f"S{variant_index:03X}{idx:03X}", layer, 0, y, 100 + scale, y))
        lines.extend(_circle_entity(f"C{variant_index:03X}", layer, 25 + scale, 20, 5 + (scale % 5)))
        lines.extend(_arc_entity(f"A{variant_index:03X}", layer, 50, 40 + scale, 12, 0, 90 + scale % 180))
        lines.extend(_lwpolyline_entity(f"P{variant_index:03X}", layer, scale))
    elif profile == "block_centered":
        for idx in range(1 + variant_index % 3):
            lines.extend(_insert_entity(f"I{variant_index:03X}{idx:02X}", layer, f"BLK_{scale:03d}", idx * 20, idx * 15))
    elif profile == "text_centered":
        lines.extend(_text_entity(f"T{variant_index:03X}", "ANNO", 0, scale * 2, f"MARK {scale}"))
        lines.extend(_mtext_entity(f"M{variant_index:03X}", "ANNO", 0, scale * 4, f"NOTE {{\\H1.2x;{scale}}}"))
    elif profile == "hatch_dimension_centered":
        lines.extend(_lwpolyline_entity(f"HP{variant_index:03X}", "DETAIL", scale))
        lines.extend(_hatch_entity(f"H{variant_index:03X}", "DETAIL", scale))
        lines.extend(_dimension_entity(f"D{variant_index:03X}", "DETAIL", scale))
    elif profile == "unsupported_malformed":
        for raw_type in ("3DSOLID", "REGION", "ACAD_TABLE"):
            lines.extend(["0", raw_type, "5", f"U{variant_index:03X}{len(lines) % 255:02X}", "8", "IGNORE"])
    elif profile == "large_synthetic":
        for idx in range(max(25, line_total)):
            y = idx * spacing_mm
            lines.extend(_line_entity(f"L{variant_index:03X}{idx:04X}", layer, 0, y, 1000, y))
    elif profile == "customer_sanitized":
        lines.extend(_line_entity(f"CS{variant_index:03X}", layer, 0, 0, 80 + scale, 0))
        lines.extend(_line_entity(f"CV{variant_index:03X}", layer, 0, 0, 0, 60 + scale))
        lines.extend(_text_entity(f"CT{variant_index:03X}", "ANNO", 5, 5 + scale, f"SHEET-{scale:03d}"))
        lines.extend(_circle_entity(f"CC{variant_index:03X}", "DETAIL", 30, 20, 3 + scale % 6))
    else:
        raise AssertionError(f"Unsupported generated profile: {profile}")
    lines.extend(["0", "ENDSEC", "0", "EOF"])
    return "\n".join(str(line) for line in lines) + "\n"


def _line_entity(handle: str, layer: str, x0: float, y0: float, x1: float, y1: float) -> list[str]:
    return [
        "0", "LINE", "5", handle, "8", layer,
        "10", _num(x0), "20", _num(y0), "30", "0",
        "11", _num(x1), "21", _num(y1), "31", "0",
    ]


def _circle_entity(handle: str, layer: str, x: float, y: float, radius: float) -> list[str]:
    return [
        "0", "CIRCLE", "5", handle, "8", layer,
        "10", _num(x), "20", _num(y), "30", "0", "40", _num(radius),
    ]


def _arc_entity(handle: str, layer: str, x: float, y: float, radius: float, start: float, end: float) -> list[str]:
    return [
        "0", "ARC", "5", handle, "8", layer,
        "10", _num(x), "20", _num(y), "30", "0",
        "40", _num(radius), "50", _num(start), "51", _num(end),
    ]


def _lwpolyline_entity(handle: str, layer: str, scale: int) -> list[str]:
    width = 20 + scale
    height = 10 + scale
    return [
        "0", "LWPOLYLINE", "5", handle, "8", layer, "90", "4", "70", "1",
        "10", "0", "20", "0",
        "10", _num(width), "20", "0",
        "10", _num(width), "20", _num(height),
        "10", "0", "20", _num(height),
    ]


def _insert_entity(handle: str, layer: str, block_name: str, x: float, y: float) -> list[str]:
    return [
        "0", "INSERT", "5", handle, "8", layer, "2", block_name,
        "10", _num(x), "20", _num(y), "30", "0",
        "41", "1", "42", "1", "43", "1", "50", "0",
    ]


def _text_entity(handle: str, layer: str, x: float, y: float, text: str) -> list[str]:
    return [
        "0", "TEXT", "5", handle, "8", layer,
        "10", _num(x), "20", _num(y), "30", "0",
        "40", "2.5", "1", text, "50", "0",
    ]


def _mtext_entity(handle: str, layer: str, x: float, y: float, text: str) -> list[str]:
    return [
        "0", "MTEXT", "5", handle, "8", layer,
        "10", _num(x), "20", _num(y), "30", "0",
        "40", "2.5", "1", text,
    ]


def _hatch_entity(handle: str, layer: str, scale: int) -> list[str]:
    width = 20 + scale
    height = 10 + scale
    return [
        "0", "HATCH", "5", handle, "8", layer, "2", "SOLID", "70", "1",
        "10", "0", "20", "0",
        "10", _num(width), "20", "0",
        "10", _num(width), "20", _num(height),
        "10", "0", "20", _num(height),
    ]


def _dimension_entity(handle: str, layer: str, scale: int) -> list[str]:
    return [
        "0", "DIMENSION", "5", handle, "8", layer, "70", "0",
        "10", "0", "20", "0", "30", "0",
        "11", _num(10 + scale), "21", _num(8 + scale), "31", "0",
        "13", "0", "23", "0", "33", "0",
        "14", _num(20 + scale), "24", "0", "34", "0",
        "42", _num(20 + scale), "1", f"{20 + scale}",
    ]


def stable_bbox(bbox: Any) -> dict[str, Any]:
    if not isinstance(bbox, dict):
        return _missing_bbox()
    return {
        "min_x": round(float(bbox.get("min_x", 0.0) or 0.0), 6),
        "min_y": round(float(bbox.get("min_y", 0.0) or 0.0), 6),
        "min_z": round(float(bbox.get("min_z", 0.0) or 0.0), 6),
        "max_x": round(float(bbox.get("max_x", 0.0) or 0.0), 6),
        "max_y": round(float(bbox.get("max_y", 0.0) or 0.0), 6),
        "max_z": round(float(bbox.get("max_z", 0.0) or 0.0), 6),
        "quality": str(bbox.get("quality") or "missing"),
    }


def aggregate_geometry_hash(entities: list[dict[str, Any]]) -> str:
    hashes = sorted(
        str((entity.get("hashes") or {}).get("geometry_hash") or "")
        for entity in entities
    )
    payload = json.dumps(hashes, ensure_ascii=False, separators=(",", ":"))
    return "cad-geom:v1:sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def run_fuzz_cases(manifest: dict[str, Any], *, root: Path = ROOT) -> list[str]:
    samples = sample_by_id(manifest)
    errors: list[str] = []
    for case in manifest.get("fuzz_cases") or []:
        text = mutate_for_fuzz(case, samples, root=root)
        try:
            DxfImporter().import_text(text, file_name=f"{case['id']}.dxf")
        except DxfParseError as exc:
            if case["expected_error"] != exc.__class__.__name__:
                errors.append(
                    f"fuzz case {case['id']} raised {exc.__class__.__name__}, expected {case['expected_error']}"
                )
        else:
            errors.append(f"fuzz case {case['id']} did not raise {case['expected_error']}")
    return errors


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate CAD format regression snapshots.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--check", action="store_true", help="Compare current results with golden-results.json.")
    parser.add_argument("--update-golden", action="store_true", help="Overwrite golden-results.json with current results.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    manifest = load_manifest(args.manifest)
    manifest_errors = validate_manifest(manifest)
    actual = build_current_results(manifest)
    golden = load_golden(args.golden)

    if args.update_golden:
        write_golden(args.golden, actual)
        golden = actual

    mismatches = compare_results(actual, golden) if args.check or not args.update_golden else []
    fuzz_errors = run_fuzz_cases(manifest)
    findings = manifest_errors + mismatches + fuzz_errors
    report = build_regression_report(
        manifest=manifest,
        golden=golden,
        actual=actual,
        mismatches=mismatches + fuzz_errors,
        manifest_errors=manifest_errors,
    )
    write_report(args.report, report)

    if findings:
        for finding in findings:
            print(f"CAD regression finding: {finding}", file=sys.stderr)
        print(f"CAD regression report written to {_resolve(ROOT, args.report)}", file=sys.stderr)
        return 1

    print(f"CAD regression report written to {_resolve(ROOT, args.report)}")
    return 0


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _num(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _missing_bbox() -> dict[str, Any]:
    return {
        "min_x": 0.0,
        "min_y": 0.0,
        "min_z": 0.0,
        "max_x": 0.0,
        "max_y": 0.0,
        "max_z": 0.0,
        "quality": "missing",
    }


def _short_hash(value: str) -> str:
    return value.rsplit(":", 1)[-1][:12] if ":" in value else value[:12]


if __name__ == "__main__":
    raise SystemExit(main())
