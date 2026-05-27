"""Shared source identity helpers for comparison/render caches."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional, Union

SOURCE_SIGNATURE_SCHEMA_VERSION = 1
DEFAULT_SIGNATURE_DIGEST_LENGTH = 24


def build_source_signature(
    path: Union[str, Path, None],
    *,
    stable_id: str = "",
    importer_version: str = "",
    render_backend_id: str = "",
    plot_profile_hash: str = "",
    config_fingerprint: str = "",
    include_sample_hash: bool = False,
    sample_size: int = 65536,
) -> dict[str, Any]:
    """Return a stable, JSON-safe signature payload for one source artifact."""

    source_path = Path(path) if path else None
    resolved_path = ""
    display_path = str(path or "")
    file_size = 0
    mtime_ns = 0
    sample_hash = ""
    exists = False
    if source_path is not None:
        try:
            resolved = source_path.resolve()
            stat = resolved.stat()
            resolved_path = str(resolved)
            display_path = str(source_path)
            file_size = int(stat.st_size)
            mtime_ns = int(stat.st_mtime_ns)
            exists = True
            if include_sample_hash and file_size > 0:
                sample_hash = _head_tail_hash(resolved, sample_size=sample_size)
        except OSError:
            resolved_path = str(source_path)
            display_path = str(source_path)

    payload = {
        "schema_version": SOURCE_SIGNATURE_SCHEMA_VERSION,
        "stable_id": str(stable_id or ""),
        "source_path": resolved_path,
        "display_path": display_path,
        "file_size": file_size,
        "mtime_ns": mtime_ns,
        "head_tail_hash": sample_hash,
        "exists": exists,
        "importer_version": str(importer_version or ""),
        "render_backend_id": str(render_backend_id or ""),
        "plot_profile_hash": str(plot_profile_hash or ""),
        "config_fingerprint": str(config_fingerprint or ""),
    }
    payload["source_hash"] = source_signature_hash(payload)
    return payload


def source_signature_hash(signature: Union[dict[str, Any], str, Path, None]) -> str:
    """Return the canonical digest for a signature payload or path."""

    if isinstance(signature, dict):
        payload = dict(signature)
        payload.pop("source_hash", None)
    else:
        payload = build_source_signature(signature)
        payload.pop("source_hash", None)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def source_cache_stem(path: Union[str, Path, None], *, max_length: int = 64) -> str:
    """Return an ASCII-safe stem with a short hash suffix for non-ASCII names."""

    source_path = Path(path) if path else Path("source")
    raw_stem = source_path.stem or "source"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_stem).strip("._")
    if not safe:
        safe = "source"
    if safe == raw_stem and len(safe) <= max_length:
        return safe
    digest = hashlib.sha1(raw_stem.encode("utf-8", errors="ignore")).hexdigest()[:8]
    trimmed = safe[: max(8, max_length - 10)].strip("._") or "source"
    return f"{trimmed}__{digest}"


def source_cache_filename(
    path: Union[str, Path, None],
    *,
    namespace: str,
    extension: str,
    stable_id: str = "",
    importer_version: str = "",
    render_backend_id: str = "",
    plot_profile_hash: str = "",
    config_fingerprint: str = "",
    include_sample_hash: bool = False,
    digest_length: int = 16,
) -> str:
    """Return ``{safe_stem}.{digest}{extension}`` for a namespaced cache."""

    signature = build_source_signature(
        path,
        stable_id=stable_id,
        importer_version=importer_version,
        render_backend_id=render_backend_id,
        plot_profile_hash=plot_profile_hash,
        config_fingerprint=config_fingerprint,
        include_sample_hash=include_sample_hash,
    )
    payload = {
        "namespace": str(namespace or ""),
        "signature": signature,
    }
    digest = source_signature_hash(payload)[: max(8, int(digest_length or DEFAULT_SIGNATURE_DIGEST_LENGTH))]
    suffix = extension if str(extension or "").startswith(".") else f".{extension}"
    return f"{source_cache_stem(path)}.{digest}{suffix}"


def _head_tail_hash(path: Path, *, sample_size: int) -> str:
    size = max(1, int(sample_size or 1))
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        head = handle.read(size)
        digest.update(head)
        try:
            total_size = path.stat().st_size
            if total_size > size:
                handle.seek(max(0, total_size - size))
                digest.update(handle.read(size))
        except OSError:
            pass
    return digest.hexdigest()


__all__ = [
    "SOURCE_SIGNATURE_SCHEMA_VERSION",
    "DEFAULT_SIGNATURE_DIGEST_LENGTH",
    "build_source_signature",
    "source_signature_hash",
    "source_cache_stem",
    "source_cache_filename",
]
