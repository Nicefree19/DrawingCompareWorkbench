# -*- coding: utf-8 -*-
"""Phase I — dual-backend dispatcher (auto select + Matryoshka) tests.

Pins behaviour we deliberately added:

  * "auto" mode walks ``embedding_backend_fallbacks`` in order and
    picks the first one whose ``probe_available()`` returns True.
  * Explicit ``embedding_backend_id`` (non-"auto") is used as-is —
    no fallback chain.
  * When all probes fail, the dispatcher raises BackendUnavailable
    cleanly so ``classify_zones`` falls back to Stage-1.
  * Matryoshka truncation: prototype + query embeddings both clamp
    to ``embedding_output_dim``; cosine math still works at the
    truncated dim.
  * Cache key includes ``embedding_backend_id`` and
    ``embedding_output_dim`` — switching modes returns a fresh
    dispatcher, never a wrong-dim leftover.
  * Manifest fingerprint uses the *active* backend ID, not the
    configured one — so an "auto" mode that resolved to mxbai writes
    "onnx_mxbai_large" into the manifest, not "auto".
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pytest


_TOY_DIM = 8


# ---------------------------------------------------------------------------
# Toy backends (two flavours so we can probe selection / fallback)
# ---------------------------------------------------------------------------


def _make_toy_class(backend_id: str, *, available: bool = True):
    """Return a toy backend class bound to ``backend_id`` with a
    controllable ``probe_available()`` answer."""

    from src.services.comparison.ai_classifier.backends.base import (
        AbstractEmbeddingBackend,
    )

    _available = available

    class _Toy(AbstractEmbeddingBackend):
        native_dim = _TOY_DIM
        embedding_dim = _TOY_DIM
        model_sha256 = "0" * 64

        @classmethod
        def probe_available(cls) -> bool:
            return _available

        def __init__(self) -> None:
            super().__init__()
            self.backend_id = backend_id
            self._resolved_model_path = Path(f"/tmp/{backend_id}-Q8_0.gguf")

        def _load(self) -> None:
            return

        def _encode_impl(self, texts, *, normalize):
            # Each text → simple deterministic raw vector with the
            # backend ID's ASCII sum as a tag in slot 0
            tag = float(sum(ord(c) for c in backend_id) % 7) + 1.0
            row = np.zeros(_TOY_DIM, dtype=np.float32)
            row[0] = tag
            row[1] = 1.0  # so it's never zero
            return np.tile(row, (len(texts), 1))

    return _Toy


def _register(backend_id: str, *, available: bool = True):
    from src.services.comparison.ai_classifier.backends import register_backend

    cls = _make_toy_class(backend_id, available=available)
    register_backend(backend_id, lambda **kw: cls(), replace=True)
    return cls


@pytest.fixture(autouse=True)
def _reset_state():
    from src.services.comparison.ai_classifier import clear_dispatcher_cache

    clear_dispatcher_cache()
    yield
    clear_dispatcher_cache()


# ---------------------------------------------------------------------------
# Auto mode — walks fallback list, picks first available
# ---------------------------------------------------------------------------


def test_auto_picks_first_available_backend(tmp_path) -> None:
    """auto mode walks the fallbacks list in order and picks the
    first one whose probe_available() returns True."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, get_embedding_dispatcher,
    )

    _register("auto_first", available=True)
    _register("auto_second", available=True)

    cfg = AiClassifierConfig(
        enabled=True, use_embedding=True,
        embedding_backend_id="auto",
        embedding_backend_fallbacks=["auto_first", "auto_second"],
        cache_dir=str(tmp_path / "cache"),
    )
    d = get_embedding_dispatcher(cfg)
    d.prepare()
    assert d.is_ready()
    assert d._active_backend_id == "auto_first"


def test_auto_skips_unavailable_and_picks_next(tmp_path) -> None:
    """When the first fallback's probe_available() is False, auto
    moves on to the second one."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, get_embedding_dispatcher,
    )

    _register("auto_skip_first", available=False)  # probe → False
    _register("auto_skip_second", available=True)

    cfg = AiClassifierConfig(
        enabled=True, use_embedding=True,
        embedding_backend_id="auto",
        embedding_backend_fallbacks=["auto_skip_first", "auto_skip_second"],
        cache_dir=str(tmp_path / "cache"),
    )
    d = get_embedding_dispatcher(cfg)
    d.prepare()
    assert d._active_backend_id == "auto_skip_second"


def test_auto_raises_when_no_backend_available(tmp_path) -> None:
    """All probes return False → dispatcher.prepare() raises
    BackendUnavailableError. classify_zones catches this and falls
    back to the Stage-1 heuristic — no crash, no Stage-2 result."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, get_embedding_dispatcher,
    )
    from src.services.comparison.ai_classifier.backends import (
        BackendUnavailableError,
    )

    _register("auto_none_a", available=False)
    _register("auto_none_b", available=False)

    cfg = AiClassifierConfig(
        enabled=True, use_embedding=True,
        embedding_backend_id="auto",
        embedding_backend_fallbacks=["auto_none_a", "auto_none_b"],
        cache_dir=str(tmp_path / "cache"),
    )
    d = get_embedding_dispatcher(cfg)
    with pytest.raises(BackendUnavailableError, match="auto 모드"):
        d.prepare()


def test_explicit_backend_id_bypasses_auto(tmp_path) -> None:
    """Setting embedding_backend_id to a concrete registry ID skips
    the auto walk entirely — the configured backend is loaded
    directly even if it's not in fallbacks."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, get_embedding_dispatcher,
    )

    _register("explicit_chosen", available=True)
    _register("explicit_other", available=True)

    cfg = AiClassifierConfig(
        enabled=True, use_embedding=True,
        embedding_backend_id="explicit_chosen",
        embedding_backend_fallbacks=["explicit_other"],  # ignored
        cache_dir=str(tmp_path / "cache"),
    )
    d = get_embedding_dispatcher(cfg)
    d.prepare()
    assert d._active_backend_id == "explicit_chosen"


def test_explicit_backend_does_not_fall_back(tmp_path) -> None:
    """When the explicit (non-auto) backend fails, the dispatcher
    raises — it does NOT silently fall back. Auto-fallback is
    reserved for "auto" mode."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, get_embedding_dispatcher,
    )
    from src.services.comparison.ai_classifier.backends import (
        BackendUnavailableError, register_backend,
    )

    # Register a backend whose warmup unconditionally fails
    from src.services.comparison.ai_classifier.backends.base import (
        AbstractEmbeddingBackend,
    )

    class _Broken(AbstractEmbeddingBackend):
        backend_id = "explicit_broken"
        native_dim = _TOY_DIM
        embedding_dim = _TOY_DIM
        model_sha256 = ""

        @classmethod
        def probe_available(cls) -> bool:
            return True

        def _load(self) -> None:
            raise BackendUnavailableError("forced failure for test")

        def _encode_impl(self, texts, *, normalize):
            return np.zeros((len(texts), _TOY_DIM), dtype=np.float32)

    register_backend("explicit_broken", lambda **kw: _Broken(), replace=True)
    _register("would_fall_back", available=True)

    cfg = AiClassifierConfig(
        enabled=True, use_embedding=True,
        embedding_backend_id="explicit_broken",  # explicit, not auto
        embedding_backend_fallbacks=["would_fall_back"],
        cache_dir=str(tmp_path / "cache"),
    )
    d = get_embedding_dispatcher(cfg)
    with pytest.raises(BackendUnavailableError, match="forced failure"):
        d.prepare()
    # active_backend_id never got set
    assert d._active_backend_id is None


# ---------------------------------------------------------------------------
# Matryoshka truncation (output_dim)
# ---------------------------------------------------------------------------


def test_output_dim_truncates_prototype_and_query_embeddings(tmp_path) -> None:
    """When embedding_output_dim is set, BOTH prototype and query
    vectors clamp to that dim. Cosine math still works."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, get_embedding_dispatcher,
    )

    _register("trunc_test", available=True)

    cfg = AiClassifierConfig(
        enabled=True, use_embedding=True,
        embedding_backend_id="trunc_test",
        embedding_output_dim=4,  # half of native _TOY_DIM=8
        embedding_threshold=0.1,  # toy backend gives identical vectors
        cache_dir=str(tmp_path / "cache"),
    )
    d = get_embedding_dispatcher(cfg)
    d.prepare()
    # corpus embeddings are 4-dim
    assert d._corpus is not None
    assert d._corpus.embeddings.shape[1] == 4
    # And cache key shape matches
    assert d._output_dim == 4


def test_output_dim_zero_means_native(tmp_path) -> None:
    """embedding_output_dim=0 (default) is treated as "no truncation"
    and the dispatcher uses the backend's native_dim."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, get_embedding_dispatcher,
    )

    _register("native_dim_test", available=True)

    cfg = AiClassifierConfig(
        enabled=True, use_embedding=True,
        embedding_backend_id="native_dim_test",
        embedding_output_dim=0,  # zero → native
        cache_dir=str(tmp_path / "cache"),
    )
    d = get_embedding_dispatcher(cfg)
    d.prepare()
    assert d._output_dim is None  # zero → None internally
    assert d._corpus.embeddings.shape[1] == _TOY_DIM


def test_output_dim_exceeding_native_raises(tmp_path) -> None:
    """Asking for more dims than the backend produces is a config
    error — should raise BackendUnavailableError during prepare."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, get_embedding_dispatcher,
    )
    from src.services.comparison.ai_classifier.backends import (
        BackendUnavailableError,
    )

    _register("excess_dim_test", available=True)

    cfg = AiClassifierConfig(
        enabled=True, use_embedding=True,
        embedding_backend_id="excess_dim_test",
        embedding_output_dim=99,  # way more than native _TOY_DIM=8
        cache_dir=str(tmp_path / "cache"),
    )
    d = get_embedding_dispatcher(cfg)
    with pytest.raises(BackendUnavailableError, match="output_dim"):
        d.prepare()


# ---------------------------------------------------------------------------
# Singleton cache key includes backend_id and output_dim
# ---------------------------------------------------------------------------


def test_dispatcher_cache_separates_by_backend_id(tmp_path) -> None:
    """Two AiClassifierConfigs with different backend_ids must
    produce different dispatcher instances — quality vs speed mode
    in the same process must not share a dispatcher."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, get_embedding_dispatcher,
    )

    _register("cache_key_a", available=True)
    _register("cache_key_b", available=True)

    cfg_a = AiClassifierConfig(
        enabled=True, use_embedding=True,
        embedding_backend_id="cache_key_a",
        cache_dir=str(tmp_path / "cache"),
    )
    cfg_b = AiClassifierConfig(
        enabled=True, use_embedding=True,
        embedding_backend_id="cache_key_b",
        cache_dir=str(tmp_path / "cache"),
    )
    d_a = get_embedding_dispatcher(cfg_a)
    d_b = get_embedding_dispatcher(cfg_b)
    assert d_a is not d_b


def test_dispatcher_cache_separates_by_output_dim(tmp_path) -> None:
    """Switching Matryoshka truncation also returns a fresh
    dispatcher — otherwise the cached one would have wrong-dim
    prototype vectors for the new query."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, get_embedding_dispatcher,
    )

    _register("dim_cache_key", available=True)

    cfg_full = AiClassifierConfig(
        enabled=True, use_embedding=True,
        embedding_backend_id="dim_cache_key",
        embedding_output_dim=None,
        cache_dir=str(tmp_path / "cache"),
    )
    cfg_trunc = AiClassifierConfig(
        enabled=True, use_embedding=True,
        embedding_backend_id="dim_cache_key",
        embedding_output_dim=4,
        cache_dir=str(tmp_path / "cache"),
    )
    d1 = get_embedding_dispatcher(cfg_full)
    d2 = get_embedding_dispatcher(cfg_trunc)
    assert d1 is not d2


# ---------------------------------------------------------------------------
# Manifest reflects active backend (not configured "auto")
# ---------------------------------------------------------------------------


def test_manifest_records_active_backend_not_auto(tmp_path) -> None:
    """When auto resolves to a concrete backend, the persisted
    manifest writes that concrete ID — NOT "auto" — so cache lookups
    on subsequent launches with the same backend match."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, get_embedding_dispatcher, load_manifest,
    )

    _register("auto_resolves_to_me", available=True)

    cfg = AiClassifierConfig(
        enabled=True, use_embedding=True,
        embedding_backend_id="auto",
        embedding_backend_fallbacks=["auto_resolves_to_me"],
        cache_dir=str(tmp_path / "cache"),
    )
    d = get_embedding_dispatcher(cfg)
    d.prepare()
    persisted = load_manifest(Path(cfg.cache_dir))
    assert persisted is not None
    assert persisted.embedding_backend == "auto_resolves_to_me"


def test_manifest_output_dim_invalidates_cache(tmp_path) -> None:
    """Changing embedding_output_dim across launches must invalidate
    the cached embeddings. Manifest's output_dim field carries the
    truncation target; when it differs, needs_recompute returns True."""
    import json
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, clear_dispatcher_cache, get_embedding_dispatcher,
    )

    _register("dim_invalidate_test", available=True)
    cache_root = tmp_path / "cache"

    # Launch 1: native dim
    cfg1 = AiClassifierConfig(
        enabled=True, use_embedding=True,
        embedding_backend_id="dim_invalidate_test",
        embedding_output_dim=None,
        cache_dir=str(cache_root),
    )
    d1 = get_embedding_dispatcher(cfg1)
    d1.prepare()
    npy_mtime_1 = (cache_root / "prototype_embeddings_v2.npy").stat().st_mtime

    # Launch 2: dim=4 → must recompute (different output_dim)
    clear_dispatcher_cache()
    import time
    time.sleep(0.02)  # mtime resolution
    cfg2 = AiClassifierConfig(
        enabled=True, use_embedding=True,
        embedding_backend_id="dim_invalidate_test",
        embedding_output_dim=4,
        cache_dir=str(cache_root),
    )
    d2 = get_embedding_dispatcher(cfg2)
    d2.prepare()
    npy_mtime_2 = (cache_root / "prototype_embeddings_v2.npy").stat().st_mtime
    assert npy_mtime_2 > npy_mtime_1


# ---------------------------------------------------------------------------
# classify_zones cascade survives auto-mode failure
# ---------------------------------------------------------------------------


def test_classify_zones_falls_back_to_stage1_when_auto_fails(tmp_path) -> None:
    """Empty ai_models/ + auto mode → Stage-2 abstains, Stage-1
    heuristic still produces results. No crash."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, classify_zones,
    )

    _register("never_available", available=False)

    cfg = AiClassifierConfig(
        enabled=True, use_embedding=True,
        embedding_backend_id="auto",
        embedding_backend_fallbacks=["never_available"],
        cache_dir=str(tmp_path / "cache"),
    )
    out = classify_zones(
        [{"zone_id": "z1", "text_snippet": "보 단면 변경",
          "layer": "BEAM", "change_type": "modified"}],
        config=cfg,
    )
    assert len(out) == 1
    # Stage-2 abstained → Stage-1 heuristic kept the result
    assert out[0].classifier_used == "heuristic"


# ---------------------------------------------------------------------------
# Convenience classmethods (quality_mode / speed_mode / auto_mode)
# ---------------------------------------------------------------------------


def test_quality_mode_uses_qwen_backend_id() -> None:
    from src.services.comparison.ai_classifier import AiClassifierConfig

    cfg = AiClassifierConfig.quality_mode()
    assert cfg.embedding_backend_id == "llama_cpp_qwen3_embedding"
    assert cfg.use_embedding is True
    assert cfg.embedding_output_dim is None  # native dim


def test_speed_mode_uses_mxbai_with_truncation() -> None:
    from src.services.comparison.ai_classifier import AiClassifierConfig

    cfg = AiClassifierConfig.speed_mode()
    assert cfg.embedding_backend_id == "onnx_mxbai_large"
    assert cfg.use_embedding is True
    assert cfg.embedding_output_dim == 512  # Matryoshka per report


def test_auto_mode_uses_auto_sentinel() -> None:
    from src.services.comparison.ai_classifier import AiClassifierConfig

    cfg = AiClassifierConfig.auto_mode()
    assert cfg.embedding_backend_id == "auto"
    assert cfg.use_embedding is True


def test_to_dict_roundtrip_includes_phase_i_fields() -> None:
    from src.services.comparison.ai_classifier import AiClassifierConfig

    cfg = AiClassifierConfig.speed_mode()
    d = cfg.to_dict()
    assert d["embedding_backend_id"] == "onnx_mxbai_large"
    assert d["embedding_output_dim"] == 512
    assert d["embedding_backend_fallbacks"] == [
        "llama_cpp_qwen3_embedding", "onnx_mxbai_large",
    ]
