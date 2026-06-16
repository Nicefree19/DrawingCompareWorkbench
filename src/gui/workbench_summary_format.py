"""Pure summary / label / count formatting helpers for the workbench.

Second satellite extraction of the ``drawing_compare_workbench`` god-object
(tech-debt audit MONO-4; follows ``workbench_overlay_model``). Every function
here is pure — dict/str/int in, str out, no Qt and no widget state — so it
lives and is unit-tested outside the GUI monolith. ``drawing_compare_workbench``
re-imports each name so the public import surface
(``from src.gui.drawing_compare_workbench import natural_change_summary`` etc.)
and all in-file call sites keep working unchanged.
"""

from __future__ import annotations


def natural_change_summary(
    data: dict,
    *,
    added: int,
    deleted: int,
    modified: int,
    moved: int,
    top_layers: str = "",
) -> str:
    """Compose a single-line natural-language summary of a change zone.

    Combines the dominant change kind with the leading layer when present so the
    reviewer can grasp the gist of the zone without parsing every count line.
    Examples:
    - ``"GRID 레이어에 추가 5건, 수정 2건"``
    - ``"치수 변경 - 수정 7건"``
    - ``"변경 없음"``

    Phase O Commit 4 [RV-20260508-010] — entity_types 에 ATTRIB/ATTDEF
    가 포함되면 사용자가 "블록 텍스트 변경" 사례임을 즉시 인지할 수
    있도록 suffix 를 추가. 사용자 사례 (DOWEL BAR ... @100 → @200) 가
    바로 이 경로로 surface.
    """

    if not isinstance(data, dict):
        data = {}
    layer = top_layers.split(" | ")[0].split(",")[0].strip() if top_layers else ""
    parts: list[str] = []
    if added:
        parts.append(f"추가 {added}건")
    if deleted:
        parts.append(f"삭제 {deleted}건")
    if modified:
        parts.append(f"수정 {modified}건")
    if moved:
        parts.append(f"이동 {moved}건")

    # Phase O Commit 4 — ATTRIB/ATTDEF 감지 후 suffix
    entity_types_raw = data.get("entity_types") or data.get("top_entity_types") or ""
    entity_types_str = ""
    if isinstance(entity_types_raw, str):
        entity_types_str = entity_types_raw.upper()
    elif isinstance(entity_types_raw, (list, tuple, set)):
        entity_types_str = " | ".join(str(t).upper() for t in entity_types_raw)
    has_block_text = (
        "ATTRIB" in entity_types_str or "ATTDEF" in entity_types_str
    )

    if not parts:
        if has_block_text:
            return "변경 없음 (블록 텍스트 영역만 포함)"
        return "변경 없음"
    body = ", ".join(parts)
    if layer:
        body = f"{layer} 레이어에 {body}"
    if has_block_text:
        body = f"{body} · 블록 텍스트 변경 포함"
    return body


def format_top_issue_label(issue: dict) -> str:
    """Render a single review-dashboard top-issue entry as a list label.

    Returns a two-line string:
    - line 1: drawing number · zone id · severity (한국어)
    - line 2: priority rank/score and raw change count for quick triage
    """

    if not isinstance(issue, dict):
        return "(이슈 정보 없음)"
    drawing = str(issue.get("drawing_label") or issue.get("drawing_number") or issue.get("display_label") or issue.get("pair_id") or "-")
    zone = str(issue.get("zone_id") or "-")
    severity_ko = str(issue.get("severity_ko") or issue.get("severity") or issue.get("category") or "-")
    rank = issue.get("priority_rank") or issue.get("priority_rank_in_drawing")
    summary = str(issue.get("change_summary_ko") or "")
    category = str(issue.get("category") or "")
    try:
        score = float(issue.get("priority_score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    raw = issue.get("raw_change_count")
    try:
        raw_int = int(float(raw)) if raw is not None else 0
    except (TypeError, ValueError):
        raw_int = 0
    rank_text = f"#{int(rank)}" if isinstance(rank, (int, float)) else "#?"
    headline = summary or f"{drawing} · {zone} · {severity_ko}"
    return f"{headline}\n{drawing} · {zone} · {rank_text} · {category or severity_ko} · 점수 {score:.1f} · raw {raw_int}"


def format_pattern_group_label(pattern: dict) -> str:
    """Render a layer-pattern group entry for the repeated-pattern tab."""

    if not isinstance(pattern, dict):
        return "(패턴 정보 없음)"
    label = str(pattern.get("pattern") or "(이름 없음)")
    affected = pattern.get("affected_drawing_count")
    try:
        affected_int = int(float(affected)) if affected is not None else 0
    except (TypeError, ValueError):
        affected_int = 0
    raw = pattern.get("raw_change_count")
    try:
        raw_int = int(float(raw)) if raw is not None else 0
    except (TypeError, ValueError):
        raw_int = 0
    zones = pattern.get("zone_count")
    try:
        zones_int = int(float(zones)) if zones is not None else 0
    except (TypeError, ValueError):
        zones_int = 0
    layers = str(pattern.get("top_layers") or "-")
    return f"{label} · 도면 {affected_int} · 변경구역 {zones_int} · 변경 {raw_int}\n주요 layer: {layers}"


def _int_value(value, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except Exception:
        return default


def _format_count(value) -> str:
    return f"{_int_value(value):,}"


def _change_grade(raw_count: int, failed: bool = False) -> str:
    if failed:
        return "실패"
    if raw_count >= 3000:
        return "큼"
    if raw_count >= 500:
        return "보통"
    if raw_count > 0:
        return "작음"
    return "변경 없음"


def _ko_change_type(value: str) -> str:
    return {
        "added": "+ 추가",
        "deleted": "- 삭제",
        "modified": "~ 수정",
        "moved": "이동",
        "mixed": "혼합",
    }.get(str(value or "").lower(), str(value or ""))
