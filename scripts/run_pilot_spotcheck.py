"""Lightweight pilot spotcheck runner — a thin wrapper over FolderComparePipeline.

Purpose: make a ~5-minute operator dry-run frictionless. Given two drawings
(before / after), run the REAL detection pipeline (``FolderComparePipeline`` —
no comparison logic is re-implemented here) and emit two operator-facing files:

  * ``pilot_spotcheck.md`` — a table of detected changes (location / type /
    Korean summary / add-delete-modify counts) plus blank operator columns
    (아는변경 / 검출 Y-N / 위치정확 Y-N / 비고) and a "누락" section for known
    changes that were not detected.
  * ``review_ground_truth.csv`` — a skeleton using the EXISTING ground-truth
    schema, with detection-derived rows. Only detection facts are filled in
    (clearly labelled via ``detection_source`` and a notes flag); the operator
    confirms or corrects each row. No ground truth is fabricated.

This script is an OUTPUT TRANSFORMER over the pipeline's
``artifacts/review_dashboard.json`` ``top_issues`` list. DXF and other supported
formats run directly; convert DWG to DXF first (see
``docs/INTERNAL_PILOT_SPOTCHECK.md``).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.comparison.dwg_backend import DWG_BACKEND_ODA_CONVERTER
from src.services.comparison.dwg_converter import converter_installation_status
from src.services.comparison.folder_compare_pipeline import (
    FolderComparePipeline,
    FolderCompareRunRequest,
)


class PilotSpotcheckError(RuntimeError):
    """Fail-loud error for unusable pilot input (e.g. DWG with no converter)."""


# Existing review_ground_truth schema — kept verbatim (see
# scripts/release_drawing_compare_workbench.py and the customer template).
GROUND_TRUTH_HEADER = [
    "drawing_label",
    "category",
    "summary_contains",
    "source_format",
    "detection_source",
    "bbox_status",
    "notes",
]

SKELETON_NOTE = "검출기반 스켈레톤 — 운영자가 아는 변경과 대조·확정"


def _load_top_issues(output_dir: Path) -> list[dict[str, Any]]:
    """Read the pipeline's review_dashboard.json and return its top_issues list."""
    dashboard = output_dir / "artifacts" / "review_dashboard.json"
    if not dashboard.exists():
        return []
    data = json.loads(dashboard.read_text(encoding="utf-8"))
    issues = data.get("top_issues")
    if isinstance(issues, list):
        return [row for row in issues if isinstance(row, dict)]
    return []


def _location(issue: dict[str, Any]) -> str:
    bbox_text = str(issue.get("bbox_text") or "").strip()
    layers = issue.get("major_layers")
    if not layers:
        raw_layers = issue.get("layers")
        if isinstance(raw_layers, list):
            layers = ", ".join(str(item) for item in raw_layers)
    layers = str(layers or "").strip()
    if bbox_text and layers:
        return f"{bbox_text} / {layers}"
    return bbox_text or layers or "(위치 정보 없음)"


def _change_type(issue: dict[str, Any]) -> str:
    ctype = str(issue.get("change_type_ko") or issue.get("change_type") or "").strip()
    severity = str(issue.get("severity_ko") or issue.get("severity") or "").strip()
    if ctype and severity:
        return f"{ctype}·{severity}"
    return ctype or severity or "-"


def _summary(issue: dict[str, Any]) -> str:
    return str(issue.get("change_summary_ko") or issue.get("reason_ko") or "").strip() or "-"


def _delta(issue: dict[str, Any]) -> str:
    added = issue.get("added_count", 0) or 0
    deleted = issue.get("deleted_count", 0) or 0
    modified = issue.get("modified_count", 0) or 0
    return f"+{added}/-{deleted}/~{modified}"


def _summary_contains(issue: dict[str, Any]) -> str:
    """Detection-derived match tokens (facts only): major layers + change type."""
    tokens: list[str] = []
    layers = str(issue.get("major_layers") or "").strip()
    if layers:
        tokens.append(layers)
    ctype = str(issue.get("change_type") or "").strip()
    if ctype:
        tokens.append(ctype)
    return ";".join(tokens)


def _pair_label(issue: dict[str, Any]) -> str:
    """Per-pair identity for grouping a folder-batch run (filename stem)."""
    return str(issue.get("display_label") or issue.get("drawing_number") or "").strip() or "(미상)"


def _group_by_pair(issues: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        groups.setdefault(_pair_label(issue), []).append(issue)
    return groups


_TABLE_HEADER = (
    "| # | 위치 (bbox / 레이어) | 타입·심각도 | 한국어 요약 | "
    "증감(+추가/-삭제/~수정) | 아는변경? | 검출됨?(Y/N) | 위치정확?(Y/N) | 비고 |"
)
_TABLE_SEP = "|---|---|---|---|---|---|---|---|---|"


def _detected_row(index: int, issue: dict[str, Any]) -> str:
    return (
        f"| {index} | {_location(issue)} | {_change_type(issue)} | "
        f"{_summary(issue)} | {_delta(issue)} | | | | |"
    )


def build_spotcheck_md(pair_name: str, issues: list[dict[str, Any]]) -> str:
    """Render the operator spotcheck sheet as Markdown.

    Single pair → one flat table (PR#56 shape preserved). Folder batch (>1
    pair) → one detected-changes table per pair, grouped by display_label.
    """
    groups = _group_by_pair(issues)
    multi = len(groups) > 1
    lines = [
        f"# 파일럿 스팟체크 — {pair_name}",
        "",
        f"- 총 검출 변경(top_issues): **{len(issues)}**",
    ]
    if multi:
        lines.append(f"- 비교 쌍: **{len(groups)}** (쌍별 섹션)")
    lines += [
        "- 검출 소스: `artifacts/review_dashboard.json` top_issues " "(실제 비교 산출 — 검출 수/정확도 미가공)",
        "- 판정 기준: 내가 아는 변경이 아래 검출 행에 **누락 0** → 배포 진행 후보 / " "**누락 1건 이상** → 배포 보류·원인 분석",
        "",
        "## 검출된 변경 (자동 나열)",
        "",
    ]
    if not issues:
        lines += [
            _TABLE_HEADER,
            _TABLE_SEP,
            "| — | (검출 0) | — | 비교가 변경을 찾지 못함(동일 도면이거나 " "변경 미검출) | — | | | | |",
        ]
    else:
        for label, rows in groups.items():
            if multi:
                lines += [f"### 쌍: {label} (검출 {len(rows)})", ""]
            lines += [_TABLE_HEADER, _TABLE_SEP]
            lines += [_detected_row(index, issue) for index, issue in enumerate(rows, start=1)]
            if multi:
                lines.append("")
    lines += [
        "",
        "## 누락 기록 (아는 변경인데 위 표에 없을 때만)",
        "",
        "| # | 아는 변경 (한 줄) | 예상 위치/레이어 | 비고(왜 못 잡았다고 보는지) |",
        "|---|---|---|---|",
        "| | | | |",
        "",
        "## 작성법 (쌍당 ~5분)",
        "1. 위 검출 표에서, 내가 아는 변경에 해당하는 행을 찾아 '아는변경?'·" "'검출됨?(Y)'·'위치정확?(Y/N)'을 표시한다.",
        "2. 아는 변경이 어느 검출 행에도 없으면 '누락 기록'에 한 줄 추가한다 " "(= 검출 누락, 제품의 진짜 미지).",
        "3. 모르는 검출이 있으면 1~2건만 진짜 변경인지 역확인한다.",
        "",
    ]
    return "\n".join(lines)


def build_ground_truth_rows(issues: list[dict[str, Any]]) -> list[list[str]]:
    """Detection-derived skeleton rows for the existing ground-truth schema."""
    rows: list[list[str]] = []
    for issue in issues:
        drawing_label = str(issue.get("display_label") or issue.get("drawing_number") or "").strip()
        rows.append(
            [
                drawing_label,
                str(issue.get("category") or issue.get("change_type") or "").strip(),
                _summary_contains(issue),
                str(issue.get("source_format") or "").strip(),
                str(issue.get("detection_source") or "").strip(),
                str(issue.get("bbox_status") or "").strip(),
                SKELETON_NOTE,
            ]
        )
    return rows


def write_ground_truth_csv(path: Path, rows: list[list[str]]) -> None:
    # utf-8-sig so Excel on Windows reads Korean correctly (matches the template).
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(GROUND_TRUTH_HEADER)
        writer.writerows(rows)


def _iter_input_files(path: Path):
    if path.is_dir():
        yield from (child for child in path.iterdir() if child.is_file())
    elif path.is_file():
        yield path


def _inputs_include_dwg(before: Path, after: Path) -> bool:
    return any(
        file.suffix.lower() == ".dwg"
        for root in (before, after)
        for file in _iter_input_files(root)
    )


def _resolve_dwg_backend_mode(before: Path, after: Path) -> str | None:
    """Wire DWG inputs through the existing approved conversion path.

    Returns the ODA-converter backend mode when a DWG input is present and a
    local converter is installed, ``None`` for DXF-only input. Raises
    ``PilotSpotcheckError`` (fail-loud) when a DWG is given but no converter is
    installed — never a silent empty result or single-file fallback.
    """
    if not _inputs_include_dwg(before, after):
        return None
    status = converter_installation_status()
    if not status.get("installed"):
        raise PilotSpotcheckError(
            "DWG 입력이 감지됐으나 로컬 DWG 변환기가 설치되어 있지 않습니다.\n"
            f"  상태: {status.get('message', '(불명)')}\n"
            "  → DWG를 먼저 DXF로 변환한 뒤 입력하거나, 변환기를 설치한 뒤 다시 실행하세요."
        )
    return DWG_BACKEND_ODA_CONVERTER


def run_pilot_spotcheck(before: Path, after: Path, output: Path) -> dict[str, Any]:
    """Run the real pipeline and emit the spotcheck sheet + ground-truth skeleton."""
    output.mkdir(parents=True, exist_ok=True)
    dwg_backend_mode = _resolve_dwg_backend_mode(before, after)
    request = FolderCompareRunRequest(before, after, output, dwg_backend_mode=dwg_backend_mode)
    result = FolderComparePipeline(request).run()
    output_dir = Path(result.output_dir)

    issues = _load_top_issues(output_dir)
    if before.is_dir() or after.is_dir():
        pair_name = f"{before.name}/ ↔ {after.name}/ (폴더 배치)"
    else:
        pair_name = f"{before.stem} → {after.stem}"

    spotcheck_path = output_dir / "pilot_spotcheck.md"
    spotcheck_path.write_text(build_spotcheck_md(pair_name, issues), encoding="utf-8")

    csv_path = output_dir / "review_ground_truth.csv"
    write_ground_truth_csv(csv_path, build_ground_truth_rows(issues))

    return {
        "output_dir": output_dir,
        "spotcheck_md": spotcheck_path,
        "ground_truth_csv": csv_path,
        "detected_count": len(issues),
    }


def _force_utf8_stdout() -> None:
    """Windows consoles default to cp949; print Korean output without crashing."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(
        description=(
            "실 도면으로 무마찰 파일럿 dry-run을 돕는 경량 러너. 실제 비교"
            "(FolderComparePipeline)를 돌려 검출 변경 표와 ground-truth 스켈레톤을 "
            "산출한다. 단일 쌍(파일) 또는 폴더(다중 쌍, 파일명으로 자동 매칭)를 받는다. "
            "DXF 우선 — DWG는 로컬 변환기 설치 시 자동 변환, 없으면 사전 변환 안내."
        )
    )
    parser.add_argument("before", type=Path, help="이전(개정 전) 도면 파일 또는 폴더")
    parser.add_argument("after", type=Path, help="이후(개정 후) 도면 파일 또는 폴더")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="산출물 디렉터리 (pilot_spotcheck.md / review_ground_truth.csv)",
    )
    args = parser.parse_args(argv)

    for label, path in (("before", args.before), ("after", args.after)):
        if not path.exists():
            parser.error(f"{label} 경로를 찾을 수 없음: {path}")

    try:
        summary = run_pilot_spotcheck(args.before, args.after, args.output)
    except PilotSpotcheckError as exc:
        print(f"[실패] {exc}", file=sys.stderr)
        return 2

    print(f"검출 변경: {summary['detected_count']}건")
    print(f"스팟체크 시트: {summary['spotcheck_md']}")
    print(f"정답 스켈레톤: {summary['ground_truth_csv']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
