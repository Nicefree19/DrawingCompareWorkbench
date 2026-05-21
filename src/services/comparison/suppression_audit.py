"""Phase Q2 (RV-20260509-002) — Suppression audit SSoT.

사용자 보고: "변경사항 미탐지가 많다, 왜 이런지 알 수 없다." Phase O/P
가 도입한 다양한 silent-drop 카운터들이 모두 ``ComparisonResult``
metadata 에만 기록되고 GUI 에 노출되지 않아, 사용자가 "기대한 변경이
왜 안 보이는지" 진단할 수 없었음.

이 모듈은 그 모든 카운터를 한 곳에 모아 종합 audit report 를 생성한다:
- 추출 단계: ``last_stats["unsupported_counts"]`` (Phase Q1) — 어느
  entity 종류가 silent drop 됐는지.
- 비교 단계: ``modified_ignored`` (Phase O P1 — significance 미달),
  ``alignment_suppressed`` (Phase O2 — global shift artifact),
  ``cosmetic_suppressed`` (Phase O3 toggle).
- Zone build 단계: ``change_zone_noise_suppressed_count`` (Phase O4 —
  single entity + noise score), ``change_zone_skipped_record_count``
  (P4 — bbox 추출 실패).
- 결과 truncation: ``truncated_changes`` (P6 — 50K cap).

CLI 사용:
    python -m src.services.comparison.suppression_audit <result_path>

Workbench dialog: ``_show_suppression_audit_v2`` 가 이 helper 를 호출.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional


@dataclass(frozen=True)
class SuppressionEntry:
    """단일 suppression 카테고리 — 사용자에게 보일 한 행."""

    category: str           # "extraction" | "comparison" | "zone" | "result"
    label_ko: str           # 한국어 라벨 ("미지원 entity 종류" 등)
    count: int              # 영향받은 변경 수 (entity 수, change 수, 등)
    detail_ko: str          # 한 줄 설명 ("HATCH 234, SOLID 12")
    fix_hint_ko: str        # 사용자 액션 제안 ("[설정] 노이즈 필터 dialog 에서 ... 토글")

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class SuppressionAuditReport:
    """전체 suppression audit 결과."""

    pair_id: str = ""
    total_visible_changes: int = 0   # result.changes 의 길이
    total_suppressed: int = 0         # 모든 silent-drop 합산
    entries: List[SuppressionEntry] = field(default_factory=list)
    raw_stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "total_visible_changes": self.total_visible_changes,
            "total_suppressed": self.total_suppressed,
            "entries": [e.to_dict() for e in self.entries],
            "raw_stats": dict(self.raw_stats),
        }

    def has_suppression(self) -> bool:
        return any(e.count > 0 for e in self.entries)

    def format_text(self) -> str:
        """Human-readable 한국어 보고서 (CLI / 다이얼로그 공통)."""
        lines = [
            f"== Suppression Audit ({self.pair_id or 'pair'}) ==",
            f"표시 중인 변경: {self.total_visible_changes}건",
            f"필터로 가려진 변경: {self.total_suppressed}건",
            "",
        ]
        if not self.entries:
            lines.append("(가려진 변경 없음 — 모든 detect 결과가 결과 패널에 노출됨)")
            return "\n".join(lines)

        # 카테고리 그룹핑
        by_cat: Dict[str, List[SuppressionEntry]] = {}
        for e in self.entries:
            by_cat.setdefault(e.category, []).append(e)
        cat_label = {
            "extraction": "[1] 추출 단계 (entity 종류 자체가 빠짐)",
            "comparison": "[2] 비교 단계 (detect 후 임계 / alignment / cosmetic 으로 폐기)",
            "zone": "[3] Zone build (group 단계에서 폐기)",
            "result": "[4] 결과 단계 (truncation / cap)",
        }
        for cat in ("extraction", "comparison", "zone", "result"):
            entries = by_cat.get(cat, [])
            if not entries:
                continue
            lines.append(cat_label.get(cat, cat))
            for e in entries:
                lines.append(f"  - {e.label_ko}: {e.count}건")
                if e.detail_ko:
                    lines.append(f"      → {e.detail_ko}")
                if e.fix_hint_ko:
                    lines.append(f"      해결: {e.fix_hint_ko}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def _safe_int(v: Any) -> int:
    try:
        n = int(v)
        return max(0, n)
    except (TypeError, ValueError):
        return 0


def build_suppression_audit(
    *,
    pair_id: str = "",
    extraction_stats_a: Optional[Mapping[str, Any]] = None,
    extraction_stats_b: Optional[Mapping[str, Any]] = None,
    comparison_stats: Optional[Mapping[str, Any]] = None,
    comparison_metadata: Optional[Mapping[str, Any]] = None,
    visible_change_count: int = 0,
) -> SuppressionAuditReport:
    """모든 단계 stats/metadata 를 종합 → 사용자용 audit report.

    Args:
        pair_id: 진단 대상 pair 의 식별자
        extraction_stats_a/b: ``DxfEntityExtractor.last_stats`` 두 도면
        comparison_stats: ``DxfComparisonResult.stats``
        comparison_metadata: ``DxfComparisonResult.metadata``
        visible_change_count: 사용자에게 노출되는 변경 수
            (보통 ``len(result.changes)``)
    """
    report = SuppressionAuditReport(
        pair_id=pair_id,
        total_visible_changes=int(visible_change_count or 0),
        raw_stats={
            "extraction_a": dict(extraction_stats_a or {}),
            "extraction_b": dict(extraction_stats_b or {}),
            "comparison_stats": dict(comparison_stats or {}),
            "comparison_metadata": dict(comparison_metadata or {}),
        },
    )

    # === 추출 단계 ===
    # Phase Q1 (RV-20260509-002) — 미지원 entity 종류 silent drop
    for side, stats in (("A", extraction_stats_a or {}),
                        ("B", extraction_stats_b or {})):
        unsupported = dict(stats.get("unsupported_counts", {}) or {})
        total_unsupp = _safe_int(stats.get("unsupported_total", 0)) or sum(
            _safe_int(v) for v in unsupported.values()
        )
        if total_unsupp > 0:
            top_types = sorted(
                unsupported.items(), key=lambda kv: -_safe_int(kv[1])
            )[:5]
            detail = ", ".join(f"{k}: {v}" for k, v in top_types)
            report.entries.append(SuppressionEntry(
                category="extraction",
                label_ko=f"{side} 도면의 미지원 entity 종류",
                count=total_unsupp,
                detail_ko=f"상위 종류 — {detail}",
                fix_hint_ko=(
                    "Phase Q1 까지 HATCH/SOLID/MULTILEADER/SPLINE/ELLIPSE/"
                    "LEADER 추가됨. 그 외 종류 (예: 3DFACE, REGION) 는 "
                    "별도 phase 에서 normalizer 신설 필요."
                ),
            ))
        if stats.get("limit_exceeded"):
            report.entries.append(SuppressionEntry(
                category="extraction",
                label_ko=f"{side} 도면 entity 추출 한계 초과",
                count=_safe_int(stats.get("max_entities", 0)),
                detail_ko=(
                    f"max_entities={stats.get('max_entities')} 초과 — "
                    f"이후 entity 모두 silent drop"
                ),
                fix_hint_ko="DxfEntityExtractor(max_entities=...) 인자 상향",
            ))
        # Phase Q3 (RV-20260509-002) — INSERT 의 block 정의 geometry
        # (LINE/CIRCLE/POLYLINE 등) 가 expand_blocks=False 로 미추출.
        # block_text fingerprint (Phase O Commit 2) 는 적용됐으나
        # geometry 변경은 silent drop.
        block_geom_skipped = _safe_int(
            stats.get("block_geometry_skipped_count", 0)
        )
        if block_geom_skipped > 0:
            report.entries.append(SuppressionEntry(
                category="extraction",
                label_ko=f"{side} 도면 INSERT block geometry 미추출",
                count=block_geom_skipped,
                detail_ko=(
                    f"{block_geom_skipped} 개 INSERT 가 expand_blocks=False "
                    "로 처리 — block 정의 내부 LINE/CIRCLE/POLYLINE 등의 "
                    "geometry 변경은 검출 안 됨 (block_text 는 fingerprint "
                    "에 포함되어 있어 검출됨)"
                ),
                fix_hint_ko=(
                    "Phase Q3 default = True. 비활성된 경우 "
                    "ComparisonConfig(expand_blocks=True) 또는 "
                    "GUI 옵션의 'INSERT 블록 확장' 체크."
                ),
            ))
        # Phase Q5 (RV-20260509-002) — paperspace entity silent drop.
        # extract_all_layouts=False 일 때만 비-zero.
        paperspace_skipped = _safe_int(
            stats.get("paperspace_entities_skipped_count", 0)
        )
        if paperspace_skipped > 0:
            report.entries.append(SuppressionEntry(
                category="extraction",
                label_ko=f"{side} 도면 paperspace entity 미추출",
                count=paperspace_skipped,
                detail_ko=(
                    f"{paperspace_skipped} 개 paperspace entity 가 "
                    "extract_all_layouts=False 로 처리 — paperspace 도면이 "
                    "비교에서 제외됨 (도면 변경이 invisible)"
                ),
                fix_hint_ko=(
                    "Phase Q5 default = True. 비활성된 경우 "
                    "DxfEntityExtractor.extract(extract_all_layouts=True) "
                    "또는 caller 의 옵션 갱신 필요."
                ),
            ))

    # === 비교 단계 ===
    cs = dict(comparison_stats or {})
    cm = dict(comparison_metadata or {})

    modified_ignored = _safe_int(cs.get("modified_ignored", 0))
    if modified_ignored > 0:
        report.entries.append(SuppressionEntry(
            category="comparison",
            label_ko="유의미 임계 미달 (sub-mm 위치 변경)",
            count=modified_ignored,
            detail_ko=(
                "near-match 페어가 잡혔으나 _is_significant_change 가 "
                "False 반환 (default position 임계 1.0mm)"
            ),
            fix_hint_ko=(
                "SensitivityConfig.position_threshold 를 0.1mm 로 낮추면 "
                "모든 변경 surface (단 false-positive 증가 가능)."
            ),
        ))

    alignment_suppressed = _safe_int(
        cs.get("alignment_suppressed",
               cm.get("alignment_suppressed_count", 0))
    )
    if alignment_suppressed > 0:
        report.entries.append(SuppressionEntry(
            category="comparison",
            label_ko="글로벌 alignment artifact",
            count=alignment_suppressed,
            detail_ko=(
                "도면 전체가 일정 방향으로 시프트 → "
                "동일 방향 변위는 registration noise 로 분류"
            ),
            fix_hint_ko=(
                "도면 일부만 시프트한 경우 inlier_ratio < 0.85 가드가 "
                "이미 보존. 그래도 사라진다면 SensitivityConfig."
                "alignment_strict_inlier_ratio 를 0.95 등으로 상향."
            ),
        ))

    cosmetic_suppressed = _safe_int(
        cs.get("cosmetic_suppressed",
               cm.get("cosmetic_suppressed_count", 0))
    )
    if cosmetic_suppressed > 0:
        report.entries.append(SuppressionEntry(
            category="comparison",
            label_ko="Cosmetic-only 변경 (color / lineweight / linetype)",
            count=cosmetic_suppressed,
            detail_ko=(
                "좌표 동일 + cosmetic 속성만 다른 페어 — "
                "suppress_cosmetic_only=True 로 폐기"
            ),
            fix_hint_ko=(
                "[설정] → 🧹 노이즈 필터... → 'cosmetic-only 변경 무시' "
                "체크 해제"
            ),
        ))

    # === Zone build 단계 ===
    zone_noise = _safe_int(cm.get("change_zone_noise_suppressed_count", 0))
    if zone_noise > 0:
        report.entries.append(SuppressionEntry(
            category="zone",
            label_ko="단일 entity + noise score 임계 초과",
            count=zone_noise,
            detail_ko=(
                "변경 1개짜리 zone 의 noise_score 가 0.7 이상 → "
                "(non-structural layer + cosmetic 등 누적)"
            ),
            fix_hint_ko=(
                "[설정] → 🧹 노이즈 필터... → 'min_changes_per_zone' 1 로 "
                "낮춤 (또는 'single_entity_noise_score_threshold' 1.0 으로)"
            ),
        ))

    zone_skipped = _safe_int(cm.get("change_zone_skipped_record_count", 0))
    if zone_skipped > 0:
        report.entries.append(SuppressionEntry(
            category="zone",
            label_ko="좌표 정보 없음 — zone bbox 산출 실패",
            count=zone_skipped,
            detail_ko=(
                "변경 record 에 location/bbox 가 없어 zone group 에 "
                "포함 안 됨 (TEXT 일부, paperspace entity)"
            ),
            fix_hint_ko=(
                "Phase Q4 (OCS→WCS) / Q5 (paperspace) 보강 후 자동 회복 "
                "예정. 임시: 결과 JSON (result.changes) 에서 직접 확인."
            ),
        ))

    # === 결과 단계 ===
    truncated = bool(
        cm.get("truncated_changes") or cs.get("truncated_changes")
    )
    if truncated:
        # Phase Q2 Codex follow-up (RV-20260509-002): use the actual
        # omitted record count rather than the cap. With the cap as the
        # count, a drawing with 50,001 changes was reported as 50,000
        # suppressed instead of 1.
        omitted_map = (
            cm.get("omitted_change_counts")
            or cs.get("omitted_change_counts")
            or {}
        )
        if isinstance(omitted_map, Mapping):
            hidden = sum(
                _safe_int(omitted_map.get(k, 0))
                for k in ("added", "deleted", "modified")
            )
        else:
            hidden = 0
        if hidden == 0:
            # Fallback when omitted_change_counts is missing — use the cap
            # as a worst-case estimate so the user still sees a non-zero
            # warning rather than a silent 0.
            cap = _safe_int(cm.get("max_change_records_in_memory",
                                    cs.get("max_change_records_in_memory", 0)))
            hidden = cap or 1  # 1 = "unknown but >0" (truncated_changes is True)
        report.entries.append(SuppressionEntry(
            category="result",
            label_ko="결과 cap 초과 — 후순위 변경 truncation",
            count=hidden,
            detail_ko=(
                "대형 도면 메모리 보호 — change list 가 cap 에서 잘림. "
                f"숨겨진 변경 수: {hidden}건"
            ),
            fix_hint_ko=(
                "ComparisonConfig.max_change_records_in_memory 상향 "
                "(메모리 사용량 비례 증가)"
            ),
        ))

    report.total_suppressed = sum(e.count for e in report.entries)
    return report


# ---------------------------------------------------------------------------
# Convenience — DxfComparisonResult 만 받아 helper 생성
# ---------------------------------------------------------------------------


def audit_from_comparison_result(
    result: Any,
    *,
    pair_id: str = "",
    extraction_stats_a: Optional[Mapping[str, Any]] = None,
    extraction_stats_b: Optional[Mapping[str, Any]] = None,
) -> SuppressionAuditReport:
    """``ComparisonResult`` / ``DxfComparisonResult`` 인스턴스 또는 dict 를
    입력으로 받음.

    Phase Q2 Codex follow-up (RV-20260509-002): caller 가 ``extraction_stats_a/b``
    를 명시하지 않으면 ``metadata['extraction_stats']['a'|'b']`` 에서 자동으로
    회수한다. 또한 ``ComparisonResult`` (DwgDiffer 결과) 는 ``.stats`` 가 없으므로
    ``metadata['comparison_suppression']`` (dwg_differ 가 surface 함) 을 보조
    소스로 활용한다.
    """

    stats: Dict[str, Any] = dict(
        getattr(result, "stats", None)
        or (result.get("stats") if isinstance(result, Mapping) else {})
        or {}
    )
    metadata: Dict[str, Any] = dict(
        getattr(result, "metadata", None)
        or (result.get("metadata") if isinstance(result, Mapping) else {})
        or {}
    )
    changes = getattr(result, "changes", None)
    if changes is None and isinstance(result, Mapping):
        changes = result.get("changes", [])
    visible = len(changes) if changes is not None else 0

    # `ComparisonResult` 에는 .stats 가 없고, dwg_differ 가
    # metadata["comparison_suppression"] 으로 surface 한 카운터를 활용해야 함.
    suppress_meta = metadata.get("comparison_suppression") or {}
    if isinstance(suppress_meta, Mapping):
        for key in ("modified_ignored", "alignment_suppressed",
                    "cosmetic_suppressed"):
            if key not in stats and key in suppress_meta:
                stats[key] = suppress_meta[key]

    # Default extraction_stats_a/b → metadata["extraction_stats"]["a"|"b"]
    if extraction_stats_a is None or extraction_stats_b is None:
        ext_meta = metadata.get("extraction_stats") or {}
        if isinstance(ext_meta, Mapping):
            if extraction_stats_a is None:
                ea = ext_meta.get("a")
                if isinstance(ea, Mapping):
                    extraction_stats_a = ea
            if extraction_stats_b is None:
                eb = ext_meta.get("b")
                if isinstance(eb, Mapping):
                    extraction_stats_b = eb

    return build_suppression_audit(
        pair_id=pair_id,
        extraction_stats_a=extraction_stats_a,
        extraction_stats_b=extraction_stats_b,
        comparison_stats=stats,
        comparison_metadata=metadata,
        visible_change_count=visible,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli(argv: Optional[List[str]] = None) -> int:
    """python -m src.services.comparison.suppression_audit <result.json>

    JSON 형식: {"changes": [...], "stats": {...}, "metadata": {...},
              "extraction_stats_a": {...}, "extraction_stats_b": {...},
              "pair_id": "..."}
    """
    args = list(argv or sys.argv[1:])
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        print("Usage: python -m src.services.comparison.suppression_audit <result.json>")
        return 0

    path = Path(args[0])
    if not path.exists():
        print(f"[error] file not found: {path}", file=sys.stderr)
        return 2

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[error] JSON parse failed: {e}", file=sys.stderr)
        return 2

    report = build_suppression_audit(
        pair_id=str(data.get("pair_id", "")),
        extraction_stats_a=data.get("extraction_stats_a"),
        extraction_stats_b=data.get("extraction_stats_b"),
        comparison_stats=data.get("stats"),
        comparison_metadata=data.get("metadata"),
        visible_change_count=len(data.get("changes", [])),
    )
    print(report.format_text())
    return 0


if __name__ == "__main__":
    sys.exit(_cli())


__all__ = [
    "SuppressionEntry",
    "SuppressionAuditReport",
    "build_suppression_audit",
    "audit_from_comparison_result",
]
