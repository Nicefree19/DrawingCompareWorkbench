# -*- coding: utf-8 -*-
"""Manual user overrides for the PDF page-matcher (Phase H4).

When :func:`match_pdf_pages` produces a ``REVIEW_REQUIRED`` candidate,
flags pages as ``UNMATCHED_*``, or simply gets a confident-looking pair
wrong, the user needs an escape hatch: pick the correct B page for an A
page (or remove a page from matching entirely) and have the next compare
run honour that decision.

This module owns the persistent representation of those decisions.

JSON layout (versioned, forward-compat):

    {
      "version": 1,
      "schema": "manual_page_override.v1",
      "overrides": {
        "<pair_uuid>": [
          {
            "page_a": 0,
            "page_b": 2,
            "reason": "auto matched to wrong sheet",
            "user": "user",
            "timestamp": "2026-01-01T12:34:56+00:00"
          },
          ...
        ],
        ...
      }
    }

Apply semantics (consumed by :func:`apply_overrides`):

    page_b >= 0   →  force matched pair (page_a → page_b),
                     replacing whatever the auto-matcher returned for
                     either of those two pages.
    page_b == -1  →  force ``page_a`` to be unmatched, regardless of any
                     auto-match.

The override always wins over the auto-match. If two overrides on the
same pair_id conflict (same page_a or same page_b appearing twice), the
later entry wins — matches the natural "last write wins" mental model
when the user is iterating in the GUI.

Out of scope (deliberately):
    - Cross-pair overrides
    - Per-pair toggle to disable auto-match
    - Per-pair score tweaks (use the global thresholds for that)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Union

from src.services.comparison.page_matcher import (
    PageMatchCandidate,
    PageMatchStatus,
)

logger = logging.getLogger(__name__)


SCHEMA_NAME = "manual_page_override.v1"
SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PageOverrideEntry:
    """One user-authored override for a single A page.

    ``page_b == -1`` is the sentinel for "force unmatched" — distinct
    from a missing entry, which means "let the auto-matcher decide".
    """

    page_a: int
    page_b: int
    reason: str = ""
    user: str = "user"
    timestamp: str = ""  # ISO-8601 UTC (filled by ``new_entry``)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "PageOverrideEntry":
        return cls(
            page_a=int(data.get("page_a", -1)),  # type: ignore[arg-type]
            page_b=int(data.get("page_b", -1)),  # type: ignore[arg-type]
            reason=str(data.get("reason", "")),
            user=str(data.get("user", "user")),
            timestamp=str(data.get("timestamp", "")),
        )


def new_entry(
    page_a: int,
    page_b: int,
    *,
    reason: str = "",
    user: str = "user",
) -> PageOverrideEntry:
    """Convenience constructor that stamps the current UTC timestamp."""

    return PageOverrideEntry(
        page_a=int(page_a),
        page_b=int(page_b),
        reason=reason,
        user=user,
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def load_overrides(path: Union[str, Path]) -> Dict[str, List[PageOverrideEntry]]:
    """Load overrides from disk; returns ``{}`` when the file is absent.

    Tolerates missing/malformed files (logs a warning and returns empty).
    The intent is "best effort" — a corrupt overrides file should not
    break a compare run, only revert to the auto-match behaviour.
    """

    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("manual_page_overrides: failed to read %s: %s", p, exc)
        return {}

    if not isinstance(raw, Mapping):
        logger.warning("manual_page_overrides: top-level not a mapping in %s", p)
        return {}

    schema = str(raw.get("schema", ""))
    if schema and schema != SCHEMA_NAME:
        logger.warning(
            "manual_page_overrides: unknown schema %r in %s — attempting best-effort parse",
            schema, p,
        )

    out: Dict[str, List[PageOverrideEntry]] = {}
    overrides_section = raw.get("overrides", {})
    if not isinstance(overrides_section, Mapping):
        logger.warning(
            "manual_page_overrides: 'overrides' must be a mapping in %s", p,
        )
        return {}

    for pair_id, entries in overrides_section.items():
        if not isinstance(entries, list):
            logger.warning(
                "manual_page_overrides: pair %s has non-list entries; skipping",
                pair_id,
            )
            continue
        parsed: List[PageOverrideEntry] = []
        for raw_entry in entries:
            if not isinstance(raw_entry, Mapping):
                continue
            try:
                parsed.append(PageOverrideEntry.from_dict(raw_entry))
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "manual_page_overrides: bad entry on pair %s: %s",
                    pair_id, exc,
                )
        if parsed:
            out[str(pair_id)] = parsed
    return out


def save_overrides(
    path: Union[str, Path],
    overrides: Mapping[str, Sequence[PageOverrideEntry]],
) -> Path:
    """Atomic write of overrides to disk.

    Writes to a sibling ``.tmp`` file then ``os.replace`` so a crash
    mid-write does not leave a half-formed JSON behind.
    """

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": SCHEMA_VERSION,
        "schema": SCHEMA_NAME,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overrides": {
            str(pair_id): [e.to_dict() for e in entries]
            for pair_id, entries in overrides.items()
            if entries
        },
    }

    # Atomic write — tempfile in same dir so os.replace stays in one volume
    fd, tmp_path = tempfile.mkstemp(
        prefix=".overrides_", suffix=".tmp", dir=str(p.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, p)
    except Exception:
        # Cleanup half-written tmp on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return p


# ---------------------------------------------------------------------------
# Mutation (used by GUI)
# ---------------------------------------------------------------------------


def upsert_override(
    overrides: Dict[str, List[PageOverrideEntry]],
    pair_id: str,
    entry: PageOverrideEntry,
) -> Dict[str, List[PageOverrideEntry]]:
    """Insert or replace an override for ``pair_id`` keyed by ``page_a``.

    Mutates and returns the same dict for chaining. If an override
    already exists for the same ``page_a``, it's replaced (matches the
    "last write wins" GUI mental model).
    """

    bucket = list(overrides.get(pair_id, []))
    bucket = [e for e in bucket if e.page_a != entry.page_a]
    bucket.append(entry)
    bucket.sort(key=lambda e: e.page_a)
    overrides[pair_id] = bucket
    return overrides


def remove_override(
    overrides: Dict[str, List[PageOverrideEntry]],
    pair_id: str,
    page_a: int,
) -> Dict[str, List[PageOverrideEntry]]:
    """Remove the override for ``page_a`` on ``pair_id``, if any.

    Mutates and returns the same dict. Empty buckets are pruned so the
    saved JSON stays tidy.
    """

    bucket = overrides.get(pair_id)
    if not bucket:
        return overrides
    new_bucket = [e for e in bucket if e.page_a != page_a]
    if new_bucket:
        overrides[pair_id] = new_bucket
    else:
        overrides.pop(pair_id, None)
    return overrides


# ---------------------------------------------------------------------------
# Application — modify match_pdf_pages output
# ---------------------------------------------------------------------------


def apply_overrides(
    candidates: Sequence[PageMatchCandidate],
    overrides: Iterable[PageOverrideEntry],
    *,
    n_a: int,
    n_b: int,
) -> List[PageMatchCandidate]:
    """Apply user overrides on top of an auto-matched candidate list.

    Algorithm:
      1. Validate overrides — drop any with out-of-range page indices,
         log a warning so the GUI can surface the issue but never crash.
      2. Drop any matched pair whose ``page_a`` or ``page_b`` is targeted
         by an override (either the override's ``page_a`` or its
         ``page_b``).
      3. For each override with ``page_b >= 0``, push a new candidate
         with status :data:`PageMatchStatus.AUTO_CONFIRMED`, score 1.0,
         and ``score_breakdown = {"manual_override": 1.0}`` so downstream
         consumers can recognise it.
      4. Recompute ``UNMATCHED_A`` / ``UNMATCHED_B`` from whatever pages
         are still unaccounted for.

    ``n_a`` / ``n_b`` are the page counts of the underlying PDFs (from
    ``len(desc_a)`` / ``len(desc_b)``). Required so we can recompute the
    unmatched lists correctly even when the auto-matcher missed pages.
    """

    overrides_list = list(overrides)
    if not overrides_list:
        # Cheap fast-path — caller can avoid wrapping when no overrides exist
        return list(candidates)

    # Step 1: validate
    valid_overrides: List[PageOverrideEntry] = []
    for entry in overrides_list:
        if entry.page_a < 0 or entry.page_a >= n_a:
            logger.warning(
                "manual override page_a=%d out of range [0, %d) — skipping",
                entry.page_a, n_a,
            )
            continue
        if entry.page_b >= n_b:
            logger.warning(
                "manual override page_b=%d out of range [0, %d) — skipping",
                entry.page_b, n_b,
            )
            continue
        # page_b == -1 (force unmatched) is allowed
        if entry.page_b < -1:
            logger.warning(
                "manual override page_b=%d invalid (only -1 or >=0) — skipping",
                entry.page_b,
            )
            continue
        valid_overrides.append(entry)

    if not valid_overrides:
        return list(candidates)

    # Apply "last write wins" inside this single apply call too — if the
    # caller passed in two overrides for the same page_a, keep the last.
    by_page_a: Dict[int, PageOverrideEntry] = {}
    for entry in valid_overrides:
        by_page_a[entry.page_a] = entry

    overridden_a_pages = set(by_page_a.keys())
    overridden_b_pages = {e.page_b for e in by_page_a.values() if e.page_b >= 0}

    # Step 2: drop conflicting matched pairs from the auto-matched list
    surviving: List[PageMatchCandidate] = []
    for c in candidates:
        if c.status in {PageMatchStatus.UNMATCHED_A, PageMatchStatus.UNMATCHED_B}:
            # Will be recomputed in Step 4
            continue
        if c.page_a_index in overridden_a_pages:
            continue
        if c.page_b_index in overridden_b_pages:
            continue
        surviving.append(c)

    # Step 3: push manual overrides as AUTO_CONFIRMED with marker score
    for entry in by_page_a.values():
        if entry.page_b < 0:
            # "Force unmatched" — handled in Step 4 by exclusion
            continue
        surviving.append(PageMatchCandidate(
            page_a_index=entry.page_a,
            page_b_index=entry.page_b,
            score=1.0,
            status=PageMatchStatus.AUTO_CONFIRMED,
            score_breakdown={
                "manual_override": 1.0,
                "override_reason": 0.0,  # placeholder so callers can detect overrides
            },
        ))

    # Step 4: recompute unmatched
    matched_a = {c.page_a_index for c in surviving if c.is_matched}
    matched_b = {c.page_b_index for c in surviving if c.is_matched}

    # "Force unmatched" pages still count as unmatched_a (the user's
    # intent), they just shouldn't appear in matched_a.
    for i in range(n_a):
        if i in matched_a:
            continue
        surviving.append(PageMatchCandidate(
            page_a_index=i,
            page_b_index=-1,
            score=0.0,
            status=PageMatchStatus.UNMATCHED_A,
        ))
    for j in range(n_b):
        if j in matched_b:
            continue
        surviving.append(PageMatchCandidate(
            page_a_index=-1,
            page_b_index=j,
            score=0.0,
            status=PageMatchStatus.UNMATCHED_B,
        ))

    # Sort matched pairs by page_a for deterministic output (matches
    # match_pdf_pages's own ordering).
    surviving.sort(key=lambda c: (
        0 if c.is_matched else 1,  # matched first
        c.page_a_index if c.page_a_index >= 0 else 10**9,
        c.page_b_index if c.page_b_index >= 0 else 10**9,
    ))
    return surviving


# ---------------------------------------------------------------------------
# Public re-exports
# ---------------------------------------------------------------------------


__all__ = [
    "SCHEMA_NAME",
    "SCHEMA_VERSION",
    "PageOverrideEntry",
    "new_entry",
    "load_overrides",
    "save_overrides",
    "upsert_override",
    "remove_override",
    "apply_overrides",
]
