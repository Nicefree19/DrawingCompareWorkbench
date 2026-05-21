# -*- coding: utf-8 -*-
"""Phase H — Local-OSS AI change-zone classifier.

Architecture overview:
  * V1 design (``docs/AI_CHANGE_CLASSIFICATION_DESIGN.md``) — original
    three-stage cascade plan. Stage-1 keyword heuristic ships here.
  * V2 design (``docs/AI_EMBEDDING_PLAN_V2.md``) — SUPERSEDES V1 on
    embedding model choice. Adopts Qwen3-Embedding-0.6B GGUF +
    llama-cpp-python in-process (BGE-M3 was disqualified by the
    800 MB hard footprint limit).

Three tiers:

  Stage 1 (always run, no model download — SHIPPED):
      keyword heuristic against layer / entity_type / text_snippet
      → category + severity in < 1 ms per zone.

  Stage 2 (optional, opt-in via AiClassifierConfig.use_embedding):
      Qwen3-Embedding-0.6B GGUF (~0.4-0.65 GB) loaded once via
      llama-cpp-python in-process. Cosine similarity against a
      pre-computed 50-prototype corpus → top-1 category. Replaces
      Stage-1 result when cosine score > threshold.
      Skeleton present (normalizer, backends, manifest); concrete
      Qwen3 backend lands in Week 2 per V2 plan.

  Stage 3 (optional, opt-in via AiClassifierConfig.use_llm):
      Ollama-hosted EXAONE-3.5-7.8B for ambiguous Stage-2 cases
      (margin < δ) with KDS-RAG context → richer Korean summary +
      KDS reference. Off when Ollama isn't reachable, the LLM
      isn't downloaded, or the user opts out via config.

The current commit ships only the Stage-1 path + Stage-2 skeleton so
the module is import-light and immediately useful (no model download
required). Concrete Stage-2 backends (Qwen3 GGUF, KR-SBERT ONNX) and
Stage-3 (Ollama) plug in behind the same ``classify_zones`` entry
point in subsequent commits — schema is already final.

Usage::

    from src.services.comparison.ai_classifier import classify_zones

    classifications = classify_zones(zones=overlay_list)
    for c in classifications:
        print(c.zone_id, c.category, c.severity, c.summary_ko)
"""

from .schema import (
    ChangeCategory,
    Severity,
    ChangeClassification,
    AiClassifierConfig,
)
from .public_api import classify_zones
# Phase H Stage-2 prep — exports added by AI_EMBEDDING_PLAN_V2 (skeleton)
from .normalizer import (
    NORMALIZER_VERSION,
    canonicalize_zone_text,
    canonical_hash,
    canonicalize_batch,
)
from .manifest import (
    EmbeddingManifest,
    load_manifest,
    save_manifest,
    needs_recompute,
    manifest_path,
)
from .backends import (
    EmbeddingBackend,
    BackendUnavailableError,
    register_backend,
    get_backend,
    available_backends,
)
from .prototype_corpus import (
    PROTOTYPE_CORPUS_VERSION,
    SEED_CORPUS,
    PrototypeEntry,
    PrototypeCorpus,
    build_default_corpus,
    compute_corpus_embeddings,
)
from .embedding_classifier import (
    DEFAULT_BACKEND_ID,
    DEFAULT_INSTRUCTION_ID,
    DEFAULT_INSTRUCTION_TEXT,
    DEFAULT_MARGIN_THRESHOLD,
    EmbeddingClassifierDispatcher,
    get_embedding_dispatcher,
    clear_dispatcher_cache,
)
# Phase J Step 3 (J1) — user-config persistence
from .config_io import (
    CONFIG_SCHEMA_VERSION,
    CONFIG_FILENAME,
    default_ai_config_path,
    schema_version,
    load_ai_config,
    save_ai_config,
)
# Phase J Step 5 (J2) — Stage-3 LLM cascade
from .llm_backends import (
    AbstractLlmBackend,
    LlmBackend,
    LlmBackendUnavailableError,
    LlmClassificationResult,
    LLM_BACKEND_REGISTRY,
    register_llm_backend,
    get_llm_backend,
    available_llm_backends,
)
from .llm_classifier import (
    DEFAULT_LLM_BACKEND_ID,
    LlmClassifierDispatcher,
    get_llm_dispatcher,
    clear_llm_dispatcher_cache,
)
# Phase K2 — KDS RAG layer
from .kds_rag import (
    AbstractKdsRagClient,
    KdsRagClient,
    KDS_RAG_REGISTRY,
    register_kds_rag_client,
    get_kds_rag_client,
    available_kds_rag_clients,
)
# Phase I — concrete backend modules. Import for the side-effect of
# self-registering each backend's factory (the modules themselves
# call register_backend at import time).
from .backends import llama_cpp_qwen3_embedding  # noqa: F401
from .backends import onnx_mxbai_large  # noqa: F401

__all__ = [
    # Existing Stage-1 surface
    "ChangeCategory",
    "Severity",
    "ChangeClassification",
    "AiClassifierConfig",
    "classify_zones",
    # Stage-2 skeleton
    "NORMALIZER_VERSION",
    "canonicalize_zone_text",
    "canonical_hash",
    "canonicalize_batch",
    "EmbeddingManifest",
    "load_manifest",
    "save_manifest",
    "needs_recompute",
    "manifest_path",
    "EmbeddingBackend",
    "BackendUnavailableError",
    "register_backend",
    "get_backend",
    "available_backends",
    # Stage-2 concrete (Week 2)
    "PROTOTYPE_CORPUS_VERSION",
    "SEED_CORPUS",
    "PrototypeEntry",
    "PrototypeCorpus",
    "build_default_corpus",
    "compute_corpus_embeddings",
    "DEFAULT_BACKEND_ID",
    "DEFAULT_INSTRUCTION_ID",
    "DEFAULT_INSTRUCTION_TEXT",
    "DEFAULT_MARGIN_THRESHOLD",
    "EmbeddingClassifierDispatcher",
    "get_embedding_dispatcher",
    "clear_dispatcher_cache",
    # Phase J Step 3 (J1) — config persistence
    "CONFIG_SCHEMA_VERSION",
    "CONFIG_FILENAME",
    "default_ai_config_path",
    "schema_version",
    "load_ai_config",
    "save_ai_config",
    # Phase J Step 5 (J2) — LLM cascade
    "AbstractLlmBackend",
    "LlmBackend",
    "LlmBackendUnavailableError",
    "LlmClassificationResult",
    "LLM_BACKEND_REGISTRY",
    "register_llm_backend",
    "get_llm_backend",
    "available_llm_backends",
    "DEFAULT_LLM_BACKEND_ID",
    "LlmClassifierDispatcher",
    "get_llm_dispatcher",
    "clear_llm_dispatcher_cache",
    # Phase K2 — KDS RAG
    "AbstractKdsRagClient",
    "KdsRagClient",
    "KDS_RAG_REGISTRY",
    "register_kds_rag_client",
    "get_kds_rag_client",
    "available_kds_rag_clients",
]
