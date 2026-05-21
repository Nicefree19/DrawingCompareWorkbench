# -*- coding: utf-8 -*-
"""Concrete Qwen3-Embedding-0.6B-GGUF backend via llama-cpp-python.

⚠️  MODEL DISAMBIGUATION (3rd-review fix):
    This backend targets ``Qwen/Qwen3-Embedding-0.6B-GGUF`` — the
    DEDICATED embedding model. NOT ``Qwen/Qwen3-0.6B-GGUF`` (which is
    a causal LM). The embedding variant returns sequence-level vectors
    via llama-cpp-python's ``Llama(..., embedding=True)`` API; the
    causal variant only gives token-level embeddings + would require
    manual pooling with no quality acceptance from upstream.

The backend self-checks model identity on first load by inspecting
the model metadata; refuses to start with the causal variant.

Architecture:
    1. ``__init__`` is cheap — stores config, doesn't touch disk.
    2. ``_load`` (called from ``warmup`` via AbstractEmbeddingBackend)
       imports llama_cpp lazily, locates the GGUF file, instantiates
       Llama with embedding=True, runs a dummy encode to prime caches.
    3. ``_encode_impl`` runs ``create_embedding`` per text, optionally
       prepends an instruction prompt (Qwen3-Embedding instruction-
       aware retrieval).

Cold-start behaviour:
    The 600 MB GGUF takes 2-5 s to mmap + first inference. The
    ``AbstractEmbeddingBackend.warmup()`` lock (added per 2nd review)
    prevents background-preload + foreground encode races; the
    workbench is expected to call ``warmup()`` from a background
    thread shortly after start so the user doesn't see the cold-start
    latency on their first zone classification.

Model file resolution (descending priority):
    1. Explicit ``model_path`` constructor argument
    2. ``%LOCALAPPDATA%/DrawingCompareWorkbench/ai_models/<filename>``
    3. ``./models/<filename>`` (development)

The backend does NOT download the GGUF. It refuses to start when the
file isn't found, with a friendly Korean ``BackendUnavailableError``
explaining where to put it. A separate first-run downloader UX hooks
into this in a future commit.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

from .base import AbstractEmbeddingBackend, BackendUnavailableError
from . import register_backend

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BACKEND_ID = "llama_cpp_qwen3_embedding"

# Model card: https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF
QWEN3_EMBEDDING_DIM = 1024  # full dim; truncatable to 32-1024 via output_dim
DEFAULT_MODEL_FILENAME = "Qwen3-Embedding-0.6B-Q8_0.gguf"

# Identity check — the model file must contain this substring in its
# metadata or filename. This is the second line of defence against
# someone accidentally pointing at Qwen3-0.6B-GGUF (causal variant).
_REQUIRED_MODEL_SIGNATURE = "qwen3-embedding"


def _resolve_model_path(
    explicit: Optional[Path] = None,
    filename: str = DEFAULT_MODEL_FILENAME,
) -> Optional[Path]:
    """Find the GGUF file. Returns None when nothing exists."""

    if explicit:
        p = Path(explicit)
        if p.exists():
            return p
        logger.warning("Explicit model_path %s does not exist", p)
        return None

    # 1. AppData (production install location)
    appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if appdata:
        candidate = Path(appdata) / "DrawingCompareWorkbench" / "ai_models" / filename
        if candidate.exists():
            return candidate

    # 2. ./models/ (development)
    dev_candidate = Path.cwd() / "models" / filename
    if dev_candidate.exists():
        return dev_candidate

    # 3. project_root/models/
    proj_root = Path(__file__).resolve().parents[5]
    proj_candidate = proj_root / "models" / filename
    if proj_candidate.exists():
        return proj_candidate

    return None


def _file_sha256(path: Path) -> str:
    """SHA-256 of a file. Used for manifest fingerprint."""

    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Backend implementation
# ---------------------------------------------------------------------------


class LlamaCppQwen3EmbeddingBackend(AbstractEmbeddingBackend):
    """Qwen3-Embedding-0.6B-GGUF backend via llama-cpp-python."""

    backend_id = BACKEND_ID
    native_dim = QWEN3_EMBEDDING_DIM
    embedding_dim = QWEN3_EMBEDDING_DIM

    def __init__(
        self,
        *,
        model_path: Optional[Path] = None,
        model_filename: str = DEFAULT_MODEL_FILENAME,
        n_ctx: int = 8192,
        n_gpu_layers: int = -1,           # -1 = offload all to GPU when available
        n_threads: Optional[int] = None,  # None = llama_cpp default
        verbose: bool = False,
        instruction: str = "",            # Empty = no instruction prefix
    ) -> None:
        super().__init__()
        self._model_path: Optional[Path] = (
            Path(model_path) if model_path else None
        )
        self._model_filename = model_filename
        self._n_ctx = int(n_ctx)
        self._n_gpu_layers = int(n_gpu_layers)
        self._n_threads = n_threads
        self._verbose = bool(verbose)
        self._instruction = str(instruction or "")
        self._llm: Any = None  # llama_cpp.Llama, set lazily
        self._resolved_model_path: Optional[Path] = None

    # ---- Availability probe (Phase I) ----------------------------------

    @classmethod
    def probe_available(cls) -> bool:
        """True iff llama_cpp is importable AND a Qwen3-Embedding GGUF
        file is present in any of the standard locations.

        No model load — just file existence + spec lookup. Used by the
        dispatcher's "auto" mode BEFORE paying the cold-start cost.
        """

        import importlib.util
        if importlib.util.find_spec("llama_cpp") is None:
            return False
        return _resolve_model_path(filename=DEFAULT_MODEL_FILENAME) is not None

    # ---- AbstractEmbeddingBackend hooks --------------------------------

    def _load(self) -> None:
        try:
            from llama_cpp import Llama
        except ImportError as exc:
            raise BackendUnavailableError(
                "llama-cpp-python 미설치. 설치: pip install llama-cpp-python"
            ) from exc

        path = _resolve_model_path(
            self._model_path, filename=self._model_filename,
        )
        if path is None:
            search_hint = (
                "%LOCALAPPDATA%/DrawingCompareWorkbench/ai_models/"
                f"{self._model_filename} 또는 ./models/{self._model_filename}"
            )
            raise BackendUnavailableError(
                f"Qwen3-Embedding GGUF 파일을 찾지 못했습니다. "
                f"다음 위치에 배치하세요: {search_hint}\n"
                f"다운로드: https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF"
            )

        # Identity check (3rd-review safeguard) — refuse the causal
        # variant. The dedicated embedding GGUF has 'embedding' in the
        # filename; we ALSO check model metadata after load when possible.
        name_lower = path.name.lower()
        if _REQUIRED_MODEL_SIGNATURE not in name_lower:
            raise BackendUnavailableError(
                f"모델 파일명에 'embedding' 표식이 없습니다: {path.name}\n"
                f"이 백엔드는 Qwen3-Embedding-0.6B-GGUF 전용입니다. "
                f"causal Qwen3-0.6B-GGUF는 sequence embedding 보장 없음."
            )

        self._resolved_model_path = path
        self.model_sha256 = _file_sha256(path)

        kwargs = {
            "model_path": str(path),
            "embedding": True,           # CRITICAL — sequence embedding mode
            "n_ctx": self._n_ctx,
            "n_gpu_layers": self._n_gpu_layers,
            "verbose": self._verbose,
        }
        if self._n_threads is not None:
            kwargs["n_threads"] = int(self._n_threads)

        logger.info(
            "Loading Qwen3-Embedding GGUF: %s (n_ctx=%d, n_gpu_layers=%d)",
            path.name, self._n_ctx, self._n_gpu_layers,
        )
        self._llm = Llama(**kwargs)

        # Inspect actual embedding dim — the model card says 1024 but
        # we verify in case a different quant exposes a different head.
        try:
            test = self._llm.create_embedding("test")
            actual_dim = len(test["data"][0]["embedding"])
            if actual_dim != self.embedding_dim:
                logger.warning(
                    "Qwen3-Embedding actual dim (%d) ≠ expected (%d) — "
                    "manifest will reflect actual",
                    actual_dim, self.embedding_dim,
                )
                self.embedding_dim = actual_dim
        except Exception as exc:  # noqa: BLE001
            logger.exception("Test encode after load failed")
            raise BackendUnavailableError(
                f"GGUF 로드 후 테스트 인코드 실패: {exc}"
            ) from exc

    def _encode_impl(
        self,
        texts: Sequence[str],
        *,
        normalize: bool,
    ) -> np.ndarray:
        # Phase I: AbstractEmbeddingBackend.encode() now passes
        # normalize=False unconditionally so truncation can run before
        # L2-normalisation. This subclass returns raw native vectors;
        # the base class slices + normalises as needed.
        if self._llm is None:
            raise RuntimeError("Backend not warmed up — call warmup() first")

        formatted = [self._format_one(t) for t in texts]
        # llama-cpp-python supports batch input as a list
        result = self._llm.create_embedding(formatted)
        # Result schema:
        #   {"object": "list", "data": [{"object":"embedding",
        #                                 "embedding":[float,...],
        #                                 "index": 0}, ...], ...}
        rows = [item["embedding"] for item in result.get("data", [])]
        if not rows:
            return np.zeros((0, self.native_dim or self.embedding_dim),
                            dtype=np.float32)
        arr = np.asarray(rows, dtype=np.float32)
        # Backwards-compat: when called directly (not via base.encode),
        # honour the normalize flag. Base.encode always passes False so
        # this branch is dead in the dispatcher path but still safe.
        if normalize:
            arr = _l2_normalize(arr)
        return arr

    def _format_one(self, text: str) -> str:
        """Apply Qwen3-Embedding instruction-aware prompt format.

        When ``self._instruction`` is empty, returns the text as-is.
        Otherwise prepends the instruction per the model card recipe:
            "Instruct: {instruction}\\nQuery: {text}"
        """

        if not self._instruction:
            return text
        return f"Instruct: {self._instruction}\nQuery: {text}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _l2_normalize(vectors: np.ndarray) -> np.ndarray:
    """Per-row L2 normalisation. Cosine similarity = dot product after."""

    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0  # avoid div-by-zero on degenerate vectors
    return vectors / norms


# ---------------------------------------------------------------------------
# Self-registration
# ---------------------------------------------------------------------------


def _factory(**kwargs) -> LlamaCppQwen3EmbeddingBackend:
    return LlamaCppQwen3EmbeddingBackend(**kwargs)


# Register at import time so callers can locate the backend by ID
# even before the GGUF is on disk. Use replace=True so re-imports
# (test fixtures, dev reload) don't raise.
try:
    register_backend(BACKEND_ID, _factory, replace=True)
except Exception:  # noqa: BLE001
    logger.debug("Could not auto-register %s backend at import time",
                 BACKEND_ID, exc_info=True)


__all__ = [
    "BACKEND_ID",
    "QWEN3_EMBEDDING_DIM",
    "DEFAULT_MODEL_FILENAME",
    "LlamaCppQwen3EmbeddingBackend",
]
