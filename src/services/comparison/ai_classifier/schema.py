# -*- coding: utf-8 -*-
"""Data classes for the AI change-zone classifier.

These pin the public contract so future tier upgrades (LLM, RAG)
can't silently break callers (workbench, exporter, harness).

See docs/AI_CHANGE_CLASSIFICATION_DESIGN.md §4 for the full design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ChangeCategory(str, Enum):
    """Top-level semantic category.

    Aligned with what the workbench heuristic already produces (so the
    AI path can replace / enrich it without breaking dashboards).
    """

    STRUCTURAL_MEMBER = "structural_member"   # 보, 기둥, 슬래브
    DIMENSION = "dimension"                   # 치수 변경
    TEXT_LABEL = "text_label"                 # 텍스트/주기
    GRID = "grid"                             # 그리드 라인
    LAYOUT = "layout"                         # 부재 위치 이동
    DETAIL_DRAWING = "detail_drawing"         # 디테일/단면
    NOTE = "note"                             # 주석
    UNKNOWN = "unknown"


# Korean display labels — kept here so dashboards/reports import a
# single source of truth.
CATEGORY_LABELS_KO: dict[ChangeCategory, str] = {
    ChangeCategory.STRUCTURAL_MEMBER: "구조 부재 변경",
    ChangeCategory.DIMENSION: "치수 변경",
    ChangeCategory.TEXT_LABEL: "텍스트/주기 변경",
    ChangeCategory.GRID: "그리드 변경",
    ChangeCategory.LAYOUT: "부재 위치 이동",
    ChangeCategory.DETAIL_DRAWING: "디테일/단면 변경",
    ChangeCategory.NOTE: "주석 변경",
    ChangeCategory.UNKNOWN: "분류 미정",
}


class Severity(str, Enum):
    """Reviewer-priority bucket."""

    CRITICAL = "critical"   # 즉시 검토 (구조 안전 영향)
    NORMAL = "normal"       # 일반 검토
    MINOR = "minor"         # 시각만 확인


SEVERITY_LABELS_KO: dict[Severity, str] = {
    Severity.CRITICAL: "🚨 즉시 검토",
    Severity.NORMAL: "🟡 일반 검토",
    Severity.MINOR: "🟢 시각 확인",
}


# Default category → severity mapping. The heuristic uses this as the
# starting point; the LLM tier can override per-zone.
DEFAULT_SEVERITY_BY_CATEGORY: dict[ChangeCategory, Severity] = {
    ChangeCategory.STRUCTURAL_MEMBER: Severity.CRITICAL,
    ChangeCategory.GRID: Severity.CRITICAL,
    ChangeCategory.DIMENSION: Severity.NORMAL,
    ChangeCategory.LAYOUT: Severity.NORMAL,
    ChangeCategory.DETAIL_DRAWING: Severity.NORMAL,
    ChangeCategory.TEXT_LABEL: Severity.MINOR,
    ChangeCategory.NOTE: Severity.MINOR,
    ChangeCategory.UNKNOWN: Severity.NORMAL,
}


# Default reviewer action by severity. The workbench surfaces this as
# the recommended button to click.
DEFAULT_ACTION_BY_SEVERITY: dict[Severity, str] = {
    Severity.CRITICAL: "review",   # full attention required
    Severity.NORMAL: "review",
    Severity.MINOR: "confirm",     # safe to auto-confirm after a glance
}


@dataclass(frozen=True)
class ChangeClassification:
    """Per-zone classification result.

    All fields are populated even when the LLM tier is off so callers
    don't have to special-case None — the heuristic provides default
    values for everything.
    """

    zone_id: str
    category: ChangeCategory
    severity: Severity
    confidence: float                    # 0.0–1.0
    suggested_action: str                # "confirm" | "review" | "ignore"
    summary_ko: str                      # one-line Korean summary
    kds_references: list[str] = field(default_factory=list)
    classifier_used: str = "heuristic"   # "heuristic" | "embedding" | "llm" | "hybrid"
    elapsed_ms: float = 0.0
    raw_evidence: dict = field(default_factory=dict)


@dataclass
class AiClassifierConfig:
    """Runtime knobs. Loaded from
    ``%LOCALAPPDATA%/DrawingCompareWorkbench/ai_config.json`` when
    present; defaults match the design doc §9 fallback values.

    Phase I added the dual-backend axis:
      * ``embedding_backend_id`` — registry key (``"auto"`` =
        filesystem-availability bootstrap; otherwise concrete ID like
        ``"llama_cpp_qwen3_embedding"`` or ``"onnx_mxbai_large"``).
      * ``embedding_backend_fallbacks`` — try-in-order list when
        ``embedding_backend_id == "auto"`` or when the configured
        backend's warmup raises BackendUnavailableError.
      * ``embedding_output_dim`` — Matryoshka truncation target
        (None = native, e.g. 1024 for both Qwen and mxbai).

    Legacy ``embedding_model`` is retained for backward compatibility —
    the dispatcher checks ``embedding_backend_id`` first, and only
    falls back to ``embedding_model`` when it's a non-default value.
    """

    enabled: bool = True
    use_embedding: bool = False           # Stage 2 (downloaded model)
    use_llm: bool = False                 # Stage 3 (Ollama)
    # Phase I — backend selection axis (replaces single embedding_model
    # for V2+; embedding_model retained as legacy fallback).
    embedding_backend_id: str = "auto"
    embedding_backend_fallbacks: list[str] = field(default_factory=lambda: [
        "llama_cpp_qwen3_embedding",
        "onnx_mxbai_large",
    ])
    embedding_output_dim: Optional[int] = None  # Matryoshka target (None = native)
    embedding_model: str = "BAAI/bge-m3"   # Legacy V1 — kept for backward compat
    # Phase J Step 5 (J2) — LLM cascade fields
    llm_backend_id: str = "ollama_exaone"  # Registry key — "ollama_exaone" | "stub_llm"
    llm_provider: str = "ollama"           # Legacy V1
    llm_model: str = "exaone3.5:7.8b"
    llm_host: str = "http://localhost:11434"
    llm_timeout_s: float = 10.0
    # Stage-2 → Stage-3 hand-off knobs
    llm_top_k_candidates: int = 3          # # of candidates to send to LLM
    llm_invoke_below_confidence: float = 0.85  # invoke when Stage-2 top-1 < this
    # Phase K2 — KDS RAG integration (kds_context for Stage-3 LLM)
    use_kds_rag: bool = False              # opt-in (off by default)
    kds_rag_client_id: str = "stub_kds"    # "stub_kds" | "local_json_kds"
    kds_rag_top_k: int = 3                 # # of clauses to inject into prompt
    kds_rag_timeout_s: float = 5.0         # per-zone wall-clock cap
    embedding_threshold: float = 0.7
    cache_dir: Optional[str] = None       # default: AppData

    @classmethod
    def heuristic_only(cls) -> "AiClassifierConfig":
        """Convenience: zero-dependency mode (no model download)."""
        return cls(enabled=True, use_embedding=False, use_llm=False)

    @classmethod
    def quality_mode(cls) -> "AiClassifierConfig":
        """Phase I — Qwen GGUF (best Korean quality, slow cold start)."""
        return cls(
            enabled=True, use_embedding=True, use_llm=False,
            embedding_backend_id="llama_cpp_qwen3_embedding",
        )

    @classmethod
    def speed_mode(cls) -> "AiClassifierConfig":
        """Phase I — mxbai ONNX (200-300ms cold start, lighter Korean
        quality). Default Matryoshka truncation 1024→512 per report."""
        return cls(
            enabled=True, use_embedding=True, use_llm=False,
            embedding_backend_id="onnx_mxbai_large",
            embedding_output_dim=512,
        )

    @classmethod
    def auto_mode(cls) -> "AiClassifierConfig":
        """Phase I — filesystem-availability bootstrap. Picks Qwen if
        the GGUF is on disk, else mxbai if its ONNX directory is on
        disk, else abstains (Stage-1 heuristic continues)."""
        return cls(
            enabled=True, use_embedding=True, use_llm=False,
            embedding_backend_id="auto",
        )

    @classmethod
    def hybrid_mode(cls) -> "AiClassifierConfig":
        """Phase J Step 5 (J2) — full 3-tier cascade.
        Heuristic → Embedding (auto) → LLM (Ollama EXAONE).

        Uses ``stub_llm`` backend by default in this iteration so the
        cascade works out-of-the-box without Ollama installed. Switch
        to ``ollama_exaone`` once Ollama is set up."""
        return cls(
            enabled=True, use_embedding=True, use_llm=True,
            embedding_backend_id="auto",
            llm_backend_id="stub_llm",  # safer default until Ollama verified
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "use_embedding": self.use_embedding,
            "use_llm": self.use_llm,
            "embedding_backend_id": self.embedding_backend_id,
            "embedding_backend_fallbacks": list(self.embedding_backend_fallbacks),
            "embedding_output_dim": self.embedding_output_dim,
            "embedding_model": self.embedding_model,
            "llm_backend_id": self.llm_backend_id,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "llm_host": self.llm_host,
            "llm_timeout_s": self.llm_timeout_s,
            "llm_top_k_candidates": self.llm_top_k_candidates,
            "llm_invoke_below_confidence": self.llm_invoke_below_confidence,
            "use_kds_rag": self.use_kds_rag,
            "kds_rag_client_id": self.kds_rag_client_id,
            "kds_rag_top_k": self.kds_rag_top_k,
            "kds_rag_timeout_s": self.kds_rag_timeout_s,
            "embedding_threshold": self.embedding_threshold,
            "cache_dir": self.cache_dir,
        }


__all__ = [
    "ChangeCategory",
    "Severity",
    "ChangeClassification",
    "AiClassifierConfig",
    "CATEGORY_LABELS_KO",
    "SEVERITY_LABELS_KO",
    "DEFAULT_SEVERITY_BY_CATEGORY",
    "DEFAULT_ACTION_BY_SEVERITY",
]
