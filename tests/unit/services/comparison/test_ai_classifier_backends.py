# -*- coding: utf-8 -*-
"""Tests for the Phase H Stage-2 backend protocol + registry + manifest.

No real model loaded — uses a stub backend. Pins:
  * Protocol surface (encode / warmup / is_ready / required attrs)
  * AbstractEmbeddingBackend default behaviour (warmup_ms tracking,
    last_error capture, lazy first encode)
  * BACKEND_REGISTRY register / get / available
  * Manifest persistence + compatibility comparison
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Stub backend used throughout
# ---------------------------------------------------------------------------


class _StubBackend:
    """Minimal Protocol-conformant backend — no model, just zeros.

    Phase I: gained ``native_dim`` (must equal embedding_dim when no
    Matryoshka), ``probe_available`` classmethod, and a ``truncate_dim``
    kwarg on ``encode``.
    """

    backend_id = "stub"
    model_sha256 = "0" * 64
    native_dim = 8
    embedding_dim = 8

    def __init__(self) -> None:
        self._ready = False

    def encode(self, texts, *, normalize=True, truncate_dim=None):
        if not self._ready:
            self.warmup()
        dim = int(truncate_dim) if truncate_dim is not None else self.native_dim
        return np.zeros((len(texts), dim), dtype=np.float32)

    def warmup(self) -> None:
        self._ready = True

    def is_ready(self) -> bool:
        return self._ready

    @classmethod
    def probe_available(cls) -> bool:
        return True  # stub is always "available"


def _stub_factory(**kwargs):
    return _StubBackend()


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_stub_satisfies_protocol() -> None:
    from src.services.comparison.ai_classifier import EmbeddingBackend
    stub = _StubBackend()
    assert isinstance(stub, EmbeddingBackend)


def test_protocol_requires_required_attrs() -> None:
    """A class missing any of the required attrs/methods does NOT
    pass the runtime_checkable Protocol check."""

    from src.services.comparison.ai_classifier import EmbeddingBackend

    class Incomplete:
        backend_id = "bad"
        # missing model_sha256, embedding_dim, encode, warmup, is_ready

    assert not isinstance(Incomplete(), EmbeddingBackend)


# ---------------------------------------------------------------------------
# Registry — register / get / available / unavailable
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_registry():
    """Reset the registry around each test so registrations don't leak."""

    from src.services.comparison.ai_classifier.backends import BACKEND_REGISTRY
    snapshot = dict(BACKEND_REGISTRY)
    BACKEND_REGISTRY.clear()
    yield BACKEND_REGISTRY
    BACKEND_REGISTRY.clear()
    BACKEND_REGISTRY.update(snapshot)


def test_register_and_get(clean_registry) -> None:
    from src.services.comparison.ai_classifier import (
        register_backend, get_backend, available_backends,
    )
    register_backend("stub", _stub_factory)
    assert "stub" in available_backends()
    backend = get_backend("stub")
    assert backend.backend_id == "stub"
    assert backend.embedding_dim == 8


def test_get_unregistered_raises(clean_registry) -> None:
    from src.services.comparison.ai_classifier import (
        get_backend, BackendUnavailableError,
    )
    with pytest.raises(BackendUnavailableError) as exc:
        get_backend("does-not-exist")
    assert "does-not-exist" in str(exc.value)


def test_available_returns_sorted_list(clean_registry) -> None:
    from src.services.comparison.ai_classifier import (
        register_backend, available_backends,
    )
    register_backend("zebra", _stub_factory)
    register_backend("alpha", _stub_factory)
    register_backend("middle", _stub_factory)
    assert available_backends() == ["alpha", "middle", "zebra"]


# ---------------------------------------------------------------------------
# AbstractEmbeddingBackend — convenience base
# ---------------------------------------------------------------------------


def test_abstract_backend_warmup_timing() -> None:
    from src.services.comparison.ai_classifier.backends.base import (
        AbstractEmbeddingBackend,
    )

    class _Backend(AbstractEmbeddingBackend):
        backend_id = "fake"
        embedding_dim = 4
        model_sha256 = "abc"

        def _load(self):
            pass

        def _encode_impl(self, texts, *, normalize):
            return np.ones((len(texts), 4), dtype=np.float32)

    b = _Backend()
    assert not b.is_ready()
    assert b.warmup_ms() is None
    b.warmup()
    assert b.is_ready()
    assert b.warmup_ms() is not None
    assert b.warmup_ms() >= 0.0


def test_abstract_backend_lazy_first_encode() -> None:
    """encode() before warmup() should still work — it warms up
    transparently on first call."""

    from src.services.comparison.ai_classifier.backends.base import (
        AbstractEmbeddingBackend,
    )

    class _Backend(AbstractEmbeddingBackend):
        backend_id = "fake"
        embedding_dim = 3
        model_sha256 = "xyz"

        def _load(self):
            pass

        def _encode_impl(self, texts, *, normalize):
            return np.full((len(texts), 3), 0.5, dtype=np.float32)

    b = _Backend()
    out = b.encode(["foo", "bar"])
    assert out.shape == (2, 3)
    assert b.is_ready()  # auto-warmed-up


def test_abstract_backend_empty_input_no_crash() -> None:
    from src.services.comparison.ai_classifier.backends.base import (
        AbstractEmbeddingBackend,
    )

    class _Backend(AbstractEmbeddingBackend):
        backend_id = "fake"
        embedding_dim = 5
        model_sha256 = "h"

        def _load(self):
            pass

        def _encode_impl(self, texts, *, normalize):
            return np.zeros((len(texts), 5), dtype=np.float32)

    b = _Backend()
    out = b.encode([])
    assert out.shape == (0, 5)


def test_abstract_backend_load_error_captured() -> None:
    from src.services.comparison.ai_classifier.backends.base import (
        AbstractEmbeddingBackend,
    )

    class _BadBackend(AbstractEmbeddingBackend):
        backend_id = "fake"
        embedding_dim = 4
        model_sha256 = "abc"

        def _load(self):
            raise RuntimeError("model missing")

        def _encode_impl(self, texts, *, normalize):
            return np.zeros((len(texts), 4), dtype=np.float32)

    b = _BadBackend()
    with pytest.raises(RuntimeError):
        b.warmup()
    assert b.last_error() is not None
    assert "model missing" in str(b.last_error())
    assert not b.is_ready()


# ---------------------------------------------------------------------------
# Manifest — schema + load / save / needs_recompute
# ---------------------------------------------------------------------------


def test_manifest_roundtrip(tmp_path: Path) -> None:
    from src.services.comparison.ai_classifier import (
        EmbeddingManifest, save_manifest, load_manifest,
    )
    m = EmbeddingManifest(
        embedding_backend="llama_cpp_qwen3",
        model_file="Qwen3-Embedding-0.6B-Q8_0.gguf",
        model_sha256="abc123",
        embedding_dim=1024,
        prototype_corpus_version="v2.0",
        normalizer_version="v1",
    )
    save_manifest(tmp_path, m)
    loaded = load_manifest(tmp_path)
    assert loaded is not None
    assert loaded.embedding_backend == "llama_cpp_qwen3"
    assert loaded.embedding_dim == 1024
    assert loaded.model_sha256 == "abc123"


def test_manifest_compatibility_match() -> None:
    from src.services.comparison.ai_classifier import EmbeddingManifest
    a = EmbeddingManifest(
        embedding_backend="x", model_file="m", model_sha256="h",
        embedding_dim=10, prototype_corpus_version="v1",
        normalizer_version="v1",
    )
    b = EmbeddingManifest(
        embedding_backend="x", model_file="m", model_sha256="h",
        embedding_dim=10, prototype_corpus_version="v1",
        normalizer_version="v1",
        computed_at_utc="2026-05-06T12:00:00",  # different timestamp
    )
    assert a.is_compatible_with(b)


def test_manifest_compatibility_mismatch_on_sha() -> None:
    from src.services.comparison.ai_classifier import EmbeddingManifest
    a = EmbeddingManifest(model_sha256="abc")
    b = EmbeddingManifest(model_sha256="xyz")
    assert not a.is_compatible_with(b)


def test_manifest_compatibility_mismatch_on_normalizer() -> None:
    from src.services.comparison.ai_classifier import EmbeddingManifest
    a = EmbeddingManifest(normalizer_version="v1")
    b = EmbeddingManifest(normalizer_version="v2")
    assert not a.is_compatible_with(b)


def test_load_returns_none_when_missing(tmp_path: Path) -> None:
    from src.services.comparison.ai_classifier import load_manifest
    assert load_manifest(tmp_path) is None


def test_load_returns_none_for_corrupt_json(tmp_path: Path) -> None:
    from src.services.comparison.ai_classifier import (
        load_manifest, manifest_path,
    )
    p = manifest_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ this is not valid json", encoding="utf-8")
    assert load_manifest(tmp_path) is None


def test_needs_recompute_first_run(tmp_path: Path) -> None:
    """No persisted manifest → recompute always."""

    from src.services.comparison.ai_classifier import (
        EmbeddingManifest, needs_recompute,
    )
    current = EmbeddingManifest(
        embedding_backend="x", model_sha256="h", embedding_dim=10,
    )
    assert needs_recompute(tmp_path, current)


def test_needs_recompute_match_skips(tmp_path: Path) -> None:
    from src.services.comparison.ai_classifier import (
        EmbeddingManifest, save_manifest, needs_recompute,
    )
    m = EmbeddingManifest(
        embedding_backend="x", model_sha256="h", embedding_dim=10,
    )
    save_manifest(tmp_path, m)
    # Same identity → no recompute
    assert not needs_recompute(tmp_path, m)


def test_manifest_extra_fields_ignored() -> None:
    """Forward-compat: a future manifest with extra fields shouldn't
    crash this version of the loader."""

    from src.services.comparison.ai_classifier import EmbeddingManifest
    m = EmbeddingManifest.from_dict({
        "embedding_backend": "x",
        "model_sha256": "h",
        "future_field": "ignored",
    })
    assert m.embedding_backend == "x"
    assert m.model_sha256 == "h"


# ---------------------------------------------------------------------------
# 2nd-review fix (P1-4) — concurrent warmup lock + empty-batch fast path
# + duplicate registration guard
# ---------------------------------------------------------------------------


def test_concurrent_warmup_loads_only_once(qapp_disabled=None) -> None:
    """8 threads hitting warmup() simultaneously must trigger _load
    exactly once (not 8 times)."""

    import threading
    from concurrent.futures import ThreadPoolExecutor
    from src.services.comparison.ai_classifier.backends.base import (
        AbstractEmbeddingBackend,
    )

    load_count = {"n": 0}
    barrier = threading.Barrier(8)

    class _SlowBackend(AbstractEmbeddingBackend):
        backend_id = "slow"
        embedding_dim = 4
        model_sha256 = "h"

        def _load(self):
            load_count["n"] += 1
            # Hold the lock long enough for all threads to pile up
            import time
            time.sleep(0.05)

        def _encode_impl(self, texts, *, normalize):
            return np.zeros((len(texts), 4), dtype=np.float32)

    b = _SlowBackend()

    def worker():
        barrier.wait()
        b.warmup()

    with ThreadPoolExecutor(max_workers=8) as ex:
        for _ in range(8):
            ex.submit(worker)

    assert load_count["n"] == 1, (
        f"_load ran {load_count['n']} times — warmup lock missing"
    )
    assert b.is_ready()


def test_empty_batch_does_not_warmup() -> None:
    """encode([]) must NOT trigger the heavy _load (would waste 600 MB
    on empty input)."""

    from src.services.comparison.ai_classifier.backends.base import (
        AbstractEmbeddingBackend,
    )

    load_count = {"n": 0}

    class _Backend(AbstractEmbeddingBackend):
        backend_id = "empty-test"
        embedding_dim = 4
        model_sha256 = "h"

        def _load(self):
            load_count["n"] += 1

        def _encode_impl(self, texts, *, normalize):
            return np.zeros((len(texts), 4), dtype=np.float32)

    b = _Backend()
    out = b.encode([])  # empty batch
    assert out.shape == (0, 4)
    assert load_count["n"] == 0  # NOT loaded
    assert not b.is_ready()


def test_register_backend_rejects_duplicate(clean_registry) -> None:
    """Re-registering the same backend_id without replace=True must
    raise to avoid silent override."""

    from src.services.comparison.ai_classifier import register_backend

    def factory_a(**kwargs):
        return _StubBackend()

    def factory_b(**kwargs):
        return _StubBackend()

    register_backend("dup-test", factory_a)
    with pytest.raises(ValueError, match="already registered"):
        register_backend("dup-test", factory_b)


def test_register_backend_allows_explicit_replace(clean_registry) -> None:
    from src.services.comparison.ai_classifier import (
        register_backend, get_backend,
    )

    class _A:
        backend_id = "rep-test"
        model_sha256 = "a"
        embedding_dim = 4

        def encode(self, texts, *, normalize=True):
            return np.zeros((len(texts), 4), dtype=np.float32)

        def warmup(self):
            pass

        def is_ready(self):
            return True

    class _B(_A):
        model_sha256 = "b"

    register_backend("rep-test", lambda **kw: _A())
    register_backend("rep-test", lambda **kw: _B(), replace=True)
    backend = get_backend("rep-test")
    assert backend.model_sha256 == "b"


# ---------------------------------------------------------------------------
# 2nd-review fix (P2) — manifest atomic save + corpus_sha256
# ---------------------------------------------------------------------------


def test_manifest_save_is_atomic_no_partial_file(tmp_path: Path) -> None:
    """save_manifest writes to tmp + rename — no partial JSON survives
    even if the process is killed mid-write."""

    from src.services.comparison.ai_classifier import (
        EmbeddingManifest, save_manifest, manifest_path,
    )
    m = EmbeddingManifest(
        embedding_backend="x", model_sha256="h", embedding_dim=10,
    )
    save_manifest(tmp_path, m)
    p = manifest_path(tmp_path)
    assert p.exists()
    # Read back — must be valid JSON, not partial
    import json
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["embedding_backend"] == "x"
    # No leftover tmp files
    tmps = list(tmp_path.glob(f".{p.name}.*.tmp"))
    assert tmps == [], f"leftover temp files: {tmps}"


def test_manifest_compatibility_corpus_sha_match() -> None:
    from src.services.comparison.ai_classifier import EmbeddingManifest
    a = EmbeddingManifest(prototype_corpus_sha256="abc")
    b = EmbeddingManifest(prototype_corpus_sha256="abc")
    assert a.is_compatible_with(b)


def test_manifest_compatibility_corpus_sha_mismatch_invalidates() -> None:
    from src.services.comparison.ai_classifier import EmbeddingManifest
    a = EmbeddingManifest(prototype_corpus_sha256="abc")
    b = EmbeddingManifest(prototype_corpus_sha256="xyz")
    assert not a.is_compatible_with(b)


def test_manifest_compatibility_legacy_no_corpus_sha_passes() -> None:
    """When either side has no corpus_sha256 (legacy or fresh manifest),
    the field is ignored from the comparison so we don't force an
    unnecessary recompute."""

    from src.services.comparison.ai_classifier import EmbeddingManifest
    a = EmbeddingManifest(prototype_corpus_sha256="")  # legacy
    b = EmbeddingManifest(prototype_corpus_sha256="abc")
    assert a.is_compatible_with(b)


# ---------------------------------------------------------------------------
# 3rd-review fix (P1) — manifest fingerprint extension
# ---------------------------------------------------------------------------


def test_manifest_instruction_id_mismatch_invalidates() -> None:
    """Different instruction templates produce different vectors →
    must trigger recompute."""

    from src.services.comparison.ai_classifier import EmbeddingManifest
    a = EmbeddingManifest(instruction_id="korean_aec_v1")
    b = EmbeddingManifest(instruction_id="english_default")
    assert not a.is_compatible_with(b)


def test_manifest_pooling_mismatch_invalidates() -> None:
    """mean vs cls vs last_token pooling produces different vectors."""

    from src.services.comparison.ai_classifier import EmbeddingManifest
    a = EmbeddingManifest(pooling="mean")
    b = EmbeddingManifest(pooling="cls")
    assert not a.is_compatible_with(b)


def test_manifest_quantization_mismatch_invalidates() -> None:
    """Q4_K_M and Q8_0 produce different vectors → recompute prototypes."""

    from src.services.comparison.ai_classifier import EmbeddingManifest
    a = EmbeddingManifest(quantization="Q4_K_M")
    b = EmbeddingManifest(quantization="Q8_0")
    assert not a.is_compatible_with(b)


def test_manifest_output_dim_mismatch_invalidates() -> None:
    """Truncating Qwen3-Embedding to 256 dim vs full 1024 dim →
    completely different vectors."""

    from src.services.comparison.ai_classifier import EmbeddingManifest
    a = EmbeddingManifest(output_dim=256)
    b = EmbeddingManifest(output_dim=1024)
    assert not a.is_compatible_with(b)


def test_manifest_legacy_empty_fields_pass_compatibility() -> None:
    """Old manifest without the new fields must NOT force recompute
    just because the new fields are empty (only matters when BOTH
    sides have non-empty values)."""

    from src.services.comparison.ai_classifier import EmbeddingManifest
    legacy = EmbeddingManifest(model_sha256="abc")
    new = EmbeddingManifest(
        model_sha256="abc",
        instruction_id="korean_aec_v1",
        pooling="mean",
        quantization="Q8_0",
        output_dim=1024,
    )
    assert legacy.is_compatible_with(new)


def test_manifest_output_dim_zero_means_default() -> None:
    """output_dim=0 means 'use full embedding_dim' — should match
    any explicit value."""

    from src.services.comparison.ai_classifier import EmbeddingManifest
    a = EmbeddingManifest(output_dim=0)
    b = EmbeddingManifest(output_dim=1024)
    assert a.is_compatible_with(b)


# ---------------------------------------------------------------------------
# Phase I — onnx_mxbai_large backend (smoke / probe / registration)
# ---------------------------------------------------------------------------


def test_onnx_mxbai_backend_constants() -> None:
    """Confirm the contract constants are stable — the dispatcher's
    auto-mode resolution and manifest fingerprint key off these."""

    from src.services.comparison.ai_classifier.backends.onnx_mxbai_large import (
        BACKEND_ID, MXBAI_NATIVE_DIM, DEFAULT_MODEL_DIRNAME,
        OnnxMxbaiLargeBackend,
    )
    assert BACKEND_ID == "onnx_mxbai_large"
    assert MXBAI_NATIVE_DIM == 1024
    assert DEFAULT_MODEL_DIRNAME == "onnx_mxbai_large"
    assert OnnxMxbaiLargeBackend.backend_id == BACKEND_ID
    assert OnnxMxbaiLargeBackend.native_dim == MXBAI_NATIVE_DIM
    assert OnnxMxbaiLargeBackend.embedding_dim == MXBAI_NATIVE_DIM


def test_onnx_mxbai_self_registers() -> None:
    """Importing the backend module must self-register the factory
    so dispatcher's get_backend('onnx_mxbai_large') succeeds."""

    from src.services.comparison.ai_classifier.backends import (
        BACKEND_REGISTRY, available_backends,
    )
    # Trigger the auto-import in backends/__init__.py
    from src.services.comparison.ai_classifier import backends  # noqa: F401
    assert "onnx_mxbai_large" in available_backends()
    assert "onnx_mxbai_large" in BACKEND_REGISTRY


def test_onnx_mxbai_probe_available_false_when_no_model_dir(tmp_path,
                                                              monkeypatch) -> None:
    """probe_available must return False when the AppData / dev /
    project model directories are all missing — even if dependency
    packages are installed. Used by auto-mode to skip this backend
    silently."""

    from src.services.comparison.ai_classifier.backends.onnx_mxbai_large import (
        OnnxMxbaiLargeBackend,
    )
    # Point LOCALAPPDATA at an empty dir — no model directory there
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "no_appdata"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "no_appdata"))
    monkeypatch.chdir(tmp_path)  # so cwd has no models/ dir
    assert OnnxMxbaiLargeBackend.probe_available() is False


def test_onnx_mxbai_probe_available_false_when_packages_missing(
    tmp_path, monkeypatch,
) -> None:
    """Even with a model dir present, missing sentence_transformers /
    onnxruntime packages must make probe return False."""

    from src.services.comparison.ai_classifier.backends.onnx_mxbai_large import (
        OnnxMxbaiLargeBackend, REQUIRED_MARKER_FILES,
    )
    # Create a fake model dir that satisfies the marker-file check
    model_dir = tmp_path / "ai_models" / "onnx_mxbai_large"
    model_dir.mkdir(parents=True)
    for marker in REQUIRED_MARKER_FILES:
        (model_dir / marker).write_text("{}")
    (model_dir / "onnx").mkdir()
    (model_dir / "onnx" / "model_quint8_avx2.onnx").write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    # Stub importlib.util.find_spec so it pretends sentence_transformers
    # is missing
    import importlib.util as _iu
    real_find = _iu.find_spec

    def _stub_find(name, *a, **kw):
        if name in {"sentence_transformers", "onnxruntime"}:
            return None
        return real_find(name, *a, **kw)

    monkeypatch.setattr(_iu, "find_spec", _stub_find)
    assert OnnxMxbaiLargeBackend.probe_available() is False


def test_onnx_mxbai_load_raises_unavailable_when_packages_missing(
    monkeypatch,
) -> None:
    """_load() must raise BackendUnavailableError (not ImportError)
    when sentence_transformers can't be imported. The dispatcher
    catches BackendUnavailableError and falls back to Stage-1."""

    from src.services.comparison.ai_classifier.backends.base import (
        BackendUnavailableError,
    )
    from src.services.comparison.ai_classifier.backends.onnx_mxbai_large import (
        OnnxMxbaiLargeBackend,
    )
    # Force the import inside _load to fail
    import builtins
    real_import = builtins.__import__

    def _stub_import(name, *args, **kwargs):
        if name == "sentence_transformers":
            raise ImportError("forced for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _stub_import)
    backend = OnnxMxbaiLargeBackend()
    with pytest.raises(BackendUnavailableError, match="sentence-transformers"):
        backend._load()


def test_onnx_mxbai_load_raises_unavailable_when_model_dir_missing(
    tmp_path, monkeypatch,
) -> None:
    """_load() must raise BackendUnavailableError with a Korean hint
    when the model directory isn't found.

    We want to verify the model-dir-missing branch specifically, NOT
    the dep-missing branch (covered by the other test). The actual
    import is more reliable than find_spec for the skip — partial
    installs report "spec available" but raise on real import.
    """

    from src.services.comparison.ai_classifier.backends.base import (
        BackendUnavailableError,
    )
    from src.services.comparison.ai_classifier.backends.onnx_mxbai_large import (
        OnnxMxbaiLargeBackend,
    )
    # Real-import skip — find_spec can be a false positive on partial installs
    try:
        import sentence_transformers  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError:
        pytest.skip("sentence_transformers / onnxruntime not importable; "
                    "this test exercises the model-dir-missing branch only")

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "no_appdata"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "no_appdata"))
    monkeypatch.chdir(tmp_path)
    backend = OnnxMxbaiLargeBackend()
    with pytest.raises(BackendUnavailableError, match="ONNX 모델 디렉토리"):
        backend._load()


def test_resolve_model_dir_explicit_arg(tmp_path) -> None:
    """Explicit model_dir wins over AppData lookup, but still requires
    the marker files to be present."""

    from src.services.comparison.ai_classifier.backends.onnx_mxbai_large import (
        _resolve_model_dir, REQUIRED_MARKER_FILES,
    )
    # Empty dir — explicit path that lacks marker files → None + warning
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert _resolve_model_dir(explicit=empty_dir) is None
    # Same dir with markers → returned
    for marker in REQUIRED_MARKER_FILES:
        (empty_dir / marker).write_text("{}")
    assert _resolve_model_dir(explicit=empty_dir) == empty_dir


def test_resolve_onnx_file_priority_order(tmp_path) -> None:
    """The resolver picks files in PREFERRED_ONNX_FILES priority order.
    Quantised AVX2 wins over fp32 fallback."""

    from src.services.comparison.ai_classifier.backends.onnx_mxbai_large import (
        _resolve_onnx_file,
    )
    onnx_dir = tmp_path / "onnx"
    onnx_dir.mkdir()
    # Only fp32 → returned
    (onnx_dir.parent / "onnx" / "model.onnx").write_bytes(b"x")
    assert _resolve_onnx_file(tmp_path).name == "model.onnx"
    # Add the preferred quint8_avx2 → that wins
    (onnx_dir.parent / "onnx" / "model_quint8_avx2.onnx").write_bytes(b"x")
    assert _resolve_onnx_file(tmp_path).name == "model_quint8_avx2.onnx"
