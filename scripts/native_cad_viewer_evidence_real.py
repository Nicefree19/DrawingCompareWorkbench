# -*- coding: utf-8 -*-
"""Emit a REAL native-CAD viewer-evidence artifact from a clean-room import.

Unlike ``scripts/native_cad_viewer_evidence_fixture.py`` (which routes a
deterministic fixture through the bridge adapter), this script runs a real
AC1015 sample through the clean-room native reader and the
``native_scene_pack_builder`` producer, then emits a real
``native-cad-viewer-evidence/v1`` LOD0 packet per sample.

It closes the reproducibility gap behind ``viewer_lod0_real_evidence_pending``:
the produced artifact is derived end-to-end from a real native import, not a
hand-authored fixture. It does NOT flip the matrix ``viewer_lod0_real`` flag or
promote AC1015 to ``viewable`` — that promotion is a claim decision reserved for
explicit user approval.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.comparison.dwg_importer import DwgImporter  # noqa: E402
from src.services.comparison.dwg_native_reader import DwgNativeAc1015Adapter  # noqa: E402
from src.services.comparison.native_scene_pack import (  # noqa: E402
    native_scene_viewer_evidence_payload,
)
from src.services.comparison.native_scene_pack_builder import (  # noqa: E402
    PRODUCER_ID,
    build_native_scene_pack,
)


DEFAULT_SAMPLE_DIR = Path(".local/native_cad_real_samples/nextgis_dwg_samples")
DEFAULT_SAMPLES = (
    "line_2000.dwg",
    "circle_2000.dwg",
    "arc_2000.dwg",
    "polyline2d_line_2000.dwg",
)
DEFAULT_OUTPUT = Path(".local/native_cad_viewer_evidence/real_native_evidence.json")


def build_real_viewer_evidence(
    sample_path: Path,
    *,
    primitive_budget: int = 5000,
    payload_byte_budget: int = 2_000_000,
) -> dict[str, Any]:
    """Run one real AC1015 sample through native import -> producer -> evidence."""

    doc = DwgImporter(adapter=DwgNativeAc1015Adapter()).import_file(sample_path)
    report = doc.get("import_report") or {}
    if report.get("status") != "ok":
        return {
            "sample": str(sample_path),
            "status": "import_failed",
            "import_report": {
                "status": report.get("status"),
                "error_code": report.get("error_code"),
            },
        }

    pack = build_native_scene_pack(doc)
    extents = doc.get("extents") or {}
    overlay = {
        "zone_id": "content",
        "change_type": "added",
        "priority_rank": 1,
        "old_bbox": None,
        "bbox": {
            "min_x": extents.get("min_x"),
            "min_y": extents.get("min_y"),
            "max_x": extents.get("max_x"),
            "max_y": extents.get("max_y"),
        },
    }
    evidence = native_scene_viewer_evidence_payload(
        pack,
        change_overlays=[overlay],
        import_report=report,
        primitive_budget=primitive_budget,
        payload_byte_budget=payload_byte_budget,
    )
    evidence["sample"] = str(sample_path)
    evidence["producer"] = PRODUCER_ID
    evidence["entity_count"] = pack.metadata.get("entity_count")
    evidence["unsupported_entity_type_counts"] = pack.metadata.get(
        "unsupported_entity_type_counts"
    )
    evidence["policy"] = {
        "real_native_import": True,
        "fixture_evidence_only": False,
        "default_support_expanded": False,
        "broad_dwg_support_claim": False,
        "matrix_promotion": "pending_user_approval",
    }
    evidence["status"] = "ok" if _evidence_ok(evidence) else "evidence_incomplete"
    return evidence


def _evidence_ok(evidence: dict[str, Any]) -> bool:
    viewer = evidence.get("viewer") or {}
    frame = evidence.get("primary_change_frame") or {}
    report = evidence.get("import_report") or {}
    return bool(
        evidence.get("schema_version") == "native-cad-viewer-evidence/v1"
        and frame.get("status") == "framed"
        and report.get("status") == "ok"
        and viewer.get("within_primitive_budget") is True
        and viewer.get("within_payload_byte_budget") is True
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-dir", type=Path, default=DEFAULT_SAMPLE_DIR)
    parser.add_argument(
        "--sample",
        action="append",
        default=None,
        help="Sample file name under --sample-dir (repeatable). Defaults to the "
        "line/circle/arc/polyline AC1015 corpus.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--primitive-budget", type=int, default=5000)
    parser.add_argument("--payload-byte-budget", type=int, default=2_000_000)
    args = parser.parse_args(argv)

    sample_names = list(args.sample) if args.sample else list(DEFAULT_SAMPLES)
    per_sample: list[dict[str, Any]] = []
    missing: list[str] = []
    for name in sample_names:
        path = args.sample_dir / name
        if not path.exists():
            missing.append(str(path))
            continue
        per_sample.append(
            build_real_viewer_evidence(
                path,
                primitive_budget=args.primitive_budget,
                payload_byte_budget=args.payload_byte_budget,
            )
        )

    ok_count = sum(1 for ev in per_sample if ev.get("status") == "ok")
    summary = {
        "schema_version": "native-cad-viewer-evidence-real-set/v1",
        "producer": PRODUCER_ID,
        "requested_samples": sample_names,
        "evaluated_samples": len(per_sample),
        "ok_samples": ok_count,
        "missing_samples": missing,
        "status": "ok" if per_sample and ok_count == len(per_sample) else "incomplete",
        "policy": {
            "real_native_import": True,
            "fixture_evidence_only": False,
            "default_support_expanded": False,
            "matrix_promotion": "pending_user_approval",
        },
        "evidence": per_sample,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "evidence"}, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
