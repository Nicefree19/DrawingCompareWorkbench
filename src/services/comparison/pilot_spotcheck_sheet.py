"""Operator spotcheck sheet + ground-truth skeleton — shared producer.

Renders ``pilot_spotcheck.md`` (a table of detected changes with blank operator
columns) and ``review_ground_truth.csv`` (a detection-derived skeleton using the
EXISTING ground-truth schema) from a finished run's
``artifacts/review_dashboard.json`` ``top_issues``.

This lives in ``src`` so EVERY caller shares one producer with no
re-implementation: the CLI runner (``scripts/run_pilot_spotcheck.py``) and the
GUI compare path (via ``FolderComparePipeline``) both emit the same sheet, so a
structural reviewer gets the dry-run artifact from a double-click — no dev
Python checkout. Only detection facts are written; the operator confirms/fills
the rest. No ground truth is fabricated.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

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


def load_top_issues(output_dir: Path) -> list[dict[str, Any]]:
    """Read the run's review_dashboard.json and return its top_issues list."""
    dashboard = Path(output_dir) / "artifacts" / "review_dashboard.json"
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

    Single pair → one flat table. Folder batch (>1 pair) → one detected-changes
    table per pair, grouped by display_label.
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
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(GROUND_TRUTH_HEADER)
        writer.writerows(rows)


def emit_spotcheck_artifacts(output_dir: Path, pair_name: str | None = None) -> dict[str, Any]:
    """Write pilot_spotcheck.md + review_ground_truth.csv into ``output_dir``.

    Reads the already-written ``artifacts/review_dashboard.json`` top_issues and
    renders the operator sheet + ground-truth skeleton. Shared by the CLI runner
    and the GUI compare-completion path.
    """
    output_dir = Path(output_dir)
    issues = load_top_issues(output_dir)
    if not pair_name:
        pair_name = output_dir.name or "비교"

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


def emit_spotcheck_artifacts_safely(output_dir: Any) -> None:
    """Fail-safe emit for non-CLI callers (e.g. the pipeline): never raises, so a
    sheet-emission problem can't break a compare run."""
    import logging

    try:
        if not output_dir:
            return
        emit_spotcheck_artifacts(Path(output_dir))
    except Exception:  # noqa: BLE001 — never break the caller's run
        logging.getLogger(__name__).warning("pilot spotcheck sheet emission failed", exc_info=True)
