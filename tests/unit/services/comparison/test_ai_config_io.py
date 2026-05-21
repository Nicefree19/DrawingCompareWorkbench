# -*- coding: utf-8 -*-
"""Phase J Step 3 (J1) — ai_config.json persistence tests.

Pins behaviour we deliberately added in config_io.py:
  * Round-trip: save_ai_config → load_ai_config preserves all
    persisted fields (backend_id, output_dim, threshold, enabled flags)
  * Missing file → returns auto_mode() silently (no exception, no log
    above INFO)
  * Corrupt JSON → backed up to .bak + auto_mode() returned
  * Schema version mismatch → auto_mode() + warning
  * Validation rejects: unknown backend_id, out-of-range output_dim,
    out-of-range threshold, non-int output_dim
  * Atomic save: tmp file goes away after rename, target atomic
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Default-path resolution
# ---------------------------------------------------------------------------


def test_default_path_under_localappdata(monkeypatch, tmp_path) -> None:
    """default_ai_config_path resolves to LOCALAPPDATA on Windows-style envs."""
    from src.services.comparison.ai_classifier import default_ai_config_path

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    p = default_ai_config_path()
    assert p == tmp_path / "DrawingCompareWorkbench" / "ai_config.json"


def test_default_path_falls_back_when_no_localappdata(monkeypatch) -> None:
    """Without LOCALAPPDATA / APPDATA, falls back to ~/.config/..."""
    from src.services.comparison.ai_classifier import default_ai_config_path

    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    p = default_ai_config_path()
    assert p.name == "ai_config.json"
    assert ".config" in str(p) or "DrawingCompareWorkbench" in str(p)


# ---------------------------------------------------------------------------
# load_ai_config — defaults + happy path
# ---------------------------------------------------------------------------


def test_load_returns_auto_mode_when_file_missing(tmp_path) -> None:
    """First-launch behaviour — no file means auto-mode default."""
    from src.services.comparison.ai_classifier import load_ai_config

    cfg = load_ai_config(tmp_path / "nonexistent.json")
    assert cfg.use_embedding is True
    assert cfg.embedding_backend_id == "auto"
    assert cfg.embedding_output_dim is None


def test_load_round_trip_quality_mode(tmp_path) -> None:
    """save → load preserves quality_mode() (Qwen + native dim)."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, load_ai_config, save_ai_config,
    )

    p = tmp_path / "ai_config.json"
    save_ai_config(AiClassifierConfig.quality_mode(), p)
    cfg = load_ai_config(p)
    assert cfg.embedding_backend_id == "llama_cpp_qwen3_embedding"
    assert cfg.embedding_output_dim is None
    assert cfg.use_embedding is True


def test_load_round_trip_speed_mode(tmp_path) -> None:
    """save → load preserves speed_mode() (mxbai + 512 truncation)."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, load_ai_config, save_ai_config,
    )

    p = tmp_path / "ai_config.json"
    save_ai_config(AiClassifierConfig.speed_mode(), p)
    cfg = load_ai_config(p)
    assert cfg.embedding_backend_id == "onnx_mxbai_large"
    assert cfg.embedding_output_dim == 512


def test_load_round_trip_auto_mode(tmp_path) -> None:
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, load_ai_config, save_ai_config,
    )

    p = tmp_path / "ai_config.json"
    save_ai_config(AiClassifierConfig.auto_mode(), p)
    cfg = load_ai_config(p)
    assert cfg.embedding_backend_id == "auto"


def test_load_preserves_disabled_state(tmp_path) -> None:
    """User opted out of embeddings — must round-trip."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, load_ai_config, save_ai_config,
    )

    p = tmp_path / "ai_config.json"
    cfg_in = AiClassifierConfig(
        enabled=True, use_embedding=False, use_llm=False,
        embedding_backend_id="auto",
    )
    save_ai_config(cfg_in, p)
    cfg_out = load_ai_config(p)
    assert cfg_out.use_embedding is False


# ---------------------------------------------------------------------------
# load_ai_config — corruption recovery
# ---------------------------------------------------------------------------


def test_load_returns_auto_mode_on_corrupt_json(tmp_path) -> None:
    """JSON parse error → corrupt file moved to .bak + auto_mode()."""
    from src.services.comparison.ai_classifier import load_ai_config

    p = tmp_path / "ai_config.json"
    p.write_text("not valid json {{{", encoding="utf-8")
    cfg = load_ai_config(p)
    assert cfg.embedding_backend_id == "auto"
    # Original should be backed up
    backup = p.with_suffix(p.suffix + ".bak")
    assert backup.exists()
    # Original gone (renamed)
    assert not p.exists()


def test_load_returns_auto_mode_on_unknown_schema_version(tmp_path) -> None:
    """Schema version we don't understand → don't crash, fall back."""
    from src.services.comparison.ai_classifier import load_ai_config

    p = tmp_path / "ai_config.json"
    p.write_text(
        json.dumps({"schema_version": "v999", "use_embedding": True}),
        encoding="utf-8",
    )
    cfg = load_ai_config(p)
    assert cfg.embedding_backend_id == "auto"


def test_load_returns_auto_mode_when_root_is_not_dict(tmp_path) -> None:
    from src.services.comparison.ai_classifier import load_ai_config

    p = tmp_path / "ai_config.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    cfg = load_ai_config(p)
    assert cfg.embedding_backend_id == "auto"


# ---------------------------------------------------------------------------
# load_ai_config — validation
# ---------------------------------------------------------------------------


def test_load_rejects_unknown_backend_id(tmp_path) -> None:
    from src.services.comparison.ai_classifier import (
        CONFIG_SCHEMA_VERSION, load_ai_config,
    )

    p = tmp_path / "ai_config.json"
    p.write_text(
        json.dumps({
            "schema_version": CONFIG_SCHEMA_VERSION,
            "embedding_backend_id": "definitely_not_a_real_backend",
        }),
        encoding="utf-8",
    )
    cfg = load_ai_config(p)
    # Validation failed → fallback
    assert cfg.embedding_backend_id == "auto"


def test_load_rejects_out_of_range_output_dim(tmp_path) -> None:
    from src.services.comparison.ai_classifier import (
        CONFIG_SCHEMA_VERSION, load_ai_config,
    )

    p = tmp_path / "ai_config.json"
    for bad_dim in (-1, 0, 9999):
        p.write_text(
            json.dumps({
                "schema_version": CONFIG_SCHEMA_VERSION,
                "embedding_backend_id": "auto",
                "embedding_output_dim": bad_dim,
            }),
            encoding="utf-8",
        )
        cfg = load_ai_config(p)
        assert cfg.embedding_backend_id == "auto"  # fallback


def test_load_rejects_out_of_range_threshold(tmp_path) -> None:
    from src.services.comparison.ai_classifier import (
        CONFIG_SCHEMA_VERSION, load_ai_config,
    )

    p = tmp_path / "ai_config.json"
    p.write_text(
        json.dumps({
            "schema_version": CONFIG_SCHEMA_VERSION,
            "embedding_backend_id": "auto",
            "embedding_threshold": 1.5,  # > 1.0 invalid
        }),
        encoding="utf-8",
    )
    cfg = load_ai_config(p)
    # Validation rejected → auto_mode() default threshold
    assert cfg.embedding_threshold == 0.7


# ---------------------------------------------------------------------------
# save_ai_config — atomic write contract
# ---------------------------------------------------------------------------


def test_save_creates_parent_directory(tmp_path) -> None:
    """Parent dir auto-created (covers fresh user-launch flow)."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, save_ai_config,
    )

    target = tmp_path / "deep" / "nested" / "ai_config.json"
    save_ai_config(AiClassifierConfig.auto_mode(), target)
    assert target.exists()


def test_save_leaves_no_temp_file_on_success(tmp_path) -> None:
    """Atomic-write contract: tmp .{name}.{pid}.{ns}.tmp is consumed
    by the rename and never left behind."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, save_ai_config,
    )

    target = tmp_path / "ai_config.json"
    save_ai_config(AiClassifierConfig.speed_mode(), target)
    # Only the target should exist
    leftover = list(tmp_path.glob(".ai_config.json.*.tmp"))
    assert leftover == []


def test_save_payload_includes_schema_version_and_timestamp(tmp_path) -> None:
    """Persisted JSON must carry both schema_version and computed_at_utc
    so the loader can reject incompatible files + we can debug timing."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, CONFIG_SCHEMA_VERSION, save_ai_config,
    )

    target = tmp_path / "ai_config.json"
    save_ai_config(AiClassifierConfig.auto_mode(), target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["schema_version"] == CONFIG_SCHEMA_VERSION
    assert "computed_at_utc" in payload
    # Persisted fields all present
    for field in ("enabled", "use_embedding", "embedding_backend_id",
                  "embedding_output_dim", "embedding_threshold"):
        assert field in payload, f"missing {field}"


def test_load_v1_file_fills_llm_defaults_from_auto_mode(tmp_path) -> None:
    """Backward compat: v1 ai_config.json (no LLM fields) → loader
    fills LLM fields from auto_mode() base. Existing users upgrading
    to v2 don't lose their existing config."""
    from src.services.comparison.ai_classifier import load_ai_config

    p = tmp_path / "ai_config.json"
    # Hand-write a v1-format file (no LLM fields)
    p.write_text(json.dumps({
        "schema_version": "v1",
        "enabled": True,
        "use_embedding": True,
        "embedding_backend_id": "llama_cpp_qwen3_embedding",
        "embedding_output_dim": None,
        "embedding_threshold": 0.7,
    }), encoding="utf-8")
    cfg = load_ai_config(p)
    # v1 fields preserved
    assert cfg.embedding_backend_id == "llama_cpp_qwen3_embedding"
    # LLM fields filled from auto_mode() base (use_llm=False default)
    assert cfg.use_llm is False
    assert cfg.llm_backend_id  # non-empty default
    assert cfg.llm_invoke_below_confidence == 0.85  # default


def test_load_rejects_unknown_llm_backend_id(tmp_path) -> None:
    from src.services.comparison.ai_classifier import (
        CONFIG_SCHEMA_VERSION, load_ai_config,
    )

    p = tmp_path / "ai_config.json"
    p.write_text(json.dumps({
        "schema_version": CONFIG_SCHEMA_VERSION,
        "embedding_backend_id": "auto",
        "llm_backend_id": "definitely_not_an_llm_backend",
    }), encoding="utf-8")
    cfg = load_ai_config(p)
    # Validation rejected → fallback to auto_mode()
    assert cfg.embedding_backend_id == "auto"
    assert cfg.use_llm is False


def test_load_rejects_out_of_range_llm_top_k(tmp_path) -> None:
    from src.services.comparison.ai_classifier import (
        CONFIG_SCHEMA_VERSION, load_ai_config,
    )

    p = tmp_path / "ai_config.json"
    for bad in (-1, 0, 99):
        p.write_text(json.dumps({
            "schema_version": CONFIG_SCHEMA_VERSION,
            "embedding_backend_id": "auto",
            "llm_top_k_candidates": bad,
        }), encoding="utf-8")
        cfg = load_ai_config(p)
        # Validation rejected → fallback. The auto_mode() default
        # should not be the value we tried to inject.
        assert cfg.llm_top_k_candidates != bad


def test_load_rejects_out_of_range_llm_invoke_threshold(tmp_path) -> None:
    from src.services.comparison.ai_classifier import (
        CONFIG_SCHEMA_VERSION, load_ai_config,
    )

    p = tmp_path / "ai_config.json"
    p.write_text(json.dumps({
        "schema_version": CONFIG_SCHEMA_VERSION,
        "embedding_backend_id": "auto",
        "llm_invoke_below_confidence": 1.5,
    }), encoding="utf-8")
    cfg = load_ai_config(p)
    # Validation failed → auto_mode() default
    assert cfg.llm_invoke_below_confidence == 0.85


def test_load_v2_file_with_kds_rag_fields_preserves_them(tmp_path) -> None:
    """A v2 file that includes the K2/L3 KDS RAG fields → loader
    preserves them (round-trip through the GUI dialog flow)."""
    from src.services.comparison.ai_classifier import (
        CONFIG_SCHEMA_VERSION, load_ai_config,
    )

    p = tmp_path / "ai_config.json"
    p.write_text(json.dumps({
        "schema_version": CONFIG_SCHEMA_VERSION,
        "embedding_backend_id": "auto",
        "use_kds_rag": True,
        "kds_rag_client_id": "local_json_kds",
        "kds_rag_top_k": 5,
        "kds_rag_timeout_s": 3.0,
    }), encoding="utf-8")
    cfg = load_ai_config(p)
    assert cfg.use_kds_rag is True
    assert cfg.kds_rag_client_id == "local_json_kds"
    assert cfg.kds_rag_top_k == 5
    assert cfg.kds_rag_timeout_s == 3.0


def test_load_rejects_unknown_kds_rag_client_id(tmp_path) -> None:
    from src.services.comparison.ai_classifier import (
        CONFIG_SCHEMA_VERSION, load_ai_config,
    )

    p = tmp_path / "ai_config.json"
    p.write_text(json.dumps({
        "schema_version": CONFIG_SCHEMA_VERSION,
        "embedding_backend_id": "auto",
        "kds_rag_client_id": "definitely_not_a_real_rag_client",
    }), encoding="utf-8")
    cfg = load_ai_config(p)
    # Validation rejected → fallback to auto_mode()
    assert cfg.use_kds_rag is False


def test_load_rejects_out_of_range_kds_rag_top_k(tmp_path) -> None:
    from src.services.comparison.ai_classifier import (
        CONFIG_SCHEMA_VERSION, load_ai_config,
    )

    p = tmp_path / "ai_config.json"
    for bad in (-1, 0, 99):
        p.write_text(json.dumps({
            "schema_version": CONFIG_SCHEMA_VERSION,
            "embedding_backend_id": "auto",
            "kds_rag_top_k": bad,
        }), encoding="utf-8")
        cfg = load_ai_config(p)
        assert cfg.kds_rag_top_k != bad


def test_load_v2_file_with_custom_llm_host_and_model_preserves_them(
    tmp_path,
) -> None:
    """Phase L4 (Issue #6 fix): a v2 file that includes custom Ollama
    endpoint values → loader preserves them (round-trip through GUI)."""
    from src.services.comparison.ai_classifier import (
        CONFIG_SCHEMA_VERSION, load_ai_config,
    )

    p = tmp_path / "ai_config.json"
    p.write_text(json.dumps({
        "schema_version": CONFIG_SCHEMA_VERSION,
        "embedding_backend_id": "auto",
        "use_llm": True,
        "llm_backend_id": "ollama_exaone",
        "llm_host": "http://192.168.1.50:11434",
        "llm_model": "llama3.2:3b",
    }), encoding="utf-8")
    cfg = load_ai_config(p)
    assert cfg.llm_host == "http://192.168.1.50:11434"
    assert cfg.llm_model == "llama3.2:3b"


def test_load_v1_file_fills_llm_host_model_defaults(tmp_path) -> None:
    """Phase L4: v1 files lack llm_host + llm_model → loader fills
    from auto_mode() base. Existing v1 users keep working."""
    from src.services.comparison.ai_classifier import load_ai_config

    p = tmp_path / "ai_config.json"
    p.write_text(json.dumps({
        "schema_version": "v1",
        "enabled": True,
        "use_embedding": True,
        "embedding_backend_id": "auto",
    }), encoding="utf-8")
    cfg = load_ai_config(p)
    # Defaults from auto_mode() base
    assert cfg.llm_host == "http://localhost:11434"
    assert cfg.llm_model == "exaone3.5:7.8b"


def test_load_rejects_llm_host_without_scheme(tmp_path) -> None:
    """llm_host must start with http:// or https://."""
    from src.services.comparison.ai_classifier import (
        CONFIG_SCHEMA_VERSION, load_ai_config,
    )

    p = tmp_path / "ai_config.json"
    for bad in ("localhost:11434", "//host:port", "ftp://host", ""):
        p.write_text(json.dumps({
            "schema_version": CONFIG_SCHEMA_VERSION,
            "embedding_backend_id": "auto",
            "llm_host": bad,
        }), encoding="utf-8")
        cfg = load_ai_config(p)
        # Validation rejected → fallback default
        assert cfg.llm_host == "http://localhost:11434", (
            f"validator should reject {bad!r}"
        )


def test_load_rejects_llm_model_with_invalid_chars(tmp_path) -> None:
    """llm_model is restricted to a sane charset."""
    from src.services.comparison.ai_classifier import (
        CONFIG_SCHEMA_VERSION, load_ai_config,
    )

    p = tmp_path / "ai_config.json"
    for bad in ("model with spaces", "model;rm -rf /", "model\nwith\nnewlines"):
        p.write_text(json.dumps({
            "schema_version": CONFIG_SCHEMA_VERSION,
            "embedding_backend_id": "auto",
            "llm_model": bad,
        }), encoding="utf-8")
        cfg = load_ai_config(p)
        # Validation rejected → fallback default
        assert cfg.llm_model == "exaone3.5:7.8b", (
            f"validator should reject {bad!r}"
        )


def test_load_rejects_llm_host_too_long(tmp_path) -> None:
    from src.services.comparison.ai_classifier import (
        CONFIG_SCHEMA_VERSION, load_ai_config,
    )

    p = tmp_path / "ai_config.json"
    too_long = "http://" + ("x" * 600) + ":11434"
    p.write_text(json.dumps({
        "schema_version": CONFIG_SCHEMA_VERSION,
        "embedding_backend_id": "auto",
        "llm_host": too_long,
    }), encoding="utf-8")
    cfg = load_ai_config(p)
    assert cfg.llm_host == "http://localhost:11434"


def test_load_v1_file_fills_kds_rag_defaults(tmp_path) -> None:
    """v1 ai_config.json (no KDS RAG fields) → loader fills RAG fields
    from auto_mode() base. Existing v2 users without RAG don't lose
    embedding settings."""
    from src.services.comparison.ai_classifier import load_ai_config

    p = tmp_path / "ai_config.json"
    p.write_text(json.dumps({
        "schema_version": "v1",
        "enabled": True,
        "use_embedding": True,
        "embedding_backend_id": "auto",
        "embedding_threshold": 0.7,
    }), encoding="utf-8")
    cfg = load_ai_config(p)
    # KDS RAG fields filled from auto_mode() base
    assert cfg.use_kds_rag is False
    assert cfg.kds_rag_client_id == "stub_kds"  # default
    assert cfg.kds_rag_top_k == 3
    assert cfg.kds_rag_timeout_s == 5.0


def test_save_persists_llm_cascade_fields_in_v2(tmp_path) -> None:
    """Schema v2 (Phase L1) persists the J2 LLM cascade fields so the
    GUI dialog can drive Stage-3 mode end-to-end. v1 used to suppress
    these — that contract was inverted in v2.

    Fields persisted: use_llm, llm_backend_id, llm_invoke_below_
    confidence, llm_top_k_candidates, llm_timeout_s.

    Fields NOT persisted (legacy / non-user-facing): llm_provider,
    llm_model, llm_host. The dispatcher reads these from the auto_mode()
    base when an in-memory dispatcher is built, so they don't NEED to
    round-trip through ai_config.json yet (Phase L2+ may add them).
    """
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, save_ai_config,
    )

    target = tmp_path / "ai_config.json"
    cfg = AiClassifierConfig(
        use_embedding=True, embedding_backend_id="auto",
        use_llm=True, llm_backend_id="stub_llm",
        llm_invoke_below_confidence=0.7,
        llm_top_k_candidates=5,
        llm_timeout_s=15.0,
        # Phase L4 (Issue #6 fix): llm_host + llm_model are NOW
        # persisted in v2 so custom Ollama deployments survive
        # Workbench restart. Was previously suppressed.
        llm_host="http://10.0.0.5:11434",
        llm_model="custom-model:7b",
    )
    save_ai_config(cfg, target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    # v2-persisted fields
    assert payload["use_llm"] is True
    assert payload["llm_backend_id"] == "stub_llm"
    assert payload["llm_invoke_below_confidence"] == 0.7
    assert payload["llm_top_k_candidates"] == 5
    assert payload["llm_timeout_s"] == 15.0
    # Phase L4: llm_host + llm_model NOW persisted (Issue #6 fix)
    assert payload["llm_host"] == "http://10.0.0.5:11434"
    assert payload["llm_model"] == "custom-model:7b"
    # llm_provider STILL not persisted (legacy free-text routing
    # field; not user-facing — hardcoded "ollama")
    assert "llm_provider" not in payload
