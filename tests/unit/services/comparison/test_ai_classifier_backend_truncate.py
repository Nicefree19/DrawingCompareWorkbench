# -*- coding: utf-8 -*-
"""Phase I — backend truncate_dim (Matryoshka) contract tests.

The dispatcher relies on ``EmbeddingBackend.encode(..., truncate_dim=N)``
slicing the raw native vector BEFORE L2-normalisation. Validating this
in isolation matters because:
  1. truncate-then-normalise is the only ordering where output is unit-
     norm AT the truncated dim — slicing after normalisation produces
     vectors with norm < 1 and breaks cosine-as-dot-product.
  2. dim mismatch between prototype matrix and query vector is silent —
     dispatcher math just throws shape errors, but a wrong-norm output
     produces correct-shape but quietly-incorrect cosine values.

These tests use a deterministic toy backend so the assertions don't
depend on any model file or external dependency.
"""

from __future__ import annotations

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Toy backend with predictable raw output
# ---------------------------------------------------------------------------


def _toy_backend():
    """Build a backend where encode of "all zeros except some pattern"
    produces a known raw vector — lets us verify truncate behaviour
    deterministically."""

    from src.services.comparison.ai_classifier.backends.base import (
        AbstractEmbeddingBackend,
    )

    class _ToyTruncate(AbstractEmbeddingBackend):
        backend_id = "toy_truncate"
        native_dim = 8
        embedding_dim = 8
        model_sha256 = "0" * 64

        @classmethod
        def probe_available(cls) -> bool:
            return True

        def _load(self) -> None:
            return

        def _encode_impl(self, texts, *, normalize):
            # Return [1, 2, 3, 4, 5, 6, 7, 8] for every text — easy to
            # verify which cells survive truncation. normalize is
            # ignored (base class handles it).
            base = np.array([1, 2, 3, 4, 5, 6, 7, 8], dtype=np.float32)
            return np.tile(base, (len(texts), 1))

    return _ToyTruncate()


# ---------------------------------------------------------------------------
# Truncation correctness
# ---------------------------------------------------------------------------


def test_no_truncate_returns_native_dim() -> None:
    backend = _toy_backend()
    out = backend.encode(["x"], normalize=False)
    assert out.shape == (1, 8)
    # Raw, not normalised
    np.testing.assert_array_equal(out[0], [1, 2, 3, 4, 5, 6, 7, 8])


def test_truncate_dim_4_keeps_first_4_cells() -> None:
    backend = _toy_backend()
    out = backend.encode(["x"], normalize=False, truncate_dim=4)
    assert out.shape == (1, 4)
    np.testing.assert_array_equal(out[0], [1, 2, 3, 4])


def test_truncate_then_normalize_yields_unit_norm() -> None:
    """CRITICAL — truncate-then-normalise gives unit-norm at truncated
    dim. The opposite order (normalise raw, then truncate) leaves
    norm < 1 and breaks cosine-as-dot-product downstream."""

    backend = _toy_backend()
    out = backend.encode(["x"], normalize=True, truncate_dim=4)
    assert out.shape == (1, 4)
    norm = float(np.linalg.norm(out[0]))
    assert norm == pytest.approx(1.0, abs=1e-5)
    # Verify the underlying values: raw [1,2,3,4] / sqrt(1+4+9+16) = /sqrt(30)
    expected = np.array([1, 2, 3, 4], dtype=np.float32) / np.sqrt(30.0)
    np.testing.assert_allclose(out[0], expected, atol=1e-6)


def test_truncate_dim_equals_native_returns_full_dim() -> None:
    backend = _toy_backend()
    out = backend.encode(["x"], normalize=True, truncate_dim=8)
    assert out.shape == (1, 8)


def test_truncate_dim_zero_raises() -> None:
    backend = _toy_backend()
    with pytest.raises(ValueError, match="truncate_dim"):
        backend.encode(["x"], truncate_dim=0)


def test_truncate_dim_exceeding_native_raises() -> None:
    backend = _toy_backend()
    with pytest.raises(ValueError, match="truncate_dim"):
        backend.encode(["x"], truncate_dim=99)


def test_truncate_dim_negative_raises() -> None:
    backend = _toy_backend()
    with pytest.raises(ValueError, match="truncate_dim"):
        backend.encode(["x"], truncate_dim=-1)


# ---------------------------------------------------------------------------
# Empty-batch fast path with truncate_dim
# ---------------------------------------------------------------------------


def test_empty_batch_with_truncate_returns_correct_shape() -> None:
    """encode([]) must respect truncate_dim — even though no model
    call happens, the empty matrix still needs the right column count
    so downstream code doesn't get a shape mismatch when the dispatcher
    pre-allocates."""

    backend = _toy_backend()
    out = backend.encode([], truncate_dim=4)
    assert out.shape == (0, 4)
    out_native = backend.encode([])
    assert out_native.shape == (0, 8)


# ---------------------------------------------------------------------------
# Backwards compat — embedding_dim default mirrors native_dim
# ---------------------------------------------------------------------------


def test_legacy_subclass_with_only_embedding_dim_still_works() -> None:
    """Old subclasses set embedding_dim but not native_dim. The base
    class auto-mirrors them so encode(truncate_dim=...) still validates
    correctly."""

    from src.services.comparison.ai_classifier.backends.base import (
        AbstractEmbeddingBackend,
    )

    class _Legacy(AbstractEmbeddingBackend):
        backend_id = "legacy"
        embedding_dim = 4
        # native_dim NOT set — should auto-mirror
        model_sha256 = ""

        def _load(self) -> None:
            return

        def _encode_impl(self, texts, *, normalize):
            return np.ones((len(texts), 4), dtype=np.float32)

    b = _Legacy()
    assert b.native_dim == 4  # auto-mirrored
    out = b.encode(["x"], truncate_dim=2)
    assert out.shape == (1, 2)


def test_default_probe_available_returns_true() -> None:
    """Subclasses that don't override probe_available default to True
    (assume the subclass is available) — concrete backends like Qwen /
    ONNX override with file-existence + spec checks."""

    from src.services.comparison.ai_classifier.backends.base import (
        AbstractEmbeddingBackend,
    )

    class _Default(AbstractEmbeddingBackend):
        backend_id = "default_probe"
        native_dim = 4
        embedding_dim = 4
        model_sha256 = ""

        def _load(self) -> None:
            return

        def _encode_impl(self, texts, *, normalize):
            return np.zeros((len(texts), 4), dtype=np.float32)

    assert _Default.probe_available() is True
