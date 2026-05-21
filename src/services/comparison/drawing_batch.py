# -*- coding: utf-8 -*-
"""Batch orchestration for drawing comparison.

This module sits above the existing single-file comparison engines.  It scans
file/folder inputs, extracts lightweight metadata, matches A/B drawing sets with
confidence scoring, and runs confirmed comparisons.
"""

from __future__ import annotations

import hashlib
import html
import inspect
import csv
import json
import logging
import math
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Literal, Optional, Sequence, Tuple, Union

from .base import ChangeRecord, ChangeType, ComparisonResult
from .comparison_config import ComparisonConfig
from .pair_identity import candidate_display_label, candidate_pair_uuid

logger = logging.getLogger(__name__)


SUPPORTED_DRAWING_EXTENSIONS = {".dwg", ".dxf", ".pdf"}
CAD_EXTENSIONS = {".dwg", ".dxf"}
PDF_EXTENSIONS = {".pdf"}
REVISION_PATTERN = r"(?<![A-Z0-9])(?:REVISION|REV|R)[ _.-]*([0-9]+[A-Z]?)(?![A-Z0-9])"
# Phase H1 follow-up — moved to ``drawing_id_pattern`` so file-level
# (here) and page-level (page_descriptor.py) matching share one source
# of truth. The local name is preserved for back-compat with any
# external code that imports it from ``drawing_batch``.
from .drawing_id_pattern import (
    PROJECT_DRAWING_NUMBER_PATTERN,
)  # noqa: E402,F401  (re-export for back-compat)
LARGE_CAD_FILE_BYTES = 50 * 1024 * 1024
DESCRIPTOR_CACHE_VERSION = 3  # Phase O Commit 3 — INSERT/ATTRIB hash 변경으로 인한 cache invalidation
MANUAL_MATCH_CSV_COLUMNS = ("a_path", "b_path", "status")
MANUAL_MATCH_STATUSES = {
    "manual_confirmed",
    "rejected",
    "review_required",
}
COMPARE_STATE_SCHEMA_VERSION = 1


class DrawingKind(str, Enum):
    """Supported drawing families for v1 matching/comparison."""

    CAD = "cad"
    PDF = "pdf"


class MatchStatus(str, Enum):
    """A/B file matching state."""

    AUTO_CONFIRMED = "auto_confirmed"
    REVIEW_REQUIRED = "review_required"
    MANUAL_CONFIRMED = "manual_confirmed"
    UNMATCHED_A = "unmatched_a"
    UNMATCHED_B = "unmatched_b"
    REJECTED = "rejected"


def quality_gate_visible_statuses(report: Dict[str, Any]) -> Optional[set[MatchStatus]]:
    """Return match statuses that should stay visible for a failed quality gate."""

    if report.get("status") != "failed":
        return None
    metrics = {
        issue.get("metric")
        for issue in report.get("issues", [])
        if isinstance(issue, dict)
    }
    if "duplicate_a_assignments" in metrics or "duplicate_b_assignments" in metrics or "compare_failed" in metrics:
        return {MatchStatus.AUTO_CONFIRMED, MatchStatus.MANUAL_CONFIRMED}
    return {
        MatchStatus.MANUAL_CONFIRMED,
        MatchStatus.REVIEW_REQUIRED,
        MatchStatus.UNMATCHED_A,
        MatchStatus.UNMATCHED_B,
        MatchStatus.REJECTED,
    }


@dataclass
class MatchAlternative:
    """Alternate B candidate retained for review diagnostics."""

    source_b: DrawingFileDescriptor
    score: float
    reasons: List[str] = field(default_factory=list)
    component_scores: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_b": self.source_b.to_dict(),
            "score": round(self.score, 4),
            "reasons": self.reasons,
            "component_scores": {k: round(v, 4) for k, v in self.component_scores.items()},
        }


@dataclass(frozen=True)
class FilenameIdentity:
    """Parsed filename identity used for conservative matching."""

    original_stem: str
    match_key: str
    tokens: Tuple[str, ...]
    revision: Optional[str] = None
    drawing_number: Optional[str] = None
    sheet: Optional[str] = None


@dataclass
class DrawingFileDescriptor:
    """Lightweight description of a drawing file."""

    path: str
    kind: DrawingKind
    extension: str
    relative_path: str = ""
    identity: FilenameIdentity = field(
        default_factory=lambda: FilenameIdentity("", "", tuple())
    )
    page_count: int = 0
    page_size: Optional[Tuple[float, float]] = None
    layers: Tuple[str, ...] = tuple()
    layouts: Tuple[str, ...] = tuple()
    entity_counts: Dict[str, int] = field(default_factory=dict)
    bbox: Optional[Tuple[float, float, float, float]] = None
    text_hints: Tuple[str, ...] = tuple()
    title_hints: Tuple[str, ...] = tuple()
    content_fingerprint: str = ""
    visual_fingerprint: str = ""
    warnings: Tuple[str, ...] = tuple()

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def path_obj(self) -> Path:
        return Path(self.path)

    @property
    def text_fingerprint(self) -> str:
        text = " ".join(self.text_hints + self.title_hints)
        return normalize_text(text)[:400]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind.value,
            "extension": self.extension,
            "relative_path": self.relative_path,
            "identity": {
                "original_stem": self.identity.original_stem,
                "match_key": self.identity.match_key,
                "tokens": list(self.identity.tokens),
                "revision": self.identity.revision,
                "drawing_number": self.identity.drawing_number,
                "sheet": self.identity.sheet,
            },
            "page_count": self.page_count,
            "page_size": self.page_size,
            "layers": list(self.layers),
            "layouts": list(self.layouts),
            "entity_counts": self.entity_counts,
            "bbox": self.bbox,
            "text_hints": list(self.text_hints),
            "title_hints": list(self.title_hints),
            "content_fingerprint": self.content_fingerprint,
            "visual_fingerprint": self.visual_fingerprint,
            "warnings": list(self.warnings),
        }


@dataclass
class MatchCandidate:
    """Potential or confirmed A/B drawing match."""

    source_a: Optional[DrawingFileDescriptor]
    source_b: Optional[DrawingFileDescriptor]
    score: float
    status: MatchStatus
    reasons: List[str] = field(default_factory=list)
    component_scores: Dict[str, float] = field(default_factory=dict)
    alternates: List[MatchAlternative] = field(default_factory=list)
    pair_uuid: str = ""
    display_label: str = ""
    # Plan §15.5 (HIGH-2 dual-queue scheduler) — cached predicted runtime
    # cost classification, populated lazily by the batch scheduler so the
    # large-CAD lane runs serially while the normal lane keeps parallelism.
    # Default "small" preserves backward-compat for direct construction.
    predicted_load: Literal["small", "medium", "large"] = "small"

    @property
    def is_confirmed(self) -> bool:
        return self.status in {MatchStatus.AUTO_CONFIRMED, MatchStatus.MANUAL_CONFIRMED}

    def to_dict(self) -> Dict[str, Any]:
        pair_uuid = candidate_pair_uuid(self)
        display_label = self.display_label or candidate_display_label(self, pair_uuid)
        return {
            "pair_uuid": pair_uuid,
            "pair_id": pair_uuid,
            "display_label": display_label,
            "source_a": self.source_a.to_dict() if self.source_a else None,
            "source_b": self.source_b.to_dict() if self.source_b else None,
            "score": round(self.score, 4),
            "status": self.status.value,
            "reasons": self.reasons,
            "component_scores": {k: round(v, 4) for k, v in self.component_scores.items()},
            "alternates": [alternate.to_dict() for alternate in self.alternates],
        }


@dataclass
class DescriptorBuildOptions:
    """Options for metadata extraction."""

    recursive: bool = False
    use_ocr_fallback: bool = False
    max_text_chars: int = 4000
    max_cad_entities: int = 20000
    pdf_text_pages: int = 3
    enable_cache: bool = True
    dxf_cache_dir: Optional[Union[str, Path]] = None


@dataclass
class MatchingOptions:
    """Options for conservative A/B matching."""

    auto_threshold: float = 0.85
    review_threshold: float = 0.60
    ambiguous_delta: float = 0.10
    alternate_limit: int = 3


@dataclass
class BatchCompareOptions:
    """Options for running confirmed comparisons."""

    comparison_config: ComparisonConfig = field(default_factory=ComparisonConfig.get_default)
    compare_pdf_all_pages: bool = True
    pdf_auto_align: bool = True
    pdf_text_compare: bool = True
    pdf_dpi: int = 150
    max_workers: Optional[int] = None
    dxf_cache_dir: Optional[Union[str, Path]] = None
    compare_state_dir: Optional[Union[str, Path]] = None
    write_compare_state_json: bool = True
    # Phase H2 — when True (default), multi-page PDF compares run the
    # page-level auto-matcher (page_matcher.match_pdf_pages) before the
    # diff so reordered/inserted/deleted pages are recovered correctly.
    # Disable for sequential page comparison (legacy behaviour).
    # Single-page PDFs ignore this flag (no matching needed).
    pdf_page_auto_match: bool = True
    pdf_page_match_auto_threshold: float = 0.85
    pdf_page_match_review_threshold: float = 0.60
    # Phase H4 — manual user overrides for the page matcher. Two ways to
    # supply them, in priority order:
    #   1. ``manual_page_overrides_lookup(pair_uuid) -> Sequence[entry]``
    #      — invoked by ``compare_candidate`` for each pair so the GUI's
    #        run-scoped overrides JSON can be honoured per-pair.
    #   2. ``manual_page_overrides`` (flat list) — used directly when
    #      callers compare a single PDF pair without the BatchCompareJob
    #      layer (smoke tests, scripts, ad-hoc scripts).
    # Both default to "no overrides" → pure auto-match behaviour.
    manual_page_overrides_lookup: Optional[Callable[[str], "Sequence[Any]"]] = None
    manual_page_overrides: Optional["Sequence[Any]"] = None
    # Phase O5 — PDF visual-diff noise filter strength preset, chosen
    # by the user via the Workbench noise filter dialog (Ctrl+Shift+N).
    # Threaded into ``DrawingDiffer(config=...)`` per page so anti-
    # aliasing tolerance, morphology kernel, and DPI-aware threshold
    # cap all match the user's choice. Valid values: "low", "medium"
    # (default), "high". Unknown values fall back to medium inside
    # DrawingDiffer (see drawing_differ._resolve_noise_profile).
    pdf_noise_filter_strength: str = "medium"
    # OCR is intentionally opt-in for PDF comparison. Some OCR stacks import
    # torch/paddle native DLLs and can terminate the GUI process on Windows.
    use_ocr_fallback: bool = False
    # Phase O Commit 3 [RV-20260508-009] — DXF/DWG INSERT block-
    # internal text fingerprint 감지. True (default) 면 InsertNormalizer
    # 가 블록 정의 내부의 TEXT/MTEXT/ATTDEF 변경을 hash 에 반영하여
    # 사용자 사례 (예: dowel callout 블록 라이브러리 텍스트 변경) 가
    # 비교 결과에 surface. False 면 INSERT hash 가 Phase O Commit 1
    # 이전 동작으로 회귀 (legacy). Workbench V2 의 "정밀 텍스트
    # 감지" 체크박스 와 1:1 매핑.
    block_text_detection: bool = True


@dataclass
class BatchCompareItemResult:
    """Result for one matched pair."""

    candidate: MatchCandidate
    result: Optional[ComparisonResult] = None
    status: str = "pending"
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "status": self.status,
            "error": self.error,
            "result": self.result.to_dict() if self.result else None,
        }


@dataclass
class BatchCompareSummary:
    """Aggregate batch comparison result."""

    started_at: datetime
    requested_pairs: int = 0
    finished_at: Optional[datetime] = None
    items: List[BatchCompareItemResult] = field(default_factory=list)
    unmatched_a: List[DrawingFileDescriptor] = field(default_factory=list)
    unmatched_b: List[DrawingFileDescriptor] = field(default_factory=list)
    cancelled: bool = False

    @property
    def total_pairs(self) -> int:
        return self.requested_pairs if self.requested_pairs else len(self.items)

    @property
    def completed_pairs(self) -> int:
        return sum(1 for item in self.items if item.status == "completed")

    @property
    def failed_pairs(self) -> int:
        return sum(1 for item in self.items if item.status == "failed")

    @property
    def cancelled_pairs(self) -> int:
        return sum(1 for item in self.items if item.status == "cancelled")

    @property
    def total_changes(self) -> int:
        return sum(_result_change_count(item.result) for item in self.items if item.result)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "requested_pairs": self.requested_pairs,
            "total_pairs": self.total_pairs,
            "completed_pairs": self.completed_pairs,
            "failed_pairs": self.failed_pairs,
            "cancelled_pairs": self.cancelled_pairs,
            "cancelled": self.cancelled,
            "total_changes": self.total_changes,
            "items": [item.to_dict() for item in self.items],
            "unmatched_a": [desc.to_dict() for desc in self.unmatched_a],
            "unmatched_b": [desc.to_dict() for desc in self.unmatched_b],
        }


def normalize_text(value: str) -> str:
    """Normalize text for deterministic matching."""

    normalized = unicodedata.normalize("NFKC", value or "").upper()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def parse_filename_identity(path: Union[str, Path]) -> FilenameIdentity:
    """Parse filename into a revision-insensitive matching identity."""

    stem = Path(path).stem
    text = normalize_text(stem)

    revision = None
    revision_match = re.search(REVISION_PATTERN, text)
    if revision_match:
        revision = revision_match.group(0).replace(" ", "")

    sheet = _extract_sheet_token(text)
    drawing_number = _extract_drawing_number(text)

    key = text
    key = re.sub(REVISION_PATTERN, " ", key)
    key = re.sub(r"\b(?:19|20)?[0-9]{6}\b", " ", key)
    key = re.sub(r"\b20[0-9]{6}\b", " ", key)
    key = re.sub(r"[_()\[\]{}.,;:+]", " ", key)
    key = re.sub(r"[-]+", " ", key)
    tokens = tuple(
        token
        for token in re.findall(r"[A-Z0-9\uAC00-\uD7A3]+", key)
        if token and token not in {"OLD", "NEW", "FINAL", "COPY"}
    )
    match_key = "".join(tokens)

    return FilenameIdentity(
        original_stem=stem,
        match_key=match_key,
        tokens=tokens,
        revision=revision,
        drawing_number=drawing_number,
        sheet=sheet,
    )


def scan_drawing_inputs(
    inputs: Union[str, Path, Sequence[Union[str, Path]]],
    options: Optional[DescriptorBuildOptions] = None,
    root: Optional[Union[str, Path]] = None,
) -> List[DrawingFileDescriptor]:
    """Scan file/folder inputs and build descriptors."""

    build_options = options or DescriptorBuildOptions()
    paths = _coerce_input_paths(inputs, recursive=build_options.recursive)
    root_path = Path(root).resolve() if root else _common_root(paths)
    descriptors = [
        build_drawing_descriptor(path, options=build_options, root=root_path)
        for path in paths
    ]
    return descriptors


def build_drawing_descriptor(
    path: Union[str, Path],
    options: Optional[DescriptorBuildOptions] = None,
    root: Optional[Union[str, Path]] = None,
) -> DrawingFileDescriptor:
    """Build a lightweight descriptor for one drawing file."""

    path = Path(path).resolve()
    build_options = options or DescriptorBuildOptions()
    extension = path.suffix.lower()
    kind = _kind_for_extension(extension)
    identity = parse_filename_identity(path)

    relative_path = path.name
    if root:
        try:
            relative_path = str(path.relative_to(Path(root).resolve()))
        except ValueError:
            relative_path = path.name

    cache_root = Path(root).resolve() if root else path.parent
    if build_options.enable_cache:
        cached = _read_descriptor_cache(path, cache_root, build_options)
        if cached is not None:
            cached.relative_path = relative_path
            return cached

    started = time.perf_counter()
    descriptor = DrawingFileDescriptor(
        path=str(path),
        kind=kind,
        extension=extension,
        relative_path=relative_path,
        identity=identity,
    )

    try:
        if kind == DrawingKind.CAD:
            _fill_cad_descriptor(descriptor, build_options)
        elif kind == DrawingKind.PDF:
            _fill_pdf_descriptor(descriptor, build_options)
    except Exception as exc:  # Descriptor extraction must not block matching.
        logger.warning("Descriptor extraction failed for %s: %s", path, exc)
        descriptor.warnings = descriptor.warnings + (str(exc),)

    _enrich_identity_from_hints(descriptor)
    descriptor.content_fingerprint = descriptor.content_fingerprint or _hash_values(
        descriptor.identity.match_key,
        descriptor.kind.value,
        descriptor.relative_path.lower(),
    )
    if build_options.enable_cache:
        _write_descriptor_cache(
            descriptor,
            path,
            cache_root,
            build_options,
            elapsed_s=time.perf_counter() - started,
        )
    return descriptor


def _descriptor_cache_path(
    path: Path,
    cache_root: Path,
    options: DescriptorBuildOptions,
) -> Path:
    stat = path.stat()
    key = _hash_values(
        str(path.resolve()).lower(),
        str(stat.st_size),
        str(stat.st_mtime_ns),
        _descriptor_options_fingerprint(options),
    )
    return cache_root / ".drawing_compare_cache" / f"{key}.json"


def _read_descriptor_cache(
    path: Path,
    cache_root: Path,
    options: DescriptorBuildOptions,
) -> Optional[DrawingFileDescriptor]:
    try:
        cache_path = _descriptor_cache_path(path, cache_root, options)
        if not cache_path.exists():
            return None
        with open(cache_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("cache_version") != DESCRIPTOR_CACHE_VERSION:
            return None
        if payload.get("options_fingerprint") != _descriptor_options_fingerprint(options):
            return None
        return _descriptor_from_dict(payload["descriptor"])
    except Exception:
        return None


def _write_descriptor_cache(
    descriptor: DrawingFileDescriptor,
    path: Path,
    cache_root: Path,
    options: DescriptorBuildOptions,
    elapsed_s: float,
) -> None:
    temp_path = None
    try:
        cache_path = _descriptor_cache_path(path, cache_root, options)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cache_version": DESCRIPTOR_CACHE_VERSION,
            "options_fingerprint": _descriptor_options_fingerprint(options),
            "extractor_options": _descriptor_options_dict(options),
            "extractor_warnings": list(descriptor.warnings),
            "elapsed_s": round(float(elapsed_s), 6),
            "created_at": datetime.now().isoformat(),
            "descriptor": descriptor.to_dict(),
        }
        temp_path = cache_path.with_name(f"{cache_path.name}.{os.getpid()}.tmp")
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(temp_path, cache_path)
    except Exception:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass
        return


def _descriptor_options_dict(options: DescriptorBuildOptions) -> Dict[str, Any]:
    return {
        "use_ocr_fallback": bool(options.use_ocr_fallback),
        "max_text_chars": int(options.max_text_chars),
        "max_cad_entities": int(options.max_cad_entities),
        "pdf_text_pages": int(options.pdf_text_pages),
    }


def _descriptor_options_fingerprint(options: DescriptorBuildOptions) -> str:
    return _hash_values(
        str(DESCRIPTOR_CACHE_VERSION),
        json.dumps(_descriptor_options_dict(options), sort_keys=True),
    )


def _descriptor_from_dict(data: Dict[str, Any]) -> DrawingFileDescriptor:
    identity_data = data.get("identity", {})
    descriptor = DrawingFileDescriptor(
        path=data["path"],
        kind=DrawingKind(data["kind"]),
        extension=data["extension"],
        relative_path=data.get("relative_path", ""),
        identity=FilenameIdentity(
            original_stem=identity_data.get("original_stem", ""),
            match_key=identity_data.get("match_key", ""),
            tokens=tuple(identity_data.get("tokens", [])),
            revision=identity_data.get("revision"),
            drawing_number=identity_data.get("drawing_number"),
            sheet=identity_data.get("sheet"),
        ),
        page_count=int(data.get("page_count", 0) or 0),
        page_size=tuple(data["page_size"]) if data.get("page_size") else None,
        layers=tuple(data.get("layers", [])),
        layouts=tuple(data.get("layouts", [])),
        entity_counts=dict(data.get("entity_counts", {})),
        bbox=tuple(data["bbox"]) if data.get("bbox") else None,
        text_hints=tuple(data.get("text_hints", [])),
        title_hints=tuple(data.get("title_hints", [])),
        content_fingerprint=data.get("content_fingerprint", ""),
        visual_fingerprint=data.get("visual_fingerprint", ""),
        warnings=tuple(data.get("warnings", [])),
    )
    return descriptor


def match_drawing_sets(
    descriptors_a: Sequence[DrawingFileDescriptor],
    descriptors_b: Sequence[DrawingFileDescriptor],
    options: Optional[MatchingOptions] = None,
) -> List[MatchCandidate]:
    """Match A/B descriptor sets with conservative confidence thresholds."""

    match_options = options or MatchingOptions()
    descriptors_a = list(descriptors_a)
    descriptors_b = list(descriptors_b)

    pair_scores: Dict[Tuple[int, int], MatchCandidate] = {}
    for i, desc_a in enumerate(descriptors_a):
        for j, desc_b in enumerate(descriptors_b):
            if not are_compatible(desc_a, desc_b):
                continue
            candidate = score_match(desc_a, desc_b)
            if candidate.score >= match_options.review_threshold:
                pair_scores[(i, j)] = candidate

    assignments = _assign_unique_pairs(
        len(descriptors_a), len(descriptors_b), pair_scores
    )

    used_a = set()
    used_b = set()
    result: List[MatchCandidate] = []

    for i, j in assignments:
        candidate = pair_scores[(i, j)]
        status = _status_for_candidate(i, j, pair_scores, match_options)
        candidate.status = status
        candidate.alternates = _match_alternates(
            i,
            j,
            pair_scores,
            match_options.alternate_limit,
        )
        _append_match_diagnostics(candidate, i, j, pair_scores, match_options)
        result.append(candidate)
        used_a.add(i)
        used_b.add(j)

    for i, desc in enumerate(descriptors_a):
        if i not in used_a:
            result.append(
                MatchCandidate(
                    source_a=desc,
                    source_b=None,
                    score=0.0,
                    status=MatchStatus.UNMATCHED_A,
                    reasons=_unmatched_a_reasons(desc, descriptors_b),
                    alternates=_match_alternates(
                        i,
                        None,
                        pair_scores,
                        match_options.alternate_limit,
                    ),
                )
            )

    for j, desc in enumerate(descriptors_b):
        if j not in used_b:
            result.append(
                MatchCandidate(
                    source_a=None,
                    source_b=desc,
                    score=0.0,
                    status=MatchStatus.UNMATCHED_B,
                    reasons=_unmatched_b_reasons(desc, descriptors_a),
                )
            )

    return sorted(
        result,
        key=lambda c: (
            c.status not in {MatchStatus.AUTO_CONFIRMED, MatchStatus.MANUAL_CONFIRMED},
            c.status.value,
            -(c.score or 0.0),
            c.source_a.name if c.source_a else "",
            c.source_b.name if c.source_b else "",
        ),
    )


def score_match(desc_a: DrawingFileDescriptor, desc_b: DrawingFileDescriptor) -> MatchCandidate:
    """Score one compatible A/B candidate."""

    if not are_compatible(desc_a, desc_b):
        return MatchCandidate(
            desc_a,
            desc_b,
            0.0,
            MatchStatus.REJECTED,
            ["Incompatible file families"],
        )

    name_score = _name_score(desc_a.identity, desc_b.identity)
    sheet_score = _sheet_score(desc_a.identity, desc_b.identity)
    text_score = _text_score(desc_a, desc_b)
    content_score = _content_score(desc_a, desc_b)
    relative_score = _relative_path_score(desc_a.relative_path, desc_b.relative_path)
    drawing_code_mismatch = _project_drawing_code_mismatch(
        desc_a.identity,
        desc_b.identity,
    )

    weighted = (
        0.35 * name_score
        + 0.20 * sheet_score
        + 0.20 * text_score
        + 0.20 * content_score
        + 0.05 * relative_score
    )
    if desc_a.identity.match_key and desc_a.identity.match_key == desc_b.identity.match_key:
        weighted = max(weighted, 0.88)
    if (
        _same_drawing_number(desc_a.identity, desc_b.identity)
        and (
            sheet_score >= 0.8
            or _same_project_drawing_code(desc_a.identity, desc_b.identity)
        )
    ):
        weighted = max(weighted, 0.86)
    if drawing_code_mismatch:
        weighted = min(weighted, 0.59)

    reasons = [
        f"name={name_score:.2f}",
        f"sheet={sheet_score:.2f}",
        f"text={text_score:.2f}",
        f"content={content_score:.2f}",
        f"path={relative_score:.2f}",
    ]
    if desc_a.identity.revision or desc_b.identity.revision:
        reasons.append(
            f"revision ignored: {desc_a.identity.revision or '-'} -> {desc_b.identity.revision or '-'}"
        )
    if _same_drawing_number(desc_a.identity, desc_b.identity):
        reasons.append(f"drawing number matched: {desc_a.identity.drawing_number}")
        if desc_a.identity.match_key != desc_b.identity.match_key:
            reasons.append("diagnostic: filename differs but drawing number matched")
    if drawing_code_mismatch:
        reasons.append(
            "diagnostic: drawing code mismatch "
            f"({desc_a.identity.drawing_number} != {desc_b.identity.drawing_number})"
        )

    return MatchCandidate(
        source_a=desc_a,
        source_b=desc_b,
        score=max(0.0, min(1.0, weighted)),
        status=MatchStatus.REVIEW_REQUIRED,
        reasons=reasons,
        component_scores={
            "name": name_score,
            "sheet": sheet_score,
            "text": text_score,
            "content": content_score,
            "relative_path": relative_score,
        },
    )


def are_compatible(desc_a: DrawingFileDescriptor, desc_b: DrawingFileDescriptor) -> bool:
    """Return True for v1-compatible comparison families."""

    if desc_a.kind == DrawingKind.CAD and desc_b.kind == DrawingKind.CAD:
        return True
    if desc_a.kind == DrawingKind.PDF and desc_b.kind == DrawingKind.PDF:
        return True
    return False


def _result_change_count(result: ComparisonResult) -> int:
    counts = result.metadata.get("change_counts") if result.metadata else None
    if counts:
        return sum(int(counts.get(name, 0) or 0) for name in ("added", "deleted", "modified"))
    return result.total_changes


def load_manual_match_csv(path: Union[str, Path]) -> List[Dict[str, str]]:
    """Load a flat review CSV shared by CLI and Workbench."""

    rows: List[Dict[str, str]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(MANUAL_MATCH_CSV_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Manual match CSV missing columns: {sorted(missing)}")
        for row in reader:
            status = (row.get("status") or "").strip().lower()
            rows.append(
                {
                    "a_path": (row.get("a_path") or "").strip(),
                    "b_path": (row.get("b_path") or "").strip(),
                    "status": status,
                }
            )
    return rows


def write_manual_match_csv(
    candidates: Sequence[MatchCandidate],
    output_path: Union[str, Path],
    root_a: Optional[Union[str, Path]] = None,
    root_b: Optional[Union[str, Path]] = None,
) -> Path:
    """Write review CSV rows that can be imported by CLI or Workbench."""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    root_a_path = Path(root_a).resolve() if root_a else None
    root_b_path = Path(root_b).resolve() if root_b else None
    with open(output_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANUAL_MATCH_CSV_COLUMNS)
        writer.writeheader()
        for candidate in candidates:
            if not candidate.source_a or not candidate.source_b:
                continue
            status = candidate.status.value
            if candidate.status == MatchStatus.AUTO_CONFIRMED:
                status = MatchStatus.MANUAL_CONFIRMED.value
            if status not in MANUAL_MATCH_STATUSES:
                continue
            writer.writerow(
                {
                    "a_path": _relative_or_absolute(candidate.source_a.path, root_a_path),
                    "b_path": _relative_or_absolute(candidate.source_b.path, root_b_path),
                    "status": status,
                }
            )
    return output_path


def apply_manual_matches(
    rows: Sequence[Dict[str, str]],
    candidates: List[MatchCandidate],
    descriptors_a: Sequence[DrawingFileDescriptor],
    descriptors_b: Sequence[DrawingFileDescriptor],
    root_a: Optional[Union[str, Path]] = None,
    root_b: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Apply review CSV rows to existing candidates in-place."""

    root_a_path = Path(root_a).resolve() if root_a else None
    root_b_path = Path(root_b).resolve() if root_b else None
    descriptors_by_a = {
        _normalize_match_path(descriptor.path, None): descriptor
        for descriptor in descriptors_a
    }
    descriptors_by_b = {
        _normalize_match_path(descriptor.path, None): descriptor
        for descriptor in descriptors_b
    }

    applied = 0
    errors: List[Dict[str, str]] = []
    for row_number, row in enumerate(rows, start=2):
        status_value = (row.get("status") or "").strip().lower()
        if status_value not in MANUAL_MATCH_STATUSES:
            errors.append(
                {
                    "row": str(row_number),
                    "error": f"Unsupported status: {status_value}",
                }
            )
            continue

        a_key = _normalize_match_path(row.get("a_path", ""), root_a_path)
        b_key = _normalize_match_path(row.get("b_path", ""), root_b_path)
        desc_a = descriptors_by_a.get(a_key)
        desc_b = descriptors_by_b.get(b_key)
        if status_value in {MatchStatus.MANUAL_CONFIRMED.value, MatchStatus.REVIEW_REQUIRED.value}:
            if not desc_a or not desc_b:
                errors.append(
                    {
                        "row": str(row_number),
                        "error": "manual_confirmed/review_required rows require valid A and B paths",
                    }
                )
                continue
            if not are_compatible(desc_a, desc_b):
                errors.append(
                    {
                        "row": str(row_number),
                        "error": "Incompatible A/B formats cannot be confirmed for comparison",
                    }
                )
                continue
            conflict = _manual_pair_conflict(candidates, a_key, b_key)
            if conflict:
                errors.append(
                    {
                        "row": str(row_number),
                        "error": conflict,
                    }
                )
                continue
            _remove_conflicting_pair_candidates(candidates, a_key, b_key)
            candidate = _upsert_pair_candidate(candidates, desc_a, desc_b)
            candidate.status = MatchStatus(status_value)
            candidate.reasons = _append_unique_reason(candidate.reasons, f"manual CSV status: {status_value}")
            _remove_unmatched_entries(candidates, desc_a, desc_b)
            applied += 1
        else:
            candidate = _find_pair_candidate(candidates, a_key, b_key)
            if candidate is None and (desc_a or desc_b):
                candidate = MatchCandidate(
                    source_a=desc_a,
                    source_b=desc_b,
                    score=0.0,
                    status=MatchStatus.REJECTED,
                    reasons=["manual CSV status: rejected"],
                )
                candidates.append(candidate)
            elif candidate is not None:
                candidate.status = MatchStatus.REJECTED
                candidate.reasons = _append_unique_reason(candidate.reasons, "manual CSV status: rejected")
            else:
                errors.append(
                    {
                        "row": str(row_number),
                        "error": "Rejected row did not match any known A or B path",
                    }
                )
                continue
            applied += 1

    return {"rows": len(rows), "applied": applied, "errors": errors}


def _upsert_pair_candidate(
    candidates: List[MatchCandidate],
    desc_a: DrawingFileDescriptor,
    desc_b: DrawingFileDescriptor,
) -> MatchCandidate:
    a_key = _normalize_match_path(desc_a.path, None)
    b_key = _normalize_match_path(desc_b.path, None)
    existing = _find_pair_candidate(candidates, a_key, b_key)
    scored = score_match(desc_a, desc_b)
    if existing:
        existing.source_a = desc_a
        existing.source_b = desc_b
        existing.score = scored.score
        existing.reasons = scored.reasons
        existing.component_scores = scored.component_scores
        existing.alternates = scored.alternates
        return existing

    scored.status = MatchStatus.REVIEW_REQUIRED
    candidates.append(scored)
    return scored


def confirmed_pair_uniqueness_violations(
    candidates: Sequence[MatchCandidate],
) -> Dict[str, List[str]]:
    """Return confirmed A/B duplicate assignments for hard validation gates."""

    a_seen: Dict[str, str] = {}
    b_seen: Dict[str, str] = {}
    duplicate_a: List[str] = []
    duplicate_b: List[str] = []
    for candidate in candidates:
        if not candidate.is_confirmed:
            continue
        pair_uuid = candidate_pair_uuid(candidate)
        a_key = _normalize_match_path(candidate.source_a.path, None) if candidate.source_a else ""
        b_key = _normalize_match_path(candidate.source_b.path, None) if candidate.source_b else ""
        if a_key:
            if a_key in a_seen and a_seen[a_key] != pair_uuid:
                duplicate_a.append(candidate.source_a.path if candidate.source_a else a_key)
            else:
                a_seen[a_key] = pair_uuid
        if b_key:
            if b_key in b_seen and b_seen[b_key] != pair_uuid:
                duplicate_b.append(candidate.source_b.path if candidate.source_b else b_key)
            else:
                b_seen[b_key] = pair_uuid
    return {
        "duplicate_a": sorted(set(duplicate_a)),
        "duplicate_b": sorted(set(duplicate_b)),
    }


def _manual_pair_conflict(
    candidates: Sequence[MatchCandidate],
    a_key: str,
    b_key: str,
) -> str:
    for candidate in candidates:
        candidate_a = _normalize_match_path(candidate.source_a.path, None) if candidate.source_a else ""
        candidate_b = _normalize_match_path(candidate.source_b.path, None) if candidate.source_b else ""
        if candidate_a == a_key and candidate_b == b_key:
            continue
        if candidate.status != MatchStatus.MANUAL_CONFIRMED:
            continue
        if a_key and candidate_a == a_key:
            return "A drawing is already assigned to another B drawing"
        if b_key and candidate_b == b_key:
            return "B drawing is already assigned to another A drawing"
    return ""


def _remove_conflicting_pair_candidates(
    candidates: List[MatchCandidate],
    a_key: str,
    b_key: str,
) -> None:
    candidates[:] = [
        candidate
        for candidate in candidates
        if (
            (
                (_normalize_match_path(candidate.source_a.path, None) if candidate.source_a else "") != a_key
                and (_normalize_match_path(candidate.source_b.path, None) if candidate.source_b else "") != b_key
            )
            or (
                (_normalize_match_path(candidate.source_a.path, None) if candidate.source_a else "") == a_key
                and (_normalize_match_path(candidate.source_b.path, None) if candidate.source_b else "") == b_key
            )
            or candidate.status == MatchStatus.MANUAL_CONFIRMED
        )
    ]


def _find_pair_candidate(
    candidates: Sequence[MatchCandidate],
    a_key: str,
    b_key: str,
) -> Optional[MatchCandidate]:
    for candidate in candidates:
        candidate_a = _normalize_match_path(candidate.source_a.path, None) if candidate.source_a else ""
        candidate_b = _normalize_match_path(candidate.source_b.path, None) if candidate.source_b else ""
        if candidate_a == a_key and candidate_b == b_key:
            return candidate
    return None


def _remove_unmatched_entries(
    candidates: List[MatchCandidate],
    desc_a: Optional[DrawingFileDescriptor],
    desc_b: Optional[DrawingFileDescriptor],
) -> None:
    a_key = _normalize_match_path(desc_a.path, None) if desc_a else ""
    b_key = _normalize_match_path(desc_b.path, None) if desc_b else ""
    candidates[:] = [
        candidate
        for candidate in candidates
        if not (
            (candidate.status == MatchStatus.UNMATCHED_A and candidate.source_a and _normalize_match_path(candidate.source_a.path, None) == a_key)
            or (candidate.status == MatchStatus.UNMATCHED_B and candidate.source_b and _normalize_match_path(candidate.source_b.path, None) == b_key)
        )
    ]


def _append_unique_reason(reasons: Sequence[str], reason: str) -> List[str]:
    output = list(reasons)
    if reason not in output:
        output.append(reason)
    return output


def _normalize_match_path(value: Union[str, Path], root: Optional[Path]) -> str:
    if not value:
        return ""
    path = Path(value)
    if path.is_absolute():
        return str(path.resolve()).casefold()
    if root is not None:
        return str((root / path).resolve()).casefold()
    return str(path.resolve()).casefold()


def _relative_or_absolute(value: Union[str, Path], root: Optional[Path]) -> str:
    path = Path(value).resolve()
    if root is not None:
        try:
            return str(path.relative_to(root))
        except ValueError:
            pass
    return str(path)


class BatchCompareJob:
    """Run confirmed drawing comparisons and export summary reports."""

    def __init__(
        self,
        candidates: Sequence[MatchCandidate],
        options: Optional[BatchCompareOptions] = None,
    ):
        self.candidates = list(candidates)
        self.options = options or BatchCompareOptions()

    def run(
        self,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> BatchCompareSummary:
        """Run all confirmed candidate pairs."""

        confirmed = [candidate for candidate in self.candidates if candidate.is_confirmed]
        violations = confirmed_pair_uniqueness_violations(confirmed)
        if violations["duplicate_a"] or violations["duplicate_b"]:
            details = []
            if violations["duplicate_a"]:
                details.append("duplicate A assignments: " + ", ".join(violations["duplicate_a"][:5]))
            if violations["duplicate_b"]:
                details.append("duplicate B assignments: " + ", ".join(violations["duplicate_b"][:5]))
            raise ValueError("Confirmed drawing pairs must be one-to-one; " + "; ".join(details))
        summary = BatchCompareSummary(started_at=datetime.now(), requested_pairs=len(confirmed))
        summary.unmatched_a = [
            c.source_a for c in self.candidates if c.status == MatchStatus.UNMATCHED_A and c.source_a
        ]
        summary.unmatched_b = [
            c.source_b for c in self.candidates if c.status == MatchStatus.UNMATCHED_B and c.source_b
        ]

        total = len(confirmed)

        # Plan §15.5 (HIGH-2 dual-queue scheduler) — partition before
        # worker resolution so the normal lane keeps parallelism even
        # when a single large CAD pair is present.
        large_lane, normal_lane = _partition_candidates_by_size(
            confirmed, self.options
        )
        workers = _resolve_batch_workers(normal_lane or confirmed, self.options)
        # Stable ordering key so summary.items reflects input order
        # regardless of completion order (R1 risk mitigation).
        index_by_id = {id(c): pos for pos, c in enumerate(confirmed)}

        use_dual_lane = (
            workers > 1
            and bool(large_lane)
            and bool(normal_lane)
        )

        if workers <= 1 and not use_dual_lane:
            # Legacy single-pool sequential path — preserved verbatim
            # for backward compat with cancel/progress assertions.
            for offset, candidate in enumerate(confirmed):
                index = offset + 1
                if is_cancelled and is_cancelled():
                    _append_cancelled_items(summary, confirmed[offset:])
                    break

                label = _candidate_label(candidate)
                if progress_callback:
                    progress_callback(index - 1, total, f"Comparing {label}")

                item = _run_candidate_item(
                    candidate,
                    self.options,
                    is_cancelled,
                    progress_callback=progress_callback,
                )
                summary.items.append(item)
                if item.status == "cancelled":
                    summary.cancelled = True
                    _append_cancelled_items(summary, confirmed[index:])
                    break

                if progress_callback:
                    progress_callback(index, total, f"Finished {label}")
        elif not use_dual_lane:
            # Legacy single-pool parallel path — used when there are no
            # large candidates (or no normal candidates) so a single
            # executor is the simpler, lower-overhead choice.
            completed = 0
            with ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_candidate = {}
                for candidate in confirmed:
                    if is_cancelled and is_cancelled():
                        summary.cancelled = True
                        break
                    future = executor.submit(
                        _run_candidate_item,
                        candidate,
                        self.options,
                        is_cancelled,
                        progress_callback,
                    )
                    future_to_candidate[future] = candidate

                handled_candidate_ids = set()
                for future in as_completed(future_to_candidate):
                    candidate = future_to_candidate[future]
                    try:
                        item = future.result()
                    except Exception as exc:
                        item = BatchCompareItemResult(
                            candidate=candidate,
                            status="failed",
                            error=str(exc),
                        )
                    summary.items.append(item)
                    handled_candidate_ids.add(id(candidate))
                    completed += 1
                    if progress_callback:
                        progress_callback(
                            completed,
                            total,
                            f"Finished {_candidate_label(item.candidate)}",
                        )
                    if item.status == "cancelled" or (is_cancelled and is_cancelled()):
                        summary.cancelled = True
                        for pending in future_to_candidate:
                            if pending is not future:
                                pending.cancel()
                        break

                if summary.cancelled:
                    remaining = [
                        candidate
                        for candidate in confirmed
                        if id(candidate) not in handled_candidate_ids
                    ]
                    _append_cancelled_items(summary, remaining)
        else:
            # Plan §15.5 (HIGH-2) dual-lane scheduler — large pool runs
            # serially (max_workers=1) while the normal pool keeps the
            # resolved worker count.  Results are collected by stable
            # input order before being appended to summary.items.
            completed = 0
            handled_candidate_ids: set = set()
            results_by_index: Dict[int, BatchCompareItemResult] = {}
            # Both pools share a single ``as_completed`` loop so cancel
            # propagation and progress reporting stay deterministic.
            large_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="dwgcmp-large"
            )
            normal_executor = ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="dwgcmp-normal"
            )
            try:
                future_to_candidate: Dict[Any, MatchCandidate] = {}
                # Submit large lane first so it can start its long-
                # running work in parallel with the small lane warm-up.
                for candidate in large_lane:
                    if is_cancelled and is_cancelled():
                        summary.cancelled = True
                        break
                    future = large_executor.submit(
                        _run_candidate_item,
                        candidate,
                        self.options,
                        is_cancelled,
                        progress_callback,
                    )
                    future_to_candidate[future] = candidate
                if not summary.cancelled:
                    for candidate in normal_lane:
                        if is_cancelled and is_cancelled():
                            summary.cancelled = True
                            break
                        future = normal_executor.submit(
                            _run_candidate_item,
                            candidate,
                            self.options,
                            is_cancelled,
                            progress_callback,
                        )
                        future_to_candidate[future] = candidate

                for future in as_completed(future_to_candidate):
                    candidate = future_to_candidate[future]
                    try:
                        item = future.result()
                    except Exception as exc:
                        item = BatchCompareItemResult(
                            candidate=candidate,
                            status="failed",
                            error=str(exc),
                        )
                    pos = index_by_id.get(id(candidate), len(confirmed))
                    results_by_index[pos] = item
                    handled_candidate_ids.add(id(candidate))
                    completed += 1
                    if progress_callback:
                        progress_callback(
                            completed,
                            total,
                            f"Finished {_candidate_label(item.candidate)}",
                        )
                    if item.status == "cancelled" or (is_cancelled and is_cancelled()):
                        summary.cancelled = True
                        # R2 mitigation — cancel both pools' pending
                        # futures eagerly so worker threads exit fast.
                        for pending in future_to_candidate:
                            if pending is not future and not pending.done():
                                pending.cancel()
                        break
            finally:
                # R2 mitigation — both executors must release worker
                # threads even when an exception bubbles up.  Python 3.9+
                # supports ``cancel_futures`` for shutdown.
                large_executor.shutdown(wait=False, cancel_futures=True)
                normal_executor.shutdown(wait=False, cancel_futures=True)

            # R1 mitigation — flush collected items in input order so
            # external consumers observe deterministic summary.items.
            for pos in sorted(results_by_index.keys()):
                summary.items.append(results_by_index[pos])

            if summary.cancelled:
                remaining = [
                    candidate
                    for candidate in confirmed
                    if id(candidate) not in handled_candidate_ids
                ]
                _append_cancelled_items(summary, remaining)

        summary.finished_at = datetime.now()
        if self.options.compare_state_dir and self.options.write_compare_state_json:
            write_compare_state(summary, self.options.compare_state_dir)
        return summary

    @staticmethod
    def export_json(summary: BatchCompareSummary, output_path: Union[str, Path]) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(summary.to_dict(), handle, ensure_ascii=False, indent=2)
        return output_path

    @staticmethod
    def export_html(summary: BatchCompareSummary, output_path: Union[str, Path]) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        rows = []
        for item in summary.items:
            candidate = item.candidate
            source_a = html.escape(candidate.source_a.name if candidate.source_a else "")
            source_b = html.escape(candidate.source_b.name if candidate.source_b else "")
            changes = _result_change_count(item.result) if item.result else ""
            rows.append(
                "<tr>"
                f"<td>{source_a}</td><td>{source_b}</td>"
                f"<td>{candidate.score:.2f}</td><td>{html.escape(item.status)}</td>"
                f"<td>{changes}</td><td>{html.escape(item.error or '')}</td>"
                "</tr>"
            )

        document = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Drawing Compare Batch Report</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #222; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; }}
    th {{ background: #f3f5f7; }}
  </style>
</head>
<body>
  <h1>Drawing Compare Batch Report</h1>
  <p>Pairs: {summary.total_pairs}, completed: {summary.completed_pairs},
     failed: {summary.failed_pairs}, cancelled: {summary.cancelled_pairs},
     total changes: {summary.total_changes}</p>
  <table>
    <thead>
      <tr><th>A</th><th>B</th><th>Match Score</th><th>Status</th><th>Changes</th><th>Error</th></tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</body>
</html>
"""
        output_path.write_text(document, encoding="utf-8")
        return output_path

    @staticmethod
    def export_excel(summary: BatchCompareSummary, output_path: Union[str, Path]) -> Path:
        try:
            import openpyxl
        except ImportError as exc:
            raise ImportError("openpyxl is required for Excel export") from exc

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Summary"
        ws.append(["A", "B", "Match Score", "Match Status", "Compare Status", "Changes", "Error"])
        for item in summary.items:
            candidate = item.candidate
            ws.append(
                [
                    candidate.source_a.path if candidate.source_a else "",
                    candidate.source_b.path if candidate.source_b else "",
                    candidate.score,
                    candidate.status.value,
                    item.status,
                    _result_change_count(item.result) if item.result else "",
                    item.error or "",
                ]
            )
        run_ws = wb.create_sheet("Run Summary")
        run_ws.append(["Metric", "Value"])
        run_ws.append(["Requested Pairs", summary.requested_pairs])
        run_ws.append(["Completed Pairs", summary.completed_pairs])
        run_ws.append(["Failed Pairs", summary.failed_pairs])
        run_ws.append(["Cancelled Pairs", summary.cancelled_pairs])
        run_ws.append(["Cancelled", summary.cancelled])
        run_ws.append(["Total Changes", summary.total_changes])
        wb.save(str(output_path))
        return output_path

    @staticmethod
    def export_change_artifacts(
        summary: BatchCompareSummary,
        output_dir: Union[str, Path],
        *,
        dxf_cache_dir: Optional[Union[str, Path]] = None,
        compare_state_dir: Optional[Union[str, Path]] = None,
        cloud_options: Any = None,
        export_cloud_marks: bool = True,
        export_before_marks: bool = False,
    ) -> Any:
        """Export grouped change-zone register and optional cloud-marked DXF."""
        from .change_zones import export_change_artifacts

        return export_change_artifacts(
            summary,
            output_dir,
            dxf_cache_dir=dxf_cache_dir,
            compare_state_dir=compare_state_dir,
            cloud_options=cloud_options,
            export_cloud_marks=export_cloud_marks,
            export_before_marks=export_before_marks,
        )


def write_compare_state(summary: BatchCompareSummary, state_dir: Union[str, Path]) -> Path:
    """Persist enough batch state to regenerate change-zone artifacts."""

    state_dir = Path(state_dir).resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "compare_state.json"
    payload = {
        "schema_version": COMPARE_STATE_SCHEMA_VERSION,
        "created_at": datetime.now().isoformat(),
        "summary": summary.to_dict(),
    }
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)
    return path


def load_compare_state(state_dir: Union[str, Path]) -> BatchCompareSummary:
    """Load a persisted compare state written by write_compare_state()."""

    state_dir = Path(state_dir).resolve()
    path = state_dir / "compare_state.json"
    if not path.exists():
        raise FileNotFoundError(f"Compare state not found: {path}")
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("schema_version") != COMPARE_STATE_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported compare state schema: {payload.get('schema_version')}"
        )
    summary_data = payload.get("summary") or {}
    return _summary_from_dict(summary_data)


def _summary_from_dict(data: Dict[str, Any]) -> BatchCompareSummary:
    summary = BatchCompareSummary(
        started_at=_parse_datetime(data.get("started_at")) or datetime.now(),
        requested_pairs=int(data.get("requested_pairs") or data.get("total_pairs") or 0),
        finished_at=_parse_datetime(data.get("finished_at")),
        cancelled=bool(data.get("cancelled", False)),
    )
    summary.items = [_item_from_dict(item) for item in data.get("items", [])]
    summary.unmatched_a = [
        _descriptor_from_dict(item)
        for item in data.get("unmatched_a", [])
        if isinstance(item, dict)
    ]
    summary.unmatched_b = [
        _descriptor_from_dict(item)
        for item in data.get("unmatched_b", [])
        if isinstance(item, dict)
    ]
    return summary


def _item_from_dict(data: Dict[str, Any]) -> BatchCompareItemResult:
    return BatchCompareItemResult(
        candidate=_candidate_from_dict(data.get("candidate") or {}),
        result=_comparison_result_from_dict(data.get("result")),
        status=data.get("status") or "pending",
        error=data.get("error"),
    )


def _candidate_from_dict(data: Dict[str, Any]) -> MatchCandidate:
    status_value = data.get("status") or MatchStatus.REVIEW_REQUIRED.value
    try:
        status = MatchStatus(status_value)
    except ValueError:
        status = MatchStatus.REVIEW_REQUIRED
    candidate = MatchCandidate(
        source_a=_descriptor_from_dict(data["source_a"]) if data.get("source_a") else None,
        source_b=_descriptor_from_dict(data["source_b"]) if data.get("source_b") else None,
        score=float(data.get("score") or 0.0),
        status=status,
        reasons=list(data.get("reasons") or []),
        component_scores=dict(data.get("component_scores") or {}),
        pair_uuid=str(data.get("pair_uuid") or data.get("pair_id") or ""),
        display_label=str(data.get("display_label") or ""),
    )
    candidate.alternates = [
        MatchAlternative(
            source_b=_descriptor_from_dict(item.get("source_b") or {}),
            score=float(item.get("score") or 0.0),
            reasons=list(item.get("reasons") or []),
            component_scores=dict(item.get("component_scores") or {}),
        )
        for item in data.get("alternates", [])
        if isinstance(item, dict) and item.get("source_b")
    ]
    return candidate


def _descriptor_from_dict(data: Dict[str, Any]) -> DrawingFileDescriptor:
    identity_data = data.get("identity") or {}
    kind_value = data.get("kind") or DrawingKind.CAD.value
    try:
        kind = DrawingKind(kind_value)
    except ValueError:
        kind = DrawingKind.CAD
    page_size = data.get("page_size")
    bbox = data.get("bbox")
    return DrawingFileDescriptor(
        path=str(data.get("path") or ""),
        kind=kind,
        extension=str(data.get("extension") or Path(str(data.get("path") or "")).suffix),
        relative_path=str(data.get("relative_path") or ""),
        identity=FilenameIdentity(
            original_stem=str(identity_data.get("original_stem") or ""),
            match_key=str(identity_data.get("match_key") or ""),
            tokens=tuple(identity_data.get("tokens") or []),
            revision=identity_data.get("revision"),
            drawing_number=identity_data.get("drawing_number"),
            sheet=identity_data.get("sheet"),
        ),
        page_count=int(data.get("page_count") or 0),
        page_size=tuple(page_size) if page_size else None,
        layers=tuple(data.get("layers") or []),
        layouts=tuple(data.get("layouts") or []),
        entity_counts=dict(data.get("entity_counts") or {}),
        bbox=tuple(bbox) if bbox else None,
        text_hints=tuple(data.get("text_hints") or []),
        title_hints=tuple(data.get("title_hints") or []),
        content_fingerprint=str(data.get("content_fingerprint") or ""),
        visual_fingerprint=str(data.get("visual_fingerprint") or ""),
        warnings=tuple(data.get("warnings") or []),
    )


def _comparison_result_from_dict(data: Optional[Dict[str, Any]]) -> Optional[ComparisonResult]:
    if not data:
        return None
    result = ComparisonResult(
        source_a=str(data.get("source_a") or ""),
        source_b=str(data.get("source_b") or ""),
        compared_at=_parse_datetime(data.get("compared_at")) or datetime.now(),
    )
    summary = data.get("summary") or {}
    result.added_count = int(summary.get("added") or 0)
    result.deleted_count = int(summary.get("deleted") or 0)
    result.modified_count = int(summary.get("modified") or 0)
    result.unchanged_count = int(summary.get("unchanged") or 0)
    result.changes = [
        _change_record_from_dict(item)
        for item in data.get("changes", [])
        if isinstance(item, dict)
    ]
    result.warnings = list(data.get("warnings") or [])
    result.metadata = dict(data.get("metadata") or {})
    return result


def _change_record_from_dict(data: Dict[str, Any]) -> ChangeRecord:
    try:
        change_type = ChangeType(data.get("change_type"))
    except ValueError:
        change_type = ChangeType.MODIFIED
    return ChangeRecord(
        key=str(data.get("key") or ""),
        change_type=change_type,
        field_name=data.get("field_name"),
        old_value=data.get("old_value"),
        new_value=data.get("new_value"),
        location=data.get("location"),
        metadata=dict(data.get("metadata") or {}),
    )


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _append_cancelled_items(
    summary: BatchCompareSummary,
    candidates: Sequence[MatchCandidate],
) -> None:
    existing = {id(item.candidate) for item in summary.items}
    appended = False
    for candidate in candidates:
        if id(candidate) in existing:
            continue
        summary.items.append(
            BatchCompareItemResult(
                candidate=candidate,
                status="cancelled",
                error="Cancelled before comparison completed",
            )
        )
        existing.add(id(candidate))
        appended = True
    if appended:
        summary.cancelled = True


def _run_candidate_item(
    candidate: MatchCandidate,
    options: BatchCompareOptions,
    is_cancelled: Optional[Callable[[], bool]] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> BatchCompareItemResult:
    label = _candidate_label(candidate)
    item = BatchCompareItemResult(candidate=candidate, status="running")
    try:
        if is_cancelled and is_cancelled():
            item.status = "cancelled"
            return item
        item.result = _call_compare_candidate(
            candidate,
            options,
            is_cancelled,
            progress_callback=progress_callback,
        )
        item.status = "completed"
    except Exception as exc:
        logger.exception("Batch comparison failed for %s", label)
        item.status = "failed"
        item.error = str(exc)
    return item


def _call_compare_candidate(
    candidate: MatchCandidate,
    options: BatchCompareOptions,
    is_cancelled: Optional[Callable[[], bool]] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> ComparisonResult:
    parameters = inspect.signature(compare_candidate).parameters
    if "is_cancelled" in parameters:
        kwargs: Dict[str, Any] = {"is_cancelled": is_cancelled}
        if "progress_callback" in parameters:
            kwargs["progress_callback"] = progress_callback
        return compare_candidate(candidate, options, **kwargs)
    return compare_candidate(candidate, options)


def _resolve_batch_workers(
    candidates: Sequence[MatchCandidate],
    options: BatchCompareOptions,
) -> int:
    """Resolve worker pool size for the *normal* (non-large) lane.

    Plan §15.5 (HIGH-2 dual-queue scheduler) behaviour change
    ---------------------------------------------------------
    Prior to §15.5 this helper inspected *all* candidates for large CAD
    pairs and collapsed the entire batch to ``workers=1`` if any were
    present.  External auditor #2 (HIGH-2) flagged this as starving the
    common "1 large + N small" folder mix of parallelism.

    The new contract:

    * ``options.max_workers`` still wins when set (no auto downshift).
    * The returned worker count applies to the *normal* lane only.  The
      large-CAD lane is scheduled separately with a fixed pool of 1
      (callers must partition via :func:`_partition_candidates_by_size`
      before calling this helper).
    * Empty input still returns 1 (no work to do, but the call sites
      historically dereferenced this as ``workers <= 1`` for the
      sequential path).
    """

    if not candidates:
        return 1
    if options.max_workers is not None:
        return max(1, int(options.max_workers))
    return max(1, min(4, len(candidates), os.cpu_count() or 1))


def _partition_candidates_by_size(
    candidates: Sequence[MatchCandidate],
    options: BatchCompareOptions,
) -> Tuple[List[MatchCandidate], List[MatchCandidate]]:
    """Split candidates into ``(large_lane, normal_lane)``.

    Plan §15.5 (HIGH-2) — the large lane runs serially so we don't pay
    the memory cost of two heavy CAD pairs concurrently, while the
    normal lane keeps full parallelism.  Predicate is the existing
    :func:`_candidate_contains_large_cad` (entity count, byte size, or
    descriptor "entity limit" warning).  The ``predicted_load`` field on
    each candidate is updated as a side-effect so downstream observers
    (telemetry, GUI badges) can read it without re-running the
    predicate.
    """

    large_lane: List[MatchCandidate] = []
    normal_lane: List[MatchCandidate] = []
    for candidate in candidates:
        if _candidate_contains_large_cad(candidate, options):
            candidate.predicted_load = "large"
            large_lane.append(candidate)
        else:
            # Leave "small" as the default; future heuristics may set
            # "medium" via a different code path (e.g. PDF page count).
            if candidate.predicted_load == "large":
                # Defensive: previously classified as large but no
                # longer satisfies the predicate (descriptor refreshed).
                candidate.predicted_load = "small"
            normal_lane.append(candidate)
    return large_lane, normal_lane


def _candidate_contains_large_cad(
    candidate: MatchCandidate,
    options: BatchCompareOptions,
) -> bool:
    threshold = options.comparison_config.large_entity_threshold
    for descriptor in (candidate.source_a, candidate.source_b):
        if not descriptor or descriptor.kind != DrawingKind.CAD:
            continue
        entity_count = sum(int(value) for value in descriptor.entity_counts.values())
        if entity_count >= threshold:
            return True
        if any("entity limit" in warning.lower() for warning in descriptor.warnings):
            return True
        try:
            if descriptor.path_obj.stat().st_size >= LARGE_CAD_FILE_BYTES:
                return True
        except OSError:
            continue
    return False


def compare_candidate(
    candidate: MatchCandidate,
    options: BatchCompareOptions,
    is_cancelled: Optional[Callable[[], bool]] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> ComparisonResult:
    """Run one confirmed candidate comparison."""

    if not candidate.source_a or not candidate.source_b:
        raise ValueError("Candidate must have both A and B sources")
    if not are_compatible(candidate.source_a, candidate.source_b):
        raise ValueError("Incompatible drawing pair")

    path_a = candidate.source_a.path_obj
    path_b = candidate.source_b.path_obj

    if candidate.source_a.kind == DrawingKind.CAD:
        from .dwg_differ import DwgDiffer

        stream_path = None
        stream_pair_id = ""
        if options.compare_state_dir:
            stream_pair_id = _candidate_state_pair_id(candidate)
            stream_path = (
                Path(options.compare_state_dir).resolve()
                / "streams"
                / f"{stream_pair_id}.jsonl"
            )
        return DwgDiffer(
            comparison_config=options.comparison_config,
            dxf_cache_dir=options.dxf_cache_dir,
            change_zone_stream_path=stream_path,
            change_zone_stream_pair_id=stream_pair_id,
            block_text_detection=options.block_text_detection,
        ).compare(
            path_a,
            path_b,
            progress_callback=progress_callback,
            is_cancelled=is_cancelled,
        )

    # Phase H4 — pull per-pair manual page overrides via the lookup
    # callback (populated by the GUI / pipeline layer). Failure to
    # resolve overrides is non-fatal — we revert to pure auto-match.
    pair_overrides: Optional[Sequence[Any]] = None
    lookup = getattr(options, "manual_page_overrides_lookup", None)
    if callable(lookup):
        try:
            pair_uuid = candidate_pair_uuid(candidate)
            pair_overrides = lookup(pair_uuid) or None
        except Exception as exc:  # noqa: BLE001 — defensive, never break compare
            logger.warning(
                "manual_page_overrides_lookup raised %s; ignoring", exc,
            )
            pair_overrides = None

    return compare_pdf_documents(
        path_a,
        path_b,
        options,
        is_cancelled=is_cancelled,
        manual_page_overrides=pair_overrides,
    )


def compare_pdf_documents(
    source_a: Union[str, Path],
    source_b: Union[str, Path],
    options: Optional[BatchCompareOptions] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
    *,
    manual_page_overrides: Optional[Sequence[Any]] = None,
) -> ComparisonResult:
    """Compare PDF documents page-by-page and aggregate the results.

    Phase H2 — when ``options.pdf_page_auto_match`` is True (default) and
    both PDFs hold more than one page, the page-level auto-matcher runs
    BEFORE the diff loop. This recovers reordered/inserted/deleted pages
    so ``A.page_2 ↔ B.page_5`` (same drawing relocated) compares
    correctly instead of garbage-comparing ``A.page_2`` against
    ``B.page_2``.

    Single-page PDFs and the ``pdf_page_auto_match=False`` legacy mode
    fall through to the original sequential ``range(min(N_a, N_b))``
    loop unchanged.
    """

    compare_options = options or BatchCompareOptions()
    source_a = Path(source_a)
    source_b = Path(source_b)
    result = ComparisonResult(source_a=str(source_a), source_b=str(source_b))

    page_count_a = _get_pdf_page_count(source_a)
    page_count_b = _get_pdf_page_count(source_b)
    effective_dpi = _guard_pdf_dpi(source_a, source_b, compare_options.pdf_dpi)

    from .drawing_differ import DrawingDiffer

    # Phase H2 — decide whether to run the page-level matcher.
    use_page_matching = bool(
        compare_options.pdf_page_auto_match
        and compare_options.compare_pdf_all_pages
        and page_count_a > 1
        and page_count_b > 1
    )

    page_match_metadata: Dict[str, Any] = {}
    page_pairs: List[Tuple[int, int, str, float, Dict[str, float]]] = []
    # Each tuple: (page_a, page_b, status, score, breakdown)
    pages_only_in_a: List[int] = []
    pages_only_in_b: List[int] = []

    # Resolve effective override list — explicit kwarg wins, otherwise
    # fall back to the options-level flat list (compare_pdf_documents
    # callers without a BatchCompareJob context).
    effective_overrides: Sequence[Any] = ()
    if manual_page_overrides is not None:
        effective_overrides = manual_page_overrides
    elif compare_options.manual_page_overrides:
        effective_overrides = compare_options.manual_page_overrides

    if use_page_matching:
        try:
            from .page_descriptor import build_per_page_descriptors
            from .page_matcher import (
                PageMatchOptions,
                PageMatchStatus,
                match_pdf_pages,
            )
            desc_a = build_per_page_descriptors(source_a)
            desc_b = build_per_page_descriptors(source_b)
            matcher_options = PageMatchOptions(
                auto_confirm_threshold=compare_options.pdf_page_match_auto_threshold,
                review_threshold=compare_options.pdf_page_match_review_threshold,
            )
            candidates = match_pdf_pages(desc_a, desc_b, options=matcher_options)

            # Phase H4 — apply user-authored overrides on top of the
            # auto-matched candidates. apply_overrides is idempotent on
            # an empty list so the cost is zero when no override exists.
            overrides_applied_count = 0
            if effective_overrides:
                from .manual_page_overrides import apply_overrides
                before_total = len(candidates)
                candidates = apply_overrides(
                    candidates,
                    effective_overrides,
                    n_a=len(desc_a),
                    n_b=len(desc_b),
                )
                overrides_applied_count = len(effective_overrides)
                logger.info(
                    "Applied %d manual page override(s): %d → %d candidates",
                    overrides_applied_count, before_total, len(candidates),
                )

            for c in candidates:
                if c.status == PageMatchStatus.UNMATCHED_A:
                    pages_only_in_a.append(c.page_a_index)
                elif c.status == PageMatchStatus.UNMATCHED_B:
                    pages_only_in_b.append(c.page_b_index)
                elif c.is_matched:
                    # Apply_overrides marks manual entries with a
                    # ``manual_override`` key in score_breakdown — surface
                    # that as a distinct per-pair status string so the
                    # GUI/metadata can label them as user-confirmed.
                    is_manual = "manual_override" in c.score_breakdown
                    pair_status = "manual_override" if is_manual else c.status.value
                    page_pairs.append((
                        c.page_a_index, c.page_b_index, pair_status,
                        c.score, dict(c.score_breakdown),
                    ))
            # Sort by A page index for deterministic compare order.
            page_pairs.sort(key=lambda t: t[0])
            page_match_metadata = {
                "page_match_enabled": True,
                "page_match_pairs_total": len(page_pairs),
                "page_match_auto_confirmed": sum(
                    1 for _, _, s, _, _ in page_pairs if s == "auto_confirmed"
                ),
                "page_match_review_required": sum(
                    1 for _, _, s, _, _ in page_pairs if s == "review_required"
                ),
                "page_match_manual_overrides": sum(
                    1 for _, _, s, _, _ in page_pairs if s == "manual_override"
                ),
                "page_match_overrides_applied": overrides_applied_count,
                "page_match_pairs": [
                    {
                        "page_a": pa, "page_b": pb,
                        "status": st, "score": round(sc, 4),
                    }
                    for pa, pb, st, sc, _ in page_pairs
                ],
            }
            logger.info(
                "PDF page-match: %d pairs (%d auto, %d review, %d override), "
                "unmatched A=%d, B=%d",
                len(page_pairs),
                page_match_metadata["page_match_auto_confirmed"],
                page_match_metadata["page_match_review_required"],
                page_match_metadata["page_match_manual_overrides"],
                len(pages_only_in_a), len(pages_only_in_b),
            )
        except Exception as exc:
            logger.exception(
                "Page-level matching failed, falling back to sequential: %s", exc,
            )
            use_page_matching = False
            page_match_metadata = {
                "page_match_enabled": False,
                "page_match_fallback_reason": f"{exc.__class__.__name__}: {exc}",
            }

    # Build the pair list — either from the matcher or sequential fallback
    if not use_page_matching:
        # Legacy / single-page path
        common_pages = min(page_count_a, page_count_b)
        sequential_range = range(common_pages) if compare_options.compare_pdf_all_pages else range(min(1, common_pages))
        page_pairs = [
            (page, page, "sequential", 1.0, {})
            for page in sequential_range
        ]
        if page_count_a > page_count_b:
            pages_only_in_a = list(range(page_count_b, page_count_a))
        elif page_count_b > page_count_a:
            pages_only_in_b = list(range(page_count_a, page_count_b))
        page_match_metadata.setdefault("page_match_enabled", False)

    pages_compared = 0
    for page_a, page_b, status, score, breakdown in page_pairs:
        if is_cancelled and is_cancelled():
            result.warnings.append("PDF comparison cancelled")
            break
        differ = DrawingDiffer(
            config={
                "alignment_enabled": compare_options.pdf_auto_align,
                "text_extraction": compare_options.pdf_text_compare,
                "dpi": effective_dpi,
                # Phase O5 — Codex review RV-20260507-003 fix: thread
                # the user-selected strength preset into DrawingDiffer
                # so the dialog's "PDF 시각 비교 강도" combo actually
                # affects this page's morphology/sigma_k/blob filter.
                "noise_filter_strength":
                    compare_options.pdf_noise_filter_strength,
                "ocr_fallback": bool(compare_options.use_ocr_fallback),
            }
        )
        page_result = differ.compare(
            source_a, source_b,
            page_a=page_a, page_b=page_b,
        )
        pages_compared += 1
        for change in page_result.changes:
            # Use page_a as the canonical "page" for the existing
            # metadata field (back-compat with viewer code that reads
            # metadata["page"]). Add page_a / page_b explicitly so
            # downstream consumers can show "A.page_2 ↔ B.page_5".
            metadata = _pdf_change_metadata(change, page=page_a, dpi=effective_dpi)
            metadata["page_a"] = page_a
            metadata["page_b"] = page_b
            if status != "sequential":
                metadata["page_match_status"] = status
                metadata["page_match_score"] = round(score, 4)
                metadata["page_match_breakdown"] = breakdown
            if metadata.get("source_format") == "pdf" and not metadata.get("bbox"):
                metadata["bbox"] = _pdf_page_pixel_bbox(source_b, page_b, effective_dpi)
                metadata["bbox_fallback"] = "page"
                metadata["bbox_status"] = "page_fallback"
            # Key uses (page_a, page_b) so cross-page changes don't
            # collide with sequential-mode keys from a previous run
            # against the same source.
            if page_a == page_b:
                key = f"page_{page_a}_{change.key}"
                location = f"page {page_a}: {change.location}" if change.location else f"page {page_a}"
            else:
                key = f"page_a{page_a}_b{page_b}_{change.key}"
                location = (
                    f"page A#{page_a}↔B#{page_b}: {change.location}"
                    if change.location
                    else f"page A#{page_a}↔B#{page_b}"
                )
            result.add_change(
                ChangeRecord(
                    key=key,
                    change_type=change.change_type,
                    field_name=change.field_name,
                    old_value=change.old_value,
                    new_value=change.new_value,
                    location=location,
                    metadata=metadata,
                )
            )
        result.warnings.extend(page_result.warnings)

    # Pages present only on one side — same metadata shape as before so
    # the viewer keeps treating them as page-level ADDED/DELETED markers.
    for page in pages_only_in_a:
        result.add_change(
            ChangeRecord(
                key=f"page_{page}_deleted",
                change_type=ChangeType.DELETED,
                location=f"page {page}",
                metadata={
                    "page": page,
                    "page_a": page,
                    "page_b": -1,
                    "pdf_page": page,
                    "pdf_dpi": effective_dpi,
                    "reason": "Page exists only in A (no match in B)",
                    "source_format": "pdf",
                    "detection_source": "pdf_visual",
                    "bbox_status": "page_fallback",
                    "bbox_coordinate_space": "image_pixels",
                    "entity_type": "PDF_PAGE",
                    "layer": f"PDF_PAGE_{page + 1}",
                    "bbox": _pdf_page_pixel_bbox(source_a, page, effective_dpi),
                    "page_match_status": "unmatched_a" if use_page_matching else "page_count_excess_a",
                },
            )
        )
    for page in pages_only_in_b:
        result.add_change(
            ChangeRecord(
                key=f"page_{page}_added",
                change_type=ChangeType.ADDED,
                location=f"page {page}",
                metadata={
                    "page": page,
                    "page_a": -1,
                    "page_b": page,
                    "pdf_page": page,
                    "pdf_dpi": effective_dpi,
                    "reason": "Page exists only in B (no match in A)",
                    "source_format": "pdf",
                    "detection_source": "pdf_visual",
                    "bbox_status": "page_fallback",
                    "bbox_coordinate_space": "image_pixels",
                    "entity_type": "PDF_PAGE",
                    "layer": f"PDF_PAGE_{page + 1}",
                    "bbox": _pdf_page_pixel_bbox(source_b, page, effective_dpi),
                    "page_match_status": "unmatched_b" if use_page_matching else "page_count_excess_b",
                },
            )
        )

    result.metadata.update(
        {
            "comparison_type": "PDF",
            "page_count_a": page_count_a,
            "page_count_b": page_count_b,
            "pages_compared": pages_compared,
            "requested_dpi": compare_options.pdf_dpi,
            "effective_dpi": effective_dpi,
            "ocr_fallback_enabled": bool(compare_options.use_ocr_fallback),
            **page_match_metadata,
        }
    )
    return result


def _pdf_change_metadata(change: ChangeRecord, *, page: int, dpi: int) -> Dict[str, Any]:
    metadata = dict(change.metadata or {})
    metadata["page"] = page
    metadata["pdf_page"] = page
    metadata["pdf_dpi"] = dpi
    metadata["source_format"] = "pdf"
    metadata["bbox_coordinate_space"] = "image_pixels"
    metadata.setdefault("layer", f"PDF_PAGE_{page + 1}")
    if _metadata_has_region_bbox(metadata):
        metadata["entity_type"] = metadata.get("entity_type") or "PDF_REGION"
        metadata["change_category"] = metadata.get("change_category") or "visual"
        metadata["detection_source"] = metadata.get("detection_source") or "pdf_visual"
        metadata["bbox_status"] = metadata.get("bbox_status") or "exact"
        try:
            x = float(metadata["x"])
            y = float(metadata["y"])
            w = float(metadata["w"])
            h = float(metadata["h"])
            metadata["bbox"] = [x, y, x + w, y + h]
        except Exception:
            pass
    elif change.field_name or str(change.key or "").startswith("Text_"):
        metadata["entity_type"] = metadata.get("entity_type") or "PDF_TEXT"
        metadata["change_category"] = metadata.get("change_category") or "text"
        metadata["detection_source"] = metadata.get("detection_source") or (
            "pdf_ocr" if metadata.get("ocr_confidence") else "pdf_text"
        )
        bbox = _bbox_from_pdf_change_location(change.location)
        if bbox:
            metadata["bbox"] = list(bbox)
            metadata["bbox_status"] = metadata.get("bbox_status") or "exact"
    else:
        metadata["entity_type"] = metadata.get("entity_type") or "PDF_CHANGE"
        metadata["detection_source"] = metadata.get("detection_source") or "pdf_visual"
    return metadata


def _metadata_has_region_bbox(metadata: Dict[str, Any]) -> bool:
    return all(metadata.get(key) is not None for key in ("x", "y", "w", "h"))


def _bbox_from_pdf_change_location(location: Any) -> Optional[Tuple[float, float, float, float]]:
    numbers = [float(value) for value in re.findall(r"[-+]?\d+(?:\.\d+)?", str(location or ""))]
    if len(numbers) < 4:
        return None
    x1, y1, x2, y2 = numbers[-4:]
    return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def _pdf_page_pixel_bbox(source: Path, page_index: int, dpi: int) -> list[float]:
    try:
        import fitz  # type: ignore

        doc = fitz.open(str(source))
        try:
            if page_index < 0 or page_index >= len(doc):
                return [0.0, 0.0, 1000.0, 1000.0]
            page = doc[page_index]
            scale = max(float(dpi), 1.0) / 72.0
            return [0.0, 0.0, float(page.rect.width) * scale, float(page.rect.height) * scale]
        finally:
            doc.close()
    except Exception:
        return [0.0, 0.0, 1000.0, 1000.0]


def _coerce_input_paths(
    inputs: Union[str, Path, Sequence[Union[str, Path]]],
    recursive: bool,
) -> List[Path]:
    if isinstance(inputs, (str, Path)):
        raw_inputs: Sequence[Union[str, Path]] = [inputs]
    else:
        raw_inputs = inputs

    paths: List[Path] = []
    for raw in raw_inputs:
        path = Path(raw)
        if path.is_dir():
            iterator = path.rglob("*") if recursive else path.iterdir()
            paths.extend(
                item.resolve()
                for item in iterator
                if item.is_file() and item.suffix.lower() in SUPPORTED_DRAWING_EXTENSIONS
            )
        elif path.is_file() and path.suffix.lower() in SUPPORTED_DRAWING_EXTENSIONS:
            paths.append(path.resolve())

    return sorted(set(paths), key=lambda p: str(p).lower())


def _common_root(paths: Sequence[Path]) -> Optional[Path]:
    if not paths:
        return None
    try:
        import os

        return Path(os.path.commonpath([str(path.parent) for path in paths])).resolve()
    except Exception:
        return paths[0].parent.resolve()


def _kind_for_extension(extension: str) -> DrawingKind:
    if extension in CAD_EXTENSIONS:
        return DrawingKind.CAD
    if extension in PDF_EXTENSIONS:
        return DrawingKind.PDF
    raise ValueError(f"Unsupported drawing extension: {extension}")


def _fill_cad_descriptor(
    descriptor: DrawingFileDescriptor,
    options: DescriptorBuildOptions,
) -> None:
    try:
        import ezdxf
    except ImportError as exc:
        descriptor.warnings = descriptor.warnings + ("ezdxf unavailable",)
        raise exc

    source_path = descriptor.path_obj
    temp_differ = None
    dxf_path = source_path
    if source_path.suffix.lower() == ".dwg":
        from .dwg_differ import DwgDiffer

        temp_differ = DwgDiffer(dxf_cache_dir=options.dxf_cache_dir)
        dxf_path = temp_differ._ensure_dxf(source_path)

    try:
        doc = ezdxf.readfile(str(dxf_path))
        descriptor.layers = tuple(sorted(layer.dxf.name for layer in doc.layers))
        descriptor.layouts = tuple(sorted(layout.name for layout in doc.layouts))

        entity_counts: Dict[str, int] = {}
        text_values: List[str] = []
        xs: List[float] = []
        ys: List[float] = []
        processed = 0

        for entity in doc.modelspace():
            if processed >= options.max_cad_entities:
                descriptor.warnings = descriptor.warnings + ("CAD descriptor entity limit reached",)
                break
            processed += 1
            entity_type = entity.dxftype()
            entity_counts[entity_type] = entity_counts.get(entity_type, 0) + 1
            _collect_entity_text(entity, text_values)
            _collect_entity_points(entity, xs, ys)

        descriptor.entity_counts = entity_counts
        descriptor.text_hints = tuple(_select_text_hints(text_values, options.max_text_chars))
        if xs and ys:
            descriptor.bbox = (min(xs), min(ys), max(xs), max(ys))

        descriptor.content_fingerprint = _hash_values(
            descriptor.identity.match_key,
            ",".join(descriptor.layers),
            ",".join(descriptor.layouts),
            json.dumps(entity_counts, sort_keys=True),
            " ".join(descriptor.text_hints),
            str(descriptor.bbox),
        )
    finally:
        if temp_differ is not None:
            temp_differ._cleanup_temp()


def _fill_pdf_descriptor(
    descriptor: DrawingFileDescriptor,
    options: DescriptorBuildOptions,
) -> None:
    try:
        import fitz
    except ImportError as exc:
        descriptor.warnings = descriptor.warnings + ("PyMuPDF unavailable",)
        raise exc

    doc = fitz.open(str(descriptor.path_obj))
    try:
        descriptor.page_count = len(doc)
        texts: List[str] = []
        if len(doc):
            rect = doc[0].rect
            descriptor.page_size = (round(rect.width, 2), round(rect.height, 2))
            descriptor.visual_fingerprint = _pdf_thumbnail_hash(doc[0])

        for page_index in range(min(len(doc), options.pdf_text_pages)):
            text = doc[page_index].get_text("text")
            if text:
                texts.append(text)

        if not texts and options.use_ocr_fallback:
            try:
                from .ocr_extractor import OCRExtractor

                ocr_result = OCRExtractor().extract(str(descriptor.path_obj))
                if ocr_result.full_text:
                    texts.append(ocr_result.full_text)
            except Exception as exc:
                descriptor.warnings = descriptor.warnings + (f"OCR fallback failed: {exc}",)

        descriptor.text_hints = tuple(_select_text_hints(texts, options.max_text_chars))
        descriptor.content_fingerprint = _hash_values(
            descriptor.identity.match_key,
            str(descriptor.page_count),
            str(descriptor.page_size),
            descriptor.visual_fingerprint,
            " ".join(descriptor.text_hints),
        )
    finally:
        doc.close()


def _collect_entity_text(entity: Any, values: List[str]) -> None:
    try:
        entity_type = entity.dxftype()
        if entity_type == "TEXT":
            text = getattr(entity.dxf, "text", "")
            if text:
                values.append(str(text))
        elif entity_type == "MTEXT":
            text = entity.text if hasattr(entity, "text") else getattr(entity, "plain_text", lambda: "")()
            if text:
                values.append(str(text))
        elif entity_type == "INSERT":
            for attrib in getattr(entity, "attribs", []):
                text = getattr(attrib.dxf, "text", "")
                tag = getattr(attrib.dxf, "tag", "")
                if text:
                    values.append(f"{tag}:{text}")
    except Exception:
        return


def _collect_entity_points(entity: Any, xs: List[float], ys: List[float]) -> None:
    points: List[Any] = []
    try:
        entity_type = entity.dxftype()
        if entity_type == "LINE":
            points = [entity.dxf.start, entity.dxf.end]
        elif entity_type in {"CIRCLE", "ARC"}:
            center = entity.dxf.center
            radius = float(getattr(entity.dxf, "radius", 0.0))
            points = [
                (center[0] - radius, center[1] - radius),
                (center[0] + radius, center[1] + radius),
            ]
        elif entity_type in {"TEXT", "MTEXT", "INSERT"}:
            point = getattr(entity.dxf, "insert", None)
            if point is not None:
                points = [point]
        elif entity_type in {"LWPOLYLINE", "POLYLINE"}:
            try:
                points = list(entity.get_points())
            except Exception:
                points = list(entity.points())
    except Exception:
        points = []

    for point in points:
        try:
            xs.append(float(point[0]))
            ys.append(float(point[1]))
        except Exception:
            continue


def _select_text_hints(values: Iterable[str], max_chars: int) -> List[str]:
    output: List[str] = []
    total = 0
    seen = set()
    for raw in values:
        text = normalize_text(raw)
        if not text or text in seen:
            continue
        seen.add(text)
        if total + len(text) > max_chars:
            remaining = max_chars - total
            if remaining > 20:
                output.append(text[:remaining])
            break
        output.append(text)
        total += len(text)
    return output


def _enrich_identity_from_hints(descriptor: DrawingFileDescriptor) -> None:
    """Fill drawing number/sheet from extracted title block or PDF text hints."""

    if descriptor.identity.drawing_number and descriptor.identity.sheet:
        return

    hint_text = normalize_text(" ".join(descriptor.title_hints + descriptor.text_hints))
    if not hint_text:
        return

    drawing_number = descriptor.identity.drawing_number or _extract_drawing_number(hint_text)
    sheet = descriptor.identity.sheet or _extract_sheet_token(hint_text)
    if (
        drawing_number == descriptor.identity.drawing_number
        and sheet == descriptor.identity.sheet
    ):
        return

    descriptor.identity = FilenameIdentity(
        original_stem=descriptor.identity.original_stem,
        match_key=descriptor.identity.match_key,
        tokens=descriptor.identity.tokens,
        revision=descriptor.identity.revision,
        drawing_number=drawing_number,
        sheet=sheet,
    )


def _pdf_thumbnail_hash(page: Any) -> str:
    try:
        import fitz
        from PIL import Image
        import numpy as np
    except ImportError:
        return ""

    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(0.2, 0.2), alpha=False)
        image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        image = image.convert("L").resize((8, 8))
        values = np.asarray(image, dtype="float32")
        average = float(values.mean())
        bits = "".join("1" if value >= average else "0" for value in values.flatten())
        return f"{int(bits, 2):016x}"
    except Exception:
        return ""


def _get_pdf_page_count(path: Path) -> int:
    try:
        import fitz
    except ImportError as exc:
        raise ImportError("PyMuPDF is required for PDF comparison") from exc

    doc = fitz.open(str(path))
    try:
        return len(doc)
    finally:
        doc.close()


def _guard_pdf_dpi(source_a: Path, source_b: Path, requested_dpi: int) -> int:
    """Reduce DPI for very large pages to avoid excessive raster memory."""
    max_pixels = 24_000_000
    largest_area = 0.0
    try:
        import fitz
    except ImportError:
        return requested_dpi

    for path in (source_a, source_b):
        doc = fitz.open(str(path))
        try:
            for page_index in range(len(doc)):
                rect = doc[page_index].rect
                largest_area = max(largest_area, float(rect.width * rect.height))
        finally:
            doc.close()

    if largest_area <= 0:
        return requested_dpi

    pixels = largest_area * (requested_dpi / 72.0) ** 2
    if pixels <= max_pixels:
        return requested_dpi

    guarded = int(72.0 * math.sqrt(max_pixels / largest_area))
    return max(72, min(requested_dpi, guarded))


def _assign_unique_pairs(
    count_a: int,
    count_b: int,
    pair_scores: Dict[Tuple[int, int], MatchCandidate],
) -> List[Tuple[int, int]]:
    if not pair_scores:
        return []

    try:
        from scipy.optimize import linear_sum_assignment

        matrix = [[1.0 for _ in range(count_b)] for _ in range(count_a)]
        for (i, j), candidate in pair_scores.items():
            matrix[i][j] = 1.0 - candidate.score
        rows, cols = linear_sum_assignment(matrix)
        assignments = [
            (int(i), int(j))
            for i, j in zip(rows, cols)
            if (int(i), int(j)) in pair_scores
        ]
        return sorted(assignments, key=lambda pair: pair_scores[pair].score, reverse=True)
    except Exception:
        used_a = set()
        used_b = set()
        assignments = []
        for (i, j), candidate in sorted(
            pair_scores.items(), key=lambda item: (-item[1].score, item[0][0], item[0][1])
        ):
            if i in used_a or j in used_b:
                continue
            assignments.append((i, j))
            used_a.add(i)
            used_b.add(j)
        return assignments


def _status_for_candidate(
    i: int,
    j: int,
    pair_scores: Dict[Tuple[int, int], MatchCandidate],
    options: MatchingOptions,
) -> MatchStatus:
    candidate = pair_scores[(i, j)]
    nearest_competitor = _nearest_competing_score(i, j, pair_scores)
    ambiguous = nearest_competitor > 0.0 and (candidate.score - nearest_competitor) < options.ambiguous_delta
    exact_unique_drawing_number = (
        bool(candidate.source_a)
        and bool(candidate.source_b)
        and _same_drawing_number(candidate.source_a.identity, candidate.source_b.identity)
        and not _has_same_drawing_number_competitor(i, j, pair_scores)
    )
    if candidate.score >= options.auto_threshold and (not ambiguous or exact_unique_drawing_number):
        return MatchStatus.AUTO_CONFIRMED
    return MatchStatus.REVIEW_REQUIRED


def _nearest_competing_score(
    i: int,
    j: int,
    pair_scores: Dict[Tuple[int, int], MatchCandidate],
) -> float:
    competing_scores = [
        other.score
        for (other_i, other_j), other in pair_scores.items()
        if (other_i == i or other_j == j) and (other_i, other_j) != (i, j)
    ]
    return max(competing_scores) if competing_scores else 0.0


def _has_same_drawing_number_competitor(
    i: int,
    j: int,
    pair_scores: Dict[Tuple[int, int], MatchCandidate],
) -> bool:
    candidate = pair_scores[(i, j)]
    if not candidate.source_a or not candidate.source_b:
        return False
    for (other_i, other_j), other in pair_scores.items():
        if (other_i, other_j) == (i, j) or (other_i != i and other_j != j):
            continue
        if other_i == i and other.source_b and _same_drawing_number(candidate.source_a.identity, other.source_b.identity):
            return True
        if other_j == j and other.source_a and _same_drawing_number(other.source_a.identity, candidate.source_b.identity):
            return True
    return False


def _match_alternates(
    i: int,
    selected_j: Optional[int],
    pair_scores: Dict[Tuple[int, int], MatchCandidate],
    limit: int,
) -> List[MatchAlternative]:
    if limit <= 0:
        return []
    alternatives: List[MatchAlternative] = []
    for (other_i, other_j), candidate in sorted(
        pair_scores.items(),
        key=lambda item: (
            -item[1].score,
            item[1].source_b.name if item[1].source_b else "",
        ),
    ):
        if other_i != i or other_j == selected_j or candidate.source_b is None:
            continue
        alternatives.append(
            MatchAlternative(
                source_b=candidate.source_b,
                score=candidate.score,
                reasons=list(candidate.reasons),
                component_scores=dict(candidate.component_scores),
            )
        )
        if len(alternatives) >= limit:
            break
    return alternatives


def _append_match_diagnostics(
    candidate: MatchCandidate,
    i: int,
    j: int,
    pair_scores: Dict[Tuple[int, int], MatchCandidate],
    options: MatchingOptions,
) -> None:
    diagnostics: List[str] = []
    nearest_competitor = _nearest_competing_score(i, j, pair_scores)
    exact_unique_drawing_number = (
        bool(candidate.source_a)
        and bool(candidate.source_b)
        and _same_drawing_number(candidate.source_a.identity, candidate.source_b.identity)
        and not _has_same_drawing_number_competitor(i, j, pair_scores)
    )
    if (
        nearest_competitor > 0.0
        and (candidate.score - nearest_competitor) < options.ambiguous_delta
        and not exact_unique_drawing_number
    ):
        diagnostics.append(
            "review reason: ambiguous competing candidate "
            f"(nearest={nearest_competitor:.2f}, delta={candidate.score - nearest_competitor:.2f})"
        )
    if candidate.score < options.auto_threshold:
        diagnostics.append(
            "review reason: confidence below auto threshold "
            f"({candidate.score:.2f} < {options.auto_threshold:.2f})"
        )
    if (
        candidate.source_a
        and candidate.source_b
        and _same_drawing_number(candidate.source_a.identity, candidate.source_b.identity)
        and candidate.source_a.identity.match_key != candidate.source_b.identity.match_key
    ):
        diagnostics.append("review hint: filename identity differs while drawing number matches")
    if (
        candidate.source_a
        and candidate.source_b
        and _project_drawing_code_mismatch(candidate.source_a.identity, candidate.source_b.identity)
    ):
        diagnostics.append("review reason: drawing code mismatch")

    for diagnostic in diagnostics:
        if diagnostic not in candidate.reasons:
            candidate.reasons.append(diagnostic)


def _unmatched_a_reasons(
    desc_a: DrawingFileDescriptor,
    descriptors_b: Sequence[DrawingFileDescriptor],
) -> List[str]:
    reasons = ["No compatible B file exceeded the review threshold"]
    blocked = _blocked_identity_count(desc_a, descriptors_b)
    if blocked:
        reasons.append(f"diagnostic: {blocked} incompatible B file(s) blocked by format")
    mismatched = _project_drawing_code_mismatch_count(desc_a, descriptors_b)
    if mismatched:
        reasons.append(
            f"diagnostic: {mismatched} compatible B candidate(s) rejected by drawing code mismatch"
        )
    return reasons


def _unmatched_b_reasons(
    desc_b: DrawingFileDescriptor,
    descriptors_a: Sequence[DrawingFileDescriptor],
) -> List[str]:
    reasons = ["No compatible A file exceeded the review threshold"]
    blocked = _blocked_identity_count(desc_b, descriptors_a)
    if blocked:
        reasons.append(f"diagnostic: {blocked} incompatible A file(s) blocked by format")
    mismatched = _project_drawing_code_mismatch_count(desc_b, descriptors_a)
    if mismatched:
        reasons.append(
            f"diagnostic: {mismatched} compatible A candidate(s) rejected by drawing code mismatch"
        )
    return reasons


def _blocked_identity_count(
    descriptor: DrawingFileDescriptor,
    others: Sequence[DrawingFileDescriptor],
) -> int:
    count = 0
    for other in others:
        if are_compatible(descriptor, other):
            continue
        same_identity = (
            descriptor.identity.match_key
            and descriptor.identity.match_key == other.identity.match_key
        )
        same_number = (
            _same_drawing_number(descriptor.identity, other.identity)
        )
        if same_identity or same_number:
            count += 1
    return count


def _project_drawing_code_mismatch_count(
    descriptor: DrawingFileDescriptor,
    others: Sequence[DrawingFileDescriptor],
) -> int:
    return sum(
        1
        for other in others
        if are_compatible(descriptor, other)
        and _project_drawing_code_mismatch(descriptor.identity, other.identity)
    )


def _same_drawing_number(a: FilenameIdentity, b: FilenameIdentity) -> bool:
    a_key = _drawing_number_key(a.drawing_number)
    b_key = _drawing_number_key(b.drawing_number)
    if not a_key or not b_key or a_key != b_key:
        return False
    return not _is_broad_drawing_prefix(a_key)


def _project_drawing_code_mismatch(a: FilenameIdentity, b: FilenameIdentity) -> bool:
    a_code = _project_drawing_code(a.drawing_number)
    b_code = _project_drawing_code(b.drawing_number)
    return bool(a_code and b_code and a_code != b_code)


def _same_project_drawing_code(a: FilenameIdentity, b: FilenameIdentity) -> bool:
    a_code = _project_drawing_code(a.drawing_number)
    b_code = _project_drawing_code(b.drawing_number)
    return bool(a_code and b_code and a_code == b_code)


def _project_drawing_code(number: Optional[str]) -> Optional[str]:
    if not number:
        return None
    text = normalize_text(number)
    separated = re.fullmatch(PROJECT_DRAWING_NUMBER_PATTERN, text)
    if separated:
        return f"{separated.group(1)}-{separated.group(2)}"
    compact = re.sub(r"[^A-Z0-9]", "", text)
    compact_match = re.fullmatch(r"([A-Z]{1,4}[0-9]{2})([0-9]{3,5}[A-Z]?)", compact)
    if compact_match:
        return f"{compact_match.group(1)}-{compact_match.group(2)}"
    return None


def _drawing_number_key(number: Optional[str]) -> str:
    project_code = _project_drawing_code(number)
    if project_code:
        return project_code
    return re.sub(r"[^A-Z0-9]", "", normalize_text(number or ""))


def _is_broad_drawing_prefix(number: str) -> bool:
    compact = re.sub(r"[^A-Z0-9]", "", normalize_text(number))
    return bool(re.fullmatch(r"[A-Z]{1,4}[0-9]{2}", compact))


def _name_score(a: FilenameIdentity, b: FilenameIdentity) -> float:
    if _project_drawing_code_mismatch(a, b):
        return 0.0
    if a.match_key and a.match_key == b.match_key:
        return 1.0
    scores = [
        SequenceMatcher(None, a.match_key, b.match_key).ratio()
        if a.match_key and b.match_key
        else 0.0,
        _jaccard(a.tokens, b.tokens),
    ]
    if _same_drawing_number(a, b):
        scores.append(1.0)
    return max(scores)


def _sheet_score(a: FilenameIdentity, b: FilenameIdentity) -> float:
    if not a.sheet and not b.sheet:
        return 1.0
    if a.sheet and b.sheet and a.sheet == b.sheet:
        return 1.0
    if a.sheet and b.sheet:
        return SequenceMatcher(None, a.sheet, b.sheet).ratio()
    return 0.0


def _text_score(a: DrawingFileDescriptor, b: DrawingFileDescriptor) -> float:
    text_a = a.text_fingerprint
    text_b = b.text_fingerprint
    if not text_a and not text_b:
        return 0.5
    if not text_a or not text_b:
        return 0.0
    return SequenceMatcher(None, text_a, text_b).ratio()


def _content_score(a: DrawingFileDescriptor, b: DrawingFileDescriptor) -> float:
    if a.kind == DrawingKind.CAD:
        count_score = _entity_count_similarity(a.entity_counts, b.entity_counts)
        layer_score = _jaccard(a.layers, b.layers) if a.layers or b.layers else 0.5
        layout_score = _jaccard(a.layouts, b.layouts) if a.layouts or b.layouts else 0.5
        bbox_score = _bbox_similarity(a.bbox, b.bbox)
        return 0.45 * count_score + 0.25 * layer_score + 0.15 * layout_score + 0.15 * bbox_score

    text_score = _text_score(a, b)
    visual_score = _hash_similarity(a.visual_fingerprint, b.visual_fingerprint)
    page_score = 1.0 if a.page_count == b.page_count else 0.0
    if a.page_count and b.page_count:
        page_score = min(a.page_count, b.page_count) / max(a.page_count, b.page_count)
    return 0.45 * visual_score + 0.35 * text_score + 0.20 * page_score


def _relative_path_score(a_path: str, b_path: str) -> float:
    a_parent = normalize_text(str(Path(a_path).parent))
    b_parent = normalize_text(str(Path(b_path).parent))
    if a_parent == "." and b_parent == ".":
        return 0.5
    if a_parent == b_parent:
        return 1.0
    return SequenceMatcher(None, a_parent, b_parent).ratio()


def _entity_count_similarity(a_counts: Dict[str, int], b_counts: Dict[str, int]) -> float:
    if not a_counts and not b_counts:
        return 0.5
    keys = sorted(set(a_counts) | set(b_counts))
    dot = sum(a_counts.get(key, 0) * b_counts.get(key, 0) for key in keys)
    norm_a = math.sqrt(sum(a_counts.get(key, 0) ** 2 for key in keys))
    norm_b = math.sqrt(sum(b_counts.get(key, 0) ** 2 for key in keys))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _bbox_similarity(
    a_bbox: Optional[Tuple[float, float, float, float]],
    b_bbox: Optional[Tuple[float, float, float, float]],
) -> float:
    if not a_bbox and not b_bbox:
        return 0.5
    if not a_bbox or not b_bbox:
        return 0.0

    a_w = max(0.0, a_bbox[2] - a_bbox[0])
    a_h = max(0.0, a_bbox[3] - a_bbox[1])
    b_w = max(0.0, b_bbox[2] - b_bbox[0])
    b_h = max(0.0, b_bbox[3] - b_bbox[1])
    width_score = _ratio_similarity(a_w, b_w)
    height_score = _ratio_similarity(a_h, b_h)
    return (width_score + height_score) / 2.0


def _ratio_similarity(a_value: float, b_value: float) -> float:
    if a_value == 0 and b_value == 0:
        return 1.0
    if a_value == 0 or b_value == 0:
        return 0.0
    return min(a_value, b_value) / max(a_value, b_value)


def _hash_similarity(a_hash: str, b_hash: str) -> float:
    if not a_hash and not b_hash:
        return 0.5
    if not a_hash or not b_hash:
        return 0.0
    if a_hash == b_hash:
        return 1.0
    if len(a_hash) == len(b_hash) and re.fullmatch(r"[0-9a-fA-F]+", a_hash + b_hash):
        bits = len(a_hash) * 4
        distance = (int(a_hash, 16) ^ int(b_hash, 16)).bit_count()
        return max(0.0, 1.0 - distance / bits)
    return 0.0


def _jaccard(a_values: Iterable[str], b_values: Iterable[str]) -> float:
    a_set = {normalize_text(value) for value in a_values if normalize_text(value)}
    b_set = {normalize_text(value) for value in b_values if normalize_text(value)}
    if not a_set and not b_set:
        return 0.5
    if not a_set or not b_set:
        return 0.0
    return len(a_set & b_set) / len(a_set | b_set)


def _extract_sheet_token(text: str) -> Optional[str]:
    patterns = [
        r"\bB[0-9]{1,2}F?\b",
        r"\b[0-9]{1,2}F\b",
        r"\bRF\b",
        r"\bPH[0-9]+\b",
        r"\b[A-Z]+[ _.-]*[0-9]{1,3}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return re.sub(r"[^A-Z0-9]", "", match.group(0))
    return None


def _extract_drawing_number(text: str) -> Optional[str]:
    text_without_revision = re.sub(REVISION_PATTERN, " ", text)
    project_codes = [
        f"{match.group(1)}-{match.group(2)}"
        for match in re.finditer(PROJECT_DRAWING_NUMBER_PATTERN, text_without_revision)
    ]
    if project_codes:
        return max(project_codes, key=len)

    for token in re.findall(r"[A-Z0-9]+", text_without_revision):
        compact_project = re.fullmatch(
            r"([A-Z]{1,4}[0-9]{2})([0-9]{3,5}[A-Z]?)",
            token,
        )
        if compact_project:
            return f"{compact_project.group(1)}-{compact_project.group(2)}"

    separated_candidates = []
    for match in re.finditer(
        r"(?<![A-Z0-9])([A-Z]{1,4})[-_. ]+([0-9]{1,5}[A-Z]?)(?![A-Z0-9])",
        text_without_revision,
    ):
        candidate = f"{match.group(1)}{match.group(2)}"
        if not _is_broad_drawing_prefix(candidate):
            separated_candidates.append(candidate)
    if separated_candidates:
        return max(separated_candidates, key=len)

    compact_candidates = []
    for token in re.findall(r"[A-Z0-9]+", text_without_revision):
        generic = re.fullmatch(r"([A-Z]{1,4})([0-9]{3,5}[A-Z]?)", token)
        if generic:
            compact_candidates.append(f"{generic.group(1)}{generic.group(2)}")
    if compact_candidates:
        return max(compact_candidates, key=len)
    return None


def _hash_values(*values: str) -> str:
    digest = hashlib.sha1()
    for value in values:
        digest.update(str(value).encode("utf-8", errors="ignore"))
        digest.update(b"\0")
    return digest.hexdigest()


def _candidate_label(candidate: MatchCandidate) -> str:
    label = candidate_display_label(candidate, "")
    if label:
        return label
    a_name = candidate.source_a.name if candidate.source_a else "-"
    b_name = candidate.source_b.name if candidate.source_b else "-"
    return f"{a_name} vs {b_name}"


def _candidate_state_pair_id(candidate: MatchCandidate) -> str:
    return candidate_pair_uuid(candidate)
