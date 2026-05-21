# -*- coding: utf-8 -*-
"""Phase I — speed-mode embedding backend: mxbai-embed-large-v1 (ONNX).

Recommended by the Korean construction drawing embedding model report
as the best license-clear (Apache-2.0) speed alternative to the
Qwen3-Embedding-0.6B-GGUF quality backend. Cold start ~200-300 ms vs
Qwen's 2-5 s, at the cost of slightly weaker Korean STS performance
(report §Top 3 Recommendation, Top 2 entry).

Architecture:
    1. ``__init__`` is cheap — stores config, doesn't touch disk.
    2. ``probe_available`` (classmethod) — verifies sentence-transformers
       + onnxruntime importability AND model directory existence on
       disk. Used by the dispatcher's "auto" mode BEFORE warmup.
    3. ``_load`` (called from ``warmup`` via AbstractEmbeddingBackend)
       imports sentence_transformers lazily, locates the model
       directory (sentence-transformers standard layout: config.json
       + tokenizer.json + onnx/model_quint8_avx2.onnx), instantiates
       SentenceTransformer with backend="onnx" and local_files_only=True
       (CRITICAL — prevents HF Hub network calls), runs a dummy encode
       to prime caches.
    4. ``_encode_impl`` runs ``model.encode(...)`` per batch and returns
       raw native vectors. Base class handles Matryoshka truncation +
       L2 normalization (truncate-then-normalise order is critical for
       unit-norm output at the truncated dimension).

Cold-start behaviour:
    The 670 MB ONNX model loads in 100-300 ms (reportedly 3.08x faster
    than full PyTorch on short text — sentence-transformers efficiency
    docs). The ``AbstractEmbeddingBackend.warmup()`` lock prevents
    background-preload + foreground encode races; the workbench is
    expected to call ``warmup()`` from a background thread shortly
    after start.

Model directory resolution (descending priority):
    1. Explicit ``model_dir`` constructor argument
    2. ``%LOCALAPPDATA%/DrawingCompareWorkbench/ai_models/onnx_mxbai_large/``
    3. ``./models/onnx_mxbai_large/`` (development)
    4. ``project_root/models/onnx_mxbai_large/``

The directory must contain (sentence-transformers standard layout):
    config.json
    tokenizer.json
    tokenizer_config.json
    sentence_bert_config.json (optional)
    modules.json
    onnx/model_quint8_avx2.onnx  (or model.onnx, model_qint8_avx512.onnx)

The backend does NOT download the model. It refuses to start when the
directory isn't found, with a friendly Korean ``BackendUnavailableError``
explaining where to put it.

Matryoshka:
    mxbai-embed-large-v1 supports MRL truncation (model card §Usage).
    Truncating 1024 → 512 halves storage and roughly halves cosine
    compute with usually <1 pp accuracy loss. The dispatcher passes
    truncate_dim through; this backend itself returns native 1024-d
    vectors and lets ``AbstractEmbeddingBackend.encode`` slice + re-
    normalise.
"""

from __future__ import annotations

import hashlib
import importlib.util
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

BACKEND_ID = "onnx_mxbai_large"

# Model card: https://huggingface.co/mixedbread-ai/mxbai-embed-large-v1
MXBAI_NATIVE_DIM = 1024
DEFAULT_MODEL_DIRNAME = "onnx_mxbai_large"

# Preferred ONNX file. Quantised int8 with AVX2 instructions —
# runs on virtually all modern x86_64 CPUs without GPU. Falls back
# to other quantisation tiers if the preferred file isn't present.
PREFERRED_ONNX_FILES = (
    "onnx/model_quint8_avx2.onnx",     # quint8, AVX2-optimised — best CPU
    "onnx/model_qint8_avx512.onnx",    # qint8, AVX-512 if available
    "onnx/model_qint8_arm64.onnx",     # ARM64 (rare on Windows but supported)
    "onnx/model_int8.onnx",            # int8 generic
    "onnx/model.onnx",                 # fp32 fallback (~1.3 GB)
)

# Marker file the directory MUST contain for probe_available() to
# return True. Sentence-transformers requires config.json minimum.
REQUIRED_MARKER_FILES = ("config.json", "tokenizer.json")


def _candidate_model_dirs(filename: str = DEFAULT_MODEL_DIRNAME) -> list[Path]:
    """All locations the resolver searches, in priority order."""

    candidates: list[Path] = []

    appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if appdata:
        candidates.append(
            Path(appdata) / "DrawingCompareWorkbench" / "ai_models" / filename
        )
    candidates.append(Path.cwd() / "models" / filename)
    try:
        proj_root = Path(__file__).resolve().parents[5]
        candidates.append(proj_root / "models" / filename)
    except IndexError:
        pass
    return candidates


def _resolve_model_dir(
    explicit: Optional[Path] = None,
    filename: str = DEFAULT_MODEL_DIRNAME,
) -> Optional[Path]:
    """Find the ONNX model directory. Returns None when nothing exists.

    Validates that the candidate directory contains the marker files
    (config.json + tokenizer.json) so a half-extracted directory
    doesn't get accepted.
    """

    if explicit:
        p = Path(explicit)
        if p.is_dir() and all((p / m).exists() for m in REQUIRED_MARKER_FILES):
            return p
        logger.warning("Explicit model_dir %s missing required marker files",
                       p)
        return None

    for candidate in _candidate_model_dirs(filename):
        if candidate.is_dir() and all(
            (candidate / m).exists() for m in REQUIRED_MARKER_FILES
        ):
            return candidate
    return None


def _resolve_onnx_file(model_dir: Path) -> Optional[Path]:
    """Pick the best available ONNX file in priority order."""

    for relpath in PREFERRED_ONNX_FILES:
        candidate = model_dir / relpath
        if candidate.exists():
            return candidate
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


class OnnxMxbaiLargeBackend(AbstractEmbeddingBackend):
    """mxbai-embed-large-v1 (ONNX quantised) via sentence-transformers.

    Speed mode for the dual-backend embedding cascade. Apache-2.0,
    1024-d native (Matryoshka-truncatable to 512 / 256 / 128).
    """

    backend_id = BACKEND_ID
    native_dim = MXBAI_NATIVE_DIM
    embedding_dim = MXBAI_NATIVE_DIM

    def __init__(
        self,
        *,
        model_dir: Optional[Path] = None,
        model_dirname: str = DEFAULT_MODEL_DIRNAME,
        onnx_filename: Optional[str] = None,  # None → first available
        batch_size: int = 16,
        instruction: str = "",  # mxbai uses no-instruction by default
    ) -> None:
        super().__init__()
        self._model_dir: Optional[Path] = (
            Path(model_dir) if model_dir else None
        )
        self._model_dirname = model_dirname
        self._onnx_filename_override = onnx_filename
        self._batch_size = int(batch_size)
        self._instruction = str(instruction or "")
        self._model: Any = None  # SentenceTransformer, set lazily
        self._resolved_model_path: Optional[Path] = None  # for manifest
        self._resolved_onnx_file: Optional[Path] = None

    # ---- Availability probe (Phase I auto-mode bootstrap) ---------------

    @classmethod
    def probe_available(cls) -> bool:
        """True iff sentence-transformers + onnxruntime are importable
        AND the model directory exists with required marker files.

        Cheap — file stat + spec lookup only. No model load.
        """

        for pkg in ("sentence_transformers", "onnxruntime"):
            if importlib.util.find_spec(pkg) is None:
                return False
        return _resolve_model_dir(filename=DEFAULT_MODEL_DIRNAME) is not None

    # ---- AbstractEmbeddingBackend hooks --------------------------------

    def _load(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise BackendUnavailableError(
                "sentence-transformers 미설치. "
                "설치: pip install sentence-transformers onnxruntime optimum"
            ) from exc
        try:
            import onnxruntime  # noqa: F401 — verify available
        except ImportError as exc:
            raise BackendUnavailableError(
                "onnxruntime 미설치. 설치: pip install onnxruntime"
            ) from exc

        model_dir = _resolve_model_dir(
            self._model_dir, filename=self._model_dirname,
        )
        if model_dir is None:
            search_hint = (
                "%LOCALAPPDATA%/DrawingCompareWorkbench/ai_models/"
                f"{self._model_dirname}/ 또는 ./models/{self._model_dirname}/"
            )
            raise BackendUnavailableError(
                f"mxbai ONNX 모델 디렉토리를 찾지 못했습니다. "
                f"다음 위치에 배치하세요: {search_hint}\n"
                f"필수 파일: config.json, tokenizer.json, "
                f"onnx/model_quint8_avx2.onnx\n"
                f"다운로드: https://huggingface.co/mixedbread-ai/mxbai-embed-large-v1"
            )

        # Pick the best ONNX file (or honour caller override).
        if self._onnx_filename_override:
            onnx_file = model_dir / self._onnx_filename_override
            if not onnx_file.exists():
                raise BackendUnavailableError(
                    f"지정한 ONNX 파일이 없음: {onnx_file}"
                )
        else:
            onnx_file = _resolve_onnx_file(model_dir)
            if onnx_file is None:
                raise BackendUnavailableError(
                    f"{model_dir}에 사용 가능한 ONNX 파일이 없음. "
                    f"우선순위: {list(PREFERRED_ONNX_FILES)}"
                )

        self._resolved_model_path = model_dir
        self._resolved_onnx_file = onnx_file
        self.model_sha256 = _file_sha256(onnx_file)

        # Compute the relative file_name sentence-transformers expects.
        # sentence-transformers' ONNX backend accepts "model_kwargs"
        # with a "file_name" key (relative to the model directory).
        # See https://sbert.net/docs/sentence_transformer/usage/efficiency.html
        try:
            relative_onnx = onnx_file.relative_to(model_dir)
        except ValueError:
            relative_onnx = Path(onnx_file.name)

        logger.info(
            "Loading ONNX mxbai-embed-large from %s (file=%s, batch=%d)",
            model_dir, relative_onnx, self._batch_size,
        )
        # CRITICAL — local_files_only=True blocks any HF Hub call.
        # Without this flag, sentence-transformers will try to fetch
        # the latest model card from HuggingFace on first load,
        # violating our offline guarantee.
        self._model = SentenceTransformer(
            str(model_dir),
            backend="onnx",
            model_kwargs={"file_name": str(relative_onnx)},
            local_files_only=True,
        )

        # Verify dim — the model card says 1024 but we double-check.
        try:
            test = self._model.encode(
                ["test"], batch_size=1, convert_to_numpy=True,
                normalize_embeddings=False,
            )
            actual_dim = int(test.shape[1])
            if actual_dim != self.native_dim:
                logger.warning(
                    "ONNX mxbai actual native_dim (%d) ≠ expected (%d) — "
                    "manifest will reflect actual",
                    actual_dim, self.native_dim,
                )
                self.native_dim = actual_dim
                if self.embedding_dim == MXBAI_NATIVE_DIM:
                    self.embedding_dim = actual_dim
        except Exception as exc:  # noqa: BLE001
            logger.exception("Test encode after ONNX load failed")
            raise BackendUnavailableError(
                f"ONNX 모델 로드 후 테스트 인코드 실패: {exc}"
            ) from exc

    def _encode_impl(
        self,
        texts: Sequence[str],
        *,
        normalize: bool,
    ) -> np.ndarray:
        # Phase I: AbstractEmbeddingBackend.encode() always passes
        # normalize=False so truncation can run before L2-normalisation.
        # This subclass returns raw native 1024-d vectors.
        if self._model is None:
            raise RuntimeError("Backend not warmed up — call warmup() first")

        formatted = [self._format_one(t) for t in texts]
        # convert_to_numpy=True bypasses torch tensor conversion in
        # ONNX backend mode — measurably faster on the encode path.
        # normalize_embeddings=False because base class handles it.
        result = self._model.encode(
            formatted,
            batch_size=self._batch_size,
            convert_to_numpy=True,
            normalize_embeddings=False,
        )
        if result.size == 0:
            return np.zeros((0, self.native_dim), dtype=np.float32)
        arr = np.asarray(result, dtype=np.float32)
        # Backwards-compat: when called directly (not via base.encode),
        # honour the normalize flag. Base.encode always passes False so
        # this branch is dead in the dispatcher path but still safe.
        if normalize:
            from .base import _l2_normalize_rows
            arr = _l2_normalize_rows(arr)
        return arr

    def _format_one(self, text: str) -> str:
        """Apply optional instruction prefix (mxbai uses no prefix by
        default — just returns text). Reserved for future instruction-
        tuned variants.
        """

        if not self._instruction:
            return text
        return f"Instruct: {self._instruction}\nQuery: {text}"


# ---------------------------------------------------------------------------
# Self-registration
# ---------------------------------------------------------------------------


def _factory(**kwargs) -> OnnxMxbaiLargeBackend:
    return OnnxMxbaiLargeBackend(**kwargs)


# Register at import time so callers can locate the backend by ID
# even before the model directory is on disk. Use replace=True so re-
# imports (test fixtures, dev reload) don't raise.
try:
    register_backend(BACKEND_ID, _factory, replace=True)
except Exception:  # noqa: BLE001
    logger.debug("Could not auto-register %s backend at import time",
                 BACKEND_ID, exc_info=True)


__all__ = [
    "BACKEND_ID",
    "MXBAI_NATIVE_DIM",
    "DEFAULT_MODEL_DIRNAME",
    "PREFERRED_ONNX_FILES",
    "REQUIRED_MARKER_FILES",
    "OnnxMxbaiLargeBackend",
]
