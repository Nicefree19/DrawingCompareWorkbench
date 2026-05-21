# -*- coding: utf-8 -*-
"""``EmbeddingBackend`` protocol — unified contract for Stage-2 backends.

Defining a Protocol (not a base class) means concrete backends DON'T
need to inherit anything — duck typing + ``runtime_checkable`` keeps
the dependency graph flat. The ``AbstractEmbeddingBackend`` ABC at
the bottom is a convenience for backends that want shared boilerplate
(warm-up timing, last-error tracking) without forcing inheritance.

Both Qwen3-GGUF (decoder, 1024-dim) and KR-SBERT-ONNX (encoder, 768-dim)
implement this contract identically. The classifier dispatcher
(``embedding_classifier.py``, Week 2) speaks ONLY this protocol —
swapping backends is a config flip, never a code change.
"""

from __future__ import annotations

import abc
import threading
import time
from typing import Any, Optional, Protocol, Sequence, runtime_checkable

import numpy as np


class BackendUnavailableError(RuntimeError):
    """Raised when a requested backend isn't installed / registered.

    The classifier catches this and falls back to the next backend
    in the configured priority list (or to heuristic-only mode).
    """


@runtime_checkable
class EmbeddingBackend(Protocol):
    """Stage-2 embedding contract.

    Required attributes:
        backend_id: Stable string ID matching the registry key
            (e.g. "llama_cpp_qwen3_embedding")
        model_sha256: Hex SHA-256 of the model weights file. Used by
            the manifest to detect "model changed → invalidate
            prototype cache".
        native_dim: Backend's *native* output dimension (1024 for
            Qwen3, 1024 for mxbai, 768 for KR-SBERT). This is what
            the model produces before any Matryoshka truncation.
        embedding_dim: *Effective* output dimension after Matryoshka
            truncation (defaults to native_dim when no truncation).
            This is what callers see in encode() output. The dispatcher
            adjusts this when a smaller output_dim is configured.

    Required methods:
        encode: Batch text → embedding matrix (with optional truncation)
        warmup: Single dummy encode to amortise cold start
        is_ready: True after warmup completes (cheap check; no I/O)

    Required classmethods:
        probe_available: Cheap availability check (file stat +
            importlib.util.find_spec). MUST NOT load the model or
            make network calls — used by the dispatcher's "auto"
            backend selection BEFORE any heavy lifting.
    """

    backend_id: str
    model_sha256: str
    native_dim: int
    embedding_dim: int

    def encode(
        self,
        texts: Sequence[str],
        *,
        normalize: bool = True,
        truncate_dim: Optional[int] = None,
    ) -> np.ndarray:
        """Encode a list of strings into an (N, dim) array.

        Args:
            texts: Input strings. Empty list → returns empty (0, dim) array.
            normalize: When True, L2-normalise each row so cosine
                similarity reduces to dot product downstream.
            truncate_dim: When set, slice each vector to this many
                dimensions BEFORE L2-normalisation (Matryoshka
                Representation Learning). Required: truncate_dim
                ≤ native_dim. None → return native_dim vectors.

        Returns:
            Float32 numpy array, shape (len(texts), truncate_dim or
            native_dim).
        """
        ...

    def warmup(self) -> None:
        """Do whatever it takes to make the next encode() fast.

        For decoder-style models (Qwen3 GGUF) this typically loads
        weights into RAM and runs one dummy forward pass. For ONNX
        encoder models this primes the operator cache.

        MUST be safe to call on a background thread (workbench calls
        it during startup so the user doesn't see the cold-start
        latency). MUST be idempotent.
        """
        ...

    def is_ready(self) -> bool:
        """Cheap status check. No I/O, no model call."""
        ...

    @classmethod
    def probe_available(cls) -> bool:
        """Cheap, no-side-effect availability check.

        Returns True iff:
          1. The backend's required dependency packages are importable
             (importlib.util.find_spec — does NOT actually import).
          2. The backend's expected model files exist on disk in any
             of the standard locations (AppData / dev / project root).

        MUST NOT:
          - Actually load / mmap the model
          - Make network calls
          - Take more than ~50 ms

        Used by EmbeddingClassifierDispatcher's "auto" mode to pick
        the first backend that can actually start, without paying the
        cold-start cost of trying.
        """
        ...


class AbstractEmbeddingBackend(abc.ABC):
    """Optional convenience base — provides warm-up timing + error
    tracking + a default ``is_ready`` based on whether ``warmup`` ran.

    Subclasses override ``_load`` (heavy init) + ``_encode_impl``
    (per-call work). The ABC handles bookkeeping including Matryoshka
    truncation (slice → re-normalise — order matters; see ``encode``).

    Backends that don't want this can implement the ``EmbeddingBackend``
    Protocol directly.
    """

    backend_id: str = "abstract"
    # native_dim is the model's *raw* output dimension. embedding_dim
    # is the *effective* dimension callers see; defaults to native_dim
    # but the dispatcher may set a smaller value when Matryoshka
    # truncation is configured.
    native_dim: int = 0
    embedding_dim: int = 0
    model_sha256: str = ""

    def __init__(self) -> None:
        self._ready: bool = False
        self._warmup_ms: Optional[float] = None
        self._last_error: Optional[BaseException] = None
        # 2nd-review fix (P1-4): Week-2 plan calls for background
        # preload thread + foreground first-encode → without this lock
        # both can enter _load() concurrently and double-load the
        # 600 MB Qwen3 GGUF. Allocated at __init__ (cheap), used
        # via double-checked locking below.
        self._warmup_lock = threading.Lock()
        # Phase I: backwards-compat — if subclass set embedding_dim
        # but not native_dim (legacy code path before native_dim
        # existed), mirror them so cosine math stays consistent.
        if self.native_dim == 0 and self.embedding_dim > 0:
            self.native_dim = self.embedding_dim
        elif self.embedding_dim == 0 and self.native_dim > 0:
            self.embedding_dim = self.native_dim

    @abc.abstractmethod
    def _load(self) -> None:
        """Heavy one-time init (file read, model decode)."""

    @abc.abstractmethod
    def _encode_impl(
        self,
        texts: Sequence[str],
        *,
        normalize: bool,
    ) -> np.ndarray:
        """Per-call encoding; MUST return (N, embedding_dim) float32."""

    # ---- EmbeddingBackend protocol surface ------------------------------

    def warmup(self) -> None:
        # 2nd-review fix (P1-4): double-checked locking protects against
        # concurrent first-callers (background preload + foreground
        # encode hitting at the same time). Fast path stays lock-free.
        if self._ready:
            return
        with self._warmup_lock:
            if self._ready:  # another thread won the race
                return
            t0 = time.perf_counter()
            try:
                self._load()
                # Single dummy encode primes JIT / GPU kernels
                _ = self._encode_impl([""], normalize=False)
                self._ready = True
            except BaseException as exc:  # noqa: BLE001
                self._last_error = exc
                self._ready = False
                raise
            finally:
                self._warmup_ms = (time.perf_counter() - t0) * 1000.0

    def is_ready(self) -> bool:
        return self._ready

    def encode(
        self,
        texts: Sequence[str],
        *,
        normalize: bool = True,
        truncate_dim: Optional[int] = None,
    ) -> np.ndarray:
        # 2nd-review fix (P1-4): empty-batch fast path BEFORE warmup.
        # Previously encode([]) triggered the warmup → loaded the
        # 600 MB Qwen3 GGUF for nothing. Now: empty input returns an
        # empty matrix with no model load.
        effective_dim = (
            int(truncate_dim) if truncate_dim is not None else self.native_dim
        )
        if not texts:
            return np.zeros((0, effective_dim), dtype=np.float32)
        if not self._ready:
            # Lazy first call — equivalent to .warmup() then .encode()
            self.warmup()
        # Phase I: subclasses produce raw native_dim vectors. The base
        # class handles truncation + (re-)normalisation — keeps the
        # subclass contract simple and ensures truncate-then-normalise
        # ordering (the only correct order for unit-norm output).
        # We always pass normalize=False to the subclass so we can do
        # the slice first; we then normalise here when requested.
        raw = self._encode_impl(list(texts), normalize=False)
        if raw.size == 0:
            return np.zeros((0, effective_dim), dtype=np.float32)
        if truncate_dim is not None:
            t = int(truncate_dim)
            if t <= 0 or t > self.native_dim:
                raise ValueError(
                    f"truncate_dim={t} out of range [1, {self.native_dim}]"
                )
            raw = raw[:, :t]
        if normalize:
            raw = _l2_normalize_rows(raw)
        return raw.astype(np.float32, copy=False)

    # ---- Diagnostics --------------------------------------------------

    def warmup_ms(self) -> Optional[float]:
        """Most recent warm-up duration in milliseconds (None when not run)."""
        return self._warmup_ms

    def last_error(self) -> Optional[BaseException]:
        """Most recent exception during warm-up or encode."""
        return self._last_error

    # ---- Availability probe (default falls back to "True") --------------

    @classmethod
    def probe_available(cls) -> bool:
        """Default: assume the subclass is always available.

        Concrete backends (Qwen GGUF, ONNX mxbai) override this to
        check both their dependency packages and their model files
        on disk.
        """

        return True


def _l2_normalize_rows(vectors: np.ndarray) -> np.ndarray:
    """Per-row L2 normalisation. Cosine = dot product after this."""

    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


__all__ = [
    "EmbeddingBackend",
    "AbstractEmbeddingBackend",
    "BackendUnavailableError",
]
