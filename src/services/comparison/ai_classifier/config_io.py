# -*- coding: utf-8 -*-
"""Phase J (Step 3) — AiClassifierConfig persistence to ai_config.json.

Replaces the hardcoded ``AiClassifierConfig.auto_mode()`` that
``drawing_compare_workbench.py:_load_ai_config_v2`` returned in Phase
I. Now: the workbench reads / writes ``ai_config.json`` so the user
can switch quality / speed / auto via the GUI settings dialog
without editing code.

File location:
    %LOCALAPPDATA%/DrawingCompareWorkbench/ai_config.json

Schema (v1):
    {
      "schema_version": "v1",
      "enabled": true,
      "use_embedding": true,
      "embedding_backend_id": "auto",
      "embedding_output_dim": null,
      "embedding_threshold": 0.7
    }

Defensive policy (matches the embedding manifest pattern):
  * File missing → returns AiClassifierConfig.auto_mode() (current default)
  * Schema version unknown → log warning + auto_mode() fallback
  * JSON parse error → move corrupt file to .bak + auto_mode()

Atomic save (mirrors manifest.save_manifest from Phase H 2nd-review):
  * Write to .{name}.{pid}.{ns}.tmp in same dir
  * Path.replace() to target — POSIX rename, never partial JSON
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .schema import AiClassifierConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_SCHEMA_VERSION = "v2"  # bumped from v1 in L1: added LLM fields
CONFIG_FILENAME = "ai_config.json"

# Schema versions this loader can read. v1 files (Phase J1) lack LLM
# fields → loader fills LLM defaults from auto_mode() base.
_SUPPORTED_SCHEMA_VERSIONS: frozenset[str] = frozenset({"v1", "v2"})

# Fields persisted in v2 schema. v1 was just the embedding fields;
# v2 adds the J2 LLM cascade fields + the K2 KDS RAG fields so the
# GUI dialog can drive the full 3-tier cascade (heuristic →
# embedding → LLM-with-RAG) end-to-end.
_PERSISTED_FIELDS: tuple[str, ...] = (
    "enabled",
    "use_embedding",
    "embedding_backend_id",
    "embedding_output_dim",
    "embedding_threshold",
    # ---- Phase J Step 5 (J2) — LLM cascade ----
    "use_llm",
    "llm_backend_id",
    "llm_invoke_below_confidence",
    "llm_top_k_candidates",
    "llm_timeout_s",
    # ---- Phase L4 — Ollama endpoint persistence (Issue #6) ----
    # Without these, custom Ollama deployments (non-localhost or
    # non-default model) couldn't survive a Workbench restart.
    "llm_host",
    "llm_model",
    # ---- Phase K2 — KDS RAG (Phase L3 GUI exposure) ----
    "use_kds_rag",
    "kds_rag_client_id",
    "kds_rag_top_k",
    "kds_rag_timeout_s",
)

# Allowed embedding_backend_id values (validation gate). "auto" plus
# the registry IDs we ship in Phase I. Future backends added here.
_VALID_BACKEND_IDS: frozenset[str] = frozenset({
    "auto",
    "llama_cpp_qwen3_embedding",
    "onnx_mxbai_large",
})

# Allowed llm_backend_id values (validation gate). Mirrors the
# LLM_BACKEND_REGISTRY auto-imports in llm_backends/__init__.py.
_VALID_LLM_BACKEND_IDS: frozenset[str] = frozenset({
    "stub_llm",
    "ollama_exaone",
})

# Allowed kds_rag_client_id values (validation gate). Mirrors the
# KDS_RAG_REGISTRY auto-imports in kds_rag/__init__.py.
_VALID_KDS_RAG_CLIENT_IDS: frozenset[str] = frozenset({
    "stub_kds",
    "local_json_kds",
})


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def default_ai_config_path() -> Path:
    """Resolve the user-config path for the current host.

    Production: ``%LOCALAPPDATA%/DrawingCompareWorkbench/ai_config.json``.
    Non-Windows / no LOCALAPPDATA: falls back to ``~/.config/...`` so
    Linux CI doesn't crash when these helpers run via tests.
    """

    appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "DrawingCompareWorkbench" / CONFIG_FILENAME
    return Path.home() / ".config" / "DrawingCompareWorkbench" / CONFIG_FILENAME


def schema_version() -> str:
    """Public read of the current schema version constant.

    External callers (tests, dialogs) check this to decide whether
    they're talking to a compatible config layer.
    """
    return CONFIG_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_payload(payload: dict[str, Any]) -> Optional[str]:
    """Returns None when the payload is valid; otherwise an error
    message string. Caller decides whether to fail-closed or fall
    back to defaults.

    Validations:
      * schema_version is in _SUPPORTED_SCHEMA_VERSIONS (v1 or v2)
      * embedding_backend_id is in _VALID_BACKEND_IDS
      * embedding_output_dim is None or positive int ≤ 1024
      * embedding_threshold is in [0, 1]
      * llm_backend_id (if present) is in _VALID_LLM_BACKEND_IDS
      * llm_invoke_below_confidence (if present) in [0, 1]
      * llm_top_k_candidates (if present) is positive int ≤ 8
      * llm_timeout_s (if present) is positive float ≤ 300
    """

    sv = payload.get("schema_version", "")
    if sv not in _SUPPORTED_SCHEMA_VERSIONS:
        return (f"unsupported schema_version {sv!r} "
                f"(expected one of {sorted(_SUPPORTED_SCHEMA_VERSIONS)})")

    bid = payload.get("embedding_backend_id", "auto")
    if not isinstance(bid, str) or bid not in _VALID_BACKEND_IDS:
        return (f"unknown embedding_backend_id {bid!r} "
                f"(expected one of {sorted(_VALID_BACKEND_IDS)})")

    od = payload.get("embedding_output_dim", None)
    if od is not None:
        try:
            od_int = int(od)
        except (TypeError, ValueError):
            return f"embedding_output_dim {od!r} not an integer"
        if od_int <= 0 or od_int > 1024:
            return (f"embedding_output_dim={od_int} out of range [1, 1024]")

    thr = payload.get("embedding_threshold", 0.7)
    try:
        thr_f = float(thr)
    except (TypeError, ValueError):
        return f"embedding_threshold {thr!r} not a number"
    if not (0.0 <= thr_f <= 1.0):
        return f"embedding_threshold={thr_f} out of range [0, 1]"

    # ---- v2-only fields (LLM cascade) ----
    # All optional in v2 — when absent, loader fills from auto_mode()
    # base. Validation only fires when the field IS present in the file.
    llm_bid = payload.get("llm_backend_id")
    if llm_bid is not None:
        if not isinstance(llm_bid, str) or llm_bid not in _VALID_LLM_BACKEND_IDS:
            return (f"unknown llm_backend_id {llm_bid!r} "
                    f"(expected one of {sorted(_VALID_LLM_BACKEND_IDS)})")

    llm_thr = payload.get("llm_invoke_below_confidence")
    if llm_thr is not None:
        try:
            v = float(llm_thr)
        except (TypeError, ValueError):
            return f"llm_invoke_below_confidence {llm_thr!r} not a number"
        if not (0.0 <= v <= 1.0):
            return f"llm_invoke_below_confidence={v} out of range [0, 1]"

    top_k = payload.get("llm_top_k_candidates")
    if top_k is not None:
        try:
            k = int(top_k)
        except (TypeError, ValueError):
            return f"llm_top_k_candidates {top_k!r} not an integer"
        if k <= 0 or k > 8:
            return f"llm_top_k_candidates={k} out of range [1, 8]"

    timeout = payload.get("llm_timeout_s")
    if timeout is not None:
        try:
            t = float(timeout)
        except (TypeError, ValueError):
            return f"llm_timeout_s {timeout!r} not a number"
        if t <= 0.0 or t > 300.0:
            return f"llm_timeout_s={t} out of range (0, 300]"

    # ---- Phase L4 — Ollama endpoint validation (Issue #6 fix) ----
    # llm_host: must be a string starting with http:// or https://.
    # We DON'T verify reachability here (that's probe_available's job
    # at dispatcher warmup time) — just structural sanity.
    host = payload.get("llm_host")
    if host is not None:
        if not isinstance(host, str) or not host.strip():
            return f"llm_host {host!r} not a non-empty string"
        host_stripped = host.strip()
        if not (host_stripped.startswith("http://")
                or host_stripped.startswith("https://")):
            return (f"llm_host {host_stripped!r} must start with "
                    f"http:// or https://")
        # Hard cap on length to prevent absurd values from making it
        # through to the HTTP layer
        if len(host_stripped) > 500:
            return f"llm_host length {len(host_stripped)} exceeds 500 chars"

    # llm_model: must be a non-empty string. Ollama model names look
    # like "exaone3.5:7.8b" or "llama3.2:3b" — alphanumeric + dot +
    # dash + underscore + colon. Hard cap on length.
    model = payload.get("llm_model")
    if model is not None:
        if not isinstance(model, str) or not model.strip():
            return f"llm_model {model!r} not a non-empty string"
        model_stripped = model.strip()
        if len(model_stripped) > 200:
            return f"llm_model length {len(model_stripped)} exceeds 200 chars"
        # Ollama model name charset (loosened a bit for future
        # backends — e.g. some HF model paths use `/`)
        import re
        if not re.match(r"^[A-Za-z0-9._:/\\-]+$", model_stripped):
            return (f"llm_model {model_stripped!r} contains characters "
                    f"outside the allowed [A-Za-z0-9._:/\\-]")

    # ---- Phase K2/L3 — KDS RAG fields (all optional) ----
    rag_client = payload.get("kds_rag_client_id")
    if rag_client is not None:
        if (not isinstance(rag_client, str)
                or rag_client not in _VALID_KDS_RAG_CLIENT_IDS):
            return (f"unknown kds_rag_client_id {rag_client!r} "
                    f"(expected one of {sorted(_VALID_KDS_RAG_CLIENT_IDS)})")

    rag_top_k = payload.get("kds_rag_top_k")
    if rag_top_k is not None:
        try:
            k = int(rag_top_k)
        except (TypeError, ValueError):
            return f"kds_rag_top_k {rag_top_k!r} not an integer"
        if k <= 0 or k > 10:
            return f"kds_rag_top_k={k} out of range [1, 10]"

    rag_timeout = payload.get("kds_rag_timeout_s")
    if rag_timeout is not None:
        try:
            t = float(rag_timeout)
        except (TypeError, ValueError):
            return f"kds_rag_timeout_s {rag_timeout!r} not a number"
        if t <= 0.0 or t > 60.0:
            return f"kds_rag_timeout_s={t} out of range (0, 60]"

    return None


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


def load_ai_config(path: Optional[Path] = None) -> AiClassifierConfig:
    """Read ai_config.json or return ``AiClassifierConfig.auto_mode()``.

    Behaviour by file state:
      * File doesn't exist → auto_mode() (silent — first launch)
      * JSON parse fails → log warning, move file to .bak, auto_mode()
      * Schema version unknown → log warning, auto_mode()
      * Validation fails → log warning, auto_mode()
      * All checks pass → AiClassifierConfig populated from JSON

    Always returns a usable config — never raises. Callers (Workbench
    boot path) rely on this contract.
    """

    if path is None:
        path = default_ai_config_path()
    path = Path(path)

    if not path.exists():
        return AiClassifierConfig.auto_mode()

    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except OSError as exc:
        logger.warning("ai_config.json read failed (%s) — using auto_mode", exc)
        return AiClassifierConfig.auto_mode()
    except json.JSONDecodeError as exc:
        logger.warning(
            "ai_config.json parse failed (%s) — moving to .bak, using auto_mode",
            exc,
        )
        try:
            backup = path.with_suffix(path.suffix + ".bak")
            path.replace(backup)
            logger.info("Corrupt ai_config.json moved to %s", backup)
        except OSError:
            logger.exception("Could not move corrupt ai_config.json to .bak")
        return AiClassifierConfig.auto_mode()

    if not isinstance(payload, dict):
        logger.warning(
            "ai_config.json root is %s, not dict — using auto_mode",
            type(payload).__name__,
        )
        return AiClassifierConfig.auto_mode()

    err = _validate_payload(payload)
    if err is not None:
        logger.warning("ai_config.json validation failed: %s — using auto_mode",
                       err)
        return AiClassifierConfig.auto_mode()

    # Apply on top of an auto_mode() base so any newly-added fields
    # in AiClassifierConfig get sensible defaults (forward compat).
    # v1 files lack LLM fields → use base defaults (use_llm=False).
    # v2 files persist LLM fields → use them when present.
    base = AiClassifierConfig.auto_mode()
    out_dim = payload.get("embedding_output_dim", None)
    cfg = AiClassifierConfig(
        enabled=bool(payload.get("enabled", base.enabled)),
        use_embedding=bool(payload.get("use_embedding", base.use_embedding)),
        embedding_backend_id=str(
            payload.get("embedding_backend_id", base.embedding_backend_id)
        ),
        embedding_output_dim=int(out_dim) if out_dim else None,
        embedding_threshold=float(
            payload.get("embedding_threshold", base.embedding_threshold)
        ),
        # ---- v2 LLM fields (absent in v1 → base defaults) ----
        use_llm=bool(payload.get("use_llm", base.use_llm)),
        llm_backend_id=str(
            payload.get("llm_backend_id", base.llm_backend_id)
        ),
        llm_invoke_below_confidence=float(
            payload.get("llm_invoke_below_confidence",
                        base.llm_invoke_below_confidence)
        ),
        llm_top_k_candidates=int(
            payload.get("llm_top_k_candidates", base.llm_top_k_candidates)
        ),
        llm_timeout_s=float(
            payload.get("llm_timeout_s", base.llm_timeout_s)
        ),
        # ---- Phase L4 — Ollama endpoint persistence (Issue #6 fix)
        llm_host=str(payload.get("llm_host", base.llm_host)).strip(),
        llm_model=str(payload.get("llm_model", base.llm_model)).strip(),
        # ---- Phase L3 — KDS RAG fields (absent in v1 → base defaults) ----
        use_kds_rag=bool(payload.get("use_kds_rag", base.use_kds_rag)),
        kds_rag_client_id=str(
            payload.get("kds_rag_client_id", base.kds_rag_client_id)
        ),
        kds_rag_top_k=int(
            payload.get("kds_rag_top_k", base.kds_rag_top_k)
        ),
        kds_rag_timeout_s=float(
            payload.get("kds_rag_timeout_s", base.kds_rag_timeout_s)
        ),
        # Preserve other fields from the auto_mode() base — these
        # aren't persisted in v1 OR v2 (legacy / non-user-facing) but
        # the dispatcher needs them populated.
        # Phase L4: llm_host + llm_model REMOVED from this block —
        # they're now persisted in v2 (see _PERSISTED_FIELDS) and
        # threaded above. Keeping them here would silently override
        # the user's saved value.
        embedding_backend_fallbacks=list(base.embedding_backend_fallbacks),
        embedding_model=base.embedding_model,
        llm_provider=base.llm_provider,
        cache_dir=base.cache_dir,
    )
    return cfg


def save_ai_config(
    cfg: AiClassifierConfig,
    path: Optional[Path] = None,
) -> Path:
    """Atomically persist the persisted-fields subset of ``cfg``.

    Atomic-write contract (mirrors manifest.save_manifest):
      1. Write to ``.{name}.{pid}.{ns}.tmp`` in the same directory
      2. ``Path.replace()`` to the target (POSIX rename — never
         leaves a partial JSON if the process crashes mid-write)
      3. Best-effort cleanup of stale tmp on exception path

    Only the fields in ``_PERSISTED_FIELDS`` are written — LLM-related
    fields are reserved for J2 schema bump. Forward compatibility:
    if v1 reader sees a v2 file, it falls back to defaults via the
    schema_version check in ``_validate_payload``.

    Returns the resolved target path.
    """

    if path is None:
        path = default_ai_config_path()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "computed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    for field in _PERSISTED_FIELDS:
        payload[field] = getattr(cfg, field)

    serialised = json.dumps(payload, indent=2, ensure_ascii=False)

    # Same-directory temp + atomic rename
    tmp = path.parent / f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        tmp.write_text(serialised, encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

    return path


__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "CONFIG_FILENAME",
    "default_ai_config_path",
    "schema_version",
    "load_ai_config",
    "save_ai_config",
]
