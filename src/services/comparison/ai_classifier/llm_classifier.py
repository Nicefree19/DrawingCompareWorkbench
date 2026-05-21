# -*- coding: utf-8 -*-
"""Phase J Step 5 (J2) — Stage-3 LLM classifier dispatcher.

Mirrors ``embedding_classifier.EmbeddingClassifierDispatcher`` (Phase I)
but for the LLM tier. Owns a single LLM backend instance per process
+ exposes ``classify_zone`` + ``prepare_async`` for the Workbench
to warm up in the background.

Cascade contract (enforced by ``public_api.classify_zones``):

    Stage 1 (heuristic) ──┐
                           ├─→ Stage 2 (embedding, may abstain)
                           │     │
                           │     ▼
                           │   ChangeClassification (top-1 + top-3 candidates)
                           │     │
                           │     ▼
                           │   Stage 3 (LLM) — invoked when:
                           │       cfg.use_llm = True AND
                           │       (Stage-2 abstained OR
                           │        Stage-2 confidence < llm_invoke_below_confidence)
                           │     │
                           │     ▼
                           ▼  LlmClassificationResult or None
                       Final classification

LLM result → ``ChangeClassification(classifier_used="hybrid")`` with:
  * Stage-1 result stashed in raw_evidence["stage1_*"]
  * Stage-2 candidates + scores in raw_evidence["stage2_*"]
  * LLM rationale in raw_evidence["llm_rationale_ko"]
  * KDS references in kds_references field

Failure modes:
  * LLM backend unavailable on warmup → dispatcher abstains silently
    (subsequent classify_zone returns None — caller keeps Stage-2)
  * LLM classify timeout / parse error / picks-out-of-candidates →
    None per zone (caller keeps Stage-2 for that zone)
  * No exceptions ever bubble up to public_api.classify_zones — the
    cascade is bounded by Stage-2 as the safety net.

Singleton cache: same per-config caching as embedding dispatcher so
the LLM backend's HTTP session / connection pool is reused.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from .schema import (
    AiClassifierConfig,
    ChangeCategory,
    ChangeClassification,
    Severity,
    DEFAULT_ACTION_BY_SEVERITY,
    DEFAULT_SEVERITY_BY_CATEGORY,
)
from .llm_backends import (
    LLM_BACKEND_REGISTRY,
    LlmBackend,
    LlmBackendUnavailableError,
    LlmClassificationResult,
    get_llm_backend,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_LLM_BACKEND_ID = "ollama_exaone"


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class LlmClassifierDispatcher:
    """Stage-3 LLM dispatcher — owns backend lifecycle + classify hook.

    One instance per (backend_id, host, model_name) tuple via
    ``get_llm_dispatcher()`` so the HTTP session / model-load only
    happens once per process.
    """

    def __init__(
        self,
        config: AiClassifierConfig,
        *,
        backend_id: Optional[str] = None,
        backend_kwargs: Optional[dict] = None,
    ) -> None:
        self._config = config
        self._configured_backend_id = (
            backend_id or config.llm_backend_id or DEFAULT_LLM_BACKEND_ID
        )
        # Default kwargs threaded from config (host / model / timeout).
        # Subclass-specific tuning (max_tokens, temperature, etc.) is
        # not yet config-exposed — uses backend defaults.
        kwargs = dict(backend_kwargs or {})
        if self._configured_backend_id == "ollama_exaone":
            kwargs.setdefault("host", config.llm_host)
            kwargs.setdefault("model_name", config.llm_model)
            kwargs.setdefault(
                "default_timeout_s", float(config.llm_timeout_s),
            )
        self._backend_kwargs = kwargs
        self._top_k = max(1, int(config.llm_top_k_candidates))
        self._invoke_below = float(config.llm_invoke_below_confidence)
        self._timeout_s = float(config.llm_timeout_s)
        # Phase K2 — KDS RAG client config
        self._use_kds_rag = bool(getattr(config, "use_kds_rag", False))
        self._kds_rag_client_id = (
            getattr(config, "kds_rag_client_id", "stub_kds") or "stub_kds"
        )
        self._kds_rag_top_k = max(1, int(
            getattr(config, "kds_rag_top_k", 3)
        ))
        self._kds_rag_timeout_s = float(
            getattr(config, "kds_rag_timeout_s", 5.0)
        )

        # Lazy-init state
        self._backend: Optional[LlmBackend] = None
        self._active_backend_id: Optional[str] = None
        # Phase K2 — KDS RAG client lazily built on first classify
        self._kds_rag_client: Optional[Any] = None
        # Phase L5 review fix (Issue #4): protect lazy RAG client
        # init against concurrent classify_zone calls from parallel
        # threads. Same double-checked locking pattern as the
        # embedding/LLM backend warmup.
        self._kds_rag_client_lock = threading.Lock()
        self._prepared: bool = False
        self._prepare_lock = threading.Lock()
        self._last_error: Optional[BaseException] = None
        self._prepare_ms: Optional[float] = None

    # ---- Lifecycle ------------------------------------------------------

    def prepare(self) -> None:
        """Synchronously instantiate + warm up the LLM backend.

        Safe to call multiple times. Raises whatever the backend
        raises on warmup failure — callers (public_api cascade)
        catch and abstain.
        """

        if self._prepared:
            return
        with self._prepare_lock:
            if self._prepared:
                return
            t0 = time.perf_counter()
            try:
                self._do_prepare()
                self._prepared = True
            except BaseException as exc:  # noqa: BLE001
                self._last_error = exc
                self._prepared = False
                raise
            finally:
                self._prepare_ms = (time.perf_counter() - t0) * 1000.0

    def _do_prepare(self) -> None:
        backend = get_llm_backend(
            self._configured_backend_id, **self._backend_kwargs,
        )
        backend.warmup()
        self._backend = backend
        self._active_backend_id = self._configured_backend_id

    def prepare_async(self) -> threading.Thread:
        """Background warm-up — daemon thread. Same pattern as
        embedding dispatcher."""

        t = threading.Thread(
            target=self._prepare_async_target,
            name="ai-llm-prepare",
            daemon=True,
        )
        t.start()
        return t

    def _prepare_async_target(self) -> None:
        try:
            self.prepare()
        except BaseException:  # noqa: BLE001
            logger.exception("Background LLM prepare() failed")

    def is_ready(self) -> bool:
        return self._prepared

    def last_error(self) -> Optional[BaseException]:
        return self._last_error

    def prepare_ms(self) -> Optional[float]:
        return self._prepare_ms

    # ---- Per-zone classification ---------------------------------------

    def should_invoke(self, stage2_result: ChangeClassification) -> bool:
        """Decide whether to escalate this Stage-2 result to Stage-3.

        Policy:
          * classifier_used="heuristic" (Stage-2 abstained) → invoke
            (Stage-1 alone may benefit from LLM disambiguation)
          * classifier_used="embedding" with confidence < threshold
            → invoke
          * classifier_used="embedding" with confidence ≥ threshold
            → skip (confident Stage-2 wins)
          * classifier_used="hybrid" / "disabled" / "error" → SKIP
            (already passed through Stage-3 once, or AI off entirely,
            or Stage-1 itself crashed — re-running LLM would cause a
            re-classification loop or noise)

        Reduces LLM call volume — without this gate, every zone goes
        through 1-3 s of LLM round-trip and the workbench grinds to a
        halt on large folders.

        Phase L3 review fix: changed from "anything not 'embedding'
        → invoke" to explicit allowlist {"heuristic", "embedding"}
        so re-feeding a "hybrid" result through the cascade doesn't
        loop, and "disabled"/"error" zones don't waste LLM calls.
        """

        used = stage2_result.classifier_used
        if used == "heuristic":
            # Stage-2 abstained. LLM may help disambiguate.
            return True
        if used == "embedding":
            # Stage-2 produced an answer — gate on its confidence.
            return float(stage2_result.confidence) < self._invoke_below
        # "hybrid" (re-cascade), "disabled", "error", any future value
        # → conservative skip.
        return False

    def classify_zone(
        self,
        zone: dict,
        stage1_result: ChangeClassification,
        stage2_result: ChangeClassification,
        candidate_categories: list[ChangeCategory],
        *,
        kds_context: str = "",
    ) -> Optional[ChangeClassification]:
        """Run Stage-3 LLM on one zone.

        Returns:
          * ChangeClassification with classifier_used="hybrid" when
            the LLM picked a candidate AND the dispatcher accepts it
          * None when the LLM abstained (timeout, parse error, picked
            outside candidates) — caller keeps Stage-2 result

        NEVER raises — abstain via None instead.
        """

        if not isinstance(zone, dict):
            return None

        # Lazy first-call init.
        #
        # Phase N hotfix — prepare-failure cooldown (mirrors the
        # embedding dispatcher fix). Without this, a non-running
        # Ollama daemon would emit one "LLM backend unavailable"
        # warning per zone per pair → tens of thousands of lines for
        # a folder of ~30 pairs → stderr buffer overflow → Qt
        # segfault on load. Once prepare fails, we cache via
        # _last_error and stay silent until clear_llm_dispatcher_cache()
        # is called (AI settings dialog save handler does this).
        if not self._prepared:
            if self._last_error is not None:
                return None
            try:
                self.prepare()
            except LlmBackendUnavailableError as exc:
                logger.warning(
                    "LLM backend unavailable, abstaining (further "
                    "attempts will be silent until config reload): %s",
                    exc,
                )
                return None
            except BaseException:  # noqa: BLE001
                logger.exception(
                    "LLM dispatcher prepare failed (further attempts "
                    "will be silent until config reload)",
                )
                return None

        if self._backend is None:
            return None

        # Build evidence text — same fields the embedding dispatcher uses
        from .embedding_classifier import _zone_evidence_text
        from .normalizer import canonicalize_zone_text

        evidence = canonicalize_zone_text(_zone_evidence_text(zone)).strip()
        if not evidence:
            return None

        # Narrow candidate list to top_k (caller may pass more)
        candidates = list(candidate_categories)[: self._top_k]
        if not candidates:
            # Fall back to Stage-2 result's own category as the
            # only candidate (safety — LLM still picks something
            # in the schema).
            candidates = [stage2_result.category]

        # Phase K2 — KDS RAG retrieval. When the caller passed an
        # explicit kds_context, honour it; otherwise consult the
        # configured RAG client (stub_kds = empty string by default).
        # The merged context is what gets injected into the LLM prompt.
        retrieved_context = ""
        if not kds_context and self._use_kds_rag:
            try:
                client = self._get_kds_rag_client()
                retrieved_context = client.retrieve(
                    evidence,
                    candidates,
                    top_k=self._kds_rag_top_k,
                    timeout_s=self._kds_rag_timeout_s,
                )
            except BaseException:  # noqa: BLE001
                logger.exception("KDS RAG retrieve crashed (abstain)")
                retrieved_context = ""
        merged_context = kds_context or retrieved_context

        t0 = time.perf_counter()
        llm_result = self._backend.classify(
            evidence,
            candidates,
            kds_context=merged_context,
            timeout_s=self._timeout_s,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        if llm_result is None:
            return None  # caller keeps Stage-2

        # Compose the merged result. classifier_used="hybrid" so
        # downstream telemetry can attribute. raw_evidence preserves
        # the lower-tier signals for diagnostics.
        category = llm_result.category
        severity = DEFAULT_SEVERITY_BY_CATEGORY[category]
        layer = str(zone.get("layer") or "")
        if layer.startswith("PDF_PAGE_"):
            severity = Severity.MINOR
        action = DEFAULT_ACTION_BY_SEVERITY[severity]

        raw_evidence: dict = {
            "stage1_category": stage1_result.category.value,
            "stage1_confidence": float(stage1_result.confidence),
            "stage2_category": stage2_result.category.value,
            "stage2_confidence": float(stage2_result.confidence),
            "stage2_classifier": stage2_result.classifier_used,
            "stage3_backend": self._active_backend_id or "?",
            "stage3_elapsed_ms": llm_result.elapsed_ms,
            "stage3_total_elapsed_ms": elapsed_ms,
            "llm_rationale_ko": llm_result.rationale_ko,
            "candidates": [c.value for c in candidates],
            # Phase K2 — RAG provenance (empty when use_kds_rag=False)
            "kds_rag_client": (
                self._kds_rag_client_id if self._use_kds_rag else "none"
            ),
            "kds_rag_context_chars": len(merged_context or ""),
        }
        # Bring across any Stage-2 raw evidence so detail panel can
        # still show top1/top2 cosine scores.
        if stage2_result.raw_evidence:
            for k, v in stage2_result.raw_evidence.items():
                raw_evidence.setdefault(f"stage2_{k}", v)

        return ChangeClassification(
            zone_id=str(zone.get("zone_id") or ""),
            category=category,
            severity=severity,
            confidence=llm_result.confidence,
            suggested_action=action,
            summary_ko=self._summary_korean(category, zone, llm_result),
            kds_references=list(llm_result.kds_references),
            classifier_used="hybrid",
            elapsed_ms=elapsed_ms,
            raw_evidence=raw_evidence,
        )

    def _get_kds_rag_client(self) -> Any:
        """Lazy-instantiate the KDS RAG client. One client per
        dispatcher (cached). Falls back to stub_kds via the registry's
        own fallback path when the configured client isn't available.

        Phase L5 review fix (Issue #4): double-checked locking against
        concurrent classify_zone calls from parallel threads. Without
        the lock, two threads could both pass the `is None` guard +
        call get_kds_rag_client() twice. GIL kept the final write
        atomic so no corruption occurred, but the double construction
        + duplicate registry lookup wasted cycles.
        """

        if self._kds_rag_client is not None:
            return self._kds_rag_client
        with self._kds_rag_client_lock:
            if self._kds_rag_client is not None:  # another thread won
                return self._kds_rag_client
            from .kds_rag import get_kds_rag_client
            self._kds_rag_client = get_kds_rag_client(self._kds_rag_client_id)
        return self._kds_rag_client

    @staticmethod
    def _summary_korean(
        category: ChangeCategory,
        zone: dict,
        llm_result: LlmClassificationResult,
    ) -> str:
        """One-line summary for the workbench detail panel.

        Format: "{카테고리 한글}: {LLM rationale 일부}"
        Truncates rationale to keep the row visually compact.
        """

        from .schema import CATEGORY_LABELS_KO

        cat_label = CATEGORY_LABELS_KO.get(category, category.value)
        rationale = (llm_result.rationale_ko or "").strip().replace("\n", " ")
        if len(rationale) > 60:
            rationale = rationale[:57] + "…"
        if rationale:
            return f"{cat_label} — {rationale}"
        return cat_label


# ---------------------------------------------------------------------------
# Per-config singleton cache
# ---------------------------------------------------------------------------

_LLM_DISPATCHER_CACHE: dict[tuple, LlmClassifierDispatcher] = {}
_LLM_DISPATCHER_CACHE_LOCK = threading.Lock()


def get_llm_dispatcher(
    config: AiClassifierConfig,
) -> LlmClassifierDispatcher:
    """Cached dispatcher — re-uses the HTTP session / model load
    across classify calls."""

    key = (
        bool(config.use_llm),
        str(config.llm_backend_id or ""),
        str(config.llm_host or ""),
        str(config.llm_model or ""),
        float(config.llm_timeout_s or 10.0),
        int(config.llm_top_k_candidates or 3),
        float(config.llm_invoke_below_confidence or 0.85),
        # Phase K2 — KDS RAG cache axis
        bool(getattr(config, "use_kds_rag", False)),
        str(getattr(config, "kds_rag_client_id", "stub_kds") or "stub_kds"),
    )
    with _LLM_DISPATCHER_CACHE_LOCK:
        existing = _LLM_DISPATCHER_CACHE.get(key)
        if existing is not None:
            return existing
        fresh = LlmClassifierDispatcher(config)
        _LLM_DISPATCHER_CACHE[key] = fresh
        return fresh


def clear_llm_dispatcher_cache() -> None:
    """Drop all cached LLM dispatchers (test helper / config reload)."""

    with _LLM_DISPATCHER_CACHE_LOCK:
        _LLM_DISPATCHER_CACHE.clear()


__all__ = [
    "DEFAULT_LLM_BACKEND_ID",
    "LlmClassifierDispatcher",
    "get_llm_dispatcher",
    "clear_llm_dispatcher_cache",
]
