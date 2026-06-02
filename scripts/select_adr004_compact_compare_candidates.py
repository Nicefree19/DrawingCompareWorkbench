"""Select compact ADR-004 DWG compare candidates from local roots.

The selector is evidence tooling only. It reads cheap DWG header/version bytes
and filenames, then ranks likely before/after pairs by filename similarity,
revision/date signals, and combined size. It does not import geometry and does
not invoke any converter.
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
TARGET_DWG_CODES = ("AC1018", "AC1021", "AC1024", "AC1027", "AC1032")
DEFAULT_MIN_SIMILARITY = 0.72
DEFAULT_MAX_CANDIDATES_PER_VERSION = 8

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class DwgSample:
    path: Path
    code: str
    size_bytes: int
    normalized_name: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "code": self.code,
            "size_bytes": self.size_bytes,
            "size_mb": round(self.size_bytes / 1024 / 1024, 3),
            "normalized_name": self.normalized_name,
        }


def discover_default_roots(*, drive_root: Path = Path("D:/")) -> list[Path]:
    """Discover known local CAD roots without hard-coding non-ASCII paths."""

    roots: list[Path] = []
    if drive_root.exists():
        for item in sorted(drive_root.iterdir(), key=lambda path: path.name):
            if not item.is_dir():
                continue
            name = item.name
            if (
                name.startswith("04.")
                or name.startswith("241217")
                or "PSRC" in name
                or "P5" in name
                or "CASE" in name
            ):
                roots.append(item)
    diff_root = drive_root / "00.Work_AI_Tool" / "07.Dwg_diff"
    if diff_root.exists():
        roots.append(diff_root)
    return _dedupe_paths(roots)


def build_report(
    roots: Sequence[Path] | None = None,
    *,
    target_versions: Sequence[str] = TARGET_DWG_CODES,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    max_candidates_per_version: int = DEFAULT_MAX_CANDIDATES_PER_VERSION,
) -> dict[str, Any]:
    roots = list(roots or discover_default_roots())
    samples = collect_dwg_samples(roots, target_versions=target_versions)
    candidates = rank_candidates(
        samples,
        target_versions=target_versions,
        min_similarity=min_similarity,
        max_candidates_per_version=max_candidates_per_version,
    )
    return {
        "schema_version": "adr004-compact-compare-candidate-selection/v1",
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
        },
        "candidates": candidates,
    }


def collect_dwg_samples(
    roots: Sequence[Path],
    *,
    target_versions: Sequence[str] = TARGET_DWG_CODES,
) -> list[DwgSample]:
    target_set = set(target_versions)
    samples: list[DwgSample] = []
    for path in _iter_dwg_files(roots):
        code = detect_dwg_code(path)
        if code not in target_set:
            continue
        try:
            size_bytes = path.stat().st_size
        except OSError:
            continue
        samples.append(
            DwgSample(
                path=path,
                code=code,
                size_bytes=size_bytes,
                normalized_name=normalize_candidate_name(path.name),
            )
        )
    return samples


def rank_candidates(
    samples: Sequence[DwgSample],
    *,
    target_versions: Sequence[str] = TARGET_DWG_CODES,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    max_candidates_per_version: int = DEFAULT_MAX_CANDIDATES_PER_VERSION,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for code in target_versions:
        pairs: list[dict[str, Any]] = []
        for group in _samples_by_parent(sample for sample in samples if sample.code == code).values():
            if len(group) < 2:
                continue
            for index, first in enumerate(group):
                for second in group[index + 1:]:
                    candidate = _candidate_pair(first, second, min_similarity=min_similarity)
                    if candidate is not None:
                        pairs.append(candidate)
        result[code] = sorted(
            pairs,
            key=lambda item: (-float(item["score"]), int(item["combined_size_bytes"])),
        )[: max(0, int(max_candidates_per_version))]
    return result


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# ADR-004 Compact Compare Candidate Selection",
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
            "| version | samples | candidates |",
            "| --- | ---: | ---: |",
        ]
    )
    version_counts = report.get("summary", {}).get("version_counts") or {}
    candidate_counts = report.get("summary", {}).get("candidate_counts") or {}
    for code in report.get("target_versions") or []:
        lines.append(f"| {code} | {version_counts.get(code, 0)} | {candidate_counts.get(code, 0)} |")

    lines.extend(
        [
            "",
            "## Top Candidates",
            "",
            "| version | rank | score | similarity | combined MiB | before | after |",
            "| --- | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for code in report.get("target_versions") or []:
        for rank, candidate in enumerate((report.get("candidates") or {}).get(code) or [], start=1):
            before = candidate.get("before") or {}
            after = candidate.get("after") or {}
            lines.append(
                "| {code} | {rank} | {score:.2f} | {similarity:.2f} | {size:.2f} | `{before}` | `{after}` |".format(
                    code=code,
                    rank=rank,
                    score=float(candidate.get("score") or 0),
                    similarity=float(candidate.get("similarity") or 0),
                    size=float(candidate.get("combined_size_mb") or 0),
                    before=_md_cell(Path(str(before.get("path") or "")).name),
                    after=_md_cell(Path(str(after.get("path") or "")).name),
                )
            )
    lines.append("")
    return "\n".join(lines)


def detect_dwg_code(path: Path) -> str:
    try:
        value = path.read_bytes()[:6].decode("ascii", errors="ignore")
    except OSError:
        return ""
    return value if value.startswith("AC") else ""


def normalize_candidate_name(name: str) -> str:
    stem = Path(name).stem.lower()
    stem = re.sub(r"^[\[\(]*\d{6,8}[_\s-]*", "", stem)
    stem = re.sub(r"[_\s-]*(?:rev|r)\.?\s*\d+[\.\d\w-]*", "", stem, flags=re.IGNORECASE)
    stem = _strip_after_first_token(
        stem,
        (
            "comment",
            "recover",
            "afc",
            "afd",
            "sen",
            "sky",
            "for",
        ),
    )
    return "".join(char for char in stem if char.isalnum())


def _candidate_pair(first: DwgSample, second: DwgSample, *, min_similarity: float) -> dict[str, Any] | None:
    similarity = _similarity(first.normalized_name, second.normalized_name)
    contains = bool(
        first.normalized_name
        and second.normalized_name
        and (
            first.normalized_name in second.normalized_name
            or second.normalized_name in first.normalized_name
        )
    )
    if similarity < min_similarity and not contains:
        return None
    before, after = _order_pair(first, second)
    combined_size = before.size_bytes + after.size_bytes
    evidence_score = _revision_evidence_score(first.path.name, second.path.name)
    score = (similarity * 100.0) + evidence_score - (combined_size / 2_000_000)
    return {
        "version": first.code,
        "score": round(score, 3),
        "similarity": round(similarity, 3),
        "revision_evidence_score": evidence_score,
        "combined_size_bytes": combined_size,
        "combined_size_mb": round(combined_size / 1024 / 1024, 3),
        "before": before.to_dict(),
        "after": after.to_dict(),
    }


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
    if _leading_date(first_name) != _leading_date(second_name):
        score += 10
    if re.search(r"comment|recover", text):
        score += 5
    return score


def _similarity(first: str, second: str) -> float:
    if not first and not second:
        return 0.0
    return SequenceMatcher(None, first, second).ratio()


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


def _strip_after_first_token(value: str, tokens: Sequence[str]) -> str:
    lowered = value.lower()
    indexes = [lowered.find(token) for token in tokens if lowered.find(token) >= 0]
    return value[: min(indexes)] if indexes else value


def _samples_by_parent(samples: Iterable[DwgSample]) -> dict[Path, list[DwgSample]]:
    grouped: dict[Path, list[DwgSample]] = {}
    for sample in samples:
        grouped.setdefault(sample.path.parent, []).append(sample)
    return grouped


def _iter_dwg_files(roots: Iterable[Path]) -> Iterable[Path]:
    seen: set[Path] = set()
    for root in roots:
        if root.is_file() and root.suffix.lower() == ".dwg":
            candidates = [root]
        elif root.is_dir():
            candidates = list(root.rglob("*.dwg")) + list(root.rglob("*.DWG"))
        else:
            continue
        for path in candidates:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield resolved


def _version_counts(samples: Sequence[DwgSample]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for sample in samples:
        counts[sample.code] = counts.get(sample.code, 0) + 1
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
    parser.add_argument("--out", type=Path, default=Path("out/adr004_compact_compare_candidate_selection.json"))
    parser.add_argument("--report-md", type=Path, default=Path("out/adr004_compact_compare_candidate_selection.md"))
    parser.add_argument("--min-similarity", type=float, default=DEFAULT_MIN_SIMILARITY)
    parser.add_argument("--max-candidates-per-version", type=int, default=DEFAULT_MAX_CANDIDATES_PER_VERSION)
    args = parser.parse_args(argv)

    roots = [_resolve(ROOT, path) for path in args.root] if args.root else discover_default_roots()
    report = build_report(
        roots,
        min_similarity=args.min_similarity,
        max_candidates_per_version=args.max_candidates_per_version,
    )
    out = _resolve(ROOT, args.out)
    report_md = _resolve(ROOT, args.report_md)
    _write_json(out, report)
    _write_text(report_md, render_markdown(report))
    print(
        "adr004 compact candidates: "
        f"samples={report['summary']['sample_count']} "
        f"candidates={report['summary']['candidate_counts']} "
        f"json={out} md={report_md}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
