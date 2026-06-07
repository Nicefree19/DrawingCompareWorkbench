# -*- coding: utf-8 -*-
"""Resolve converted DXF fallbacks for unsupported DWG file pairs."""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Tuple, Union

from .dwg_importer import DwgImportError, DwgVersionDetector
from .source_signature import source_cache_stem, source_signature_hash

logger = logging.getLogger(__name__)

FALLBACK_ROOT_NAMES = (
    "dxf_registered",
    "dxf_clean",
    "dxf_coordclean",
    "dxf_shifted",
    "dxf_compare",
)


@dataclass(frozen=True)
class DwgDxfFallbackResolution:
    """Effective source pair after optional DWG-to-DXF fallback resolution."""

    source_a: Path
    source_b: Path
    effective_source_a: Path
    effective_source_b: Path
    used: bool = False
    reason: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_a": str(self.source_a),
            "source_b": str(self.source_b),
            "effective_source_a": str(self.effective_source_a),
            "effective_source_b": str(self.effective_source_b),
            "used": self.used,
            "reason": self.reason,
            "diagnostics": self.diagnostics,
        }


def fallback_review_notice(resolution: DwgDxfFallbackResolution) -> dict[str, Any]:
    if not resolution.used:
        return {}
    diagnostics = resolution.diagnostics if isinstance(resolution.diagnostics, dict) else {}
    return {
        "mode": "converted_dxf_fallback",
        "message": (
            "Original DWG inputs are unsupported by the native adapter, "
            "so this run compared converted DXF inputs instead."
        ),
        "reason": resolution.reason,
        "fallback_kind": diagnostics.get("fallback_kind", ""),
        "source_a": str(resolution.source_a),
        "source_b": str(resolution.source_b),
        "effective_source_a": str(resolution.effective_source_a),
        "effective_source_b": str(resolution.effective_source_b),
        "dwg_versions": diagnostics.get("dwg_versions", {}),
    }


def resolve_dwg_dxf_fallback_pair(
    source_a: Union[str, Path],
    source_b: Union[str, Path],
) -> DwgDxfFallbackResolution:
    """Use nearby converted DXFs when a selected DWG pair is unsupported.

    The resolver is intentionally conservative: it only changes the effective
    inputs for an explicit DWG file pair, and only when at least one DWG version
    is unsupported by the native adapter.
    """

    original_a = Path(source_a).resolve()
    original_b = Path(source_b).resolve()
    base = DwgDxfFallbackResolution(
        source_a=original_a,
        source_b=original_b,
        effective_source_a=original_a,
        effective_source_b=original_b,
    )

    folder_resolution = _resolve_folder_fallback(original_a, original_b)
    if folder_resolution is not None:
        return folder_resolution

    if not _is_existing_file_with_suffix(original_a, ".dwg") or not _is_existing_file_with_suffix(original_b, ".dwg"):
        return base

    version_a = _detect_dwg_version(original_a)
    version_b = _detect_dwg_version(original_b)
    diagnostics: dict[str, Any] = {
        "dwg_versions": {
            "a": version_a,
            "b": version_b,
        }
    }

    if _version_supported(version_a) and _version_supported(version_b):
        return DwgDxfFallbackResolution(
            source_a=original_a,
            source_b=original_b,
            effective_source_a=original_a,
            effective_source_b=original_b,
            diagnostics=diagnostics,
        )

    candidate = _best_converted_dxf_pair(original_a, original_b)
    if candidate is None:
        diagnostics["fallback_candidates"] = []
        return DwgDxfFallbackResolution(
            source_a=original_a,
            source_b=original_b,
            effective_source_a=original_a,
            effective_source_b=original_b,
            diagnostics=diagnostics,
        )

    fallback_a, fallback_b, fallback_details = candidate
    diagnostics.update(fallback_details)
    return DwgDxfFallbackResolution(
        source_a=original_a,
        source_b=original_b,
        effective_source_a=fallback_a,
        effective_source_b=fallback_b,
        used=True,
        reason="unsupported_dwg_version_with_converted_dxf",
        diagnostics=diagnostics,
    )


def _resolve_folder_fallback(original_a: Path, original_b: Path) -> Optional[DwgDxfFallbackResolution]:
    if not original_a.exists() or not original_b.exists():
        return None
    if not original_a.is_dir() or not original_b.is_dir():
        return None

    diagnostics: dict[str, Any] = {
        "dwg_versions": _dwg_version_summary([original_a, original_b], limit=20),
    }
    if not diagnostics["dwg_versions"]["unsupported"]:
        return None

    candidates = list(_folder_dxf_pair_candidates(original_a, original_b))
    if not candidates:
        diagnostics["fallback_candidates"] = []
        return DwgDxfFallbackResolution(
            source_a=original_a,
            source_b=original_b,
            effective_source_a=original_a,
            effective_source_b=original_b,
            diagnostics=diagnostics,
        )

    candidates.sort(key=lambda item: (-item[0], item[1], str(item[2]), str(item[3])))
    score, label, fallback_a, fallback_b, counts = candidates[0]
    diagnostics.update(
        {
            "fallback_kind": label,
            "fallback_score": score,
            "fallback_counts": counts,
            "fallback_candidates": [
                {
                    "kind": item_label,
                    "score": item_score,
                    "source_a": str(item_a),
                    "source_b": str(item_b),
                    "counts": item_counts,
                }
                for item_score, item_label, item_a, item_b, item_counts in candidates[:10]
            ],
        }
    )
    return DwgDxfFallbackResolution(
        source_a=original_a,
        source_b=original_b,
        effective_source_a=fallback_a,
        effective_source_b=fallback_b,
        used=True,
        reason="unsupported_dwg_folder_with_converted_dxf_dirs",
        diagnostics=diagnostics,
    )


def _detect_dwg_version(path: Path) -> dict[str, Any]:
    try:
        return DwgVersionDetector.detect_file(path).to_dict()
    except DwgImportError as exc:
        return {
            "supported": False,
            "error_code": exc.code,
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "supported": False,
            "error": str(exc),
        }


def _version_supported(version: dict[str, Any]) -> bool:
    return bool(version.get("supported"))


def _dwg_version_summary(paths: Iterable[Path], *, limit: int) -> dict[str, Any]:
    supported: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    unreadable: list[dict[str, Any]] = []
    for path in _iter_dwg_files(paths, limit=limit):
        version = _detect_dwg_version(path)
        item = {"path": str(path), **version}
        if version.get("supported"):
            supported.append(item)
        elif version.get("code") or version.get("error"):
            unsupported.append(item)
        else:
            unreadable.append(item)
    return {
        "sample_count": len(supported) + len(unsupported) + len(unreadable),
        "supported": supported,
        "unsupported": unsupported,
        "unreadable": unreadable,
    }


def _iter_dwg_files(paths: Iterable[Path], *, limit: int) -> Iterable[Path]:
    emitted = 0
    seen: set[Path] = set()
    for path in paths:
        iterator: Iterable[Path]
        if path.is_file() and path.suffix.lower() == ".dwg":
            iterator = [path]
        elif path.is_dir():
            iterator = path.rglob("*.dwg")
        else:
            continue
        for child in iterator:
            resolved = child.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield resolved
            emitted += 1
            if emitted >= limit:
                return


def _folder_dxf_pair_candidates(original_a: Path, original_b: Path) -> Iterable[tuple[int, str, Path, Path, dict[str, int]]]:
    for root in _folder_fallback_roots(original_a, original_b):
        for root_index, root_name in enumerate(FALLBACK_ROOT_NAMES):
            before_dir = root / root_name / "before"
            after_dir = root / root_name / "after"
            before_files = _candidate_dxf_files(before_dir)
            after_files = _candidate_dxf_files(after_dir)
            if not before_files or not after_files:
                continue
            if len(before_files) != len(after_files):
                continue
            counts = {"before_dxf_count": len(before_files), "after_dxf_count": len(after_files)}
            yield (
                9_000 - (root_index * 100) + min(len(before_files), len(after_files)),
                f"{root_name}/before_after_dirs",
                before_dir.resolve(),
                after_dir.resolve(),
                counts,
            )


def _folder_fallback_roots(original_a: Path, original_b: Path) -> list[Path]:
    if original_a == original_b:
        return [original_a.resolve()]
    common_root = _common_parent(original_a, original_b)
    return [common_root] if common_root is not None else []


def _best_converted_dxf_pair(original_a: Path, original_b: Path) -> Optional[tuple[Path, Path, dict[str, Any]]]:
    candidates = list(_converted_dxf_pair_candidates(original_a, original_b))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1], str(item[2]), str(item[3])))
    score, label, fallback_a, fallback_b = candidates[0]
    return (
        fallback_a,
        fallback_b,
        {
            "fallback_kind": label,
            "fallback_score": score,
            "fallback_candidates": [
                {
                    "kind": item_label,
                    "score": item_score,
                    "source_a": str(item_a),
                    "source_b": str(item_b),
                }
                for item_score, item_label, item_a, item_b in candidates[:10]
            ],
        },
    )


def _converted_dxf_pair_candidates(original_a: Path, original_b: Path) -> Iterable[tuple[int, str, Path, Path]]:
    same_stem_a = original_a.with_suffix(".dxf")
    same_stem_b = original_b.with_suffix(".dxf")
    if same_stem_a.exists() and same_stem_b.exists():
        yield (
            10_000 + _stem_score(original_a, same_stem_a) + _stem_score(original_b, same_stem_b),
            "same_directory_same_stem",
            same_stem_a.resolve(),
            same_stem_b.resolve(),
        )

    common_root = _common_parent(original_a, original_b)
    if common_root is None:
        return

    for root_index, root_name in enumerate(FALLBACK_ROOT_NAMES):
        fallback_root = common_root / root_name
        before_dir = fallback_root / "before"
        after_dir = fallback_root / "after"
        before_files = _candidate_dxf_files(before_dir)
        after_files = _candidate_dxf_files(after_dir)
        if not before_files or not after_files:
            continue
        for before_file in before_files:
            for after_file in after_files:
                score = (
                    9_000
                    - (root_index * 100)
                    + _stem_score(original_a, before_file)
                    + _stem_score(original_b, after_file)
                )
                yield (
                    score,
                    f"{root_name}/before_after",
                    before_file.resolve(),
                    after_file.resolve(),
                )


def _candidate_dxf_files(path: Path) -> list[Path]:
    if not path.exists() or not path.is_dir():
        return []
    return sorted(
        child
        for child in path.iterdir()
        if child.is_file() and child.suffix.lower() == ".dxf"
    )


def _common_parent(path_a: Path, path_b: Path) -> Optional[Path]:
    parent_a = path_a.parent.resolve()
    parent_b = path_b.parent.resolve()
    if parent_a == parent_b:
        return parent_a
    if (
        parent_a.parent == parent_b.parent
        and parent_a.name.lower() in {"before", "old", "a"}
        and parent_b.name.lower() in {"after", "new", "b"}
    ):
        return parent_a.parent
    return None


def _stem_score(original: Path, candidate: Path) -> int:
    original_norm = _normal_stem(original.stem)
    candidate_norm = _normal_stem(candidate.stem)
    if not original_norm or not candidate_norm:
        return 0
    if original_norm == candidate_norm:
        return 500
    if original_norm in candidate_norm or candidate_norm in original_norm:
        return 300
    original_tokens = set(original_norm.split())
    candidate_tokens = set(candidate_norm.split())
    if not original_tokens or not candidate_tokens:
        return 0
    return int(200 * (len(original_tokens & candidate_tokens) / len(original_tokens | candidate_tokens)))


def _normal_stem(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[_\-]+", " ", lowered)
    lowered = re.sub(r"\br\d+\b", "", lowered)
    lowered = re.sub(r"\brev(?:ision)?\s*\d*\b", "", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def _is_existing_file_with_suffix(path: Path, suffix: str) -> bool:
    return path.exists() and path.is_file() and path.suffix.lower() == suffix


def auto_convert_unsupported_dwg(
    source: Union[str, Path],
    dxf_cache_dir: Union[str, Path],
    *,
    output_version: str = "ACAD2018",
    timeout_seconds: int = 180,
) -> Tuple[Path, bool, str]:
    """Auto-convert a natively-unsupported DWG to a cached DXF via ODA File Converter.

    This makes "just give a DWG" work: when ``resolve_dwg_dxf_fallback_pair`` found
    no pre-converted sibling DXF and the DWG version is unreadable by the native
    adapter (e.g. AC1032), convert it once with ODA File Converter (if installed)
    and reuse the cached DXF on later runs. The result is a DXF, so the downstream
    preflight + native DXF reader proceed normally.

    Returns ``(effective_path, converted, note)``. When ``source`` is not a DWG,
    is a natively-supported DWG, or ODA is unavailable / fails, the original path
    is returned with ``converted=False`` so existing behaviour (pre-converted DXF
    or the honest preflight error) is preserved — never a silent substitution.
    """

    src = Path(source)
    if src.suffix.lower() != ".dwg":
        return src, False, "not_dwg"

    # Only convert versions the native adapter cannot read; a supported DWG is
    # left for the native path. An undetectable version still gets a repair-convert
    # attempt (ODA's audit pass often recovers it) rather than failing outright.
    try:
        version = DwgVersionDetector.detect_file(src)
        if version.supported:
            return src, False, "native_supported"
    except Exception:  # noqa: BLE001 — detection best-effort
        pass

    try:
        from .dwg_converter import convert_with_configured_converter
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("DWG converter module unavailable: %s", exc)
        return src, False, "converter_module_unavailable"

    # Conversion is delegated below through the quarantined converter shim.

    cache_dir = Path(dxf_cache_dir) / "oda_auto"
    cached = cache_dir / f"{source_cache_stem(src)}__{source_signature_hash(src)[:16]}.dxf"
    try:
        if cached.exists() and cached.stat().st_size > 0:
            return cached, True, "oda_cache_hit"
    except OSError:
        pass

    try:
        converted, convert_note = convert_with_configured_converter(
            src,
            output_version=output_version,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 — conversion failure stays non-fatal
        logger.warning("ODA auto-convert failed for %s: %s", src, exc)
        return src, False, f"oda_failed:{type(exc).__name__}"

    if converted is None:
        if convert_note.startswith("oda_failed:"):
            logger.warning("ODA auto-convert failed for %s: %s", src, convert_note)
        return src, False, convert_note

    converted = Path(converted)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(converted, cached)
        result = cached
        note = "oda_converted"
    except Exception as exc:  # noqa: BLE001 — fall back to the temp output path
        logger.warning("Failed to cache ODA-converted DXF for %s: %s", src, exc)
        result, note = converted, "oda_converted_uncached"
    finally:
        if note != "oda_converted_uncached":
            # ODA writes the DXF into a temp output dir; drop it once cached.
            try:
                shutil.rmtree(converted.parent, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass

    logger.info("ODA auto-converted unsupported DWG -> DXF: %s -> %s", src.name, result)
    return result, True, note
