# -*- coding: utf-8 -*-
"""Phase K2 — local-JSON KDS RAG client.

Reads pre-parsed KDS / KCS clauses from a local JSON file and runs
keyword-based retrieval (no embedding model — fast + deterministic +
no extra dependency). Targets the offline-first deployment scenario
documented in the V2 plan §하드 제약.

JSON file schema (``kds_clauses.json``):

    {
      "version": "v1",
      "clauses": [
        {
          "code": "KDS 14 24 00",
          "section": "5.3",
          "title": "휨강도",
          "category_hints": ["structural_member", "dimension"],
          "keywords": ["보", "휨", "단면"],
          "text": "휨모멘트에 대한 단면강도는..."
        },
        ...
      ]
    }

Retrieval algorithm (intentionally simple — Phase L+ may upgrade
to embedding-based or BM25):
  1. Filter clauses by ``category_hints`` overlap with the
     dispatcher's candidate_categories
  2. Within filtered set, score each clause by keyword-overlap with
     the canonicalised zone_evidence (case-insensitive substring
     match, weighted by keyword length)
  3. Return top-K clauses formatted as ``"[code §section] title:
     text"`` for direct LLM-prompt injection

File location resolution (descending priority):
  1. Explicit ``path`` constructor argument
  2. ``%LOCALAPPDATA%/DrawingCompareWorkbench/kds_clauses.json``
  3. ``./data/kds_clauses.json`` (development)
  4. ``project_root/data/kds_clauses.json``

Returns "" when the file isn't found OR no clauses match — the LLM
proceeds without RAG context (safe default).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, List, Optional

from .base import AbstractKdsRagClient
from . import register_kds_rag_client


logger = logging.getLogger(__name__)


CLIENT_ID = "local_json_kds"
DEFAULT_FILENAME = "kds_clauses.json"


def _candidate_paths(filename: str = DEFAULT_FILENAME) -> List[Path]:
    """All file locations the resolver checks, in priority order."""

    out: List[Path] = []
    appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if appdata:
        out.append(Path(appdata) / "DrawingCompareWorkbench" / filename)
    out.append(Path.cwd() / "data" / filename)
    try:
        proj_root = Path(__file__).resolve().parents[5]
        out.append(proj_root / "data" / filename)
    except IndexError:
        pass
    return out


def _resolve_clauses_path(
    explicit: Optional[Path] = None,
    filename: str = DEFAULT_FILENAME,
) -> Optional[Path]:
    """Locate the kds_clauses.json file. Returns None when no
    candidate exists."""

    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    for candidate in _candidate_paths(filename):
        if candidate.exists():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Backend implementation
# ---------------------------------------------------------------------------


class LocalJsonKdsRagClient(AbstractKdsRagClient):
    """KDS clause retrieval from a local JSON file."""

    client_id = CLIENT_ID

    def __init__(
        self,
        *,
        path: Optional[Path] = None,
        filename: str = DEFAULT_FILENAME,
    ) -> None:
        super().__init__()
        self._explicit_path = path
        self._filename = filename
        self._clauses: List[dict] = []
        self._loaded: bool = False
        self._resolved_path: Optional[Path] = None
        # Phase L5 review fix (Issue #4): protect lazy file load
        # against concurrent retrieve() calls from parallel LLM
        # dispatcher threads. Without the lock, two threads both
        # passing the `if self._loaded` guard would each read +
        # parse the file. Python's GIL keeps the final write atomic
        # (no corruption), but the redundant I/O wastes a few ms per
        # parallel cold-start. Mirrors the AbstractEmbeddingBackend
        # warmup-lock pattern from Phase H 2nd-review.
        self._load_lock = threading.Lock()

    @classmethod
    def probe_available(cls) -> bool:
        """True iff a kds_clauses.json file exists in any standard
        location. Cheap (file stat only)."""
        return _resolve_clauses_path() is not None

    def _ensure_loaded(self) -> None:
        # Double-checked locking: cheap fast-path read of self._loaded
        # outside the lock; lock only on first call. After the first
        # successful load, every subsequent call returns immediately
        # without lock contention.
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:  # another thread won the race
                return
            path = _resolve_clauses_path(self._explicit_path, self._filename)
            if path is None:
                self._clauses = []
                self._loaded = True
                return
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("kds_clauses.json read failed: %s", exc)
                self._clauses = []
                self._loaded = True
                return
            if not isinstance(payload, dict):
                self._clauses = []
                self._loaded = True
                return
            clauses = payload.get("clauses") or []
            if isinstance(clauses, list):
                # Filter to dict entries with required keys
                self._clauses = [
                    c for c in clauses
                    if isinstance(c, dict) and c.get("text")
                ]
            else:
                self._clauses = []
            self._loaded = True
            self._resolved_path = path
            logger.info(
                "Loaded %d KDS clauses from %s",
                len(self._clauses), path,
            )

    def _retrieve_impl(
        self,
        zone_evidence: str,
        candidate_categories: List[Any],
        *,
        top_k: int,
        timeout_s: float,
    ) -> str:
        self._ensure_loaded()
        if not self._clauses:
            return ""

        evidence_lower = zone_evidence.lower()
        # Normalise candidate categories to strings (accepts both
        # ChangeCategory enum values and bare strings).
        cand_strs = set()
        for c in candidate_categories:
            if hasattr(c, "value"):
                cand_strs.add(str(c.value))
            else:
                cand_strs.add(str(c))

        # Score each clause
        scored: List[tuple[float, dict]] = []
        for clause in self._clauses:
            # Category hint filter — when a clause specifies hints,
            # require overlap with our candidates. Hint-less clauses
            # apply to all categories.
            hints = clause.get("category_hints") or []
            if hints:
                hint_set = {str(h) for h in hints}
                if cand_strs and not (hint_set & cand_strs):
                    continue
            # Keyword score: sum of len(keyword) for each keyword
            # whose lowercase form appears in the evidence
            score = 0.0
            for kw in clause.get("keywords") or []:
                kw_str = str(kw).lower()
                if kw_str and kw_str in evidence_lower:
                    score += float(len(kw_str))
            if score > 0.0:
                scored.append((score, clause))

        if not scored:
            return ""
        # Sort descending + take top-K
        scored.sort(key=lambda item: -item[0])
        top = scored[: max(1, top_k)]

        # Format for LLM prompt — one line per clause
        lines: List[str] = []
        for _, clause in top:
            code = clause.get("code") or ""
            section = clause.get("section") or ""
            title = clause.get("title") or ""
            text = clause.get("text") or ""
            head = f"[{code}"
            if section:
                head += f" §{section}"
            head += "]"
            line = f"{head} {title}: {text}".strip()
            # Truncate per-clause text to keep prompt size bounded
            if len(line) > 400:
                line = line[:397] + "..."
            lines.append(line)
        return "\n".join(lines)


def _factory(**kwargs) -> LocalJsonKdsRagClient:
    return LocalJsonKdsRagClient(**kwargs)


try:
    register_kds_rag_client(CLIENT_ID, _factory, replace=True)
except Exception:  # noqa: BLE001
    logger.debug(
        "Could not auto-register %s client at import time",
        CLIENT_ID, exc_info=True,
    )


__all__ = [
    "CLIENT_ID",
    "DEFAULT_FILENAME",
    "LocalJsonKdsRagClient",
]
