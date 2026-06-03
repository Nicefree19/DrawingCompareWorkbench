"""Generate supplemental release-readiness evidence for drawing compare.

This probe covers evidence classes that are not produced by the golden DXF
baseline run: PDF/PDF pairs, fail-closed negative controls, additional
block/text/dimension focused DXF pairs, and CAD-to-PDF overlay alignment error.
It uses existing product compare helpers and records generated artifacts rather
than filling release metrics with placeholders.
"""

from __future__ import annotations

import argparse
import json
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

from src.services.comparison.cad_pdf_alignment import (  # noqa: E402
    align_cad_to_pdf,
    build_display_overlays,
)


SCHEMA_VERSION = "dwg-release-supplemental-evidence/v1"
DEFAULT_OUT = Path("build/reports/dwg-release-supplemental-evidence.json")
DEFAULT_WORK_DIR = Path("build/reports/dwg-release-supplemental-evidence")
PDF_DPI = 150
PDF_PIXEL_SIZE = (1500, 900)
PDF_PAGE_SIZE_POINTS = (PDF_PIXEL_SIZE[0] * 72.0 / PDF_DPI, PDF_PIXEL_SIZE[1] * 72.0 / PDF_DPI)
CAD_FRAME_MM = (0.0, 0.0, 254.0, 152.4)


@dataclass(frozen=True)
class CompareExecution:
    exit_code: int | None
    elapsed_s: float
    timed_out: bool = False
    stdout_tail: str = ""
    stderr_tail: str = ""


CadCompareRunner = Callable[[Sequence[str], float], CompareExecution]
PdfCompareRunner = Callable[[Path, Path, Path, float], dict[str, Any]]


def build_probe(
    *,
    out: Path = DEFAULT_OUT,
    work_dir: Path = DEFAULT_WORK_DIR,
    python_executable: str = sys.executable,
    pdf_pair_count: int = 10,
    negative_sample_count: int = 2,
    block_text_dimension_pair_count: int = 2,
    pair_timeout_seconds: float = 30.0,
    pdf_compare_runner: PdfCompareRunner | None = None,
    cad_compare_runner: CadCompareRunner | None = None,
) -> dict[str, Any]:
    out = _resolve(out)
    work_dir = _resolve(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    pdf_pairs = _run_pdf_pairs(
        work_dir / "pdf-pairs",
        count=pdf_pair_count,
        timeout_seconds=pair_timeout_seconds,
        runner=pdf_compare_runner or _run_pdf_compare,
    )
    negative_samples = _run_negative_samples(
        work_dir / "negative-samples",
        count=negative_sample_count,
        python_executable=python_executable,
        timeout_seconds=pair_timeout_seconds,
        runner=cad_compare_runner or _run_cad_compare_command,
    )
    focused_pairs = _run_focused_dxf_pairs(
        work_dir / "block-text-dimension",
        count=block_text_dimension_pair_count,
        python_executable=python_executable,
        timeout_seconds=pair_timeout_seconds,
        runner=cad_compare_runner or _run_cad_compare_command,
    )
    overlay_probe = _measure_overlay_error()

    evidence_counts = {
        "pdf_pairs": sum(1 for item in pdf_pairs if item.get("status") == "passed"),
        "negative_failure_samples": sum(1 for item in negative_samples if item.get("status") == "passed"),
        "block_text_dimension_pairs": sum(1 for item in focused_pairs if item.get("status") == "passed"),
    }
    metrics: dict[str, Any] = {}
    if overlay_probe.get("status") == "passed":
        metrics["overlay_error_px_150dpi"] = overlay_probe["max_error_px"]

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(),
        "source_policy": (
            "synthetic PDF/DXF release probes generated locally and compared "
            "through existing product helpers"
        ),
        "work_dir": str(work_dir),
        "evidence_counts": evidence_counts,
        "metrics": metrics,
        "pdf_pairs": pdf_pairs,
        "negative_samples": negative_samples,
        "block_text_dimension_pairs": focused_pairs,
        "overlay_probe": overlay_probe,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return report


def _run_pdf_pairs(
    root: Path,
    *,
    count: int,
    timeout_seconds: float,
    runner: PdfCompareRunner,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index in range(max(0, count)):
        pair_dir = root / f"pdf-{index + 1:02d}"
        pair_dir.mkdir(parents=True, exist_ok=True)
        before = pair_dir / "before.pdf"
        after = pair_dir / "after.pdf"
        _write_pdf(before, f"S{index + 1:02d}-BASE REV A")
        _write_pdf(after, f"S{index + 1:02d}-BASE REV B")
        result_json = pair_dir / "pdf_compare_result.json"
        results.append(
            {
                "pair_id": f"pdf-{index + 1:02d}",
                "before_path": str(before),
                "after_path": str(after),
                "result_json": str(result_json),
                **runner(before, after, result_json, timeout_seconds),
            }
        )
    return results


def _run_pdf_compare(before: Path, after: Path, result_json: Path, timeout_seconds: float) -> dict[str, Any]:
    del timeout_seconds
    started = time.perf_counter()
    try:
        from src.services.comparison.drawing_batch import BatchCompareOptions, compare_pdf_documents

        result = compare_pdf_documents(
            before,
            after,
            BatchCompareOptions(
                compare_pdf_all_pages=False,
                pdf_text_compare=True,
                pdf_dpi=PDF_DPI,
                use_ocr_fallback=False,
            ),
        )
        payload = {"mode": "pdf_file", "status": "ok", "result": result.to_dict()}
        result_json.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        status = "passed" if metadata.get("comparison_type") == "PDF" and metadata.get("pages_compared", 0) >= 1 else "failed"
        return {
            "status": status,
            "elapsed_s": round(time.perf_counter() - started, 6),
            "summary": result.to_dict().get("summary", {}),
            "metadata": metadata,
        }
    except Exception as exc:
        result_json.write_text(
            json.dumps(
                {
                    "mode": "pdf_file",
                    "status": "failed",
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                },
                ensure_ascii=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return {
            "status": "failed",
            "elapsed_s": round(time.perf_counter() - started, 6),
            "error_type": exc.__class__.__name__,
            "message": str(exc),
        }


def _run_negative_samples(
    root: Path,
    *,
    count: int,
    python_executable: str,
    timeout_seconds: float,
    runner: CadCompareRunner,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index in range(max(0, count)):
        sample_dir = root / f"negative-{index + 1:02d}"
        sample_dir.mkdir(parents=True, exist_ok=True)
        before = sample_dir / "before-invalid.dxf"
        after = sample_dir / "after-invalid.dxf"
        before.write_text(f"not a valid dxf sample {index}\n", encoding="utf-8")
        after.write_text(f"also not a valid dxf sample {index}\n", encoding="utf-8")
        result_json = sample_dir / "cad_compare_result.json"
        execution = _run_cad_pair(
            before,
            after,
            result_json,
            python_executable=python_executable,
            timeout_seconds=timeout_seconds,
            runner=runner,
        )
        payload = _load_json(result_json)
        results.append(
            {
                "sample_id": f"negative-{index + 1:02d}",
                "before_path": str(before),
                "after_path": str(after),
                "result_json": str(result_json),
                "exit_code": execution.exit_code,
                "timed_out": execution.timed_out,
                "elapsed_s": round(execution.elapsed_s, 6),
                "status": "passed" if _negative_sample_passed(payload, execution) else "failed",
                "error_code": _result_error_code(payload),
                "stdout_tail": execution.stdout_tail,
                "stderr_tail": execution.stderr_tail,
            }
        )
    return results


def _run_focused_dxf_pairs(
    root: Path,
    *,
    count: int,
    python_executable: str,
    timeout_seconds: float,
    runner: CadCompareRunner,
) -> list[dict[str, Any]]:
    specs = [
        ("supplemental_text_detail", "ANNO_TEXT", "MARK A", "MARK B"),
        ("supplemental_dimension_text", "DIMENSION", "1000", "1200"),
        ("supplemental_block_note", "BLOCK_NOTE", "DOWEL @100", "DOWEL @200"),
    ]
    results: list[dict[str, Any]] = []
    for index, spec in enumerate(specs[: max(0, count)]):
        pair_id, layer, before_text, after_text = spec
        pair_dir = root / pair_id
        pair_dir.mkdir(parents=True, exist_ok=True)
        before = pair_dir / "before.dxf"
        after = pair_dir / "after.dxf"
        before.write_text(_dxf_for_text(before_text, layer=layer), encoding="utf-8")
        after.write_text(_dxf_for_text(after_text, layer=layer), encoding="utf-8")
        result_json = pair_dir / "cad_compare_result.json"
        execution = _run_cad_pair(
            before,
            after,
            result_json,
            python_executable=python_executable,
            timeout_seconds=timeout_seconds,
            runner=runner,
        )
        payload = _load_json(result_json)
        summary = _result_summary(payload)
        results.append(
            {
                "pair_id": pair_id,
                "before_path": str(before),
                "after_path": str(after),
                "result_json": str(result_json),
                "exit_code": execution.exit_code,
                "timed_out": execution.timed_out,
                "elapsed_s": round(execution.elapsed_s, 6),
                "status": "passed" if execution.exit_code == 0 and not execution.timed_out and _summary_total(summary) > 0 else "failed",
                "summary": summary,
                "stdout_tail": execution.stdout_tail,
                "stderr_tail": execution.stderr_tail,
            }
        )
    return results


def _run_cad_pair(
    before: Path,
    after: Path,
    result_json: Path,
    *,
    python_executable: str,
    timeout_seconds: float,
    runner: CadCompareRunner,
) -> CompareExecution:
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
        "200000",
        "--max-dxf-tokens",
        "30000000",
    ]
    return runner(command, timeout_seconds)


def _run_cad_compare_command(command: Sequence[str], timeout_seconds: float) -> CompareExecution:
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
            timed_out=True,
            stdout_tail=_tail(exc.stdout),
            stderr_tail=_tail(exc.stderr),
        )


def _measure_overlay_error() -> dict[str, Any]:
    alignment = align_cad_to_pdf(CAD_FRAME_MM, PDF_PIXEL_SIZE)
    zones = [
        {"zone_id": "z1", "bbox": [25.4, 25.4, 50.8, 50.8]},
        {"zone_id": "z2", "bbox": [100.0, 40.0, 130.0, 70.0]},
        {"zone_id": "z3", "bbox": [180.0, 100.0, 220.0, 130.0]},
    ]
    overlays = build_display_overlays(zones, alignment)
    errors: list[float] = []
    samples: list[dict[str, Any]] = []
    for zone, overlay in zip(zones, overlays):
        expected = _expected_pixel_bbox(zone["bbox"])
        actual = overlay.get("display_bbox")
        error = _bbox_max_abs_error(expected, actual)
        errors.append(error)
        samples.append(
            {
                "zone_id": zone["zone_id"],
                "cad_bbox": zone["bbox"],
                "expected_display_bbox": list(expected),
                "actual_display_bbox": actual,
                "max_error_px": round(error, 6),
            }
        )
    max_error = max(errors or [float("inf")])
    return {
        "status": "passed" if alignment.is_usable and max_error <= 10.0 else "failed",
        "dpi": PDF_DPI,
        "cad_frame_bbox": list(CAD_FRAME_MM),
        "pdf_pixel_size": list(PDF_PIXEL_SIZE),
        "alignment": alignment.to_dict(),
        "max_error_px": round(max_error, 6),
        "samples": samples,
    }


def _expected_pixel_bbox(bbox: Sequence[float]) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = [float(value) for value in bbox[:4]]
    frame_w = CAD_FRAME_MM[2] - CAD_FRAME_MM[0]
    frame_h = CAD_FRAME_MM[3] - CAD_FRAME_MM[1]
    scale = min(PDF_PIXEL_SIZE[0] / frame_w, PDF_PIXEL_SIZE[1] / frame_h)
    points = [
        ((x0 - CAD_FRAME_MM[0]) * scale, PDF_PIXEL_SIZE[1] - (y0 - CAD_FRAME_MM[1]) * scale),
        ((x0 - CAD_FRAME_MM[0]) * scale, PDF_PIXEL_SIZE[1] - (y1 - CAD_FRAME_MM[1]) * scale),
        ((x1 - CAD_FRAME_MM[0]) * scale, PDF_PIXEL_SIZE[1] - (y0 - CAD_FRAME_MM[1]) * scale),
        ((x1 - CAD_FRAME_MM[0]) * scale, PDF_PIXEL_SIZE[1] - (y1 - CAD_FRAME_MM[1]) * scale),
    ]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_max_abs_error(expected: Sequence[float], actual: Any) -> float:
    if not isinstance(actual, (list, tuple)) or len(actual) < 4:
        return float("inf")
    try:
        return max(abs(float(left) - float(right)) for left, right in zip(expected[:4], actual[:4]))
    except (TypeError, ValueError):
        return float("inf")


def _write_pdf(path: Path, text: str) -> None:
    try:
        import fitz  # type: ignore[import-not-found]

        doc = fitz.open()
        try:
            page = doc.new_page(width=float(PDF_PAGE_SIZE_POINTS[0]), height=float(PDF_PAGE_SIZE_POINTS[1]))
            page.insert_text((72, 72), text, fontsize=12)
            page.insert_text((72, 110), "DrawingCompare release PDF probe", fontsize=10)
            doc.save(str(path))
        finally:
            doc.close()
        return
    except Exception:
        _write_minimal_pdf(path, text)


def _write_minimal_pdf(path: Path, text: str) -> None:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 360 Td ({escaped}) Tj ET".encode("ascii", errors="replace")
    objects = [
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n",
        b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n",
        (
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 720 432]"
            b"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj\n"
        ),
        b"4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n",
        b"5 0 obj<</Length " + str(len(stream)).encode("ascii") + b">>stream\n" + stream + b"\nendstream\nendobj\n",
    ]
    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(content))
        content.extend(obj)
    xref_offset = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(
        (
            f"trailer<</Size {len(objects) + 1}/Root 1 0 R>>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(bytes(content))


def _dxf_for_text(text: str, *, layer: str) -> str:
    parts = [
        "0", "SECTION", "2", "HEADER",
        "9", "$ACADVER", "1", "AC1032",
        "9", "$INSUNITS", "70", "4",
        "0", "ENDSEC",
        "0", "SECTION", "2", "TABLES",
        "0", "TABLE", "2", "LAYER",
        "0", "LAYER", "2", layer, "70", "0", "62", "7", "6", "Continuous",
        "0", "LAYER", "2", "GRID", "70", "0", "62", "8", "6", "Continuous",
        "0", "ENDTAB",
        "0", "ENDSEC",
        "0", "SECTION", "2", "ENTITIES",
        "0", "LINE",
        "8", "GRID",
        "10", "0",
        "20", "0",
        "11", "200",
        "21", "0",
        "0", "TEXT",
        "8", layer,
        "10", "100",
        "20", "50",
        "40", "2.5",
        "1", text,
        "0", "ENDSEC",
        "0", "EOF",
    ]
    return "\n".join(parts) + "\n"


def _negative_sample_passed(payload: Any, execution: CompareExecution) -> bool:
    return (
        execution.exit_code not in (0, None)
        and not execution.timed_out
        and isinstance(payload, dict)
        and str(payload.get("status") or "").lower() == "failed"
        and bool(_result_error_code(payload))
    )


def _result_error_code(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    return str(metadata.get("error_code") or payload.get("error_code") or "")


def _result_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    return dict(summary)


def _summary_total(summary: dict[str, Any]) -> int:
    if "total_changes" in summary:
        return _as_int(summary.get("total_changes"))
    return _as_int(summary.get("added")) + _as_int(summary.get("deleted")) + _as_int(summary.get("modified"))


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _tail(value: Any, *, max_chars: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value)[-max_chars:]


def _resolve(path: Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else (ROOT / path).resolve()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--pdf-pair-count", type=int, default=10)
    parser.add_argument("--negative-sample-count", type=int, default=2)
    parser.add_argument("--block-text-dimension-pair-count", type=int, default=2)
    parser.add_argument("--pair-timeout-seconds", type=float, default=30.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_probe(
        out=args.out,
        work_dir=args.work_dir,
        pdf_pair_count=args.pdf_pair_count,
        negative_sample_count=args.negative_sample_count,
        block_text_dimension_pair_count=args.block_text_dimension_pair_count,
        pair_timeout_seconds=args.pair_timeout_seconds,
    )
    print(
        json.dumps(
            {
                "status": "written",
                "out": str(args.out),
                "evidence_counts": report["evidence_counts"],
                "metrics": report["metrics"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
