# -*- coding: utf-8 -*-
"""Embedding-classifier manifest — pins backend + corpus + normalizer.

Single source of truth so an embedding model upgrade can't silently
re-shuffle prototype space (Risk R5 in
``docs/AI_EMBEDDING_PLAN_V2.md`` §7).

Lifecycle:
  1. At app startup, ``load_manifest()`` reads
     ``%LOCALAPPDATA%/DrawingCompareWorkbench/ai_cache/manifest.json``
  2. ``compute_current()`` builds a fresh manifest from the active
     backend + normalizer + corpus
  3. If they differ in ANY field, the prototype embeddings are
     invalidated and recomputed (background thread).
  4. After successful recompute, ``save_manifest()`` persists the
     fresh values.

Schema is intentionally tiny — fewer fields = fewer false-positive
invalidations. Add a field only when its change MUST trigger
recompute.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MANIFEST_SCHEMA_VERSION = "v2"
MANIFEST_FILENAME = "manifest.json"


@dataclass(frozen=True)
class EmbeddingManifest:
    """Pinned identity of the embedding pipeline.

    3rd-review fix (P1): the fingerprint now covers EVERY input that
    could change the resulting vector — instruction template, output
    dim, pooling policy, quantisation tier — not just the model SHA.

    Qwen's official ``Qwen3-Embedding-0.6B-GGUF`` model card flags
    instruction-aware retrieval (1-5% quality loss without proper
    instruction prompts) and 32-1024 variable output dim. Either of
    those silently changing would produce vectors incompatible with
    pre-computed prototypes, but the model SHA wouldn't budge.
    Including them in the manifest forces a recompute on any drift.
    """

    schema_version: str = MANIFEST_SCHEMA_VERSION
    embedding_backend: str = ""        # registry id, e.g. "llama_cpp_qwen3_embedding"
    model_file: str = ""                # model file basename
    model_sha256: str = ""              # weights hash (raw .gguf file)
    embedding_dim: int = 0
    prototype_corpus_version: str = "" # bumped manually when seeds change
    # 2nd-review fix (P2): version_string alone is too easy to forget
    # to bump when the corpus file changes. Hashing the corpus content
    # gives content-addressed invalidation as a backstop. Empty string
    # = corpus not yet computed (legacy or first-run).
    prototype_corpus_sha256: str = ""
    normalizer_version: str = ""       # bumped when normalizer regex chain changes
    # 3rd-review fix (P1): vector-affecting runtime knobs.
    instruction_id: str = ""           # e.g. "korean_construction_zone_v1"
    output_dim: int = 0                # ≤ embedding_dim (Qwen3 supports 32-1024 truncation)
    pooling: str = ""                  # "mean" | "cls" | "last_token" | ""
    quantization: str = ""             # "Q4_K_M" | "Q8_0" | "fp16" | ""
    computed_at_utc: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "EmbeddingManifest":
        # Only keep known fields — defensive against schema additions
        known = {f.name for f in cls.__dataclass_fields__.values()}
        clean = {k: v for k, v in data.items() if k in known}
        return cls(**clean)

    def is_compatible_with(self, other: "EmbeddingManifest") -> bool:
        """Two manifests are compatible iff every identity field matches.

        ``computed_at_utc`` is excluded — it's a timestamp, not an
        identity dimension.

        2nd-review fix (P2): ``prototype_corpus_sha256`` joined the
        identity list. If either side has an empty hash (legacy), it's
        ignored from the comparison so old manifests don't all
        invalidate at once on first launch with the new schema.
        """

        if self.schema_version != other.schema_version:
            return False
        # Always-required identity fields
        ident = (
            "embedding_backend", "model_file", "model_sha256",
            "embedding_dim", "prototype_corpus_version",
            "normalizer_version",
        )
        if not all(getattr(self, k) == getattr(other, k) for k in ident):
            return False
        # corpus_sha256 — only enforce when BOTH sides have it
        if self.prototype_corpus_sha256 and other.prototype_corpus_sha256:
            if self.prototype_corpus_sha256 != other.prototype_corpus_sha256:
                return False
        # 3rd-review fix (P1): vector-affecting fields. Treat the same
        # way as corpus_sha256 — only enforce when BOTH sides have a
        # non-default value, so legacy manifests don't all invalidate
        # at once on first launch with the new schema.
        for k in ("instruction_id", "pooling", "quantization"):
            v_self = getattr(self, k)
            v_other = getattr(other, k)
            if v_self and v_other and v_self != v_other:
                return False
        # output_dim: 0 = "use full embedding_dim" (default). When BOTH
        # explicitly set a non-zero target, they must match.
        if self.output_dim and other.output_dim:
            if self.output_dim != other.output_dim:
                return False
        return True


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def manifest_path(cache_dir: Path) -> Path:
    """Resolve the manifest's on-disk path."""

    return Path(cache_dir) / MANIFEST_FILENAME


def load_manifest(cache_dir: Path) -> Optional[EmbeddingManifest]:
    """Read the persisted manifest. Returns ``None`` when missing or
    unreadable (caller should treat as "first run, recompute")."""

    p = manifest_path(cache_dir)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return EmbeddingManifest.from_dict(data)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning(
            "Embedding manifest at %s unreadable (%s) — will recompute",
            p, exc,
        )
        return None


def save_manifest(cache_dir: Path, manifest: EmbeddingManifest) -> Path:
    """Persist the manifest atomically.

    2nd-review fix (P2): write to a temp file in the same directory
    then ``Path.replace()`` (POSIX rename) so a crash mid-write
    leaves either the previous file intact or the new one — never
    a partial JSON. Without this, an interrupted write produced
    corrupt JSON that the loader treated as "missing" → forced an
    unnecessary prototype recompute on every subsequent launch.
    """

    import os

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    p = manifest_path(cache_dir)
    payload = manifest.to_dict()
    if not payload.get("computed_at_utc"):
        payload["computed_at_utc"] = datetime.now(timezone.utc).isoformat()
    serialised = json.dumps(payload, indent=2, ensure_ascii=False)

    # Same-directory temp + atomic rename. PID + nanosecond keeps
    # concurrent writes (very unlikely but cheap) collision-free.
    tmp = cache_dir / f".{p.name}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        tmp.write_text(serialised, encoding="utf-8")
        tmp.replace(p)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return p


def needs_recompute(
    cache_dir: Path,
    current: EmbeddingManifest,
) -> bool:
    """True when the persisted manifest is missing or doesn't match
    ``current`` (any identity field differs)."""

    persisted = load_manifest(cache_dir)
    if persisted is None:
        return True
    return not persisted.is_compatible_with(current)


__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "MANIFEST_FILENAME",
    "EmbeddingManifest",
    "manifest_path",
    "load_manifest",
    "save_manifest",
    "needs_recompute",
]
