"""Benchmark multi-sheet matching accuracy on synthetic fixtures.

This benchmark is intentionally scoped to the ``sheet_match_*`` metric
namespace. Its synthetic output is a repeatable signal for matcher behavior,
not customer-grade release evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.services.comparison.page_descriptor import (
    PerPageDescriptor,
    build_dwg_page_descriptor,
)
from src.services.comparison.page_matcher import match_pdf_pages
from src.services.comparison.sheet_match_metrics import (
    SheetMatchPrediction,
    compute_sheet_match_metrics,
)


MIN_PRECISION = 0.95
MIN_RECALL = 0.90


def run_benchmark(fixture_root: Path, out: Path) -> dict[str, Any]:
    manifest_path = fixture_root / "multi_sheet_ground_truth.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    fixture_payloads: list[dict[str, Any]] = []
    aggregate_predictions: list[SheetMatchPrediction] = []
    aggregate_truth: list[Mapping[str, Any]] = []

    for fixture in manifest.get("fixtures", []):
        payload = _run_fixture(fixture_root, fixture)
        fixture_payloads.append(payload)
        aggregate_predictions.extend(payload["predictions_for_metrics"])
        aggregate_truth.extend(fixture.get("ground_truth", []))

    aggregate_metrics = compute_sheet_match_metrics(
        aggregate_predictions,
        aggregate_truth,
    )
    metrics_dict = aggregate_metrics.to_dict()
    passed = (
        aggregate_metrics.precision >= MIN_PRECISION
        and aggregate_metrics.recall >= MIN_RECALL
        and aggregate_metrics.false_match_count == 0
    )
    message = "ready to gate real fixtures" if passed else "synthetic sheet-match metric below threshold"

    payload = {
        "schema_version": 1,
        "synthetic": bool(manifest.get("synthetic")),
        "fixture_count": len(fixture_payloads),
        "status": "passed" if passed else "failed",
        "message": message,
        "thresholds": {
            "precision": MIN_PRECISION,
            "recall": MIN_RECALL,
            "false_match_count": 0,
        },
        **metrics_dict,
        "fixtures": [
            {
                key: value
                for key, value in fixture_payload.items()
                if key != "predictions_for_metrics"
            }
            for fixture_payload in fixture_payloads
        ],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _run_fixture(fixture_root: Path, fixture: Mapping[str, Any]) -> dict[str, Any]:
    before_sheets = list(fixture.get("before_sheets", []))
    after_sheets = list(fixture.get("after_sheets", []))
    before_descriptors = _build_descriptors(
        fixture_root / str(fixture.get("before_path", "")),
        before_sheets,
    )
    after_descriptors = _build_descriptors(
        fixture_root / str(fixture.get("after_path", "")),
        after_sheets,
    )

    candidates = match_pdf_pages(before_descriptors, after_descriptors)
    predictions = _predictions_from_candidates(candidates, before_sheets, after_sheets)
    metrics = compute_sheet_match_metrics(predictions, fixture.get("ground_truth", []))
    return {
        "name": str(fixture.get("name", "")),
        "synthetic": bool(fixture.get("synthetic")),
        "before_sheet_count": len(before_sheets),
        "after_sheet_count": len(after_sheets),
        "metrics": metrics.to_dict(),
        "predictions": [
            {
                "before_id": prediction.before_id,
                "after_id": prediction.after_id,
                "status": prediction.status,
                "confidence": round(prediction.confidence, 6),
            }
            for prediction in predictions
        ],
        "candidates": [_candidate_payload(candidate, before_sheets, after_sheets) for candidate in candidates],
        "predictions_for_metrics": predictions,
    }


def _build_descriptors(
    source_path: Path,
    sheets: Sequence[Mapping[str, Any]],
) -> list[PerPageDescriptor]:
    descriptors: list[PerPageDescriptor] = []
    for index, sheet in enumerate(sheets):
        frame_bbox = sheet.get("frame_bbox") or (0.0, 0.0, 0.0, 0.0)
        descriptors.append(
            build_dwg_page_descriptor(
                source_path,
                texts=[str(text) for text in sheet.get("texts", [])],
                frame_bbox=_as_bbox(frame_bbox),
                page_index=index,
                title_texts=[str(sheet.get("title", ""))],
            )
        )
    return descriptors


def _predictions_from_candidates(
    candidates: Sequence[Any],
    before_sheets: Sequence[Mapping[str, Any]],
    after_sheets: Sequence[Mapping[str, Any]],
) -> list[SheetMatchPrediction]:
    predictions: list[SheetMatchPrediction] = []
    for candidate in candidates:
        if not candidate.is_matched:
            continue
        if candidate.page_a_index < 0 or candidate.page_b_index < 0:
            continue
        try:
            before_id = str(before_sheets[candidate.page_a_index]["id"])
            after_id = str(after_sheets[candidate.page_b_index]["id"])
        except (IndexError, KeyError):
            continue
        predictions.append(
            SheetMatchPrediction(
                before_id=before_id,
                after_id=after_id,
                status=str(candidate.status.value),
                confidence=float(candidate.score),
            )
        )
    return predictions


def _candidate_payload(
    candidate: Any,
    before_sheets: Sequence[Mapping[str, Any]],
    after_sheets: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    payload = candidate.to_dict()
    if candidate.page_a_index >= 0 and candidate.page_a_index < len(before_sheets):
        payload["before_id"] = before_sheets[candidate.page_a_index].get("id", "")
    if candidate.page_b_index >= 0 and candidate.page_b_index < len(after_sheets):
        payload["after_id"] = after_sheets[candidate.page_b_index].get("id", "")
    return payload


def _as_bbox(value: object) -> tuple[float, float, float, float]:
    if not isinstance(value, Sequence) or len(value) != 4:
        return (0.0, 0.0, 0.0, 0.0)
    return tuple(float(part) for part in value)  # type: ignore[return-value]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    payload = run_benchmark(args.fixture_root, args.out)
    print(
        json.dumps(
            {
                "synthetic": payload["synthetic"],
                "status": payload["status"],
                "precision": payload["precision"],
                "recall": payload["recall"],
                "f1": payload["f1"],
                "false_match_count": payload["false_match_count"],
                "message": payload["message"],
                "out": str(args.out),
            },
            ensure_ascii=False,
        )
    )
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
