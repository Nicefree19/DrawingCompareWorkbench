# -*- coding: utf-8 -*-
"""Phase J Step 5 (J2) — Stage-3 LLM backends.

Mirrors ``ai_classifier.backends`` (Phase H/I embedding registry).
Concrete LLM backends register themselves with ``LLM_BACKEND_REGISTRY``
at import time; ``get_llm_backend(name)`` returns the registered
constructor.

Built-in backends (auto-imported, both optional — fail-silent if
their deps aren't importable):
  * ``stub_llm`` — deterministic test backend, no network. Always
    available. Picks the first candidate as the answer.
  * ``ollama_exaone`` — HTTP client to a local Ollama server with
    EXAONE-3.5-7.8B pulled. Requires ``requests`` + Ollama running.

Future:
  * ``openai_compat`` — generic OpenAI-format API for self-hosted
    vLLM / LM-Studio
  * ``llama_cpp_decoder`` — direct llama-cpp-python decoder mode
    (no daemon, no network — but slower cold-start)

The skeleton this commit ships exposes the protocol + registry +
two concrete backends; downstream consumers (dispatcher,
public_api cascade) plug in via the registered IDs.
"""

from __future__ import annotations

from typing import Callable

from .base import (
    AbstractLlmBackend,
    LlmBackend,
    LlmBackendUnavailableError,
    LlmClassificationResult,
)

# Map: backend_id → factory callable (lazy so importing this package
# doesn't pull heavy dependencies)
LLM_BACKEND_REGISTRY: dict[str, Callable[..., LlmBackend]] = {}


def register_llm_backend(
    backend_id: str,
    factory: Callable[..., LlmBackend],
    *,
    replace: bool = False,
) -> None:
    """Register an LLM backend factory under ``backend_id``.

    Same fail-closed contract as the embedding registry — duplicate
    ID raises ValueError unless replace=True. Used in production to
    catch import-order bugs early.

    Args:
        backend_id: Stable string ID (matches what
            ``AiClassifierConfig.llm_backend_id`` references — added
            in J2 schema bump).
        factory: Zero-arg callable returning a fresh ``LlmBackend``
            on each call.
        replace: When True, allow overwriting an existing registration.
            Default False — duplicate ID raises ValueError. Tests
            and bootstrap migrations are the only legitimate users
            of replace=True.

    Raises:
        ValueError: ``backend_id`` already registered and replace=False.
    """

    if backend_id in LLM_BACKEND_REGISTRY and not replace:
        raise ValueError(
            f"LLM backend {backend_id!r} already registered. "
            f"Pass replace=True to override explicitly."
        )
    LLM_BACKEND_REGISTRY[backend_id] = factory


def get_llm_backend(backend_id: str, **kwargs) -> LlmBackend:
    """Resolve and instantiate the named LLM backend.

    Raises ``LlmBackendUnavailableError`` when the backend isn't
    registered (typically because its third-party dependency isn't
    installed or its self-registration block was suppressed).
    """

    factory = LLM_BACKEND_REGISTRY.get(backend_id)
    if factory is None:
        available = sorted(LLM_BACKEND_REGISTRY.keys())
        raise LlmBackendUnavailableError(
            f"LLM backend {backend_id!r} not registered. "
            f"Available: {available or '(none)'}"
        )
    return factory(**kwargs)


def available_llm_backends() -> list[str]:
    """Snapshot of currently-registered LLM backend IDs."""

    return sorted(LLM_BACKEND_REGISTRY.keys())


__all__ = [
    "AbstractLlmBackend",
    "LlmBackend",
    "LlmBackendUnavailableError",
    "LlmClassificationResult",
    "LLM_BACKEND_REGISTRY",
    "register_llm_backend",
    "get_llm_backend",
    "available_llm_backends",
]


# ---------------------------------------------------------------------------
# Auto-import concrete backends so their self-registration runs.
# Each block is wrapped in try/except so missing deps (requests for
# ollama_exaone) don't break the package import.
# ---------------------------------------------------------------------------

try:
    from . import stub_llm  # noqa: F401  (registers BACKEND_ID)
except Exception:  # noqa: BLE001
    import logging as _log
    _log.getLogger(__name__).debug(
        "stub_llm backend not auto-importable", exc_info=True,
    )

try:
    from . import ollama_exaone  # noqa: F401  (registers BACKEND_ID)
except Exception:  # noqa: BLE001
    import logging as _log
    _log.getLogger(__name__).debug(
        "ollama_exaone backend not auto-importable", exc_info=True,
    )
