# -*- coding: utf-8 -*-
"""Phase H Stage-2 embedding backends.

Concrete backends register themselves with ``BACKEND_REGISTRY`` at
import time; ``get_backend(name)`` returns the registered constructor.
This keeps the backend choice config-driven (env var or runtime
setting) without hard-coupling ``embedding_classifier.py`` to any
single implementation.

Built-in backends (all optional — depend on third-party packages
that may not be installed):
  * ``llama_cpp_qwen3_embedding`` — ``Qwen/Qwen3-Embedding-0.6B-GGUF``
    (the dedicated EMBEDDING model, NOT the causal ``Qwen3-0.6B-GGUF``)
    via llama-cpp-python
  * ``onnx_kr_sbert``             — KR-SBERT-V40K via sentence-transformers + ONNX

Future:
  * ``ollama_embedding``          — Ollama embedding endpoint
  * ``onnx_qwen3_embedding``      — Qwen3-Embedding ONNX export (if Qwen
    ships an official ONNX variant)

The skeleton package this commit ships exposes only the protocol
+ registry; real backend code arrives in Week 2 (per
``docs/AI_EMBEDDING_PLAN_V2.md``).
"""

from __future__ import annotations

from typing import Callable, Optional

from .base import EmbeddingBackend, BackendUnavailableError

# Map: backend_id → factory callable (lazy so importing this package
# doesn't pull heavy dependencies)
BACKEND_REGISTRY: dict[str, Callable[..., EmbeddingBackend]] = {}


def register_backend(
    backend_id: str,
    factory: Callable[..., EmbeddingBackend],
    *,
    replace: bool = False,
) -> None:
    """Register a backend factory under ``backend_id``.

    Behaviour is **fail-closed by default**: a duplicate ``backend_id``
    raises ``ValueError`` unless the caller explicitly passes
    ``replace=True``. This is a deliberate contract — silently
    overwriting a registration was hiding configuration mistakes
    (two backends registering as "llama_cpp_qwen3_embedding" because
    of import-order surprises) which only manifested as wrong-vector
    bugs much later.

    Audit guarantees (3rd-review fix P1):
    - ``register_backend("foo", f)`` then ``register_backend("foo", g)``
      → ``ValueError`` (no overwrite).
    - ``register_backend("foo", g, replace=True)`` is the ONLY way to
      replace; this is intended for tests + bootstrap migrations and
      MUST NOT appear in production registration paths.
    - There is no environment variable, sentinel object, or "soft"
      override path that downgrades this to a warning.

    Args:
        backend_id: Stable string ID (matches what
            ``AiClassifierConfig.embedding_backend`` references).
        factory: Zero-arg callable returning a fresh ``EmbeddingBackend``
            on each call.
        replace: When True, allow overwriting an existing registration.
            Default False — duplicate ID raises ValueError.

    Raises:
        ValueError: ``backend_id`` already registered and ``replace=False``.
    """

    if backend_id in BACKEND_REGISTRY and not replace:
        raise ValueError(
            f"Embedding backend {backend_id!r} already registered. "
            f"Pass replace=True to override explicitly."
        )
    BACKEND_REGISTRY[backend_id] = factory


def get_backend(
    backend_id: str,
    **kwargs,
) -> EmbeddingBackend:
    """Resolve and instantiate the named backend.

    Raises ``BackendUnavailableError`` when the backend isn't
    registered (typically because its third-party dependency isn't
    installed).
    """

    factory = BACKEND_REGISTRY.get(backend_id)
    if factory is None:
        available = sorted(BACKEND_REGISTRY.keys())
        raise BackendUnavailableError(
            f"Embedding backend {backend_id!r} not registered. "
            f"Available: {available or '(none)'}"
        )
    return factory(**kwargs)


def available_backends() -> list[str]:
    """Snapshot of currently-registered backend IDs."""

    return sorted(BACKEND_REGISTRY.keys())


__all__ = [
    "BACKEND_REGISTRY",
    "register_backend",
    "get_backend",
    "available_backends",
    "EmbeddingBackend",
    "BackendUnavailableError",
]


# ---------------------------------------------------------------------------
# Auto-import concrete backends so their self-registration runs.
#
# Each block is wrapped in try/except because the heavy dependencies
# (llama_cpp_python, sentence_transformers + onnxruntime) are optional —
# users without them installed should still be able to import the
# ai_classifier package and use the Stage-1 heuristic fallback.
#
# Registration uses replace=True so re-imports during dev/test don't
# raise the fail-closed ValueError on duplicate IDs.
# ---------------------------------------------------------------------------

try:
    from . import llama_cpp_qwen3_embedding  # noqa: F401  (registers BACKEND_ID)
except Exception:  # noqa: BLE001
    import logging as _log
    _log.getLogger(__name__).debug(
        "llama_cpp_qwen3_embedding backend not auto-importable", exc_info=True,
    )

try:
    from . import onnx_mxbai_large  # noqa: F401  (registers BACKEND_ID)
except Exception:  # noqa: BLE001
    import logging as _log
    _log.getLogger(__name__).debug(
        "onnx_mxbai_large backend not auto-importable", exc_info=True,
    )
