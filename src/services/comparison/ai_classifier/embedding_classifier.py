# -*- coding: utf-8 -*-
"""Phase H Stage-2 — embedding-based zone classifier dispatcher.

Cosine-similarity classification against a pre-computed prototype
corpus. Replaces / augments the Stage-1 heuristic when:
  * AiClassifierConfig.use_embedding is True
  * backend successfully warms up
  * top-1 cosine >= embedding_threshold AND
  * (top-1 - top-2) >= margin_threshold

Otherwise the dispatcher abstains — returns ``None`` — and the public
``classify_zones()`` falls back to the Stage-1 heuristic result.

Lifecycle:
  1. ``__init__`` is cheap — stores config, resolves cache dir.
  2. ``prepare()`` (sync or via ``prepare_async()``) does the heavy
     lifting:
       a. Backend instantiation + warmup (loads GGUF)
       b. Corpus load (or build_default_corpus)
       c. Embeddings load from .npy (manifest match) or recompute
       d. Manifest save
  3. ``classify_zone(zone_dict)`` encodes one zone and runs cosine
     match. Cheap: ~50 dot products on 1024-dim vectors = sub-ms.

Cache layout (see V2 plan §6):
    %LOCALAPPDATA%/DrawingCompareWorkbench/ai_cache/
        manifest.json
        prototype_corpus_v2.json
        prototype_embeddings_v2.npy

Thread safety: ``prepare()`` uses double-checked locking; concurrent
first-callers all block on the same warm-up. ``classify_zone`` is
read-only against the prepared state and is safe to call from any
thread once ``prepare()`` returns.

Abstention policy is intentional. Stage-2 only "speaks" when the
prototype match is BOTH strong (cosine ≥ τ) and unambiguous (margin
to top-2 ≥ δ). Anything else returns ``None`` so Stage-1's keyword
heuristic stays in charge — that protects the user from an over-
confident embedding model giving the wrong category just because no
prototype was a good match.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .schema import (
    AiClassifierConfig,
    ChangeCategory,
    ChangeClassification,
    Severity,
    CATEGORY_LABELS_KO,
    DEFAULT_ACTION_BY_SEVERITY,
    DEFAULT_SEVERITY_BY_CATEGORY,
)
from .normalizer import NORMALIZER_VERSION, canonicalize_zone_text
from .manifest import (
    EmbeddingManifest,
    needs_recompute,
    save_manifest,
)
from .prototype_corpus import (
    CORPUS_FILENAME,
    EMBEDDINGS_FILENAME,
    PROTOTYPE_CORPUS_VERSION,
    PrototypeCorpus,
    build_default_corpus,
    compute_corpus_embeddings,
    load_corpus_json,
    load_embeddings_npy,
    save_corpus_json,
    save_embeddings_npy,
)
from .backends import (
    BACKEND_REGISTRY,
    BackendUnavailableError,
    EmbeddingBackend,
    get_backend,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_BACKEND_ID = "llama_cpp_qwen3_embedding"

# Identifier persisted into the manifest. Bump when the instruction
# TEXT changes — that invalidates pre-computed prototype embeddings.
DEFAULT_INSTRUCTION_ID = "korean_construction_zone_v1"

# Korean instruction prepended to every embedding query (Qwen3-
# Embedding instruction-aware retrieval). Keep short — Qwen's model
# card recommends task-focused, single-sentence instructions.
DEFAULT_INSTRUCTION_TEXT = (
    "다음 한국어 구조 도면 변경 설명을 분류 카테고리(구조부재, 치수, "
    "텍스트, 그리드, 배치, 디테일, 주석, 미분류) 중 하나로 매핑하세요"
)

# Cosine margin between top-1 and top-2 prototype scores. Below this,
# the embedding tier abstains and Stage-1 wins. 0.03 is intentionally
# loose — Stage-3 LLM (Phase I) will tighten ambiguous calls later.
DEFAULT_MARGIN_THRESHOLD = 0.03


def _default_cache_dir() -> Path:
    """``%LOCALAPPDATA%/DrawingCompareWorkbench/ai_cache/`` (production).

    Falls back to ``~/.cache/...`` on non-Windows hosts so unit tests
    on Linux CI don't crash.
    """

    appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "DrawingCompareWorkbench" / "ai_cache"
    return Path.home() / ".cache" / "DrawingCompareWorkbench" / "ai_cache"


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class EmbeddingClassifierDispatcher:
    """Stage-2 dispatcher — owns backend + corpus + manifest lifecycle.

    Single instance per (backend_id, threshold, cache_dir) tuple via
    ``get_embedding_dispatcher()`` so the GGUF only loads once per
    process even when callers create fresh ``AiClassifierConfig``
    objects.
    """

    def __init__(
        self,
        config: AiClassifierConfig,
        *,
        backend_id: Optional[str] = None,
        backend_kwargs: Optional[dict] = None,
        instruction: str = DEFAULT_INSTRUCTION_TEXT,
        instruction_id: str = DEFAULT_INSTRUCTION_ID,
        margin_threshold: float = DEFAULT_MARGIN_THRESHOLD,
    ) -> None:
        self._config = config
        # Phase I — resolve the configured backend id with priority:
        #   1. Explicit ``backend_id`` constructor arg (test path)
        #   2. ``config.embedding_backend_id`` (V2 — including "auto")
        #   3. ``config.embedding_model`` legacy fallback (V1)
        cfg_backend = (config.embedding_backend_id or "").strip()
        cfg_model = (config.embedding_model or "").strip()
        if backend_id is not None:
            self._configured_backend_id = backend_id
        elif cfg_backend:
            self._configured_backend_id = cfg_backend
        elif cfg_model and "/" not in cfg_model:
            # V1 path that stored a registry ID in embedding_model
            self._configured_backend_id = cfg_model
        else:
            self._configured_backend_id = "auto"
        # Fallback list used when "auto" picks none, or when the
        # configured backend's warmup fails with BackendUnavailableError.
        self._backend_fallbacks = list(
            config.embedding_backend_fallbacks or [DEFAULT_BACKEND_ID]
        )
        # The actual backend ID that prepare() ends up loading. Set
        # during _do_prepare() — manifest writes this, not the configured
        # ID, so cached prototypes never get reused across backends.
        self._active_backend_id: Optional[str] = None

        self._backend_kwargs = dict(backend_kwargs or {})
        self._instruction = instruction
        self._instruction_id = instruction_id
        self._cosine_threshold = float(config.embedding_threshold or 0.7)
        self._margin_threshold = float(margin_threshold)
        # Phase I — Matryoshka truncation. None = native (no truncation).
        # The backend produces native vectors; AbstractEmbeddingBackend.
        # encode() slices + re-normalises when truncate_dim is set.
        out_dim = config.embedding_output_dim
        self._output_dim: Optional[int] = (
            int(out_dim) if out_dim and int(out_dim) > 0 else None
        )

        self._cache_dir = (
            Path(config.cache_dir) if config.cache_dir else _default_cache_dir()
        )

        # Lazy-init state — populated by prepare()
        self._backend: Optional[EmbeddingBackend] = None
        self._corpus: Optional[PrototypeCorpus] = None
        self._prepared: bool = False
        # Mirror AbstractEmbeddingBackend pattern: lock allocated at
        # construction (cheap), used via double-checked locking so the
        # fast path stays lock-free.
        self._prepare_lock = threading.Lock()
        self._last_error: Optional[BaseException] = None
        self._prepare_ms: Optional[float] = None

    # ---- Lifecycle ------------------------------------------------------

    def prepare(self) -> None:
        """Synchronously: instantiate backend + warm up + load/compute
        prototype embeddings + save manifest.

        Safe to call multiple times (no-op when prepared). Safe to
        call from a background thread — uses double-checked locking
        so a concurrent foreground ``classify_zone`` won't double-load.

        Raises whatever the backend raises on warm-up failure (the
        caller — ``public_api.classify_zones`` — catches and falls back
        to Stage-1).
        """

        if self._prepared:
            return
        with self._prepare_lock:
            if self._prepared:  # another thread won the race
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

    def _resolve_backend_id(self) -> str:
        """Phase I — pick the backend ID to actually instantiate.

        When ``embedding_backend_id != "auto"``, returns that ID
        unconditionally. When it IS "auto", walks the fallback list
        in order and picks the first one whose ``probe_available()``
        returns True. Cheap — no model loading.

        Raises BackendUnavailableError when no backend is available
        (caller — public_api — catches and falls back to Stage-1).
        """

        if self._configured_backend_id != "auto":
            return self._configured_backend_id
        for bid in self._backend_fallbacks:
            try:
                # Look up the registered factory and pull the class off
                # the instance (the factory may be a lambda).
                factory = BACKEND_REGISTRY.get(bid)
                if factory is None:
                    continue
                # Instantiate cheaply (constructors are cheap by contract)
                # so we can call probe_available on the class. Avoids
                # registering classes separately.
                inst = factory()
                cls = inst.__class__
                if cls.probe_available():
                    logger.info(
                        "auto-mode selected backend %s (probe_available=True)",
                        bid,
                    )
                    return bid
            except Exception:  # noqa: BLE001
                logger.debug("auto-mode probe of %s failed", bid, exc_info=True)
                continue
        raise BackendUnavailableError(
            "auto 모드에서 사용 가능한 백엔드 없음. "
            "ai_models/ 디렉토리에 Qwen3-Embedding-0.6B GGUF "
            "또는 onnx_mxbai_large 디렉토리 배치 필요. "
            f"확인된 fallback 순서: {self._backend_fallbacks}"
        )

    def _do_prepare(self) -> None:
        # 1. Resolve + instantiate + warm up backend (with fallback chain
        #    when warmup raises BackendUnavailableError).
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        primary_id = self._resolve_backend_id()
        # Build try-list: primary first, then the rest of fallbacks
        # (deduped, and only when primary itself was from "auto" mode —
        # explicit user-configured IDs don't auto-fall-back).
        if self._configured_backend_id == "auto":
            try_list = [primary_id] + [
                b for b in self._backend_fallbacks if b != primary_id
            ]
        else:
            try_list = [primary_id]

        last_exc: Optional[BaseException] = None
        for attempt_id in try_list:
            try:
                backend = get_backend(attempt_id, **self._backend_kwargs)
                backend.warmup()
                # Phase I: stamp the active embedding_dim with the
                # truncation target so cosine math + cache validation
                # see the *effective* dim everywhere downstream.
                if self._output_dim is not None:
                    if self._output_dim > backend.native_dim:
                        raise BackendUnavailableError(
                            f"embedding_output_dim={self._output_dim} > "
                            f"backend native_dim={backend.native_dim}"
                        )
                    backend.embedding_dim = self._output_dim
                self._backend = backend
                self._active_backend_id = attempt_id
                if attempt_id != primary_id:
                    logger.info(
                        "Backend %s warmup failed; fell back to %s",
                        primary_id, attempt_id,
                    )
                break
            except BackendUnavailableError as exc:
                last_exc = exc
                logger.info(
                    "Backend %s unavailable (%s) — trying next in fallback list",
                    attempt_id, exc,
                )
                continue
        if self._backend is None:
            # Re-raise the last seen failure so public_api logs it
            raise last_exc or BackendUnavailableError(
                "No embedding backend could be loaded"
            )

        # 2. Build current expected manifest (with active backend ID)
        current_manifest = self._build_current_manifest()

        # 3. Try to reuse on-disk cache
        embeddings_path = self._cache_dir / EMBEDDINGS_FILENAME
        corpus_path = self._cache_dir / CORPUS_FILENAME

        cached_corpus = load_corpus_json(corpus_path)
        cached_embeddings = load_embeddings_npy(embeddings_path)

        # Phase I: cache validity now compares against effective dim
        # (post-truncation), not native_dim. Two different truncation
        # targets produce different .npy files but the manifest's
        # output_dim field invalidates cross-target reuse.
        effective_dim = self._output_dim or self._backend.native_dim
        cache_valid = (
            cached_corpus is not None
            and cached_embeddings is not None
            and len(cached_corpus) > 0
            and not needs_recompute(self._cache_dir, current_manifest)
            and cached_embeddings.shape[0] == len(cached_corpus)
            and cached_embeddings.shape[1] == effective_dim
        )

        if cache_valid:
            cached_corpus.embeddings = cached_embeddings
            cached_corpus.embedding_dim = int(cached_embeddings.shape[1])
            self._corpus = cached_corpus
            logger.info(
                "Loaded cached prototype embeddings: %d entries, dim=%d "
                "(backend=%s)",
                len(cached_corpus), cached_corpus.embedding_dim,
                self._active_backend_id,
            )
            return

        # 4. Recompute from default seeds (with truncation if configured)
        logger.info(
            "Prototype embeddings cache miss — recomputing for backend=%s, "
            "model=%s, normalizer=%s, instruction=%s, output_dim=%s",
            self._active_backend_id, current_manifest.model_file,
            NORMALIZER_VERSION, self._instruction_id,
            self._output_dim or "native",
        )
        seed_corpus = build_default_corpus()
        computed = compute_corpus_embeddings(
            seed_corpus,
            self._backend,
            canonicalise=True,
            instruction=self._instruction,
            truncate_dim=self._output_dim,
        )
        # Persist for next launch (best-effort — disk full / read-only
        # share shouldn't sink the in-memory dispatcher).
        try:
            save_corpus_json(computed, corpus_path)
            save_embeddings_npy(computed, embeddings_path)
            save_manifest(self._cache_dir, current_manifest)
        except OSError as exc:
            logger.warning("Could not persist prototype cache: %s", exc)
        self._corpus = computed

    def prepare_async(self) -> threading.Thread:
        """Fire-and-forget: launch ``prepare()`` in a daemon thread.

        Use during workbench startup so the user doesn't wait on the
        2-5 s GGUF cold-start when they trigger their first AI
        classification.

        Returns the spawned thread (caller can join() if desired).
        Backend-unavailable failures are expected on customer machines
        without optional embedding models installed, so they are logged
        as fallback warnings. Unexpected failures remain error logs. The
        next foreground ``classify_zone`` re-tries via the lazy path and
        surfaces the failure to the caller.
        """

        t = threading.Thread(
            target=self._prepare_async_target,
            name="ai-embedding-prepare",
            daemon=True,
        )
        t.start()
        return t

    def _prepare_async_target(self) -> None:
        try:
            self.prepare()
        except BackendUnavailableError as exc:
            logger.warning(
                "Embedding backend unavailable during background prepare; "
                "falling back to heuristic-only classification: %s",
                exc,
            )
        except BaseException:  # noqa: BLE001
            logger.exception("Background prepare() failed")

    def is_ready(self) -> bool:
        return self._prepared

    def last_error(self) -> Optional[BaseException]:
        return self._last_error

    def prepare_ms(self) -> Optional[float]:
        return self._prepare_ms

    # ---- Per-zone classification ---------------------------------------

    def classify_zone(
        self,
        zone: dict,
    ) -> Optional[ChangeClassification]:
        """Encode the zone, find best prototype match, return result.

        Returns:
            * ``ChangeClassification`` with classifier_used="embedding"
              when top-1 cosine ≥ threshold AND margin to top-2 ≥
              margin_threshold.
            * ``None`` when the dispatcher abstains (zone has no usable
              text, top score below threshold, or margin too tight).
              Caller falls back to Stage-1 result.
        """

        if not isinstance(zone, dict):
            return None

        # Lazy first-call init (blocks). Failures abstain quietly so
        # the caller falls back to Stage-1; we never crash classify_zones.
        #
        # Phase N hotfix — prepare-failure cooldown.
        # Without this guard each zone in a workbench pair (~5-50 zones)
        # would re-call prepare() and re-emit the "backend unavailable"
        # warning. With Phase N now wiring the cascade into the workbench
        # pair-classification loop, that re-emission produced ~2K log
        # lines per pair load → 576 KB stderr buffer → Qt event loop
        # crashed (segfault 139). Once prepare has failed once, we
        # cache the failure on _last_error and abstain silently for
        # the remainder of the session. clear_dispatcher_cache() is
        # the explicit retry path (called from the AI settings dialog
        # save handler), so users can still recover after dropping a
        # model file into ai_models/ and re-saving the dialog.
        if not self._prepared:
            if self._last_error is not None:
                # Already tried + failed once. Stay silent.
                return None
            try:
                self.prepare()
            except BackendUnavailableError as exc:
                # _last_error is set inside prepare() — this branch
                # only fires on the very first failure, so the warning
                # appears exactly once per dispatcher lifetime.
                logger.warning(
                    "Embedding backend unavailable, abstaining "
                    "(further attempts will be silent until config "
                    "reload): %s", exc,
                )
                return None
            except BaseException:  # noqa: BLE001
                logger.exception(
                    "Embedding dispatcher prepare failed (further "
                    "attempts will be silent until config reload)",
                )
                return None

        if self._backend is None or self._corpus is None:
            return None
        if self._corpus.embeddings is None or len(self._corpus) == 0:
            return None

        t0 = time.perf_counter()

        evidence = _zone_evidence_text(zone)
        canonical = canonicalize_zone_text(evidence)
        if not canonical.strip():
            return None

        formatted = self._format_query(canonical)
        try:
            # Phase I: pass truncate_dim so query and prototype embeddings
            # have matching dimensions (mandatory for cosine to work).
            vec = self._backend.encode(
                [formatted],
                normalize=True,
                truncate_dim=self._output_dim,
            )
        except BaseException:  # noqa: BLE001
            logger.exception("Embedding encode failed for zone %s",
                             zone.get("zone_id"))
            return None
        if vec.size == 0 or vec.shape[1] != self._corpus.embeddings.shape[1]:
            return None

        # Both vec and corpus.embeddings are L2-normalised → cosine = dot
        sims = (self._corpus.embeddings @ vec[0]).astype(np.float32)
        if sims.size == 0:
            return None

        # Aggregate per-CATEGORY (max-pool across that category's seeds).
        # Margin must compare different CATEGORIES, not different seeds
        # within the same one — two semantically equivalent prototypes
        # in STRUCTURAL_MEMBER would otherwise tie at cosine=1.0 and
        # collapse the margin to zero, forcing a spurious abstain.
        category_top: dict[ChangeCategory, float] = {}
        category_top_idx: dict[ChangeCategory, int] = {}
        for i in range(len(self._corpus.entries)):
            cat = self._corpus.entries[i].category
            sim = float(sims[i])
            if sim > category_top.get(cat, -1.0):
                category_top[cat] = sim
                category_top_idx[cat] = i

        # Rank categories descending by their best in-category score
        ranked = sorted(category_top.items(), key=lambda kv: -kv[1])
        top1_cat, top1_score = ranked[0]
        top2_score = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = top1_score - top2_score

        if top1_score < self._cosine_threshold:
            return None
        if margin < self._margin_threshold:
            return None

        # Resolve back to the seed entry that drove the top category
        # (used purely for diagnostics — not for the user-facing label).
        top1_idx = category_top_idx[top1_cat]
        category = top1_cat
        seed_text = self._corpus.entries[top1_idx].raw_text
        severity = DEFAULT_SEVERITY_BY_CATEGORY[category]
        # PDF_PAGE_x layer (visual diff) → minor severity (mirror Stage-1)
        layer = str(zone.get("layer") or "")
        if layer.startswith("PDF_PAGE_"):
            severity = Severity.MINOR
        action = DEFAULT_ACTION_BY_SEVERITY[severity]

        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        summary = self._summary_korean(category, zone, seed_text)

        # Phase J Step 5 (J2) hook: surface top-K categories in
        # raw_evidence so the public_api Stage-3 cascade can hand
        # them to the LLM as the candidate constraint set. Without
        # this, Stage-3 would have to re-run the embedding model
        # just to get the candidate list.
        top_categories: list[tuple[str, float]] = [
            (cat.value, score) for cat, score in ranked[:5]
        ]

        return ChangeClassification(
            zone_id=str(zone.get("zone_id") or ""),
            category=category,
            severity=severity,
            confidence=float(top1_score),
            suggested_action=action,
            summary_ko=summary,
            kds_references=[],
            classifier_used="embedding",
            elapsed_ms=elapsed_ms,
            raw_evidence={
                "top1_score": top1_score,
                "top2_score": top2_score,
                "margin": margin,
                "seed_id": int(self._corpus.entries[top1_idx].seed_id),
                "seed_text": seed_text,
                "canonical_query": canonical,
                "top_categories": top_categories,
            },
        )

    # ---- Helpers -------------------------------------------------------

    def _format_query(self, canonical: str) -> str:
        if not self._instruction:
            return canonical
        return f"Instruct: {self._instruction}\nQuery: {canonical}"

    def _build_current_manifest(self) -> EmbeddingManifest:
        backend = self._backend
        # Pull whatever metadata the backend exposes; defensive on
        # missing attributes so non-Qwen backends still produce a
        # comparable manifest.
        model_file = getattr(backend, "_resolved_model_path", None)
        model_basename = (
            Path(str(model_file)).name if model_file else ""
        )
        # Best-effort quantisation parsing from filename (Q8_0, Q4_K_M,
        # etc). Used only as a manifest field — not load-critical.
        quant = ""
        if model_basename:
            for tag in ("Q4_K_M", "Q4_0", "Q5_K_M", "Q5_0",
                        "Q8_0", "fp16", "f16", "f32"):
                if tag in model_basename:
                    quant = tag
                    break

        # Compute corpus hash from default corpus (cache key for embeddings)
        seed_sha = build_default_corpus().corpus_sha256

        # Phase I — embedding_dim is the *effective* (post-truncation) dim
        # callers see; output_dim is the *configured* truncation target
        # (0 = use native). With both populated the manifest can detect:
        #   - backend swap (embedding_backend changes)
        #   - model file swap (model_sha256 changes)
        #   - truncation target change (output_dim changes)
        # ... and invalidate the .npy cache appropriately.
        native_dim = int(getattr(backend, "native_dim",
                                 getattr(backend, "embedding_dim", 0)))
        effective_dim = self._output_dim or native_dim
        return EmbeddingManifest(
            embedding_backend=self._active_backend_id or self._configured_backend_id,
            model_file=model_basename,
            model_sha256=getattr(backend, "model_sha256", "") or "",
            embedding_dim=effective_dim,
            prototype_corpus_version=PROTOTYPE_CORPUS_VERSION,
            prototype_corpus_sha256=seed_sha,
            normalizer_version=NORMALIZER_VERSION,
            instruction_id=self._instruction_id,
            output_dim=int(self._output_dim or 0),
            pooling="last_token",  # Qwen3-Embedding default
            quantization=quant,
            computed_at_utc=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _summary_korean(
        category: ChangeCategory,
        zone: dict,
        seed_text: str,
    ) -> str:
        """Build a one-line Korean summary similar to Stage-1 style.

        We don't put the prototype seed text into the user-visible
        summary (that would feel weird — "you matched seed #17"). The
        seed sits in raw_evidence for diagnostics only.
        """

        cat_label = CATEGORY_LABELS_KO[category]
        change_type = str(zone.get("change_type") or "").lower()
        change_word = {
            "added": "추가", "deleted": "삭제",
            "modified": "수정", "moved": "이동",
        }.get(change_type, "변경")

        layer = str(zone.get("layer") or "")
        raw_count = int(zone.get("raw_change_count") or 0)
        snippet = str(zone.get("text_snippet") or "").strip()

        parts = [cat_label]
        if layer:
            parts.append(f"({layer} 레이어)")
        parts.append(change_word)
        if raw_count:
            parts.append(f"{raw_count}건")
        if snippet:
            preview = snippet.replace("\n", " ")[:30]
            if preview:
                parts.append(f'"{preview}"')
        return " ".join(parts)


# ---------------------------------------------------------------------------
# Per-config singleton cache
# ---------------------------------------------------------------------------

_DISPATCHER_CACHE: dict[tuple, EmbeddingClassifierDispatcher] = {}
_DISPATCHER_CACHE_LOCK = threading.Lock()


def get_embedding_dispatcher(
    config: AiClassifierConfig,
) -> EmbeddingClassifierDispatcher:
    """Return a cached dispatcher for ``config`` — re-using the same
    instance avoids reloading the GGUF on every classify call.

    Phase I: cache key includes ``embedding_backend_id`` and
    ``embedding_output_dim`` so quality/speed/auto switches AND
    Matryoshka truncation changes both produce fresh dispatchers
    instead of leaking a wrong-dim one across modes.
    """

    key = (
        bool(config.use_embedding),
        str(config.embedding_backend_id or ""),
        str(config.embedding_model or ""),
        int(config.embedding_output_dim or 0),
        float(config.embedding_threshold or 0.7),
        str(config.cache_dir or ""),
    )
    with _DISPATCHER_CACHE_LOCK:
        existing = _DISPATCHER_CACHE.get(key)
        if existing is not None:
            return existing
        fresh = EmbeddingClassifierDispatcher(config)
        _DISPATCHER_CACHE[key] = fresh
        return fresh


def clear_dispatcher_cache() -> None:
    """Drop all cached dispatchers (test helper / config reload).

    Released GGUFs are reclaimed when the underlying backend object
    goes out of scope — llama-cpp-python frees the model on GC.
    """

    with _DISPATCHER_CACHE_LOCK:
        _DISPATCHER_CACHE.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _zone_evidence_text(zone: dict) -> str:
    """Concatenate the fields that carry semantic signal.

    Mirrors what ``classify_zone_heuristic`` reads, so Stage-1 and
    Stage-2 look at the same evidence. Order: text snippet first
    (richest signal), then layer + entity_type + change_type as
    context.

    Returns "" when none of the fields have content — caller skips
    embedding entirely so an empty zone never wastes a model call.
    """

    parts: list[str] = []
    snippet = str(zone.get("text_snippet") or "").strip()
    if snippet:
        parts.append(snippet)
    layer = str(zone.get("layer") or "").strip()
    if layer:
        parts.append(layer)
    entity_type = str(zone.get("entity_type") or "").strip()
    if entity_type:
        parts.append(entity_type)
    change_type = str(zone.get("change_type") or "").strip()
    if change_type:
        parts.append(change_type)
    return " ".join(parts)


__all__ = [
    "DEFAULT_BACKEND_ID",
    "DEFAULT_INSTRUCTION_ID",
    "DEFAULT_INSTRUCTION_TEXT",
    "DEFAULT_MARGIN_THRESHOLD",
    "EmbeddingClassifierDispatcher",
    "get_embedding_dispatcher",
    "clear_dispatcher_cache",
]
