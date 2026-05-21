# -*- coding: utf-8 -*-
"""``LlmBackend`` protocol — unified contract for Stage-3 LLM backends.

Mirrors the Phase H/I ``EmbeddingBackend`` pattern: define a runtime-
checkable Protocol so concrete backends (Ollama, OpenAI-compat,
llama-cpp-python decoder mode, vLLM, …) all implement the same surface.
The dispatcher (``llm_classifier.py``, J2) speaks ONLY this protocol —
swapping backends is a config flip, never a code change.

Architectural difference from EmbeddingBackend:
  * Embedding: ``encode(text) -> vector``. Cosine math downstream.
  * LLM: ``classify(text, candidates) -> result``. The LLM PICKS one
    of N candidate categories with rationale + confidence, AFTER
    Stage-2 has already narrowed the candidate set. We never let the
    LLM hallucinate categories outside the schema's ChangeCategory
    enum — narrowing the candidates is the safety boundary.

Cost model: Embedding is per-zone CPU (~5-50 ms/zone via Qwen GGUF or
mxbai ONNX). LLM is per-zone network round-trip + token generation
(~1-5 s/zone via Ollama). Stage-3 only fires when Stage-2 abstained
or the margin was tight, so the cost stays bounded.
"""

from __future__ import annotations

import abc
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from ..schema import ChangeCategory


class LlmBackendUnavailableError(RuntimeError):
    """Raised when a requested LLM backend isn't installed / running.

    Like ``BackendUnavailableError`` (the embedding equivalent), the
    classifier catches this and abstains — Stage-3 produces no result
    and the Stage-2 (or Stage-1) classification stands.
    """


@dataclass(frozen=True)
class LlmClassificationResult:
    """Per-zone Stage-3 LLM result.

    Returned by ``LlmBackend.classify`` when the LLM successfully
    picked one of the candidate categories. None means the LLM
    abstained (couldn't pick from candidates, JSON parse failed,
    timeout, etc.) and the dispatcher should fall back to the
    Stage-2 / Stage-1 result.

    Attributes:
        category: One of the candidates passed in. NEVER outside
            the ChangeCategory enum — this is the safety boundary
            against LLM hallucination.
        confidence: 0.0-1.0. Subjective; LLM-reported. Used by
            the dispatcher for downstream UI sorting / filtering
            but NOT for accept/reject (the cascade already accepted
            by reaching Stage-3).
        rationale_ko: One-sentence Korean explanation of WHY this
            category was picked. Surfaced in the workbench detail
            panel for the reviewer.
        kds_references: Optional list of KDS clause IDs the LLM
            cited (RAG integration — Phase K). Empty when no RAG
            context was provided.
        elapsed_ms: Wall-clock LLM round-trip time (network +
            generation). Logged via viewer_perf for J2 tuning.
    """

    category: ChangeCategory
    confidence: float
    rationale_ko: str
    kds_references: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0


@runtime_checkable
class LlmBackend(Protocol):
    """Stage-3 LLM contract.

    Required attributes:
        backend_id: Stable string ID matching the registry key
            (e.g. "ollama_exaone", "stub_llm")
        model_name: Concrete model identifier (e.g. "exaone3.5:7.8b")
            — surfaced in dispatcher logs + manifest.

    Required methods:
        classify: Per-zone classification with optional KDS context
        warmup: Cheap connectivity / model-load test
        is_ready: True after warmup completes
        probe_available: classmethod — cheap availability check
            (HTTP ping + tag list lookup). NO model load.
    """

    backend_id: str
    model_name: str

    def classify(
        self,
        zone_evidence: str,
        candidate_categories: list[ChangeCategory],
        *,
        kds_context: str = "",
        timeout_s: float = 10.0,
    ) -> Optional[LlmClassificationResult]:
        """Ask the LLM to pick the best category from the candidates.

        Args:
            zone_evidence: Canonicalised zone description (same text
                that Stage-2 fed to the embedding backend).
            candidate_categories: Narrowed set from Stage-2 (typically
                top-3 by cosine score). LLM MUST pick one of these.
            kds_context: Optional KDS clause text pre-fetched by the
                RAG layer (Phase K). Empty when RAG is off.
            timeout_s: Maximum wall-clock for the round-trip.

        Returns:
            LlmClassificationResult on success.
            None when the LLM abstained (parse failure, timeout,
            picked a category outside candidates, etc.). Caller
            falls back to Stage-2 result.

        MUST NOT raise — abstain via None instead so the cascade
        keeps the Stage-2 answer as a safety net.
        """
        ...

    def warmup(self) -> None:
        """Verify the backend is reachable. For Ollama: HTTP /api/tags
        + check the model is pulled. For stub: no-op.

        MUST be safe to call from a background thread (workbench
        prepare_async pattern). MUST be idempotent.
        """
        ...

    def is_ready(self) -> bool:
        """Cheap status check. No I/O, no LLM call."""
        ...

    @classmethod
    def probe_available(cls) -> bool:
        """Cheap, no-side-effect availability check.

        Returns True iff the LLM service is reachable AND the
        configured model is loaded. For Ollama: GET /api/tags then
        check our model is in the list. For stub: always True.

        MUST NOT actually invoke the LLM — used by the dispatcher's
        bootstrap before paying the model-load cost.
        """
        ...


class AbstractLlmBackend(abc.ABC):
    """Optional convenience base — provides warm-up timing + error
    tracking + a default ``is_ready`` based on whether ``warmup`` ran.

    Mirrors ``AbstractEmbeddingBackend`` (Phase I). Subclasses
    override ``_warmup_impl`` (cheap connectivity check) +
    ``_classify_impl`` (LLM round-trip).
    """

    backend_id: str = "abstract_llm"
    model_name: str = "abstract"

    def __init__(self) -> None:
        self._ready: bool = False
        self._warmup_ms: Optional[float] = None
        self._last_error: Optional[BaseException] = None
        self._warmup_lock = threading.Lock()

    @abc.abstractmethod
    def _warmup_impl(self) -> None:
        """Cheap connectivity check. Raise LlmBackendUnavailableError
        when the service is not reachable."""

    @abc.abstractmethod
    def _classify_impl(
        self,
        zone_evidence: str,
        candidate_categories: list[ChangeCategory],
        *,
        kds_context: str,
        timeout_s: float,
    ) -> Optional[LlmClassificationResult]:
        """Per-call LLM round-trip. Returns None on any error path."""

    # ---- LlmBackend protocol surface -----------------------------------

    def warmup(self) -> None:
        # Same double-checked-locking pattern as embedding backends.
        if self._ready:
            return
        with self._warmup_lock:
            if self._ready:
                return
            t0 = time.perf_counter()
            try:
                self._warmup_impl()
                self._ready = True
            except BaseException as exc:  # noqa: BLE001
                self._last_error = exc
                self._ready = False
                raise
            finally:
                self._warmup_ms = (time.perf_counter() - t0) * 1000.0

    def is_ready(self) -> bool:
        return self._ready

    def classify(
        self,
        zone_evidence: str,
        candidate_categories: list[ChangeCategory],
        *,
        kds_context: str = "",
        timeout_s: float = 10.0,
    ) -> Optional[LlmClassificationResult]:
        if not zone_evidence or not candidate_categories:
            return None  # nothing to classify
        if not self._ready:
            try:
                self.warmup()
            except LlmBackendUnavailableError:
                return None  # abstain on backend down
        try:
            return self._classify_impl(
                zone_evidence,
                list(candidate_categories),
                kds_context=str(kds_context or ""),
                timeout_s=float(timeout_s),
            )
        except BaseException:  # noqa: BLE001
            # NEVER raise from the public classify — abstain instead.
            # Caller falls back to Stage-2 result.
            return None

    # ---- Diagnostics --------------------------------------------------

    def warmup_ms(self) -> Optional[float]:
        return self._warmup_ms

    def last_error(self) -> Optional[BaseException]:
        return self._last_error

    @classmethod
    def probe_available(cls) -> bool:
        """Default: assume always available. Concrete backends
        (OllamaExaoneLlmBackend) override with HTTP / file checks."""
        return True


__all__ = [
    "LlmBackend",
    "AbstractLlmBackend",
    "LlmBackendUnavailableError",
    "LlmClassificationResult",
]
