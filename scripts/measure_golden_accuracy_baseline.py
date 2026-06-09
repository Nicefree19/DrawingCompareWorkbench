# -*- coding: utf-8 -*-
"""Golden-corpus accuracy baseline — first measured recall/precision/noise.

Why this exists (2026-06-10): the release gate requires recall>=0.90 /
precision>=0.85 (audit_drawing_compare_release_readiness.py) but no completed
ground-truth measurement existed — every accuracy report had precision=None /
recall=None and the customer ground-truth CSVs are empty templates. Meanwhile
``tests/data/comparison/golden/dxf/*/truth.json`` already carries labeled
expected changes (including pure-noise fixtures 01_identical /
05_cosmetic_only). This harness closes that gap at golden-corpus scale:

    golden pair --DwgDiffer (canonical DEFAULT path)--> predicted changes
        --accuracy_metrics.match_changes_to_truth--> TP/FP/FN
        --compute_metrics--> per-pair + corpus micro precision/recall/F1

It measures the engine AS SHIPPED (heuristic-only classification, no
embedding/LLM) so later AI-tier experiments have an honest baseline to beat.

Honest scope: the golden corpus is small and synthetic — corpus numbers here
are a BASELINE / regression anchor, not a customer-grade accuracy claim (that
still needs the real labeled corpus the MVP audit demands).

Adapter note: the canonical pipeline emits ``ChangeRecord`` whose ``location``
is a bare ``"x,y"`` string and whose layer/bbox live in ``metadata`` —
``accuracy_metrics._normalise_predicted`` expects a tuple / ``"(x, y)"`` form,
so predictions are adapted here (script-local; src untouched).

Usage:
    python scripts/measure_golden_accuracy_baseline.py \
        [--golden-root tests/data/comparison/golden/dxf] \
        [--out-json build/reports/golden_accuracy_baseline.json] \
        [--out-md docs/GOLDEN_ACCURACY_BASELINE_REPORT.md] \
        [--location-tol 50.0]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Windows consoles default to cp949 here; report strings carry em-dashes and
# Korean fixture comments, so force utf-8 (same fix as other repo scripts).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — non-reconfigurable stream (tests)
        pass

from src.services.comparison.accuracy_metrics import (  # noqa: E402
    AccuracyMetrics,
    ExpectedChange,
    compute_metrics,
    match_changes_to_truth,
)

DEFAULT_GOLDEN_ROOT = ROOT / "tests" / "data" / "comparison" / "golden" / "dxf"
DEFAULT_OUT_JSON = ROOT / "build" / "reports" / "golden_accuracy_baseline.json"
DEFAULT_OUT_MD = ROOT / "docs" / "GOLDEN_ACCURACY_BASELINE_REPORT.md"
#: Fallback matching tolerance when a truth entry has no ``tolerance_mm``.
#: Golden fixtures are mm-scale drawings; 50 mm matches the tolerance the
#: fixtures themselves declare (02/06/... all use 50.0).
DEFAULT_LOCATION_TOL = 50.0


@dataclass(frozen=True)
class AdaptedPrediction:
    """ChangeRecord adapted to the shape ``_normalise_predicted`` reads.

    ``location`` is a parsed (x, y) tuple (or None) so the matcher does not
    depend on the bare-``"x,y"`` string format the canonical pipeline emits.
    """

    raw_key: str
    location: Optional[Tuple[float, float]]
    change_type: Any
    layer: Optional[str]
    entity_type: Optional[str]
    change_category: Optional[str]
    metadata: dict


def _parse_xy_string(text: Any) -> Optional[Tuple[float, float]]:
    """``"500.0,400.0"`` (canonical ChangeRecord form) → (500.0, 400.0)."""

    if not isinstance(text, str):
        return None
    parts = text.replace("(", "").replace(")", "").split(",")
    if len(parts) < 2:
        return None
    try:
        return (float(parts[0].strip()), float(parts[1].strip()))
    except ValueError:
        return None


def _bbox_centroid(meta: Any) -> Optional[Tuple[float, float]]:
    """metadata['bbox'] = {min_x,min_y,max_x,max_y} → centroid."""

    if not isinstance(meta, dict):
        return None
    bbox = meta.get("bbox")
    if not isinstance(bbox, dict):
        return None
    try:
        return (
            (float(bbox["min_x"]) + float(bbox["max_x"])) / 2.0,
            (float(bbox["min_y"]) + float(bbox["max_y"])) / 2.0,
        )
    except (KeyError, TypeError, ValueError):
        return None


def adapt_prediction(record: Any) -> AdaptedPrediction:
    """Adapt a canonical ``ChangeRecord`` (or compatible object) for matching.

    Location priority: tuple location → ``"x,y"`` string → metadata bbox
    centroid → None (location-free; can only match location-free truths).
    """

    raw_loc = getattr(record, "location", None)
    location: Optional[Tuple[float, float]] = None
    if isinstance(raw_loc, tuple) and len(raw_loc) >= 2:
        try:
            location = (float(raw_loc[0]), float(raw_loc[1]))
        except (TypeError, ValueError):
            location = None
    if location is None:
        location = _parse_xy_string(raw_loc)
    meta = getattr(record, "metadata", None) or {}
    if location is None:
        location = _bbox_centroid(meta)
    # Entity-type vocabulary: golden truth files were authored in the legacy
    # DxfComparator era and use uppercase DXF names ("LINE", "CIRCLE"), while
    # the canonical pipeline emits lowercase canonical names ("line",
    # "circle", "block_reference"). The matcher's entity filter is an exact
    # string compare, so without case-folding EVERY typed truth is rejected
    # (verified: fixture 14 failed at distance 0.0). Lowercase both sides
    # here; true vocabulary gaps (ATTRIB vs block_reference) stay visible.
    entity_type = meta.get("entity_type") if isinstance(meta, dict) else None
    return AdaptedPrediction(
        raw_key=str(getattr(record, "key", "") or ""),
        location=location,
        change_type=getattr(record, "change_type", None),
        layer=(meta.get("layer") if isinstance(meta, dict) else None),
        entity_type=(str(entity_type).lower() if entity_type else None),
        change_category=(
            meta.get("change_category") if isinstance(meta, dict) else None
        ),
        metadata=dict(meta) if isinstance(meta, dict) else {},
    )


def load_truth(truth_path: Path) -> Tuple[List[ExpectedChange], str]:
    """truth.json → (ExpectedChange list, human comment)."""

    data = json.loads(truth_path.read_text(encoding="utf-8"))
    expected: List[ExpectedChange] = []
    for item in data.get("expected_changes") or []:
        loc_raw = item.get("location")
        location: Optional[Tuple[float, float]] = None
        if isinstance(loc_raw, (list, tuple)) and len(loc_raw) >= 2:
            location = (float(loc_raw[0]), float(loc_raw[1]))
        entity_type = item.get("entity_type")
        expected.append(
            ExpectedChange(
                location=location,
                change_type=str(item.get("change_type") or "modified"),
                layer=item.get("layer"),
                # Lowercase to the canonical vocabulary — see adapt_prediction.
                entity_type=(str(entity_type).lower() if entity_type else None),
                tolerance_mm=(
                    float(item["tolerance_mm"])
                    if item.get("tolerance_mm") is not None
                    else None
                ),
                notes=str(item.get("notes") or ""),
            )
        )
    return expected, str(data.get("comment") or "")


def _is_cosmetic(prediction: Any) -> bool:
    """A false positive that the cosmetic/noise demotion would down-rank."""

    category = str(getattr(prediction, "change_category", "") or "").lower()
    if category == "cosmetic":
        return True
    meta = getattr(prediction, "metadata", None) or {}
    return str(meta.get("change_category") or "").lower() == "cosmetic"


def evaluate_pair(
    pair_dir: Path,
    *,
    location_tol: float = DEFAULT_LOCATION_TOL,
) -> dict:
    """Run the production compare on one golden pair and score it vs truth."""

    from src.services.comparison.dwg_differ import DwgDiffer

    before = pair_dir / "before.dxf"
    after = pair_dir / "after.dxf"
    truth_path = pair_dir / "truth.json"
    expected, comment = load_truth(truth_path)

    started = time.perf_counter()
    result = DwgDiffer().compare(before, after)
    elapsed_s = time.perf_counter() - started

    predictions = [adapt_prediction(ch) for ch in (result.changes or [])]
    report = match_changes_to_truth(
        predictions, expected, location_tol=location_tol, strict_type=False
    )
    metrics: AccuracyMetrics = compute_metrics(report)

    cosmetic_fp = sum(1 for fp in report.false_positives if _is_cosmetic(fp))
    return {
        "pair": pair_dir.name,
        "comment": comment,
        "expected_count": len(expected),
        "predicted_count": len(predictions),
        "tp": metrics.tp_count,
        "fp": metrics.fp_count,
        "fn": metrics.fn_count,
        "precision": metrics.precision,
        "recall": metrics.recall,
        "f1": metrics.f1,
        "cosmetic_fp": cosmetic_fp,
        "missed": [
            {"location": fn.location, "change_type": fn.change_type, "notes": fn.notes}
            for fn in report.false_negatives
        ],
        "pipeline_status": (result.metadata or {}).get("pipeline_status"),
        "warnings": list(result.warnings or [])[:5],
        "elapsed_s": round(elapsed_s, 2),
    }


def aggregate(rows: List[dict]) -> dict:
    """Micro-aggregate TP/FP/FN across the corpus + noise-fixture stats."""

    tp = sum(int(r["tp"]) for r in rows)
    fp = sum(int(r["fp"]) for r in rows)
    fn = sum(int(r["fn"]) for r in rows)
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and (precision + recall)
        else None
    )
    noise_rows = [r for r in rows if int(r["expected_count"]) == 0]
    return {
        "pair_count": len(rows),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "micro_precision": precision,
        "micro_recall": recall,
        "micro_f1": f1,
        "cosmetic_fp": sum(int(r["cosmetic_fp"]) for r in rows),
        "noise_fixture_count": len(noise_rows),
        "noise_fixture_fp_total": sum(int(r["fp"]) for r in noise_rows),
    }


def discover_pairs(golden_root: Path) -> List[Path]:
    pairs: List[Path] = []
    for truth in sorted(golden_root.rglob("truth.json")):
        d = truth.parent
        if (d / "before.dxf").exists() and (d / "after.dxf").exists():
            pairs.append(d)
    return pairs


def _fmt(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.3f}"


def render_markdown(rows: List[dict], agg: dict, *, location_tol: float) -> str:
    lines = [
        "# Golden-Corpus Accuracy Baseline (heuristic-only engine)",
        "",
        "First measured recall/precision/noise for the comparison engine as",
        "shipped (canonical `DwgDiffer` default path; no embedding/LLM tier).",
        "Generated by `scripts/measure_golden_accuracy_baseline.py` against",
        "`tests/data/comparison/golden/dxf/*/truth.json`.",
        "",
        "**Honest scope** — the golden corpus is small and synthetic. These",
        "numbers are a regression anchor and an AI-tier A/B baseline, NOT a",
        "customer-grade accuracy claim (the real labeled corpus required by",
        "the MVP audit is still missing). Matching: greedy nearest within",
        f"`tolerance_mm` (truth value, fallback {location_tol:g}), non-strict",
        "type (a deleted+added pair may satisfy one `modified` truth — the",
        "unmatched half then counts as FP).",
        "",
        "## Corpus aggregate (micro)",
        "",
        "| pairs | TP | FP | FN | precision | recall | F1 | cosmetic FP | noise-fixture FP |",
        "|---|---|---|---|---|---|---|---|---|",
        (
            f"| {agg['pair_count']} | {agg['tp']} | {agg['fp']} | {agg['fn']} "
            f"| {_fmt(agg['micro_precision'])} | {_fmt(agg['micro_recall'])} "
            f"| {_fmt(agg['micro_f1'])} | {agg['cosmetic_fp']} "
            f"| {agg['noise_fixture_fp_total']} (on {agg['noise_fixture_count']} fixtures) |"
        ),
        "",
        "## Per-pair",
        "",
        "| pair | truth | predicted | TP | FP | FN | precision | recall | note |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        note = "NOISE FIXTURE (0 expected)" if r["expected_count"] == 0 else ""
        if r["fn"]:
            note = (note + " " if note else "") + f"MISSED {r['fn']}"
        lines.append(
            f"| {r['pair']} | {r['expected_count']} | {r['predicted_count']} "
            f"| {r['tp']} | {r['fp']} | {r['fn']} | {_fmt(r['precision'])} "
            f"| {_fmt(r['recall'])} | {note} |"
        )
    missed_any = [r for r in rows if r["missed"]]
    if missed_any:
        lines += ["", "## Missed changes (false negatives — the dangerous kind)", ""]
        for r in missed_any:
            for m in r["missed"]:
                lines.append(
                    f"- `{r['pair']}` @ {m['location']} ({m['change_type']}): {m['notes']}"
                )
    lines += [
        "",
        "## Reading the numbers",
        "",
        "- **recall** is the safety-critical metric (missed real changes).",
        "- **FP on noise fixtures** (01_identical / 05_cosmetic_only) is the",
        "  raw noise floor; `cosmetic FP` is the share the existing cosmetic",
        "  demotion already down-ranks (demote-not-drop).",
        "- Re-run after any engine/classifier change and diff this report;",
        "  it is the baseline any AI tier (embedding/LLM) must beat.",
        "",
    ]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden-root", type=Path, default=DEFAULT_GOLDEN_ROOT)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    parser.add_argument("--location-tol", type=float, default=DEFAULT_LOCATION_TOL)
    args = parser.parse_args(argv)

    pairs = discover_pairs(args.golden_root)
    if not pairs:
        print(f"no golden pairs with truth.json under {args.golden_root}")
        return 2

    rows: List[dict] = []
    for pair_dir in pairs:
        try:
            row = evaluate_pair(pair_dir, location_tol=args.location_tol)
        except Exception as exc:  # noqa: BLE001 — one bad pair must not kill the corpus run
            row = {
                "pair": pair_dir.name,
                "comment": "",
                "expected_count": 0,
                "predicted_count": 0,
                "tp": 0,
                "fp": 0,
                "fn": 0,
                "precision": None,
                "recall": None,
                "f1": None,
                "cosmetic_fp": 0,
                "missed": [],
                "pipeline_status": f"EVAL_ERROR: {type(exc).__name__}: {exc}",
                "warnings": [],
                "elapsed_s": 0.0,
                "eval_error": True,
            }
        rows.append(row)
        print(
            f"{row['pair']}: truth={row['expected_count']} pred={row['predicted_count']} "
            f"tp={row['tp']} fp={row['fp']} fn={row['fn']} "
            f"p={_fmt(row['precision'])} r={_fmt(row['recall'])} ({row['elapsed_s']}s)"
        )

    scored = [r for r in rows if not r.get("eval_error")]
    agg = aggregate(scored)
    print(
        f"\nAGGREGATE pairs={agg['pair_count']} tp={agg['tp']} fp={agg['fp']} fn={agg['fn']} "
        f"precision={_fmt(agg['micro_precision'])} recall={_fmt(agg['micro_recall'])} "
        f"f1={_fmt(agg['micro_f1'])} noise_fp={agg['noise_fixture_fp_total']}"
    )

    payload = {
        "generated_by": "scripts/measure_golden_accuracy_baseline.py",
        "engine": "DwgDiffer canonical default (heuristic-only classification)",
        "matching": {
            "location_tol_fallback": args.location_tol,
            "strict_type": False,
            "require_layer_match": False,
        },
        "aggregate": agg,
        "pairs": rows,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.write_text(
        render_markdown(rows, agg, location_tol=args.location_tol), encoding="utf-8"
    )
    print(f"\nwrote {args.out_json}\nwrote {args.out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
