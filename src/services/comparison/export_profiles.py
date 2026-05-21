# -*- coding: utf-8 -*-
"""Export profile helpers for internal and sharable drawing review packages."""

from __future__ import annotations

import functools
import json
import os
import re
import csv
import zipfile
from pathlib import Path
from typing import Any, Optional, Union

EXPORT_PROFILES = {"internal", "sharable"}

# §14: Path.resolve() cache for the sharable export profile hot path.
#
# cProfile baseline (docs/VIEWER_BUILD_BOTTLENECK_REPORT.md, §13 Phase C) showed
# that `_apply_export_profile_outputs` consumed ~75% of viewer-build wall time.
# 4,913 `Path.resolve()` calls amplified into 14,385 `nt._getfinalpathname`
# calls (~465ms on Windows) because Windows must walk reparse points/junctions
# for every resolve. The same paths (package_root, repeated relative
# substrings) get resolved over and over while `redact_payload_paths` recurses
# through a single JSON payload.
#
# Caching by **string** input (not Path) is more robust because callers pass
# str/Path/PathLike interchangeably, and Path equality on Windows is
# case-insensitive whereas the cache key needs to be exact.
#
# Cache lifetime: lru_cache eviction at maxsize is sufficient because pipeline
# runs are short-lived (subprocess-isolated in production). If a file moves
# mid-process the cached resolution is stale, but no caller in the export
# profile path moves files between resolves within a single redaction pass.
@functools.lru_cache(maxsize=4096)
def _cached_resolve(path_str: str) -> Path:
    """Return ``Path(path_str).resolve()``, cached by the input string.

    Note: callers MUST pass a string. This avoids the cost of hashing Path
    objects (which involves OS-level normalisation on Windows) and keeps the
    cache deterministic across str/Path mixed call-sites.

    Exceptions are NOT cached: ``lru_cache`` re-raises but does not store
    failed-call entries, so a transient FileNotFoundError stays transient.
    """
    return Path(path_str).resolve()


def _clear_resolve_cache() -> None:
    """Test hook: drop the cached path resolutions.

    Production callers should not need this — pipeline runs are isolated. Tests
    may invoke this between cases to assert deterministic behaviour after the
    filesystem changes.
    """
    _cached_resolve.cache_clear()

SENSITIVE_PATH_KEYS = {
    "source_a",
    "source_b",
    "a_path",
    "b_path",
    "before_path",
    "after_path",
    "old_path",
    "new_path",
    "dxf_cache_dir",
    "compare_state_dir",
    "review_state",
    "review_project",
}

TEXT_AUDIT_EXTENSIONS = {".csv", ".htm", ".html", ".jsonl", ".log", ".md", ".txt"}
TEXT_AUDIT_FILENAMES = {"_FAILED", "_SUCCESS"}
XLSX_AUDIT_EXTENSIONS = {".xlsx"}
PDF_AUDIT_EXTENSIONS = {".pdf"}


def normalize_export_profile(value: str | None) -> str:
    profile = str(value or "internal").strip().lower()
    if profile not in EXPORT_PROFILES:
        raise ValueError(f"Unsupported export_profile: {value}")
    return profile


def profile_path_value(
    value: Union[str, Path, None],
    *,
    profile: str,
    package_root: Optional[Union[str, Path]] = None,
    sensitive: bool = False,
) -> str:
    if not value:
        return ""
    text = str(value)
    if profile != "sharable":
        return text
    path = Path(text)
    # §14: route through `_cached_resolve` so the same string inputs (notably
    # `package_root`, which arrives once per redact-payload recursion level)
    # do not re-trigger Windows `_getfinalpathname` lookups.
    try:
        root = _cached_resolve(str(package_root)) if package_root else None
    except Exception:
        root = None
    try:
        resolved = _cached_resolve(text)
        if root is not None:
            try:
                return str(resolved.relative_to(root)).replace("\\", "/")
            except ValueError:
                pass
        if sensitive or path.is_absolute() or _looks_absolute(text):
            return f"<redacted>/{path.name}"
    except Exception:
        if sensitive or _looks_absolute(text):
            return f"<redacted>/{path.name}"
    return text


def redact_payload_paths(
    payload: Any,
    *,
    profile: str,
    package_root: Optional[Union[str, Path]] = None,
    key: str = "",
) -> Any:
    profile = normalize_export_profile(profile)
    if profile != "sharable":
        return payload
    if isinstance(payload, dict):
        return {
            str(item_key): redact_payload_paths(
                item_value,
                profile=profile,
                package_root=package_root,
                key=str(item_key),
            )
            for item_key, item_value in payload.items()
        }
    if isinstance(payload, list):
        return [
            redact_payload_paths(item, profile=profile, package_root=package_root, key=key)
            for item in payload
        ]
    if isinstance(payload, str):
        sensitive = key in SENSITIVE_PATH_KEYS or key.endswith("_dir") or key.endswith("_path")
        return profile_path_value(payload, profile=profile, package_root=package_root, sensitive=sensitive)
    return payload


def apply_export_profile_to_json(
    path: Union[str, Path, None],
    *,
    profile: str,
    package_root: Optional[Union[str, Path]] = None,
) -> None:
    profile = normalize_export_profile(profile)
    if profile != "sharable" or not path:
        return
    target = Path(path)
    if not target.exists():
        return
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return
    redacted = redact_payload_paths(payload, profile=profile, package_root=package_root)
    temp_path = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        temp_path.write_text(json.dumps(redacted, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)


def apply_export_profile_to_csv(
    path: Union[str, Path, None],
    *,
    profile: str,
    package_root: Optional[Union[str, Path]] = None,
) -> None:
    profile = normalize_export_profile(profile)
    if profile != "sharable" or not path:
        return
    target = Path(path)
    if not target.exists():
        return
    try:
        with open(target, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                return
            fieldnames = list(reader.fieldnames)
            rows = [
                {
                    key: _redact_profile_cell(
                        value,
                        key=key,
                        profile=profile,
                        package_root=package_root,
                    )
                    for key, value in row.items()
                }
                for row in reader
            ]
    except Exception:
        return

    temp_path = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)


def apply_export_profile_to_xlsx(
    path: Union[str, Path, None],
    *,
    profile: str,
    package_root: Optional[Union[str, Path]] = None,
) -> None:
    profile = normalize_export_profile(profile)
    if profile != "sharable" or not path:
        return
    target = Path(path)
    if not target.exists():
        return
    try:
        import openpyxl

        workbook = openpyxl.load_workbook(str(target))
    except Exception:
        return

    changed = False
    for worksheet in workbook.worksheets:
        for row in worksheet.iter_rows():
            for cell in row:
                value = cell.value
                if not isinstance(value, str):
                    continue
                redacted = _redact_profile_cell(
                    value,
                    key="",
                    profile=profile,
                    package_root=package_root,
                )
                if redacted != value:
                    cell.value = redacted
                    changed = True
    if changed:
        workbook.save(str(target))


def apply_export_profile_to_text(
    path: Union[str, Path, None],
    *,
    profile: str,
    package_root: Optional[Union[str, Path]] = None,
) -> None:
    profile = normalize_export_profile(profile)
    if profile != "sharable" or not path:
        return
    target = Path(path)
    if not target.exists():
        return
    try:
        text = target.read_text(encoding="utf-8-sig", errors="ignore")
    except Exception:
        return
    redacted = text
    for value in sorted(set(_absolute_path_substrings(text)), key=len, reverse=True):
        redacted = redacted.replace(
            value,
            profile_path_value(
                value,
                profile=profile,
                package_root=package_root,
                sensitive=True,
            ),
        )
    if redacted == text:
        return
    temp_path = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        temp_path.write_text(redacted, encoding="utf-8")
        os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)


def apply_export_profile_to_file(
    path: Union[str, Path, None],
    *,
    profile: str,
    package_root: Optional[Union[str, Path]] = None,
) -> None:
    profile = normalize_export_profile(profile)
    if profile != "sharable" or not path:
        return
    target = Path(path)
    suffix = target.suffix.lower()
    if suffix == ".json":
        apply_export_profile_to_json(target, profile=profile, package_root=package_root)
    elif suffix == ".csv":
        apply_export_profile_to_csv(target, profile=profile, package_root=package_root)
    elif suffix == ".xlsx":
        apply_export_profile_to_xlsx(target, profile=profile, package_root=package_root)
    elif _is_text_audit_file(target):
        apply_export_profile_to_text(target, profile=profile, package_root=package_root)


def _redact_profile_cell(
    value: Any,
    *,
    key: str,
    profile: str,
    package_root: Optional[Union[str, Path]] = None,
) -> Any:
    if not isinstance(value, str):
        return value
    sensitive = key in SENSITIVE_PATH_KEYS or key.endswith("_dir") or key.endswith("_path")
    redacted = profile_path_value(
        value,
        profile=profile,
        package_root=package_root,
        sensitive=sensitive,
    )
    if redacted != value:
        return redacted
    for path_value in sorted(set(_absolute_path_substrings(value)), key=len, reverse=True):
        redacted = redacted.replace(
            path_value,
            profile_path_value(
                path_value,
                profile=profile,
                package_root=package_root,
                sensitive=True,
            ),
        )
    return redacted


def looks_like_absolute_path(value: str) -> bool:
    text = str(value or "")
    if not text:
        return False
    normalized = text.replace("\\", "/")
    if normalized.startswith("<redacted>/") or normalized.startswith("/redacted/"):
        return False
    return _looks_absolute(text)


def audit_sharable_paths(out_dir: Union[str, Path]) -> list[dict[str, str]]:
    """Return path leaks that would make a sharable package unsafe."""

    root = Path(out_dir)
    if not root.exists():
        return []
    leaks: list[dict[str, str]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        try:
            file_label = path.relative_to(root).as_posix()
        except ValueError:
            file_label = path.name
        suffix = path.suffix.lower()
        if suffix == ".json":
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            _collect_path_leaks(payload, file_label=file_label, key_path="", leaks=leaks)
        elif _is_text_audit_file(path):
            _collect_text_path_leaks(path, file_label=file_label, leaks=leaks)
        elif suffix in XLSX_AUDIT_EXTENSIONS:
            _collect_xlsx_path_leaks(path, file_label=file_label, leaks=leaks)
        elif suffix in PDF_AUDIT_EXTENSIONS:
            _collect_pdf_text_path_leaks(path, file_label=file_label, leaks=leaks)
    return leaks


def _collect_path_leaks(
    payload: Any,
    *,
    file_label: str,
    key_path: str,
    leaks: list[dict[str, str]],
) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            child_key = str(key)
            child_path = f"{key_path}.{child_key}" if key_path else child_key
            _collect_path_leaks(value, file_label=file_label, key_path=child_path, leaks=leaks)
        return
    if isinstance(payload, list):
        for index, value in enumerate(payload):
            child_path = f"{key_path}[{index}]" if key_path else f"[{index}]"
            _collect_path_leaks(value, file_label=file_label, key_path=child_path, leaks=leaks)
        return
    if not isinstance(payload, str):
        return

    key = key_path.rsplit(".", 1)[-1]
    is_sensitive_key = key in SENSITIVE_PATH_KEYS or key.endswith("_dir") or key.endswith("_path")
    if is_sensitive_key and looks_like_absolute_path(payload):
        leaks.append(
            {
                "file": file_label,
                "key": key_path,
                "reason": "sensitive_key_not_redacted",
                "value": payload,
            }
        )
        return
    if looks_like_absolute_path(payload):
        leaks.append(
            {
                "file": file_label,
                "key": key_path,
                "reason": "absolute_path_in_string_value",
                "value": payload,
            }
        )


def _collect_text_path_leaks(path: Path, *, file_label: str, leaks: list[dict[str, str]]) -> None:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
    except Exception:
        return
    _collect_absolute_path_substrings(text, file_label=file_label, key="", leaks=leaks)


def _collect_xlsx_path_leaks(path: Path, *, file_label: str, leaks: list[dict[str, str]]) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if not (
                    name == "xl/sharedStrings.xml"
                    or (name.startswith("xl/worksheets/") and name.endswith(".xml"))
                ):
                    continue
                try:
                    text = archive.read(name).decode("utf-8", errors="ignore")
                except Exception:
                    continue
                _collect_absolute_path_substrings(
                    text,
                    file_label=file_label,
                    key=name,
                    leaks=leaks,
                )
    except Exception:
        return


def _collect_pdf_text_path_leaks(path: Path, *, file_label: str, leaks: list[dict[str, str]]) -> None:
    try:
        import fitz  # PyMuPDF
    except Exception:
        return

    doc = None
    try:
        doc = fitz.open(str(path))
        parts = []
        for page in doc:
            try:
                parts.append(page.get_text("text"))
            except Exception:
                continue
        text = "\n".join(parts)
    except Exception:
        return
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
    _collect_absolute_path_substrings(text, file_label=file_label, key="pdf_text", leaks=leaks)


def _collect_absolute_path_substrings(
    text: str,
    *,
    file_label: str,
    key: str,
    leaks: list[dict[str, str]],
) -> None:
    seen: set[str] = set()
    for value in _absolute_path_substrings(text):
        if value in seen:
            continue
        seen.add(value)
        leaks.append(
            {
                "file": file_label,
                "key": key,
                "reason": "absolute_path_in_text",
                "value": value,
            }
        )


def _absolute_path_substrings(text: str) -> list[str]:
    values: list[str] = []
    patterns = (
        re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\)[^\s\"'<>|,;]+"),
        re.compile(r"(?<![:/;A-Za-z0-9_<])/(?!/|redacted/)[A-Za-z0-9._~+-][A-Za-z0-9._~+\-/]*"),
    )
    for pattern in patterns:
        for match in pattern.finditer(text or ""):
            value = match.group(0).rstrip(").]")
            if (
                value
                and not value.startswith("<redacted>/")
                and not value.startswith("/redacted/")
                and not _redacted_match_context(text, match.start())
            ):
                values.append(value)
    return values


def _is_text_audit_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_AUDIT_EXTENSIONS or path.name in TEXT_AUDIT_FILENAMES


def _redacted_match_context(text: str, start: int) -> bool:
    prefix = (text or "")[max(0, start - 32) : start].lower()
    return prefix.endswith("<redacted>") or prefix.endswith("&lt;redacted&gt;")


def _looks_absolute(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith("\\\\") or value.startswith("/"))
