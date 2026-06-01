"""Score region_match_summary.json against sheet-match ground truth.

This adapter lets an actual region-aware compare run feed the same
``sheet_match_*`` metric namespace used by the synthetic benchmark. It maps
region ids such as ``before-frame-1`` / ``after-frame-1`` back to fixture sheet
ids, then delegates precision/recall calculation to
``compute_sheet_match_metrics``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src.services.comparison.sheet_match_metrics import (  # noqa: E402
    SheetMatchPrediction,
    compute_sheet_match_metrics,
)


MIN_PRECISION = 0.95
MIN_RECALL = 0.90
REGION_INDEX_RE = re.compile(r"(\d+)$")


def score_region_match_summary(
    *,
    region_summary_path: Path,
    ground_truth_path: Path,
    fixture_name: str,
    out: Path | None = None,
) -> dict[str, Any]:
    region_summary = _read_json(region_summary_path)
    manifest = _read_json(ground_truth_path)
    fixture = _find_fixture(manifest, fixture_name)

    before_sheets = list(fixture.get("before_sheets", []))
    after_sheets = list(fixture.get("after_sheets", []))
    predictions = _predictions_from_region_summary(
        region_summary,
        before_sheets=before_sheets,
        after_sheets=after_sheets,
    )
    metrics = compute_sheet_match_metrics(predictions, fixture.get("ground_truth", []))
    passed = (
        metrics.precision >= MIN_PRECISION
        and metrics.recall >= MIN_RECALL
        and metrics.false_match_count == 0
    )
    payload = {
        "schema_version": 1,
        "source": "region_match_summary",
        "fixture_name": fixture_name,
        "synthetic": bool(fixture.get("synthetic") or manifest.get("synthetic")),
        "status": "passed" if passed else "failed",
        "message": "region sheet-match metrics ready" if passed else "region sheet-match metric below threshold",
        "thresholds": {
            "precision": MIN_PRECISION,
            "recall": MIN_RECALL,
            "false_match_count": 0,
        },
        **metrics.to_dict(),
        "prediction_count": len(predictions),
        "predictions": [
            {
                "before_id": prediction.before_id,
                "after_id": prediction.after_id,
                "status": prediction.status,
                "confidence": round(prediction.confidence, 6),
            }
            for prediction in predictions
        ],
        "region_summary_path": str(region_summary_path),
        "ground_truth_path": str(ground_truth_path),
    }
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _predictions_from_region_summary(
    region_summary: Mapping[str, Any],
    *,
    before_sheets: Sequence[Mapping[str, Any]],
    after_sheets: Sequence[Mapping[str, Any]],
) -> list[SheetMatchPrediction]:
    predictions: list[SheetMatchPrediction] = []
    for summary in region_summary.get("summaries", []):
        if not isinstance(summary, Mapping):
            continue
        for match in summary.get("matches", []):
            if not isinstance(match, Mapping):
                continue
            status = _prediction_status(str(match.get("status") or ""))
            if not status:
                continue
            before_id = _sheet_id_from_region_id(
                match.get("before_region_id"),
                before_sheets,
            )
            after_id = _sheet_id_from_region_id(
                match.get("after_region_id"),
                after_sheets,
            )
            if not before_id or not after_id:
                continue
            predictions.append(
                SheetMatchPrediction(
                    before_id=before_id,
                    after_id=after_id,
                    status=status,
                    confidence=_float(match.get("score")),
                )
            )
    return predictions


def _prediction_status(region_status: str) -> str:
    normalized = region_status.strip().lower()
    if normalized == "auto_matched":
        return "auto_confirmed"
    if normalized in {"manual_matched", "review_required"}:
        return "review_required"
    if normalized == "matched":
        return "matched"
    return ""


def _sheet_id_from_region_id(
    region_id: object,
    sheets: Sequence[Mapping[str, Any]],
) -> str:
    text = str(region_id or "").strip()
    match = REGION_INDEX_RE.search(text)
    if not match:
        return ""
    index = int(match.group(1)) - 1
    if index < 0 or index >= len(sheets):
        return ""
    return str(sheets[index].get("id") or "")


def _find_fixture(manifest: Mapping[str, Any], fixture_name: str) -> Mapping[str, Any]:
    for fixture in manifest.get("fixtures", []):
        if isinstance(fixture, Mapping) and fixture.get("name") == fixture_name:
            return fixture
    raise ValueError(f"fixture not found: {fixture_name}")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region-summary", type=Path, required=True)
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument("--fixture-name", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    payload = score_region_match_summary(
        region_summary_path=args.region_summary,
        ground_truth_path=args.ground_truth,
        fixture_name=args.fixture_name,
        out=args.out,
    )
    print(
        json.dumps(
            {
                "status": payload["status"],
                "fixture_name": payload["fixture_name"],
                "precision": payload["precision"],
                "recall": payload["recall"],
                "f1": payload["f1"],
                "manual_match_required_count": payload["manual_match_required_count"],
                "false_match_count": payload["false_match_count"],
                "prediction_count": payload["prediction_count"],
                "out": str(args.out),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
