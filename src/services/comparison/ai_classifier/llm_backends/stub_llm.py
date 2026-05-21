# -*- coding: utf-8 -*-
"""Phase J Step 5 (J2) — deterministic stub LLM backend.

Used in:
  * Unit tests — exercises the dispatcher cascade without needing
    Ollama installed
  * Dev mode — when the user has ``llm_backend_id="stub_llm"`` set
    while developing the GUI / cascade and doesn't want to wait
    1-5 s/zone for real LLM round-trips
  * "What if AI was off but I still want hybrid metadata in
    raw_evidence?" diagnostic mode

Behaviour:
  * Picks the FIRST candidate as the answer (deterministic — no
    randomness, no actual LLM call)
  * Confidence = 0.5 (admittedly low; communicates "this is a stub")
  * Rationale = a fixed Korean sentence indicating it's a stub
  * Always available — probe_available() is hardcoded True
  * 0 ms elapsed (no real round-trip)
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from ..schema import ChangeCategory
from .base import AbstractLlmBackend, LlmClassificationResult
from . import register_llm_backend

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BACKEND_ID = "stub_llm"
MODEL_NAME = "stub-deterministic-v1"


# ---------------------------------------------------------------------------
# Backend implementation
# ---------------------------------------------------------------------------


class StubLlmBackend(AbstractLlmBackend):
    """Deterministic stub LLM — picks the first candidate."""

    backend_id = BACKEND_ID
    model_name = MODEL_NAME

    def __init__(self) -> None:
        super().__init__()

    @classmethod
    def probe_available(cls) -> bool:
        """Stub is always available — no model files, no network."""
        return True

    # ---- AbstractLlmBackend hooks --------------------------------------

    def _warmup_impl(self) -> None:
        # No real init — just mark ready. Tests rely on this being
        # cheap so warmup() in fixtures doesn't add measurable latency.
        return

    def _classify_impl(
        self,
        zone_evidence: str,
        candidate_categories: list[ChangeCategory],
        *,
        kds_context: str,
        timeout_s: float,
    ) -> Optional[LlmClassificationResult]:
        """Deterministic: first candidate wins.

        The classify() wrapper in AbstractLlmBackend already filters
        out empty evidence / empty candidates, so we can assume both
        are non-empty here.
        """

        t0 = time.perf_counter()
        first = candidate_categories[0]
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return LlmClassificationResult(
            category=first,
            confidence=0.5,
            rationale_ko=(
                "(stub) 첫 번째 후보 자동 선택 — 실제 LLM 미연결 상태"
            ),
            kds_references=[],
            elapsed_ms=elapsed_ms,
        )


# ---------------------------------------------------------------------------
# Self-registration
# ---------------------------------------------------------------------------


def _factory(**kwargs) -> StubLlmBackend:
    return StubLlmBackend(**kwargs)


try:
    register_llm_backend(BACKEND_ID, _factory, replace=True)
except Exception:  # noqa: BLE001
    logger.debug(
        "Could not auto-register %s backend at import time",
        BACKEND_ID, exc_info=True,
    )


__all__ = [
    "BACKEND_ID",
    "MODEL_NAME",
    "StubLlmBackend",
]
