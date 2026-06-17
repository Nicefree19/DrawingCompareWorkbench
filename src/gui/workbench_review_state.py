"""Pure review-state helpers for the workbench (MONO-4 #6).

The review-state cluster is heavily UI-entangled — the status setters orchestrate
~7 widgets and lazily persist via ``_result``-derived paths — so only the pure,
stateless record logic is cleanly extractable. These two functions (status
localization + per-pair done/confirmed counting) are exactly what
``tests/unit/services/comparison/test_review_state_characterization.py`` pins;
``DrawingCompareWorkbenchV2`` delegates to them. The owning ``_review_records_v2``
dict + persistence path stay on V2 (they thread through untested UI flows).
"""

from __future__ import annotations

from typing import Mapping, Tuple

from src.services.comparison.review_project import normalize_review_status


def review_status_ko(status: str) -> str:
    """Render a review status as its Korean label (unknown -> 추가 검토)."""
    return {
        "needs_review": "추가 검토",
        "confirmed": "확인",
        "hold": "보류",
        "false_positive": "오탐",
    }.get(normalize_review_status(status), "추가 검토")


def count_review_records(records: Mapping[str, object], pair_id: str) -> Tuple[int, int]:
    """Count (done, confirmed) review records for one pair.

    ``done`` = any record whose normalized status is not ``needs_review``;
    ``confirmed`` = records explicitly marked ``confirmed``. Scoped to keys that
    start with ``"{pair_id}:"`` (the ``review_state_key`` format).
    """
    prefix = f"{pair_id}:"
    done = 0
    confirmed = 0
    for key, record in (records or {}).items():
        if not str(key).startswith(prefix):
            continue
        status = normalize_review_status(record.status)
        if status != "needs_review":
            done += 1
        if status == "confirmed":
            confirmed += 1
    return done, confirmed
