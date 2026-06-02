"""Select AC1018/AC1021 real before-after candidates for ADR-004 Phase 0-C.

This evidence script expands beyond same-folder matching. It scans local DWG
headers for AC1018/AC1021 files, compares candidates across folders, classifies
the result, and writes a JSON/Markdown packet. It does not read geometry and
does not invoke any converter.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
TARGET_DWG_CODES = ("AC1018", "AC1021")
DEFAULT_MIN_SIMILARITY = 0.52
DEFAULT_MAX_CANDIDATES_PER_VERSION = 12

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.select_adr004_compact_compare_candidates import (  # noqa: E402
    DwgSample,
    collect_dwg_samples,
)


@dataclass(frozen=True)
class VersionClassification:
    status: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"status": self.status, "reason": self.reason}


def discover_ac1018_ac1021_roots(*, drive_root: Path = Path("D:/"), include_drive_cad_roots: bool = False) -> list[Path]:
    roots: list[Path] = []
    if drive_root.exists():
        for item in sorted(drive_root.iterdir(), key=lambda path: path.name):
            if not item.is_dir():
                continue
            name = item.name
            if "서울" in name or "에코" in name or "PSRC" in name:
                roots.append(item)
            elif include_drive_cad_roots and _looks_like_cad_root(name):
                roots.append(item)
    return _dedupe_paths(roots)


def build_report(
    roots: Sequence[Path] | None = None,
    *,
    target_versions: Sequence[str] = TARGET_DWG_CODES,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    max_candidates_per_version: int = DEFAULT_MAX_CANDIDATES_PER_VERSION,
    include_drive_cad_roots: bool = False,
) -> dict[str, Any]:
    roots = list(roots or discover_ac1018_ac1021_roots(include_drive_cad_roots=include_drive_cad_roots))
    samples = collect_dwg_samples(roots, target_versions=target_versions)
    candidates = rank_cross_folder_candidates(
        samples,
        target_versions=target_versions,
        min_similarity=min_similarity,
        max_candidates_per_version=max_candidates_per_version,
    )
    classifications = {
        code: classify_version(
            sample_count=sum(1 for sample in samples if sample.code == code),
            candidates=candidates.get(code) or [],
        ).to_dict()
        for code in target_versions
    }
    return {
        "schema_version": "adr004-ac1018-ac1021-candidate-selection/v1",
        "generated_at": datetime.now().isoformat(),
        "roots": [str(path) for path in roots],
        "target_versions": list(target_versions),
        "min_similarity": min_similarity,
        "max_candidates_per_version": max_candidates_per_version,
        "summary": {
            "sample_count": len(samples),
            "version_counts": _version_counts(samples),
            "candidate_counts": {
                code: len(candidates.get(code) or [])
                for code in target_versions
            },
            "classification_counts": _classification_counts(classifications),
        },
        "classifications": classifications,
        "samples": {
            code: [
                _sample_with_context(sample)
                for sample in samples
                if sample.code == code
            ]
            for code in target_versions
        },
        "candidates": candidates,
    }


def rank_cross_folder_candidates(
    samples: Sequence[DwgSample],
    *,
    target_versions: Sequence[str] = TARGET_DWG_CODES,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    max_candidates_per_version: int = DEFAULT_MAX_CANDIDATES_PER_VERSION,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for code in target_versions:
        version_samples = [sample for sample in samples if sample.code == code]
        pairs = []
        for index, first in enumerate(version_samples):
            for second in version_samples[index + 1:]:
                candidate = _candidate_pair(first, second, min_similarity=min_similarity)
                if candidate is not None:
                    pairs.append(candidate)
        result[code] = sorted(
            pairs,
            key=lambda item: (-float(item["score"]), int(item["combined_size_bytes"])),
        )[: max(0, int(max_candidates_per_version))]
    return result


def classify_version(*, sample_count: int, candidates: Sequence[dict[str, Any]]) -> VersionClassification:
    if not sample_count:
        return VersionClassification("missing_compare_candidate", "no AC1018/AC1021 DWG samples were found")
    if sample_count == 1:
        return VersionClassification("single_file_import_only", "only one DWG sample exists for this version")
    if not candidates:
        return VersionClassification(
            "missing_compare_candidate",
            "multiple samples exist, but no filename/path/revision evidence supports a real before-after pair",
        )
    top = candidates[0]
    if top.get("classification") == "confirmed_revision_pair":
        return VersionClassification("confirmed_revision_pair", str(top.get("selection_reason") or ""))
    return VersionClassification("likely_revision_pair", str(top.get("selection_reason") or ""))


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# ADR-004 AC1018/AC1021 Candidate Selection",
        "",
        f"- Generated: `{report.get('generated_at', '')}`",
        f"- Samples scanned: `{report.get('summary', {}).get('sample_count', 0)}`",
        f"- Min similarity: `{report.get('min_similarity')}`",
        "",
        "## Roots",
        "",
    ]
    lines.extend(f"- `{root}`" for root in report.get("roots") or [])
    lines.extend(
        [
            "",
            "## Version Summary",
            "",
            "| version | samples | candidates | classification | reason |",
            "| --- | ---: | ---: | --- | --- |",
        ]
    )
    version_counts = report.get("summary", {}).get("version_counts") or {}
    candidate_counts = report.get("summary", {}).get("candidate_counts") or {}
    classifications = report.get("classifications") or {}
    for code in report.get("target_versions") or []:
        classification = classifications.get(code) or {}
        lines.append(
            "| {code} | {samples} | {candidates} | {status} | {reason} |".format(
                code=code,
                samples=version_counts.get(code, 0),
                candidates=candidate_counts.get(code, 0),
                status=_md_cell(str(classification.get("status") or "")),
                reason=_md_cell(str(classification.get("reason") or "")),
            )
        )

    lines.extend(
        [
            "",
            "## Top Candidates",
            "",
            "| version | rank | classification | score | similarity | path proximity | evidence | combined MiB | before | after |",
            "| --- | ---: | --- | ---: | ---: | ---: | --- | ---: | --- | --- |",
        ]
    )
    for code in report.get("target_versions") or []:
        for rank, candidate in enumerate((report.get("candidates") or {}).get(code) or [], start=1):
            before = candidate.get("before") or {}
            after = candidate.get("after") or {}
            lines.append(
                "| {code} | {rank} | {classification} | {score:.2f} | {similarity:.2f} | {path_score} | {evidence} | {size:.2f} | `{before}` | `{after}` |".format(
                    code=code,
                    rank=rank,
                    classification=_md_cell(str(candidate.get("classification") or "")),
                    score=float(candidate.get("score") or 0),
                    similarity=float(candidate.get("similarity") or 0),
                    path_score=int(candidate.get("path_proximity_score") or 0),
                    evidence=_md_cell(str(candidate.get("selection_reason") or "")),
                    size=float(candidate.get("combined_size_mb") or 0),
                    before=_md_cell(Path(str(before.get("path") or "")).name),
                    after=_md_cell(Path(str(after.get("path") or "")).name),
                )
            )

    lines.extend(
        [
            "",
            "## Samples",
            "",
            "| version | size MiB | folder | file |",
            "| --- | ---: | --- | --- |",
        ]
    )
    for code in report.get("target_versions") or []:
        for sample in (report.get("samples") or {}).get(code) or []:
            lines.append(
                "| {code} | {size:.2f} | `{folder}` | `{file}` |".format(
                    code=code,
                    size=float(sample.get("size_mb") or 0),
                    folder=_md_cell(str(sample.get("folder") or "")),
                    file=_md_cell(str(sample.get("file") or "")),
                )
            )
    lines.append("")
    return "\n".join(lines)


def _candidate_pair(first: DwgSample, second: DwgSample, *, min_similarity: float) -> dict[str, Any] | None:
    similarity = _similarity(first.normalized_name, second.normalized_name)
    path_score = _path_proximity_score(first.path, second.path)
    revision_score = _revision_evidence_score(first.path.name, second.path.name)
    contains = bool(
        first.normalized_name
        and second.normalized_name
        and (
            first.normalized_name in second.normalized_name
            or second.normalized_name in first.normalized_name
        )
    )
    evidence_score = revision_score + path_score
    if similarity < min_similarity and not contains:
        return None
    if revision_score == 0 and similarity < 0.86:
        return None
    if evidence_score < 10 and similarity < 0.86:
        return None
    before, after = _order_pair(first, second)
    combined_size = before.size_bytes + after.size_bytes
    classification = _candidate_classification(
        similarity=similarity,
        revision_score=revision_score,
        path_score=path_score,
    )
    reason = _selection_reason(
        classification=classification,
        similarity=similarity,
        revision_score=revision_score,
        path_score=path_score,
    )
    score = (similarity * 100.0) + revision_score + path_score - (combined_size / 2_000_000)
    return {
        "version": first.code,
        "classification": classification,
        "selection_reason": reason,
        "score": round(score, 3),
        "similarity": round(similarity, 3),
        "revision_evidence_score": revision_score,
        "path_proximity_score": path_score,
        "combined_size_bytes": combined_size,
        "combined_size_mb": round(combined_size / 1024 / 1024, 3),
        "before": _sample_with_context(before),
        "after": _sample_with_context(after),
    }


def _candidate_classification(*, similarity: float, revision_score: int, path_score: int) -> str:
    if similarity >= 0.88 and revision_score >= 20:
        return "confirmed_revision_pair"
    if similarity >= 0.82 and (revision_score >= 10 or path_score >= 12):
        return "likely_revision_pair"
    return "likely_revision_pair"


def _selection_reason(*, classification: str, similarity: float, revision_score: int, path_score: int) -> str:
    reasons = [classification, f"similarity={similarity:.3f}"]
    if revision_score:
        reasons.append(f"revision_evidence={revision_score}")
    if path_score:
        reasons.append(f"path_proximity={path_score}")
    return "; ".join(reasons)


def _order_pair(first: DwgSample, second: DwgSample) -> tuple[DwgSample, DwgSample]:
    keyed = sorted(
        (first, second),
        key=lambda item: (
            _leading_date(item.path.name),
            _revision_number(item.path.name),
            item.path.name.lower(),
        ),
    )
    return keyed[0], keyed[1]


def _revision_evidence_score(first_name: str, second_name: str) -> int:
    text = f"{first_name} {second_name}".lower()
    score = 0
    if re.search(r"(?:rev|_r|\br)\.?\s*\d+", text):
        score += 20
    if _leading_date(first_name) and _leading_date(first_name) != _leading_date(second_name):
        score += 10
    if re.search(r"comment|recover", text):
        score += 5
    if any(token in text for token in ("수정", "검토", "변경")):
        score += 10
    return score


def _path_proximity_score(first: Path, second: Path) -> int:
    if first.parent == second.parent:
        return 25
    first_parts = first.parent.parts
    second_parts = second.parent.parts
    common = 0
    for left, right in zip(first_parts, second_parts):
        if left != right:
            break
        common += 1
    if common >= min(len(first_parts), len(second_parts)) - 1:
        return 18
    if common >= 3:
        return 12
    if common >= 2:
        return 6
    return 0


def _leading_date(name: str) -> int:
    match = re.match(r"^[\[\(]*(\d{6,8})", Path(name).stem)
    if not match:
        return 0
    value = match.group(1)
    if len(value) == 6:
        return int("20" + value)
    return int(value)


def _revision_number(name: str) -> int:
    match = re.search(r"(?:rev|_r|\br)\.?\s*(\d+)", Path(name).stem, flags=re.IGNORECASE)
    if not match:
        return 0
    return int(match.group(1))


def _similarity(first: str, second: str) -> float:
    if not first and not second:
        return 0.0
    return SequenceMatcher(None, first, second).ratio()


def _sample_with_context(sample: DwgSample) -> dict[str, Any]:
    payload = sample.to_dict()
    payload["folder"] = str(sample.path.parent)
    payload["file"] = sample.path.name
    return payload


def _looks_like_cad_root(name: str) -> bool:
    return any(token in name for token in ("도면", "DWG", "Dwg", "PSRC", "P5", "복합", "구조", "CASE"))


def _version_counts(samples: Sequence[DwgSample]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for sample in samples:
        counts[sample.code] = counts.get(sample.code, 0) + 1
    return dict(sorted(counts.items()))


def _classification_counts(classifications: dict[str, dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in classifications.values():
        status = item.get("status") or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(resolved)
    return result


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", type=Path, default=[])
    parser.add_argument("--out", type=Path, default=Path("out/adr004_ac1018_ac1021_candidate_selection.json"))
    parser.add_argument("--report-md", type=Path, default=Path("out/adr004_ac1018_ac1021_candidate_selection.md"))
    parser.add_argument("--min-similarity", type=float, default=DEFAULT_MIN_SIMILARITY)
    parser.add_argument("--max-candidates-per-version", type=int, default=DEFAULT_MAX_CANDIDATES_PER_VERSION)
    parser.add_argument("--include-drive-cad-roots", action="store_true")
    args = parser.parse_args(argv)

    roots = [_resolve(ROOT, path) for path in args.root] if args.root else discover_ac1018_ac1021_roots(
        include_drive_cad_roots=args.include_drive_cad_roots
    )
    report = build_report(
        roots,
        min_similarity=args.min_similarity,
        max_candidates_per_version=args.max_candidates_per_version,
        include_drive_cad_roots=args.include_drive_cad_roots,
    )
    out = _resolve(ROOT, args.out)
    report_md = _resolve(ROOT, args.report_md)
    _write_json(out, report)
    _write_text(report_md, render_markdown(report))
    print(
        "adr004 ac1018/ac1021 candidates: "
        f"samples={report['summary']['sample_count']} "
        f"classifications={report['classifications']} "
        f"json={out} md={report_md}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
