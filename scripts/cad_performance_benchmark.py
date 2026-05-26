from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.comparison.cad_stability import CadStabilityLimits
from src.services.comparison.drawing_compare_engine import DrawingCompareEngine
from src.services.comparison.drawing_normalizer import DrawingNormalizer
from src.services.comparison.dxf_importer import DxfImporter


def _dxf_for_lines(line_count: int, *, spacing: float = 1.0) -> str:
    lines = [
        "0", "SECTION", "2", "HEADER",
        "9", "$ACADVER", "1", "AC1032",
        "9", "$INSUNITS", "70", "4",
        "0", "ENDSEC",
        "0", "SECTION", "2", "TABLES",
        "0", "TABLE", "2", "LAYER",
        "0", "LAYER", "2", "0", "70", "0", "62", "7", "6", "Continuous",
        "0", "LAYER", "2", "BENCH", "70", "0", "62", "8", "6", "Continuous",
        "0", "ENDTAB",
        "0", "ENDSEC",
        "0", "SECTION", "2", "BLOCKS",
        "0", "ENDSEC",
        "0", "SECTION", "2", "ENTITIES",
    ]
    for idx in range(line_count):
        y = idx * spacing
        lines.extend(
            [
                "0", "LINE",
                "5", f"B{idx:08X}",
                "8", "BENCH",
                "10", "0",
                "20", _num(y),
                "11", "1000",
                "21", _num(y),
            ]
        )
    lines.extend(["0", "ENDSEC", "0", "EOF"])
    return "\n".join(lines) + "\n"


def _pad_to_target_bytes(text: str, target_bytes: int) -> str:
    current_bytes = len(text.encode("utf-8"))
    if current_bytes >= target_bytes:
        return text
    footer = "0\nEOF\n"
    body = text[: -len(footer)] if text.endswith(footer) else text
    chunks: list[str] = []
    while current_bytes < target_bytes:
        remaining = target_bytes - current_bytes
        comment_len = max(1, min(2048, remaining - len("999\n\n")))
        chunk = "999\n" + ("P" * comment_len) + "\n"
        chunks.append(chunk)
        current_bytes += len(chunk.encode("utf-8"))
    return body + "".join(chunks) + footer


def _num(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:.6f}".rstrip("0").rstrip(".")


def _measure_case(
    line_count: int,
    limits: CadStabilityLimits,
    *,
    label: str | None = None,
    target_input_bytes: int | None = None,
) -> dict[str, Any]:
    text = _dxf_for_lines(line_count)
    if target_input_bytes is not None:
        text = _pad_to_target_bytes(text, target_input_bytes)
    importer = DxfImporter(
        expand_blocks=True,
        max_block_depth=limits.max_block_depth,
        max_entities=limits.max_entities,
        max_tokens=limits.max_dxf_tokens,
        timeout_seconds=limits.import_timeout_seconds,
    )
    started = time.perf_counter()
    imported = importer.import_text(text, file_name=f"bench-{line_count}.dxf")
    import_s = time.perf_counter() - started

    started = time.perf_counter()
    normalized, normalization_report = DrawingNormalizer().normalize(imported)
    normalize_s = time.perf_counter() - started

    started = time.perf_counter()
    diff = DrawingCompareEngine().compare(normalized, normalized)
    compare_s = time.perf_counter() - started

    return {
        "label": label or f"{line_count}-lines",
        "line_count": line_count,
        "target_input_bytes": target_input_bytes,
        "input_bytes": len(text.encode("utf-8")),
        "entity_count": len(normalized["entities"]),
        "import_s": round(import_s, 6),
        "normalize_s": round(normalize_s, 6),
        "compare_s": round(compare_s, 6),
        "diff_summary": diff.summary,
        "normalization": {
            "changed_entity_count": normalization_report.changed_entity_count,
            "recomputed_hash_count": normalization_report.recomputed_hash_count,
        },
    }


def _unsupported_heavy_dxf(count: int = 100) -> str:
    lines = [
        "0", "SECTION", "2", "HEADER",
        "9", "$ACADVER", "1", "AC1032",
        "0", "ENDSEC",
        "0", "SECTION", "2", "TABLES",
        "0", "TABLE", "2", "LAYER",
        "0", "LAYER", "2", "UNSUPPORTED", "70", "0", "62", "1", "6", "Continuous",
        "0", "ENDTAB",
        "0", "ENDSEC",
        "0", "SECTION", "2", "BLOCKS",
        "0", "ENDSEC",
        "0", "SECTION", "2", "ENTITIES",
    ]
    for index in range(count):
        lines.extend(["0", "3DSOLID", "5", f"U{index:04X}", "8", "UNSUPPORTED"])
    lines.extend(["0", "ENDSEC", "0", "EOF"])
    return "\n".join(lines) + "\n"


def _measure_stability_cases(limits: CadStabilityLimits) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    malformed = "XX\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n"
    try:
        DxfImporter(
            max_entities=limits.max_entities,
            max_tokens=limits.max_dxf_tokens,
            timeout_seconds=limits.import_timeout_seconds,
        ).import_text(malformed, file_name="malformed.dxf")
    except Exception as exc:  # noqa: BLE001 - benchmark records explicit parser failures
        cases.append(
            {
                "label": "malformed-dxf",
                "status": "explicit_error",
                "error_class": exc.__class__.__name__,
                "message": str(exc),
            }
        )
    else:
        cases.append({"label": "malformed-dxf", "status": "unexpected_ok"})

    unsupported_text = _unsupported_heavy_dxf()
    started = time.perf_counter()
    imported = DxfImporter(
        max_entities=limits.max_entities,
        max_tokens=limits.max_dxf_tokens,
        timeout_seconds=limits.import_timeout_seconds,
    ).import_text(unsupported_text, file_name="unsupported-heavy.dxf")
    elapsed = time.perf_counter() - started
    stats = imported.get("import_report", {}).get("stats", {})
    cases.append(
        {
            "label": "unsupported-heavy-dxf",
            "status": imported.get("import_report", {}).get("status"),
            "import_s": round(elapsed, 6),
            "warning_count": len(imported.get("import_report", {}).get("warnings") or []),
            "unsupported_entity_count": stats.get("unsupported_entity_count", 0),
        }
    )
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Canonical CAD performance benchmarks.")
    parser.add_argument("--line-counts", default="1000,10000,100000")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--max-entities", type=int, default=120000)
    parser.add_argument("--max-tokens", type=int, default=3000000)
    parser.add_argument("--target-mb", default="")
    parser.add_argument("--size-case-lines", type=int, default=100000)
    parser.add_argument("--skip-stability-cases", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    limits = CadStabilityLimits(
        import_timeout_seconds=args.timeout,
        max_entities=args.max_entities,
        max_dxf_tokens=args.max_tokens,
    )
    cases = [
        _measure_case(int(value.strip()), limits)
        for value in args.line_counts.split(",")
        if value.strip()
    ]
    cases.extend(
        _measure_case(
            args.size_case_lines,
            limits,
            label=f"{int(float(value.strip()))}MB-input",
            target_input_bytes=int(float(value.strip()) * 1024 * 1024),
        )
        for value in args.target_mb.split(",")
        if value.strip()
    )
    report = {
        "schema_version": "cad-performance-benchmark/v1",
        "limits": limits.to_dict(),
        "cases": cases,
        "stability_cases": [] if args.skip_stability_cases else _measure_stability_cases(limits),
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
