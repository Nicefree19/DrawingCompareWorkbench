# -*- coding: utf-8 -*-
"""Phase H Stage-2 — embedding classifier dispatcher + cascade tests.

No real GGUF loaded. A toy backend produces deterministic vectors
based on which category-marker word appears in the input. With that
stand-in we can pin:
  * Dispatcher lifecycle (prepare → classify_zone → cache reuse)
  * Cosine top-1 + margin gating
  * Stage-1 + Stage-2 cascade (replacement vs abstain)
  * Backend-unavailable → graceful Stage-1 fallback
  * Per-config singleton cache
  * Manifest written with the right fingerprint fields
  * AppData cache layout (manifest + corpus + .npy)

These tests intentionally bypass the production registry — each
test injects its toy backend via ``register_backend(replace=True)``
into a one-off backend ID so test collisions / leftover state can't
cross-pollute. ``clear_dispatcher_cache()`` also runs in a fixture
to reset the per-config singleton.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Toy backend — deterministic, marker-word based
# ---------------------------------------------------------------------------


# Map: substring → embedding-slot. Each ChangeCategory gets exactly
# one orthogonal slot. The toy backend sums slot indicators per text
# and L2-normalises so cosine becomes "fraction of category markers
# matched". The markers were chosen to survive normalizer rewrites
# (e.g. "단면" stays as "단면", H-beam codes get rewritten to H_BEAM_).
_CATEGORY_MARKERS: dict[str, int] = {
    # STRUCTURAL_MEMBER → slot 0
    "단면": 0,
    "H_BEAM": 0,
    "SQR_TUBE": 0,
    "철골보": 0,
    "슬래브": 0,
    "벽체": 0,
    "데크": 0,
    "REBAR": 0,
    # DIMENSION → slot 1
    "DIM_": 1,
    "치수": 1,
    "스팬": 1,
    "층고": 1,
    "PLATE_": 1,  # plate thickness reads as a dim signal in the toy
    # TEXT_LABEL → slot 2
    "주기": 2,
    "표기": 2,
    "S20-": 2,
    "명칭": 2,
    "폰트": 2,
    # GRID → slot 3
    "GRID_": 3,
    "그리드": 3,
    "축선": 3,
    # LAYOUT → slot 4
    "위치": 4,
    "이동": 4,
    "재배치": 4,
    "동선": 4,
    # DETAIL_DRAWING → slot 5
    "DETAIL_": 5,
    "디테일": 5,
    "접합부": 5,
    "거푸집": 5,
    "베이스": 5,
    # NOTE → slot 6
    "REMARK": 6,
    "주석": 6,
    "노트": 6,
    "심의": 6,
    "유의사항": 6,
    "검토": 6,
    # UNKNOWN → slot 7
    "기타": 7,
    "미정": 7,
    "비분류": 7,
}
_TOY_DIM = 8


def _toy_backend_factory(backend_id: str = "toy_embedding"):
    """Build a fresh toy backend class bound to ``backend_id`` so each
    test gets its own registry entry."""

    from src.services.comparison.ai_classifier.backends.base import (
        AbstractEmbeddingBackend,
    )

    class _ToyEmbeddingBackend(AbstractEmbeddingBackend):
        """Test backend: outputs one of 8 orthogonal vectors based on
        which category-marker word appears in the input."""

        # Class attrs — clobbered per-instance below to dodge the
        # "all instances share the same backend_id" gotcha.
        # Phase I: native_dim + embedding_dim both = _TOY_DIM. Set
        # native_dim explicitly so AbstractEmbeddingBackend's
        # truncate_dim handling is exercised.
        native_dim = _TOY_DIM
        embedding_dim = _TOY_DIM
        model_sha256 = "f" * 64

        @classmethod
        def probe_available(cls) -> bool:
            return True  # toy is always available

        def __init__(self) -> None:
            super().__init__()
            self.backend_id = backend_id
            # Mark a fake resolved model path so the dispatcher's
            # manifest builder has something non-empty to record.
            self._resolved_model_path = Path(
                f"/tmp/toy_{backend_id}-Q8_0.gguf"
            )

        def _load(self) -> None:  # no real model
            return

        def _encode_impl(self, texts, *, normalize):
            rows = []
            for t in texts:
                vec = np.zeros(_TOY_DIM, dtype=np.float32)
                for marker, slot in _CATEGORY_MARKERS.items():
                    if marker in t:
                        vec[slot] += 1.0
                if not vec.any():
                    # Very small magnitude on slot 0 so cosine stays
                    # below threshold — no spurious matches.
                    vec[0] = 1e-6
                if normalize:
                    norm = np.linalg.norm(vec)
                    if norm > 0:
                        vec = vec / norm
                rows.append(vec)
            return np.array(rows, dtype=np.float32)

    return _ToyEmbeddingBackend


def _register_toy(backend_id: str) -> None:
    from src.services.comparison.ai_classifier.backends import register_backend

    cls = _toy_backend_factory(backend_id)
    register_backend(backend_id, lambda **kw: cls(), replace=True)


def test_prepare_async_backend_unavailable_logs_fallback_warning(tmp_path, caplog) -> None:
    """Missing optional embedding models are a supported fallback state,
    so background warmup should not emit an ERROR traceback."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig,
        get_embedding_dispatcher,
    )

    cfg = AiClassifierConfig(
        enabled=True,
        use_embedding=True,
        embedding_backend_id="auto",
        embedding_backend_fallbacks=["not_registered_backend"],
        cache_dir=str(tmp_path / "cache"),
    )
    dispatcher = get_embedding_dispatcher(cfg)

    caplog.set_level(
        logging.INFO,
        logger="src.services.comparison.ai_classifier.embedding_classifier",
    )
    worker = dispatcher.prepare_async()
    worker.join(timeout=5.0)

    assert not worker.is_alive()
    assert dispatcher.last_error() is not None
    assert any(
        "falling back to heuristic-only classification" in record.getMessage()
        and record.levelno == logging.WARNING
        for record in caplog.records
    )
    assert not any(
        "Background prepare() failed" in record.getMessage()
        and record.levelno >= logging.ERROR
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_dispatcher_cache():
    """Fresh dispatcher singleton cache per test so config tweaks
    aren't shared across tests."""
    from src.services.comparison.ai_classifier import clear_dispatcher_cache

    clear_dispatcher_cache()
    yield
    clear_dispatcher_cache()


@pytest.fixture
def toy_config(tmp_path):
    """AiClassifierConfig wired to a toy backend + temp cache dir.

    Phase I: embedding_backend_id (NOT embedding_model) is the primary
    selection axis. Setting it explicitly bypasses "auto" mode which
    would try the unregistered Qwen / mxbai fallbacks first.
    """
    backend_id = "toy_embedding_main"
    _register_toy(backend_id)
    from src.services.comparison.ai_classifier import AiClassifierConfig

    return AiClassifierConfig(
        enabled=True,
        use_embedding=True,
        use_llm=False,
        embedding_backend_id=backend_id,
        embedding_model=backend_id,  # legacy compat
        embedding_threshold=0.5,
        cache_dir=str(tmp_path / "ai_cache"),
    )


# ---------------------------------------------------------------------------
# Dispatcher lifecycle
# ---------------------------------------------------------------------------


def test_dispatcher_prepare_loads_corpus_and_persists_artifacts(toy_config) -> None:
    """First prepare() should: warm up backend, compute embeddings,
    write manifest + corpus + .npy to cache_dir."""
    from src.services.comparison.ai_classifier import (
        EmbeddingClassifierDispatcher,
        get_embedding_dispatcher,
    )

    d = get_embedding_dispatcher(toy_config)
    assert isinstance(d, EmbeddingClassifierDispatcher)
    assert not d.is_ready()
    d.prepare()
    assert d.is_ready()
    cache = Path(toy_config.cache_dir)
    assert (cache / "manifest.json").exists()
    assert (cache / "prototype_corpus_v2.json").exists()
    assert (cache / "prototype_embeddings_v2.npy").exists()


def test_dispatcher_prepare_is_idempotent(toy_config) -> None:
    """Second prepare() must NOT recompute (no extra warmups)."""
    from src.services.comparison.ai_classifier import get_embedding_dispatcher

    d = get_embedding_dispatcher(toy_config)
    d.prepare()
    first_ms = d.prepare_ms()
    d.prepare()
    # No-op second call → prepare_ms unchanged
    assert d.prepare_ms() == first_ms


def test_dispatcher_cache_reuse_across_dispatchers(toy_config) -> None:
    """A second dispatcher pointing at the same cache_dir should read
    the persisted .npy/manifest instead of recomputing."""
    from src.services.comparison.ai_classifier import (
        clear_dispatcher_cache,
        get_embedding_dispatcher,
    )

    d1 = get_embedding_dispatcher(toy_config)
    d1.prepare()
    # Spy on the cache dir to confirm files exist before second run
    cache = Path(toy_config.cache_dir)
    npy_mtime_before = (cache / "prototype_embeddings_v2.npy").stat().st_mtime

    # Force a brand new dispatcher (clear singleton cache)
    clear_dispatcher_cache()
    d2 = get_embedding_dispatcher(toy_config)
    d2.prepare()

    # .npy mtime should be unchanged (no recompute on second prepare)
    npy_mtime_after = (cache / "prototype_embeddings_v2.npy").stat().st_mtime
    assert npy_mtime_after == npy_mtime_before


def test_dispatcher_singleton_cache_returns_same_instance(toy_config) -> None:
    from src.services.comparison.ai_classifier import get_embedding_dispatcher

    d1 = get_embedding_dispatcher(toy_config)
    d2 = get_embedding_dispatcher(toy_config)
    assert d1 is d2


# ---------------------------------------------------------------------------
# classify_zone — happy path + abstain
# ---------------------------------------------------------------------------


def test_classify_zone_returns_structural_member_for_beam_phrase(toy_config) -> None:
    """A zone whose evidence text matches STRUCTURAL_MEMBER markers
    strongly should return that category from Stage-2."""
    from src.services.comparison.ai_classifier import (
        ChangeCategory,
        get_embedding_dispatcher,
    )

    d = get_embedding_dispatcher(toy_config)
    d.prepare()
    zone = {
        "zone_id": "z1",
        "text_snippet": "보 단면 H400×200×8×13 변경",
        "layer": "BEAM",
        "change_type": "modified",
    }
    result = d.classify_zone(zone)
    assert result is not None
    assert result.category == ChangeCategory.STRUCTURAL_MEMBER
    assert result.classifier_used == "embedding"
    assert result.confidence >= 0.5
    # Diagnostics are populated
    assert "top1_score" in result.raw_evidence
    assert "margin" in result.raw_evidence


def test_classify_zone_returns_grid_for_grid_phrase(toy_config) -> None:
    from src.services.comparison.ai_classifier import (
        ChangeCategory,
        get_embedding_dispatcher,
    )

    d = get_embedding_dispatcher(toy_config)
    d.prepare()
    zone = {
        "zone_id": "z2",
        "text_snippet": "그리드 X3 위치 변경",
        "layer": "",
        "change_type": "moved",
    }
    result = d.classify_zone(zone)
    assert result is not None
    # "그리드" → slot 3 (GRID), "위치" → slot 4 (LAYOUT). Margin
    # exists because GRID prototypes hit slot 3 strongly while LAYOUT
    # prototypes also have GRID/이동 mixed in.
    assert result.category in (ChangeCategory.GRID, ChangeCategory.LAYOUT)
    assert result.classifier_used == "embedding"


def test_classify_zone_abstains_on_empty_evidence(toy_config) -> None:
    from src.services.comparison.ai_classifier import get_embedding_dispatcher

    d = get_embedding_dispatcher(toy_config)
    d.prepare()
    zone = {"zone_id": "z3", "text_snippet": "", "layer": "", "change_type": ""}
    result = d.classify_zone(zone)
    assert result is None  # abstain → caller falls back to Stage-1


def test_classify_zone_abstains_on_no_marker_match(toy_config) -> None:
    """Evidence text with zero category markers → toy emits the tiny
    ε-magnitude vector → cosine ≪ threshold → abstain."""
    from src.services.comparison.ai_classifier import get_embedding_dispatcher

    d = get_embedding_dispatcher(toy_config)
    d.prepare()
    zone = {
        "zone_id": "z4",
        "text_snippet": "xyz unrelated 모르는단어",
        "layer": "",
        "change_type": "",
    }
    result = d.classify_zone(zone)
    assert result is None


def test_classify_zone_abstains_on_non_dict_input(toy_config) -> None:
    from src.services.comparison.ai_classifier import get_embedding_dispatcher

    d = get_embedding_dispatcher(toy_config)
    d.prepare()
    assert d.classify_zone("not a dict") is None  # type: ignore[arg-type]
    assert d.classify_zone(None) is None  # type: ignore[arg-type]


def test_classify_zone_pdf_page_layer_drops_severity_to_minor(toy_config) -> None:
    """PDF visual-only zones get MINOR severity even when category
    would normally be CRITICAL — mirrors Stage-1 behaviour."""
    from src.services.comparison.ai_classifier import (
        Severity,
        get_embedding_dispatcher,
    )

    d = get_embedding_dispatcher(toy_config)
    d.prepare()
    # Full beam code (with × notation) — bare "H400" is ambiguous in
    # Korean drawing text ("단면 H400" could be a section CALLOUT) so
    # we use the realistic complete form.
    zone = {
        "zone_id": "z5",
        "text_snippet": "보 단면 H400×200×8×13 변경",
        "layer": "PDF_PAGE_3",
        "change_type": "modified",
    }
    result = d.classify_zone(zone)
    assert result is not None
    assert result.severity == Severity.MINOR


# ---------------------------------------------------------------------------
# Stage-1 + Stage-2 cascade — public_api.classify_zones
# ---------------------------------------------------------------------------


def test_cascade_replaces_stage1_when_embedding_confident(toy_config) -> None:
    """When use_embedding=True, a confident Stage-2 result should
    replace the Stage-1 heuristic answer."""
    from src.services.comparison.ai_classifier import (
        ChangeCategory,
        classify_zones,
        get_embedding_dispatcher,
    )

    # Pre-warm so the test isn't measuring lazy init
    get_embedding_dispatcher(toy_config).prepare()

    zones = [{
        "zone_id": "z1",
        "text_snippet": "보 단면 H400×200×8×13 변경",
        "layer": "",  # no Stage-1 layer hint
        "change_type": "modified",
    }]
    out = classify_zones(zones, config=toy_config)
    assert len(out) == 1
    assert out[0].category == ChangeCategory.STRUCTURAL_MEMBER
    assert out[0].classifier_used == "embedding"
    # Stage-1 metadata stashed in raw_evidence for diagnostics
    assert "stage1_category" in out[0].raw_evidence


def test_cascade_keeps_stage1_when_embedding_abstains(toy_config) -> None:
    """When Stage-2 abstains (low confidence), the Stage-1 heuristic
    result must pass through unchanged."""
    from src.services.comparison.ai_classifier import (
        classify_zones,
        get_embedding_dispatcher,
    )

    get_embedding_dispatcher(toy_config).prepare()
    zones = [{
        "zone_id": "z1",
        # No text → embedding abstains. Layer hint → Stage-1 wins.
        "text_snippet": "",
        "layer": "BEAM",
        "change_type": "added",
    }]
    out = classify_zones(zones, config=toy_config)
    assert len(out) == 1
    assert out[0].classifier_used == "heuristic"
    # Stage-1 still produced its STRUCTURAL_MEMBER answer
    from src.services.comparison.ai_classifier import ChangeCategory
    assert out[0].category == ChangeCategory.STRUCTURAL_MEMBER


def test_cascade_skipped_when_use_embedding_false(tmp_path) -> None:
    """use_embedding=False → no Stage-2 invoked, all results are
    classifier_used='heuristic'."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig,
        classify_zones,
    )

    cfg = AiClassifierConfig(
        enabled=True,
        use_embedding=False,  # opt-out
        use_llm=False,
        embedding_threshold=0.5,
        cache_dir=str(tmp_path / "ai_cache"),
    )
    out = classify_zones(
        [{"zone_id": "z1", "text_snippet": "보 단면 변경",
          "layer": "BEAM", "change_type": "modified"}],
        config=cfg,
    )
    assert out[0].classifier_used == "heuristic"


def test_cascade_falls_back_when_backend_unavailable(tmp_path) -> None:
    """When the configured backend isn't registered, Stage-2 abstains
    silently and Stage-1 results pass through."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig,
        classify_zones,
    )

    cfg = AiClassifierConfig(
        enabled=True,
        use_embedding=True,
        use_llm=False,
        embedding_backend_id="this_backend_does_not_exist",
        embedding_model="this_backend_does_not_exist",
        embedding_threshold=0.5,
        cache_dir=str(tmp_path / "ai_cache"),
    )
    out = classify_zones(
        [{"zone_id": "z1", "text_snippet": "보 단면 변경",
          "layer": "BEAM", "change_type": "modified"}],
        config=cfg,
    )
    # Backend missing → Stage-2 abstains → Stage-1 returns
    assert len(out) == 1
    assert out[0].classifier_used == "heuristic"


def test_cascade_handles_empty_zones_list(toy_config) -> None:
    from src.services.comparison.ai_classifier import classify_zones

    assert classify_zones([], config=toy_config) == []


def test_cascade_handles_disabled_config(tmp_path) -> None:
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig,
        classify_zones,
    )

    cfg = AiClassifierConfig(enabled=False, use_embedding=True,
                             cache_dir=str(tmp_path))
    out = classify_zones(
        [{"zone_id": "z1", "text_snippet": "보 변경"}], config=cfg,
    )
    assert out[0].classifier_used == "disabled"
    from src.services.comparison.ai_classifier import ChangeCategory
    assert out[0].category == ChangeCategory.UNKNOWN


# ---------------------------------------------------------------------------
# Manifest fingerprint
# ---------------------------------------------------------------------------


def test_manifest_records_full_fingerprint_after_prepare(toy_config) -> None:
    """After prepare(), the on-disk manifest should include all the
    3rd-review fingerprint fields (instruction_id, output_dim, pooling,
    quantization) populated from the toy backend."""
    from src.services.comparison.ai_classifier import (
        get_embedding_dispatcher,
        load_manifest,
    )

    d = get_embedding_dispatcher(toy_config)
    d.prepare()
    persisted = load_manifest(Path(toy_config.cache_dir))
    assert persisted is not None
    assert persisted.embedding_backend == "toy_embedding_main"
    assert persisted.embedding_dim == _TOY_DIM
    assert persisted.normalizer_version  # non-empty
    assert persisted.prototype_corpus_version
    assert persisted.prototype_corpus_sha256
    # 3rd-review fields
    assert persisted.instruction_id == "korean_construction_zone_v1"
    assert persisted.pooling == "last_token"
    # Quantisation parsed from the toy's fake "Q8_0.gguf" filename
    assert persisted.quantization == "Q8_0"


def test_manifest_change_invalidates_cache(toy_config, tmp_path) -> None:
    """If the persisted manifest's instruction_id doesn't match the
    dispatcher's current instruction_id, the dispatcher should
    recompute embeddings instead of trusting the .npy."""
    import json
    from src.services.comparison.ai_classifier import (
        clear_dispatcher_cache,
        get_embedding_dispatcher,
    )

    d = get_embedding_dispatcher(toy_config)
    d.prepare()
    cache = Path(toy_config.cache_dir)
    manifest_path = cache / "manifest.json"
    npy_path = cache / "prototype_embeddings_v2.npy"
    npy_mtime_before = npy_path.stat().st_mtime

    # Tamper the manifest to flip instruction_id
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["instruction_id"] = "different_instruction_v999"
    manifest_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # New dispatcher reads tampered manifest, finds it incompatible,
    # recomputes → npy mtime advances.
    clear_dispatcher_cache()
    # Wait a tick so mtime resolution catches the new write
    import time
    time.sleep(0.02)
    d2 = get_embedding_dispatcher(toy_config)
    d2.prepare()
    npy_mtime_after = npy_path.stat().st_mtime
    assert npy_mtime_after > npy_mtime_before


# ---------------------------------------------------------------------------
# Concurrent prepare()
# ---------------------------------------------------------------------------


def test_prepare_thread_safe_under_concurrent_first_callers(toy_config) -> None:
    """Two threads racing on prepare() should both end up ready
    without double-loading. The lock guarantees only one thread does
    the heavy work — but both must see is_ready()=True after."""
    from src.services.comparison.ai_classifier import get_embedding_dispatcher

    d = get_embedding_dispatcher(toy_config)
    errors: list[BaseException] = []

    def _worker():
        try:
            d.prepare()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not errors
    assert d.is_ready()


# ---------------------------------------------------------------------------
# Helper: _zone_evidence_text
# ---------------------------------------------------------------------------


def test_zone_evidence_text_concatenates_in_priority_order() -> None:
    from src.services.comparison.ai_classifier.embedding_classifier import (
        _zone_evidence_text,
    )

    zone = {
        "text_snippet": "보 단면 변경",
        "layer": "BEAM",
        "entity_type": "MTEXT",
        "change_type": "modified",
    }
    out = _zone_evidence_text(zone)
    # Snippet first, then layer / entity / change
    assert out.startswith("보 단면 변경")
    assert "BEAM" in out
    assert "MTEXT" in out
    assert "modified" in out


def test_zone_evidence_text_returns_empty_for_blank_zone() -> None:
    from src.services.comparison.ai_classifier.embedding_classifier import (
        _zone_evidence_text,
    )

    assert _zone_evidence_text({}) == ""
    assert _zone_evidence_text({"zone_id": "z1"}) == ""


# ---------------------------------------------------------------------------
# S1.3.5 — failure_code surfaced by EmbeddingClassifierDispatcher
# ---------------------------------------------------------------------------


def test_failure_code_is_ok_before_prepare_runs(toy_config) -> None:
    """S1.3.5: a fresh dispatcher reports failure_code() == 'ok'.

    No prepare() call has run yet, so the silent-fallback path can't
    have triggered. The badge (S1.4) should show nothing.
    """
    from src.services.comparison.ai_classifier import get_embedding_dispatcher

    dispatcher = get_embedding_dispatcher(toy_config)
    assert dispatcher.failure_code() == "ok"


def test_failure_code_stays_ok_after_successful_prepare(toy_config) -> None:
    """S1.3.5: a successful prepare() leaves failure_code at 'ok'.

    Only BackendUnavailableError should trip the flag — other paths
    keep AI classification fully operational.
    """
    from src.services.comparison.ai_classifier import get_embedding_dispatcher

    dispatcher = get_embedding_dispatcher(toy_config)
    dispatcher.prepare()
    assert dispatcher.is_ready()
    assert dispatcher.failure_code() == "ok"


def test_failure_code_set_when_backend_unavailable(tmp_path) -> None:
    """S1.3.5: BackendUnavailableError sets failure_code to
    ``ai_heuristic_fallback`` so the GUI badge can show the user that
    AI classification has degraded to heuristic-only.
    """
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig,
        get_embedding_dispatcher,
    )
    from src.services.comparison.ai_classifier.backends import (
        BackendUnavailableError,
    )

    cfg = AiClassifierConfig(
        enabled=True,
        use_embedding=True,
        embedding_backend_id="auto",
        embedding_backend_fallbacks=["not_registered_backend"],
        cache_dir=str(tmp_path / "cache"),
    )
    dispatcher = get_embedding_dispatcher(cfg)

    with pytest.raises(BackendUnavailableError):
        dispatcher.prepare()

    assert dispatcher.failure_code() == "ai_heuristic_fallback"
    # last_error remains the existing diagnostic channel — not regressed.
    assert dispatcher.last_error() is not None


def test_failure_code_set_via_prepare_async_route(tmp_path) -> None:
    """S1.3.5: prepare_async() routes through prepare(), so the
    failure_code is set even when warmup runs on a background thread.
    Complements ``test_prepare_async_backend_unavailable_logs_fallback_warning``
    by asserting the new dispatcher.failure_code() contract alongside
    the existing log behaviour.
    """
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig,
        get_embedding_dispatcher,
    )

    cfg = AiClassifierConfig(
        enabled=True,
        use_embedding=True,
        embedding_backend_id="auto",
        embedding_backend_fallbacks=["not_registered_backend"],
        cache_dir=str(tmp_path / "cache"),
    )
    dispatcher = get_embedding_dispatcher(cfg)
    worker = dispatcher.prepare_async()
    worker.join(timeout=5.0)

    assert not worker.is_alive()
    assert dispatcher.failure_code() == "ai_heuristic_fallback"
