# -*- coding: utf-8 -*-
"""Inventory DWG native-support Phase 0 corpus readiness.

This script does not import or compare DWG geometry. It records cheap version
signals plus converted-DXF fallback readiness so AC1032/native reader planning
can start from measured corpus facts.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.comparison.dwg_dxf_fallback import resolve_dwg_dxf_fallback_pair
from src.services.comparison.dwg_importer import DwgImportError, DwgVersionDetector


SCHEMA_VERSION = 2
TARGET_DWG_CODES = ("AC1018", "AC1021", "AC1024", "AC1027", "AC1032")
MARKDOWN_ROW_LIMIT = 50


def inventory_dwg_native_phase0(
    roots: Sequence[Path],
    *,
    out: Path | None = None,
    report_md: Path | None = None,
    max_dwg_samples: int = 200,
) -> dict[str, Any]:
    resolved_roots = [Path(root).resolve() for root in roots]
    root_summaries = [_root_summary(root, limit=max(1, int(max_dwg_samples))) for root in resolved_roots]
    version_items_by_path: dict[str, dict[str, Any]] = {}
    for summary in root_summaries:
        for item in summary["dwg_files"]:
            version_items_by_path.setdefault(str(item["path"]), item)
    version_items = list(version_items_by_path.values())
    fallback_items = [
        summary["folder_fallback"]
        for summary in root_summaries
        if isinstance(summary.get("folder_fallback"), dict)
    ]
    version_counts = _version_counts(version_items)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(),
        "roots": [str(root) for root in resolved_roots],
        "dwg_sample_limit": max(1, int(max_dwg_samples)),
        "dwg_sample_limit_per_root": max(1, int(max_dwg_samples)),
        "dwg_count": len(version_items),
        "dwg_sampled_count": len(version_items),
        "version_counts": _version_counts(version_items),
        "unsupported_count": sum(1 for item in version_items if not item.get("supported")),
        "converted_dxf_fallback_ready_count": sum(
            1 for summary in root_summaries if summary.get("converted_dxf_fallback_ready")
        ),
        "target_versions": list(TARGET_DWG_CODES),
        "missing_target_versions": [
            code for code in TARGET_DWG_CODES if int(version_counts.get(code, 0)) == 0
        ],
        "root_summaries": root_summaries,
        "corpus_gaps": _corpus_gaps(root_summaries, version_counts),
        "dwg_files": version_items,
        "folder_fallbacks": fallback_items,
    }
    if out is not None:
        out = Path(out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if report_md is not None:
        write_markdown_report(payload, Path(report_md))
    return payload


def _root_summary(root: Path, *, limit: int) -> dict[str, Any]:
    dwg_files, sample_limit_reached = _collect_dwg_files(root, limit=limit)
    version_items = [_version_item(path) for path in dwg_files]
    unsupported_count = sum(1 for item in version_items if not item.get("supported"))
    fallback_item = (
        _folder_fallback_item(root)
        if unsupported_count and root.exists() and root.is_dir()
        else None
    )
    fallback_ready = bool(fallback_item and fallback_item.get("fallback_used"))
    return {
        "root": str(root),
        "exists": root.exists(),
        "is_file": root.is_file(),
        "is_dir": root.is_dir(),
        "dwg_sample_limit": max(1, int(limit)),
        "dwg_count": len(version_items),
        "dwg_sampled_count": len(version_items),
        "sample_limit_reached": sample_limit_reached,
        "version_counts": _version_counts(version_items),
        "supported_count": sum(1 for item in version_items if item.get("supported")),
        "unsupported_count": unsupported_count,
        "converted_dxf_fallback_ready": fallback_ready,
        "fallback_kind": str(fallback_item.get("fallback_kind", "")) if fallback_item else "",
        "fallback_score": fallback_item.get("fallback_score") if fallback_item else None,
        "fallback_counts": fallback_item.get("fallback_counts", {}) if fallback_item else {},
        "fallback_candidate_count": len(fallback_item.get("fallback_candidates", [])) if fallback_item else 0,
        "missing_converted_dxf_baseline": bool(unsupported_count and not fallback_ready),
        "dwg_files": version_items,
        "folder_fallback": fallback_item,
    }


def _collect_dwg_files(root: Path, *, limit: int) -> tuple[list[Path], bool]:
    collected: list[Path] = []
    seen: set[Path] = set()
    for item in _iter_dwg_files([root]):
        resolved = item.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if len(collected) >= max(1, int(limit)):
            return collected, True
        collected.append(resolved)
    return collected, False


def _iter_dwg_files(roots: Iterable[Path]) -> Iterable[Path]:
    seen: set[Path] = set()
    for root in roots:
        iterators: list[Iterable[Path]]
        if root.is_file() and root.suffix.lower() == ".dwg":
            iterators = [[root]]
        elif root.is_dir():
            iterators = [root.rglob("*.dwg"), root.rglob("*.DWG")]
        else:
            continue
        for iterator in iterators:
            for item in iterator:
                resolved = item.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                yield resolved


def _version_item(path: Path) -> dict[str, Any]:
    try:
        version = DwgVersionDetector.detect_file(path).to_dict()
    except DwgImportError as exc:
        version = {
            "code": "",
            "family": "",
            "release": "",
            "supported": False,
            "error_code": exc.code,
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        version = {
            "code": "",
            "family": "",
            "release": "",
            "supported": False,
            "error": str(exc),
        }
    return {"path": str(path), **version}


def _version_counts(items: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        code = str(item.get("code") or item.get("error_code") or "unknown")
        counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items()))


def _folder_fallback_item(root: Path) -> dict[str, Any]:
    resolution = resolve_dwg_dxf_fallback_pair(root, root)
    payload = resolution.to_dict()
    diagnostics = payload.get("diagnostics")
    if isinstance(diagnostics, dict):
        payload["fallback_kind"] = diagnostics.get("fallback_kind", "")
        payload["fallback_score"] = diagnostics.get("fallback_score")
        payload["fallback_counts"] = diagnostics.get("fallback_counts", {})
        payload["fallback_candidates"] = diagnostics.get("fallback_candidates", [])
    payload["root"] = str(root.resolve())
    payload["fallback_used"] = bool(payload.pop("used", False))
    return payload


def _corpus_gaps(
    root_summaries: Sequence[dict[str, Any]],
    version_counts: dict[str, int],
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for summary in root_summaries:
        root = str(summary.get("root", ""))
        if not summary.get("exists"):
            gaps.append({"scope": "root", "root": root, "gap": "missing_root"})
            continue
        if not summary.get("is_file") and not summary.get("is_dir"):
            gaps.append({"scope": "root", "root": root, "gap": "unsupported_root_type"})
            continue
        if int(summary.get("dwg_count", 0)) == 0:
            gaps.append({"scope": "root", "root": root, "gap": "no_dwg_samples"})
            continue
        if summary.get("sample_limit_reached"):
            gaps.append(
                {
                    "scope": "root",
                    "root": root,
                    "gap": "sample_limit_reached",
                    "sample_limit": summary.get("dwg_sample_limit"),
                }
            )
        if summary.get("missing_converted_dxf_baseline"):
            gaps.append(
                {
                    "scope": "root",
                    "root": root,
                    "gap": "unsupported_without_converted_dxf_baseline",
                    "unsupported_count": summary.get("unsupported_count", 0),
                    "version_counts": summary.get("version_counts", {}),
                }
            )

    missing_versions = [code for code in TARGET_DWG_CODES if int(version_counts.get(code, 0)) == 0]
    if missing_versions:
        gaps.append(
            {
                "scope": "global",
                "gap": "missing_target_version_coverage",
                "versions": missing_versions,
            }
        )
    return gaps


def write_markdown_report(payload: dict[str, Any], path: Path) -> None:
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown_report(payload), encoding="utf-8")


def render_markdown_report(payload: dict[str, Any]) -> str:
    roots = payload.get("root_summaries", [])
    lines: list[str] = [
        "# ADR-004 Phase 0 DWG Corpus Report",
        "",
        f"- Generated: `{payload.get('generated_at', '')}`",
        f"- Schema version: `{payload.get('schema_version', '')}`",
        f"- DWG sample limit per root: `{payload.get('dwg_sample_limit_per_root', payload.get('dwg_sample_limit', ''))}`",
        f"- Unique sampled DWG files: `{payload.get('dwg_count', 0)}`",
        f"- Unsupported sampled DWG files: `{payload.get('unsupported_count', 0)}`",
        f"- Converted-DXF fallback-ready roots: `{payload.get('converted_dxf_fallback_ready_count', 0)}`",
        "",
        "## Guardrails",
        "",
        "- This report is corpus/readiness inventory only.",
        "- It does not implement a native DWG parser.",
        "- AC1032 native support remains unclaimed; AC1018+ DWG still uses user-provided converted DXF.",
        "- No ODA/GPL/AGPL converter or library path is introduced by this Phase 0 report.",
        "",
        "## Root Summary",
        "",
    ]
    lines.extend(
        _markdown_table(
            [
                "Root",
                "Exists",
                "DWG sampled",
                "Versions",
                "Unsupported",
                "Fallback ready",
                "Fallback kind",
                "Fallback counts",
                "Gap",
            ],
            [
                [
                    f"`{summary.get('root', '')}`",
                    _yes_no(summary.get("exists")),
                    _sample_label(summary),
                    _counts_label(summary.get("version_counts", {})),
                    str(summary.get("unsupported_count", 0)),
                    _yes_no(summary.get("converted_dxf_fallback_ready")),
                    str(summary.get("fallback_kind") or "-"),
                    _counts_label(summary.get("fallback_counts", {})),
                    _root_gap_label(summary),
                ]
                for summary in roots
            ],
        )
    )
    lines.extend(["", "## Version Distribution", ""])
    lines.extend(
        _markdown_table(
            ["Version", "Count", "Phase 0 target"],
            [
                [code, str(count), _yes_no(code in TARGET_DWG_CODES)]
                for code, count in sorted(dict(payload.get("version_counts", {})).items())
            ]
            or [["-", "0", "-"]],
        )
    )
    lines.extend(["", "## Converted-DXF Fallback Readiness", ""])
    lines.extend(
        _markdown_table(
            ["Root", "Ready", "Reason", "Effective before", "Effective after", "Top candidate"],
            [
                _fallback_row(summary)
                for summary in roots
                if isinstance(summary.get("folder_fallback"), dict)
            ]
            or [["-", "No", "-", "-", "-", "-"]],
        )
    )
    lines.extend(["", "## Unsupported DWG Sample Summary", ""])
    unsupported_rows = [
        [
            f"`{item.get('path', '')}`",
            str(item.get("code") or item.get("error_code") or "unknown"),
            str(item.get("release") or "-"),
            str(item.get("error") or "-"),
        ]
        for item in payload.get("dwg_files", [])
        if not item.get("supported")
    ][:MARKDOWN_ROW_LIMIT]
    lines.extend(
        _markdown_table(
            ["Path", "Version/code", "Release", "Error"],
            unsupported_rows or [["-", "-", "-", "-"]],
        )
    )
    if len([item for item in payload.get("dwg_files", []) if not item.get("supported")]) > MARKDOWN_ROW_LIMIT:
        lines.append("")
        lines.append(f"> Unsupported sample list truncated to {MARKDOWN_ROW_LIMIT} rows.")

    lines.extend(["", "## Corpus Gaps", ""])
    lines.extend(
        _markdown_table(
            ["Scope", "Root", "Gap", "Detail"],
            [
                [
                    str(gap.get("scope", "")),
                    f"`{gap.get('root', '-')}`" if gap.get("root") else "-",
                    str(gap.get("gap", "")),
                    _gap_detail(gap),
                ]
                for gap in payload.get("corpus_gaps", [])
            ]
            or [["-", "-", "-", "No gaps detected in sampled corpus."]],
        )
    )
    lines.extend(
        [
            "",
            "## Next Phase 0-B Priorities",
            "",
            "1. Collect version-stratified DWG plus converted-DXF pairs for AC1018, AC1021, AC1024, AC1027, and AC1032.",
            "2. Capture converted-DXF baseline compare summaries for each sample pair before evaluating any native reader candidate.",
            "3. Define recall, false-positive delta, entity coverage, runtime, and memory thresholds against the converted-DXF baseline.",
            "4. Keep native DWG support claims blocked until version-specific corpus and clean-room parser gates are satisfied.",
            "",
        ]
    )
    return "\n".join(lines)


def _markdown_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    output = [
        "| " + " | ".join(_md_escape(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        output.append("| " + " | ".join(_md_escape(str(cell)) for cell in row) + " |")
    return output


def _md_escape(value: str) -> str:
    return value.replace("\n", " ").replace("|", "\\|")


def _yes_no(value: Any) -> str:
    return "Yes" if bool(value) else "No"


def _counts_label(counts: Any) -> str:
    if not isinstance(counts, dict) or not counts:
        return "-"
    return ", ".join(f"{key}={counts[key]}" for key in sorted(counts))


def _sample_label(summary: dict[str, Any]) -> str:
    value = str(summary.get("dwg_count", 0))
    if summary.get("sample_limit_reached"):
        return f"{value}+"
    return value


def _root_gap_label(summary: dict[str, Any]) -> str:
    labels: list[str] = []
    if not summary.get("exists"):
        labels.append("missing root")
    elif int(summary.get("dwg_count", 0)) == 0:
        labels.append("no DWG samples")
    if summary.get("sample_limit_reached"):
        labels.append("sample limit reached")
    if summary.get("missing_converted_dxf_baseline"):
        labels.append("missing converted-DXF baseline")
    return ", ".join(labels) if labels else "-"


def _fallback_row(summary: dict[str, Any]) -> list[str]:
    fallback = summary.get("folder_fallback")
    if not isinstance(fallback, dict):
        return [f"`{summary.get('root', '')}`", "No", "-", "-", "-", "-"]
    candidates = fallback.get("fallback_candidates")
    top_candidate = "-"
    if isinstance(candidates, list) and candidates:
        candidate = candidates[0]
        if isinstance(candidate, dict):
            top_candidate = f"{candidate.get('kind', '-')} score={candidate.get('score', '-')}"
    return [
        f"`{summary.get('root', '')}`",
        _yes_no(fallback.get("fallback_used")),
        str(fallback.get("reason") or "-"),
        f"`{fallback.get('effective_source_a', '-')}`",
        f"`{fallback.get('effective_source_b', '-')}`",
        top_candidate,
    ]


def _gap_detail(gap: dict[str, Any]) -> str:
    if gap.get("versions"):
        return "versions=" + ",".join(str(item) for item in gap["versions"])
    if gap.get("unsupported_count") is not None:
        return f"unsupported_count={gap.get('unsupported_count')}; versions={_counts_label(gap.get('version_counts', {}))}"
    if gap.get("sample_limit") is not None:
        return f"sample_limit={gap.get('sample_limit')}"
    return "-"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path, help="DWG corpus file/folder roots")
    parser.add_argument("--out", type=Path, help="Optional JSON output path")
    parser.add_argument("--report-md", type=Path, help="Optional Markdown report output path")
    parser.add_argument("--max-dwg-samples", type=int, default=200)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    payload = inventory_dwg_native_phase0(
        args.roots,
        out=args.out,
        report_md=args.report_md,
        max_dwg_samples=args.max_dwg_samples,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
