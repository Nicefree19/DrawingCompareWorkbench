"""CLI bridge for structural drawing evidence analysis.

The bridge is designed for MCP wrappers: it runs the ODA-free canonical import
path, writes artifacts, and prints a compact JSON packet to stdout.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from src.services.comparison.cad_stability import CadStabilityLimits
from src.services.comparison.dwg_diagnostics import diagnose_dwg_file
from src.services.comparison.drawing_compare_engine import (
    DrawingCompareEngine,
    DrawingCompareOptions,
)
from src.services.comparison.import_pipeline import ImportPipeline, ImportPipelineOptions
from src.services.comparison.structural_comparison_evidence_adapter import (
    build_comparison_evidence_packet,
    compact_comparison_diff_payload,
)
from src.services.comparison.structural_evidence_analyzer import (
    ANALYZER_VERSION,
    SCHEMA_VERSION,
    analyze_structural_evidence,
    make_run_id,
)
from src.services.comparison.structural_review_draft_composer import (
    DEFAULT_DRAFT_TYPE,
    compose_structural_review_draft,
)
from src.services.comparison.structural_output_safety import assert_structural_output_safe


DEFAULT_ARTIFACT_DIR = Path("build") / "mcp-artifacts"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="structural-evidence-bridge",
        description="Build bounded structural drawing evidence packets.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print compact JSON to stdout.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="Analyze one DXF/DWG drawing.")
    analyze_parser.add_argument("--path", type=Path, required=True, help="Drawing path.")
    analyze_parser.add_argument("--question", default="", help="Optional review question.")
    analyze_parser.add_argument(
        "--checklist",
        action="append",
        default=None,
        help="Optional checklist item. May be supplied multiple times.",
    )
    analyze_parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=DEFAULT_ARTIFACT_DIR,
        help="Root directory for JSON artifacts.",
    )
    analyze_parser.add_argument(
        "--max-evidence",
        type=int,
        default=30,
        help="Maximum evidence items in compact output. Hard-capped at 30.",
    )
    analyze_parser.set_defaults(func=_run_analyze)

    compare_parser = subparsers.add_parser(
        "compare",
        help="Compare two drawings and emit bounded structural evidence.",
    )
    compare_parser.add_argument("--before", type=Path, required=True, help="Baseline drawing path.")
    compare_parser.add_argument("--after", type=Path, required=True, help="New drawing path.")
    compare_parser.add_argument("--question", default="", help="Optional review question.")
    compare_parser.add_argument(
        "--checklist",
        action="append",
        default=None,
        help="Optional checklist item. May be supplied multiple times.",
    )
    compare_parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=DEFAULT_ARTIFACT_DIR,
        help="Root directory for JSON artifacts.",
    )
    compare_parser.add_argument(
        "--max-evidence",
        type=int,
        default=30,
        help="Maximum evidence items in compact output. Hard-capped at 30.",
    )
    compare_parser.set_defaults(func=_run_compare)

    draft_parser = subparsers.add_parser(
        "draft",
        help="Compose a human-review draft from a compact evidence packet.",
    )
    draft_parser.add_argument("--packet", type=Path, required=True, help="Compact evidence packet JSON.")
    draft_parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=DEFAULT_ARTIFACT_DIR,
        help="Root directory for draft artifacts.",
    )
    draft_parser.add_argument(
        "--language",
        choices=["ko", "en"],
        default="ko",
        help="Draft language.",
    )
    draft_parser.add_argument(
        "--draft-type",
        default=DEFAULT_DRAFT_TYPE,
        help=(
            "Draft profile: review_note, rfi_reply, or checklist_findings. "
            "Unsupported values produce a blocked JSON draft."
        ),
    )
    draft_parser.set_defaults(func=_run_draft)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args, emit_json=bool(args.json)))
    except KeyboardInterrupt:
        print("structural evidence analysis cancelled.", file=sys.stderr)
        return 130


def _run_analyze(args: argparse.Namespace, *, emit_json: bool) -> int:
    source_path = Path(args.path)
    question = str(args.question or "")
    checklist = [str(item) for item in args.checklist or []]
    max_evidence = int(args.max_evidence or 0)
    run_id = make_run_id(
        str(source_path),
        question,
        checklist,
        {
            "max_evidence": min(max(max_evidence, 0), 30),
            "analyzer_version": ANALYZER_VERSION,
            "bridge_version": "0.1.0",
        },
    )

    if not source_path.exists():
        packet = _missing_path_packet(source_path, question, checklist, max_evidence, run_id)
        _emit(packet, full_json=emit_json)
        return 2

    pipeline = ImportPipeline(
        ImportPipelineOptions(
            allow_oda_fallback=False,
            stability_limits=CadStabilityLimits(),
        )
    )
    import_result = pipeline.import_file(source_path)
    packet = analyze_structural_evidence(
        import_result,
        question=question,
        checklist=checklist,
        max_evidence=max_evidence,
        run_id=run_id,
    )
    if source_path.suffix.lower() == ".dwg":
        packet["diagnostics"]["dwg_native"] = diagnose_dwg_file(source_path).to_dict()

    assert_structural_output_safe(packet)
    artifact_paths = _write_artifacts(
        root=Path(args.artifact_dir),
        run_id=run_id,
        packet=packet,
        import_result=import_result,
        source_path=source_path,
        question=question,
        checklist=checklist,
    )
    packet["artifact_paths"] = artifact_paths
    _rewrite_packet_artifacts(packet)
    _emit(packet, full_json=emit_json)
    return 0 if packet["status"] in {"ok", "partial"} else 2


def _run_compare(args: argparse.Namespace, *, emit_json: bool) -> int:
    before_path = Path(args.before)
    after_path = Path(args.after)
    question = str(args.question or "")
    checklist = [str(item) for item in args.checklist or []]
    max_evidence = int(args.max_evidence or 0)
    run_id = make_run_id(
        f"{before_path} -> {after_path}",
        question,
        checklist,
        {
            "max_evidence": min(max(max_evidence, 0), 30),
            "adapter": "comparison-diff",
            "bridge_version": "0.1.0",
        },
    )

    missing = [path for path in (before_path, after_path) if not path.exists()]
    if missing:
        packet = _missing_path_packet(missing[0], question, checklist, max_evidence, run_id)
        _emit(packet, full_json=emit_json)
        return 2

    pipeline = ImportPipeline(
        ImportPipelineOptions(
            allow_oda_fallback=False,
            stability_limits=CadStabilityLimits(),
        )
    )
    before_import = pipeline.import_file(before_path)
    after_import = pipeline.import_file(after_path)
    before_drawing = before_import.normalized_drawing or before_import.canonical_drawing or {}
    after_drawing = after_import.normalized_drawing or after_import.canonical_drawing or {}
    comparison = DrawingCompareEngine(
        DrawingCompareOptions(include_entity_snapshots=False)
    ).compare(before_drawing, after_drawing)
    comparison_payload = comparison.to_dict()
    packet = build_comparison_evidence_packet(
        comparison_payload,
        question=question,
        checklist=checklist,
        max_evidence=max_evidence,
        run_id=run_id,
    )
    assert_structural_output_safe(packet)
    artifact_paths = _write_comparison_artifacts(
        root=Path(args.artifact_dir),
        run_id=run_id,
        packet=packet,
        comparison_payload=comparison_payload,
        before_path=before_path,
        after_path=after_path,
        question=question,
        checklist=checklist,
    )
    packet["artifact_paths"] = artifact_paths
    _rewrite_packet_artifacts(packet)
    _emit(packet, full_json=emit_json)
    return 0 if packet["status"] in {"ok", "partial"} else 2


def _missing_path_packet(
    source_path: Path,
    question: str,
    checklist: Sequence[str],
    max_evidence: int,
    run_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "status": "failed",
        "source": {
            "path": str(source_path),
            "format": source_path.suffix.lower().lstrip(".") or "unknown",
            "importer": "none",
            "import_status": "failed",
            "source_health": "missing",
            "version": None,
            "entity_count": 0,
            "layer_count": 0,
            "bbox": None,
            "elapsed_ms": 0.0,
        },
        "question": {
            "text": question.strip(),
            "keywords": [],
            "checklist": list(checklist),
        },
        "intent": {
            "name": "general_review",
            "confidence": "low",
            "method": "deterministic-rule-v0.1",
        },
        "summary": {
            "answer": "Drawing path does not exist; no evidence analysis was run.",
            "confidence": "low",
            "source_health": "missing",
            "judgment_level": "issue_suggestion_only",
            "requires_human_review": True,
            "evidence_count": 0,
            "notes": [
                "No structural safety approval or drawing release decision is made.",
            ],
        },
        "issue_suggestions": [
            {
                "suggestion_id": "is:0001",
                "kind": "source_health_review",
                "title": "Review missing drawing path before using evidence",
                "rationale": "The requested drawing path does not exist, so no evidence was parsed.",
                "evidence_ids": [],
                "confidence": "low",
                "next_action": "Fix the drawing path and rerun evidence analysis.",
                "human_review_required": True,
                "judgment_level": "issue_suggestion_only",
            }
        ],
        "evidence": [],
        "diagnostics": {
            "error_code": "CAD_PATH_INVALID",
            "message": "Drawing path does not exist.",
            "requested_max_evidence": max_evidence,
        },
        "unsupported_counts": {},
        "artifact_paths": {},
    }


def _run_draft(args: argparse.Namespace, *, emit_json: bool) -> int:
    packet_path = Path(args.packet)
    if not packet_path.is_file():
        draft = compose_structural_review_draft(
            {"schema_version": "missing"},
            language=str(args.language or "ko"),
            draft_type=str(args.draft_type or DEFAULT_DRAFT_TYPE),
        )
        _emit_draft(draft, full_json=emit_json)
        return 2
    try:
        evidence_packet = json.loads(packet_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        draft = compose_structural_review_draft(
            {"schema_version": "invalid"},
            language=str(args.language or "ko"),
            draft_type=str(args.draft_type or DEFAULT_DRAFT_TYPE),
        )
        _emit_draft(draft, full_json=emit_json)
        return 2

    draft = compose_structural_review_draft(
        evidence_packet,
        language=str(args.language or "ko"),
        draft_type=str(args.draft_type or DEFAULT_DRAFT_TYPE),
    )
    assert_structural_output_safe(draft)
    artifact_paths = _write_draft_artifacts(
        root=Path(args.artifact_dir),
        source_packet_path=packet_path,
        draft=draft,
    )
    draft["artifact_paths"] = artifact_paths
    _rewrite_draft_artifact(draft)
    _emit_draft(draft, full_json=emit_json)
    return 0 if draft["status"] == "drafted" else 2


def _write_artifacts(
    *,
    root: Path,
    run_id: str,
    packet: dict[str, Any],
    import_result: Any,
    source_path: Path,
    question: str,
    checklist: Sequence[str],
) -> dict[str, str]:
    day = datetime.now(UTC).strftime("%Y%m%d")
    run_dir = root / "structural-evidence" / day / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "compact": run_dir / "compact.json",
        "full_evidence": run_dir / "full_evidence.json",
        "canonical_summary": run_dir / "canonical_summary.json",
        "diagnostics": run_dir / "diagnostics.json",
        "manifest": run_dir / "manifest.json",
    }
    artifact_paths = {key: str(path.resolve()) for key, path in paths.items()}
    packet["artifact_paths"] = artifact_paths

    _write_json(paths["compact"], packet)
    _write_json(paths["full_evidence"], packet)
    _write_json(paths["canonical_summary"], _canonical_summary(import_result))
    _write_json(paths["diagnostics"], packet["diagnostics"])
    _write_json(
        paths["manifest"],
        {
            "schema_version": "structural-drawing-evidence-manifest/v0.1",
            "payload_kind": "structural_evidence",
            "run_id": run_id,
            "created_at": datetime.now(UTC).isoformat(),
            "source_path": str(source_path.resolve()),
            "question": question,
            "checklist": list(checklist),
            "analyzer_version": ANALYZER_VERSION,
            "bridge_version": "0.1.0",
            "max_evidence": len(packet.get("evidence") or []),
            "raw_payload_included": False,
            "judgment_level": packet.get("summary", {}).get("judgment_level"),
            "human_review_required": packet.get("summary", {}).get("requires_human_review"),
            "llm_used": False,
            "artifact_paths": artifact_paths,
        },
    )
    return artifact_paths


def _write_comparison_artifacts(
    *,
    root: Path,
    run_id: str,
    packet: dict[str, Any],
    comparison_payload: dict[str, Any],
    before_path: Path,
    after_path: Path,
    question: str,
    checklist: Sequence[str],
) -> dict[str, str]:
    day = datetime.now(UTC).strftime("%Y%m%d")
    run_dir = root / "structural-evidence" / day / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "compact": run_dir / "compact.json",
        "full_evidence": run_dir / "full_evidence.json",
        "comparison_diff": run_dir / "comparison_diff.json",
        "diagnostics": run_dir / "diagnostics.json",
        "manifest": run_dir / "manifest.json",
    }
    artifact_paths = {key: str(path.resolve()) for key, path in paths.items()}
    packet["artifact_paths"] = artifact_paths
    _write_json(paths["compact"], packet)
    _write_json(paths["full_evidence"], packet)
    _write_json(paths["comparison_diff"], compact_comparison_diff_payload(comparison_payload))
    _write_json(paths["diagnostics"], packet["diagnostics"])
    _write_json(
        paths["manifest"],
        {
            "schema_version": "structural-drawing-evidence-manifest/v0.1",
            "payload_kind": "structural_comparison_evidence",
            "run_id": run_id,
            "created_at": datetime.now(UTC).isoformat(),
            "source_path": f"{before_path.resolve()} -> {after_path.resolve()}",
            "question": question,
            "checklist": list(checklist),
            "bridge_version": "0.1.0",
            "max_evidence": len(packet.get("evidence") or []),
            "raw_payload_included": False,
            "judgment_level": packet.get("summary", {}).get("judgment_level"),
            "human_review_required": packet.get("summary", {}).get("requires_human_review"),
            "llm_used": False,
            "artifact_paths": artifact_paths,
        },
    )
    return artifact_paths


def _write_draft_artifacts(
    *,
    root: Path,
    source_packet_path: Path,
    draft: dict[str, Any],
) -> dict[str, str]:
    day = datetime.now(UTC).strftime("%Y%m%d")
    run_dir = root / "structural-drafts" / day / draft["source_run_id"] / draft["draft_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "draft_json": run_dir / "draft.json",
        "draft_markdown": run_dir / "draft.md",
        "manifest": run_dir / "manifest.json",
        "source_packet": source_packet_path,
    }
    artifact_paths = {key: str(path.resolve()) for key, path in paths.items()}
    draft["artifact_paths"] = artifact_paths
    _write_json(paths["draft_json"], draft)
    paths["draft_markdown"].write_text(_draft_markdown(draft), encoding="utf-8")
    _write_json(
        paths["manifest"],
        {
            "schema_version": "structural-review-draft-manifest/v0.1",
            "payload_kind": "structural_review_draft",
            "draft_id": draft["draft_id"],
            "source_run_id": draft["source_run_id"],
            "draft_type": draft["draft_type"],
            "created_at": datetime.now(UTC).isoformat(),
            "llm_used": False,
            "auto_submit_allowed": False,
            "human_review_required": (draft.get("safety") or {}).get("human_review_required"),
            "judgment_level": (draft.get("safety") or {}).get("judgment_level"),
            "raw_payload_included": False,
            "artifact_paths": artifact_paths,
        },
    )
    return artifact_paths


def _rewrite_packet_artifacts(packet: dict[str, Any]) -> None:
    compact = packet.get("artifact_paths", {}).get("compact")
    full = packet.get("artifact_paths", {}).get("full_evidence")
    if compact:
        _write_json(Path(compact), packet)
    if full:
        _write_json(Path(full), packet)


def _rewrite_draft_artifact(draft: dict[str, Any]) -> None:
    draft_json = draft.get("artifact_paths", {}).get("draft_json")
    if draft_json:
        _write_json(Path(draft_json), draft)


def _canonical_summary(import_result: Any) -> dict[str, Any]:
    drawing = import_result.normalized_drawing or import_result.canonical_drawing or {}
    return {
        "source": import_result.to_dict(),
        "layers": [
            {
                "id": layer.get("id"),
                "name": layer.get("name"),
                "visible": layer.get("visible"),
                "locked": layer.get("locked"),
            }
            for layer in drawing.get("layers") or []
        ],
        "entity_counts_by_type": _counts_by_type(drawing.get("entities") or []),
        "extents": drawing.get("extents"),
        "full_canonical_dump_included": False,
    }


def _counts_by_type(entities: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entity in entities:
        entity_type = str(entity.get("type") or "unknown")
        counts[entity_type] = counts.get(entity_type, 0) + 1
    return dict(sorted(counts.items()))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _draft_markdown(draft: dict[str, Any]) -> str:
    body = (draft.get("draft") or {}).get("body") or ""
    subject = (draft.get("draft") or {}).get("subject") or "Structural review draft"
    lines = [
        f"# {subject}",
        "",
        f"- draft_id: {draft.get('draft_id')}",
        f"- source_run_id: {draft.get('source_run_id')}",
        f"- draft_type: {draft.get('draft_type')}",
        f"- status: {draft.get('status')}",
        f"- auto_submit_allowed: {(draft.get('safety') or {}).get('auto_submit_allowed')}",
        "",
        body,
        "",
    ]
    return "\n".join(lines)


def _emit(packet: dict[str, Any], *, full_json: bool) -> None:
    if full_json:
        print(json.dumps(packet, ensure_ascii=False, indent=2))
        return
    summary = packet.get("summary") or {}
    print(
        "structural evidence: "
        f"status={packet.get('status')} "
        f"source_health={(packet.get('source') or {}).get('source_health')} "
        f"evidence={summary.get('evidence_count', 0)} "
        f"run_id={packet.get('run_id')}"
    )


def _emit_draft(draft: dict[str, Any], *, full_json: bool) -> None:
    if full_json:
        print(json.dumps(draft, ensure_ascii=False, indent=2))
        return
    print(
        "structural review draft: "
        f"status={draft.get('status')} "
        f"draft_id={draft.get('draft_id')} "
        f"source_run_id={draft.get('source_run_id')}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
