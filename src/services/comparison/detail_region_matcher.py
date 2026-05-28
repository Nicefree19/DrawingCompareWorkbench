"""Match detected drawing/detail regions between before and after sources."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .region_match_overrides import RegionMatchOverride
from .sheet_region_detector import SheetRegion


@dataclass(frozen=True)
class RegionMatch:
    """One before/after detail-region matching decision."""

    match_id: str
    before_region_id: str = ""
    after_region_id: str = ""
    status: str = "review_required"
    score: float = 0.0
    component_scores: dict[str, float] = field(default_factory=dict)
    reasons: tuple[str, ...] = tuple()

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "before_region_id": self.before_region_id,
            "after_region_id": self.after_region_id,
            "status": self.status,
            "score": round(float(self.score), 4),
            "component_scores": {
                key: round(float(value), 4)
                for key, value in self.component_scores.items()
            },
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class RegionMatchSummary:
    """Aggregate matching output for one drawing pair."""

    pair_id: str
    before_count: int
    after_count: int
    matches: tuple[RegionMatch, ...] = tuple()
    status: str = "passed"
    warnings: tuple[str, ...] = tuple()

    @property
    def auto_matched_count(self) -> int:
        return sum(1 for match in self.matches if match.status == "auto_matched")

    @property
    def review_required_count(self) -> int:
        return sum(1 for match in self.matches if match.status == "review_required")

    @property
    def manual_matched_count(self) -> int:
        return sum(1 for match in self.matches if match.status == "manual_matched")

    @property
    def unmatched_before_count(self) -> int:
        return sum(1 for match in self.matches if match.status == "unmatched_before")

    @property
    def unmatched_after_count(self) -> int:
        return sum(1 for match in self.matches if match.status == "unmatched_after")

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "status": self.status,
            "before_count": self.before_count,
            "after_count": self.after_count,
            "auto_matched_count": self.auto_matched_count,
            "manual_matched_count": self.manual_matched_count,
            "review_required_count": self.review_required_count,
            "unmatched_before_count": self.unmatched_before_count,
            "unmatched_after_count": self.unmatched_after_count,
            "matches": [match.to_dict() for match in self.matches],
            "warnings": list(self.warnings),
        }


def match_sheet_regions(
    before_regions: Sequence[SheetRegion],
    after_regions: Sequence[SheetRegion],
    *,
    pair_id: str = "",
    auto_threshold: float = 0.82,
    review_threshold: float = 0.60,
    auto_margin: float = 0.12,
    overrides: Sequence[RegionMatchOverride] | None = None,
) -> RegionMatchSummary:
    """Greedily match logical before/after detail regions.

    Text identity gets the highest weight, but geometry and histograms keep
    the matcher useful for structural details with weak title text.
    """

    used_before: set[int] = set()
    used_after: set[int] = set()
    matches: list[RegionMatch] = []
    warnings: list[str] = []
    if overrides:
        override_matches, override_warnings = _apply_region_match_overrides(
            before_regions,
            after_regions,
            overrides,
            pair_id=pair_id,
            used_before=used_before,
            used_after=used_after,
        )
        matches.extend(override_matches)
        warnings.extend(override_warnings)

    scored: list[tuple[float, int, int, dict[str, float], tuple[str, ...]]] = []
    for i, before in enumerate(before_regions):
        if i in used_before:
            continue
        for j, after in enumerate(after_regions):
            if j in used_after:
                continue
            score, components, reasons = _score_region_pair(before, after)
            scored.append((score, i, j, components, reasons))
    scored.sort(key=lambda item: item[0], reverse=True)

    for score, i, j, components, reasons in scored:
        if i in used_before or j in used_after:
            continue
        if score < review_threshold:
            if not _should_emit_low_score_review_candidate(
                before_regions[i],
                after_regions[j],
                scored,
                used_before=used_before,
                used_after=used_after,
            ):
                continue
        used_before.add(i)
        used_after.add(j)
        components = dict(components)
        margin = _ambiguity_margin(scored, score, i, j)
        components["ambiguity_margin"] = margin
        status = "review_required"
        reasons_list = list(reasons)
        if score < review_threshold:
            reasons_list.append(
                "unique region pair below review threshold; manual review required"
            )
        if score >= auto_threshold:
            if margin >= auto_margin and _allows_auto_match(components):
                status = "auto_matched"
            else:
                reasons_list.append("auto match blocked by ambiguity or weak identity evidence")
        matches.append(
            RegionMatch(
                match_id=f"{pair_id or 'pair'}-region-{len(matches) + 1}",
                before_region_id=before_regions[i].region_id,
                after_region_id=after_regions[j].region_id,
                status=status,
                score=score,
                component_scores=components,
                reasons=tuple(reasons_list),
            )
        )

    for i, region in enumerate(before_regions):
        if i not in used_before:
            matches.append(
                RegionMatch(
                    match_id=f"{pair_id or 'pair'}-before-unmatched-{i + 1}",
                    before_region_id=region.region_id,
                    status="unmatched_before",
                    reasons=("before detail has no matching after region",),
                )
            )
    for j, region in enumerate(after_regions):
        if j not in used_after:
            matches.append(
                RegionMatch(
                    match_id=f"{pair_id or 'pair'}-after-unmatched-{j + 1}",
                    after_region_id=region.region_id,
                    status="unmatched_after",
                    reasons=("after detail has no matching before region",),
                )
            )

    if before_regions and after_regions and not any(
        match.status in {"auto_matched", "manual_matched", "review_required"} for match in matches
    ):
        warnings.append("no detail regions reached review threshold")
    return RegionMatchSummary(
        pair_id=pair_id,
        before_count=len(before_regions),
        after_count=len(after_regions),
        matches=tuple(matches),
        status="passed",
        warnings=tuple(warnings),
    )


def _apply_region_match_overrides(
    before_regions: Sequence[SheetRegion],
    after_regions: Sequence[SheetRegion],
    overrides: Sequence[RegionMatchOverride],
    *,
    pair_id: str,
    used_before: set[int],
    used_after: set[int],
) -> tuple[list[RegionMatch], list[str]]:
    before_index = {region.region_id: index for index, region in enumerate(before_regions)}
    after_index = {region.region_id: index for index, region in enumerate(after_regions)}
    matches: list[RegionMatch] = []
    warnings: list[str] = []
    for override in overrides:
        if override.pair_id and pair_id and override.pair_id != pair_id:
            continue
        status = override.normalized_status()
        before_id = override.before_region_id
        after_id = override.after_region_id
        reason = override.reason or "manual region match override"
        if status == "manual_match":
            if before_id not in before_index or after_id not in after_index:
                warnings.append(f"manual region match references unknown region: {before_id} -> {after_id}")
                continue
            before_i = before_index[before_id]
            after_i = after_index[after_id]
            if before_i in used_before or after_i in used_after:
                warnings.append(f"manual region match conflicts with an earlier override: {before_id} -> {after_id}")
                continue
            used_before.add(before_i)
            used_after.add(after_i)
            matches.append(
                RegionMatch(
                    match_id=f"{pair_id or 'pair'}-manual-{len(matches) + 1}",
                    before_region_id=before_id,
                    after_region_id=after_id,
                    status="manual_matched",
                    score=1.0,
                    component_scores={"manual_override": 1.0},
                    reasons=(reason,),
                )
            )
        elif status == "unmatched_before":
            if before_id not in before_index:
                warnings.append(f"manual unmatched-before references unknown region: {before_id}")
                continue
            before_i = before_index[before_id]
            if before_i in used_before:
                warnings.append(f"manual unmatched-before conflicts with an earlier override: {before_id}")
                continue
            used_before.add(before_i)
            matches.append(
                RegionMatch(
                    match_id=f"{pair_id or 'pair'}-manual-before-unmatched-{len(matches) + 1}",
                    before_region_id=before_id,
                    status="unmatched_before",
                    reasons=(reason,),
                )
            )
        elif status == "unmatched_after":
            if after_id not in after_index:
                warnings.append(f"manual unmatched-after references unknown region: {after_id}")
                continue
            after_i = after_index[after_id]
            if after_i in used_after:
                warnings.append(f"manual unmatched-after conflicts with an earlier override: {after_id}")
                continue
            used_after.add(after_i)
            matches.append(
                RegionMatch(
                    match_id=f"{pair_id or 'pair'}-manual-after-unmatched-{len(matches) + 1}",
                    after_region_id=after_id,
                    status="unmatched_after",
                    reasons=(reason,),
                )
            )
        else:
            warnings.append(f"unsupported manual region override status: {override.status}")
    return matches, warnings


def write_region_match_summary(
    summaries: Sequence[RegionMatchSummary],
    output_path: str | Path,
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "pair_count": len(summaries),
        "summaries": [summary.to_dict() for summary in summaries],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _score_region_pair(
    before: SheetRegion,
    after: SheetRegion,
) -> tuple[float, dict[str, float], tuple[str, ...]]:
    number_score = 0.0
    reasons: list[str] = []
    if before.drawing_number and after.drawing_number:
        number_score = 1.0 if before.drawing_number == after.drawing_number else 0.0
        if number_score:
            reasons.append(f"drawing number matched: {before.drawing_number}")
        else:
            reasons.append("drawing number differs")

    title_score = _token_similarity(before.title_text, after.title_text)
    if title_score >= 0.5:
        reasons.append("title/text tokens similar")

    geometry_score = _geometry_similarity(before, after)
    if geometry_score >= 0.7:
        reasons.append("region geometry similar")

    histogram_score = max(
        _histogram_cosine(before.entity_histogram, after.entity_histogram),
        _histogram_cosine(before.layer_histogram, after.layer_histogram) * 0.85,
    )
    if histogram_score >= 0.7:
        reasons.append("entity/layer mix similar")

    method_score = _method_compatibility(before, after)
    if method_score >= 0.7:
        reasons.append("region detection methods compatible")

    score = (
        number_score * 0.30
        + title_score * 0.20
        + geometry_score * 0.20
        + histogram_score * 0.25
        + method_score * 0.05
    )
    if before.drawing_number and after.drawing_number and before.drawing_number != after.drawing_number:
        score = min(score, 0.49)
    # If there is no text identity at all, let geometry+histogram reach review
    # threshold, but the auto gate still requires stronger identity evidence.
    if not before.drawing_number and not after.drawing_number and title_score < 0.2:
        fallback_score = geometry_score * 0.35 + histogram_score * 0.45 + method_score * 0.10
        score = max(score, min(0.82, fallback_score))
    return (
        max(0.0, min(1.0, score)),
        {
            "drawing_number": number_score,
            "title_text": title_score,
            "geometry": geometry_score,
            "histogram": histogram_score,
            "method_compatibility": method_score,
        },
        tuple(reasons),
    )


def _token_similarity(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    inter = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return inter / union if union else 0.0


def _tokens(text: str) -> set[str]:
    return {
        token.upper()
        for token in re.findall(r"[0-9A-Za-z가-힣]+", str(text or ""))
        if len(token) >= 2
    }


def _ambiguity_margin(
    scored: Sequence[tuple[float, int, int, dict[str, float], tuple[str, ...]]],
    score: float,
    before_index: int,
    after_index: int,
) -> float:
    alternatives = [
        other_score
        for other_score, other_before, other_after, _components, _reasons in scored
        if (other_before == before_index or other_after == after_index)
        and not (other_before == before_index and other_after == after_index)
    ]
    next_best = max(alternatives) if alternatives else 0.0
    return max(0.0, score - next_best)


def _should_emit_low_score_review_candidate(
    before: SheetRegion,
    after: SheetRegion,
    scored: Sequence[tuple[float, int, int, dict[str, float], tuple[str, ...]]],
    *,
    used_before: set[int],
    used_after: set[int],
) -> bool:
    """Keep the only possible weak pair visible so a user can approve it."""

    if before.drawing_number and after.drawing_number and before.drawing_number != after.drawing_number:
        return False
    remaining_before = {
        before_index
        for _score, before_index, _after_index, _components, _reasons in scored
        if before_index not in used_before
    }
    remaining_after = {
        after_index
        for _score, _before_index, after_index, _components, _reasons in scored
        if after_index not in used_after
    }
    return len(remaining_before) == 1 and len(remaining_after) == 1


def _allows_auto_match(components: dict[str, float]) -> bool:
    if components.get("drawing_number", 0.0) >= 1.0:
        return True
    return (
        components.get("title_text", 0.0) >= 0.35
        and components.get("geometry", 0.0) >= 0.80
        and components.get("histogram", 0.0) >= 0.85
    )


def _method_compatibility(before: SheetRegion, after: SheetRegion) -> float:
    before_method = str(before.detection_method or "")
    after_method = str(after.detection_method or "")
    if before_method == after_method:
        return 1.0
    frame_methods = {"cad_frame", "cad_line_frame", "viewport_frame", "pdf_vector_frame"}
    if before_method in frame_methods and after_method in frame_methods:
        return 0.9
    if "cluster" in before_method and "cluster" in after_method:
        return 0.75
    if before_method in frame_methods and "cluster" in after_method:
        return 0.55
    if after_method in frame_methods and "cluster" in before_method:
        return 0.55
    return 0.4


def _geometry_similarity(before: SheetRegion, after: SheetRegion) -> float:
    area_score = _ratio_similarity(before.area, after.area)
    aspect_score = _ratio_similarity(
        before.width / max(before.height, 1e-9),
        after.width / max(after.height, 1e-9),
    )
    entity_score = _ratio_similarity(before.entity_count, after.entity_count)
    return area_score * 0.35 + aspect_score * 0.35 + entity_score * 0.30


def _ratio_similarity(left: float, right: float) -> float:
    left = abs(float(left or 0.0))
    right = abs(float(right or 0.0))
    if left <= 0 and right <= 0:
        return 1.0
    if left <= 0 or right <= 0:
        return 0.0
    return min(left, right) / max(left, right)


def _histogram_cosine(left: dict[str, int], right: dict[str, int]) -> float:
    keys = set(left) | set(right)
    if not keys:
        return 0.0
    dot = sum(float(left.get(key, 0)) * float(right.get(key, 0)) for key in keys)
    norm_left = math.sqrt(sum(float(value) ** 2 for value in left.values()))
    norm_right = math.sqrt(sum(float(value) ** 2 for value in right.values()))
    if norm_left <= 0 or norm_right <= 0:
        return 0.0
    return dot / (norm_left * norm_right)
