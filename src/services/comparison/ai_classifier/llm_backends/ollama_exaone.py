# -*- coding: utf-8 -*-
"""Phase J Step 5 (J2) — Ollama EXAONE-3.5-7.8B Stage-3 LLM backend.

HTTP client to a local Ollama daemon. Sends a structured prompt
asking the model to pick one of the candidate ChangeCategory values
+ provide a one-sentence Korean rationale.

Default config (matches AiClassifierConfig fields from V1 design):
  * llm_host: ``http://localhost:11434``
  * model_name: ``exaone3.5:7.8b``

Prompt design:
  * System: "당신은 한국 구조 도면 변경 분류 전문가입니다."
  * User: structured template with zone evidence + candidate list +
    optional KDS context, requesting JSON response.
  * Response format: ``{"category": "...", "confidence": 0.85,
    "rationale": "..."}``

Safety / robustness:
  * The LLM picks ONLY from the candidate list — we validate the
    response category against ChangeCategory enum AND against the
    candidate list. Anything outside both → abstain (None).
  * JSON parse failure → abstain
  * HTTP timeout / connection error → abstain
  * Probe: GET /api/tags + check our model is in the list
  * Lazy ``requests`` import so users without the dependency can
    still use Stage-1 + Stage-2

Real-world testing of this backend requires:
  * Ollama daemon running (``ollama serve``)
  * EXAONE-3.5-7.8B pulled (``ollama pull exaone3.5:7.8b``)
This commit ships the implementation + mock-HTTP unit tests; actual
end-to-end run is deferred to the next session per Phase J Step 5
plan ("Ollama 미설치 환경에서도 진행 가능").
"""

from __future__ import annotations

import importlib.util
import json
import logging
import time
from typing import Any, Optional

from ..schema import ChangeCategory
from .base import (
    AbstractLlmBackend,
    LlmBackendUnavailableError,
    LlmClassificationResult,
)
from . import register_llm_backend

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BACKEND_ID = "ollama_exaone"
DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "exaone3.5:7.8b"
DEFAULT_TIMEOUT_S = 10.0

# Korean prompt template — keeps the LLM focused on the closed-set
# task. The LLM must respond in JSON; we parse the first JSON object
# in the response (handles cases where the model wraps JSON in ``).
_PROMPT_TEMPLATE = """당신은 한국 구조 도면 변경 분류 전문가입니다.

다음 도면 변경 영역을 분류 카테고리 중 하나로 분류하세요.

[변경 영역 설명]
{evidence}

[후보 카테고리 (이 중 하나만 선택)]
{candidates_block}

[참고 자료 (선택)]
{kds_context}

응답은 반드시 다음 JSON 형식으로만 출력하세요. 다른 텍스트는 포함하지 마세요.
{{"category": "<후보 ID 정확히>", "confidence": <0.0-1.0>, "rationale": "<한 문장 한국어 설명>"}}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_candidates_block(candidates: list[ChangeCategory]) -> str:
    """Human-readable list of candidate categories with their enum
    values. The LLM's JSON ``category`` field must match one of
    these enum values (not the Korean label) so the parser can
    map back to the enum cleanly."""
    from ..schema import CATEGORY_LABELS_KO

    lines = []
    for c in candidates:
        label = CATEGORY_LABELS_KO.get(c, c.value)
        lines.append(f"  - {c.value} ({label})")
    return "\n".join(lines)


def _extract_first_json(text: str) -> Optional[dict]:
    """Find and parse the first {...} JSON object in ``text``.

    LLMs sometimes wrap JSON in markdown fences or add explanatory
    prose before/after — tolerate that by scanning for the first
    balanced {...} block. Returns None on parse failure or no match.
    """

    if not text:
        return None
    # Try direct parse first (covers strict JSON-only responses —
    # the common case under Ollama's `format: json` mode).
    text = text.strip()
    if text.startswith("```"):
        # Strip markdown fences (```json ... ``` → middle segment)
        parts = text.split("```")
        # Triple-fence split: ['', 'json\n{...}\n', ''] for paired fences,
        # or ['', 'json\n{...}'] for unclosed. Pick the second segment
        # (index 1) which holds the inner content.
        if len(parts) >= 2:
            text = parts[1]
        # Strip leading "json" language tag if present
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Phase L5 review fix (Issue #3): scan for the first balanced
    # {...} via JSONDecoder.raw_decode() instead of a hand-rolled
    # depth counter. The hand-rolled version mis-matched braces
    # appearing inside string values (e.g. Korean rationale like
    # "보의 {단면} 변경" — the inner `}` closed depth prematurely).
    # raw_decode walks every candidate `{` start position and lets
    # the real JSON parser handle string escaping, brace nesting in
    # values, and Unicode correctly.
    decoder = json.JSONDecoder()
    start = 0
    while True:
        idx = text.find("{", start)
        if idx < 0:
            return None
        try:
            obj, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            start = idx + 1  # try next `{` position
            continue
        if isinstance(obj, dict):
            return obj
        # raw_decode parsed something else (number, list, etc.) at the
        # `{` position — practically unreachable since we anchored on
        # `{`, but defensive: try the next position anyway.
        start = idx + 1


def _validate_response_category(
    raw_value: Any,
    candidates: list[ChangeCategory],
) -> Optional[ChangeCategory]:
    """Return the ChangeCategory matching ``raw_value`` IFF it's in
    the candidate list. Otherwise None (LLM abstain).

    Both a hard schema check (must be in ChangeCategory enum) AND a
    candidate-list check are required — without the candidate
    constraint the LLM could "abstain" by picking UNKNOWN even when
    Stage-2 had confident-but-margin-tight signal.
    """

    if not isinstance(raw_value, str):
        return None
    try:
        cat = ChangeCategory(raw_value)
    except ValueError:
        return None
    if cat not in candidates:
        return None
    return cat


# ---------------------------------------------------------------------------
# Backend implementation
# ---------------------------------------------------------------------------


class OllamaExaoneLlmBackend(AbstractLlmBackend):
    """Stage-3 LLM via Ollama HTTP endpoint."""

    backend_id = BACKEND_ID

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        model_name: str = DEFAULT_MODEL,
        default_timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        super().__init__()
        self._host = host.rstrip("/")
        self.model_name = model_name
        self._default_timeout_s = float(default_timeout_s)
        self._requests: Any = None  # lazy import

    # ---- Availability probe --------------------------------------------

    @classmethod
    def probe_available(
        cls,
        host: str = DEFAULT_HOST,
        model_name: str = DEFAULT_MODEL,
    ) -> bool:
        """True iff:
          1. ``requests`` package is importable
          2. GET ``host/api/tags`` returns 200 with the model in the list

        Cheap (single HTTP call, no model load). On any failure
        returns False — the dispatcher's auto path then skips this
        backend.

        Phase L3 review fix: ``host`` and ``model_name`` were
        hardcoded to ``DEFAULT_HOST`` / ``DEFAULT_MODEL`` even when
        the dispatcher passed a non-default config. That misled the
        GUI probe indicator on remote-Ollama deployments. Now both
        values are accepted as classmethod parameters with the
        defaults preserved for the auto-mode bootstrap path
        (which has no instance to read host from).
        """

        if importlib.util.find_spec("requests") is None:
            return False
        try:
            import requests  # type: ignore[import-not-found]
        except ImportError:
            return False
        try:
            resp = requests.get(
                f"{host.rstrip('/')}/api/tags", timeout=2.0,
            )
            if resp.status_code != 200:
                return False
            data = resp.json()
            models = data.get("models") or []
            family = model_name.split(":")[0]
            return any(
                m.get("name", "").startswith(family)
                for m in models
            )
        except Exception:  # noqa: BLE001
            return False

    def probe_with_instance_config(self) -> bool:
        """Instance-level probe wrapper — uses the configured host +
        model. Used by the GUI dialog's probe indicator + by
        anywhere else that needs to verify THIS dispatcher's specific
        endpoint, not the registry-default endpoint."""
        return type(self).probe_available(
            host=self._host, model_name=self.model_name,
        )

    # ---- AbstractLlmBackend hooks --------------------------------------

    def _warmup_impl(self) -> None:
        try:
            import requests  # type: ignore[import-not-found]
        except ImportError as exc:
            raise LlmBackendUnavailableError(
                "requests 미설치 — Ollama 백엔드 사용 불가. "
                "설치: pip install requests"
            ) from exc
        self._requests = requests

        # GET /api/tags to verify reachability + model presence
        try:
            resp = requests.get(
                f"{self._host}/api/tags", timeout=self._default_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            raise LlmBackendUnavailableError(
                f"Ollama 서버({self._host}) 연결 실패: {exc}. "
                f"`ollama serve` 명령으로 서버 시작 필요."
            ) from exc
        if resp.status_code != 200:
            raise LlmBackendUnavailableError(
                f"Ollama /api/tags returned {resp.status_code}: "
                f"{resp.text[:200]}"
            )
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise LlmBackendUnavailableError(
                f"Ollama /api/tags returned non-JSON: {exc}"
            ) from exc
        models = data.get("models") or []
        names = [m.get("name", "") for m in models]
        if not any(
            n.startswith(self.model_name.split(":")[0]) for n in names
        ):
            raise LlmBackendUnavailableError(
                f"Ollama에 모델 {self.model_name!r} 미설치. "
                f"설치 명령: `ollama pull {self.model_name}`. "
                f"현재 설치된 모델: {names}"
            )

    def _classify_impl(
        self,
        zone_evidence: str,
        candidate_categories: list[ChangeCategory],
        *,
        kds_context: str,
        timeout_s: float,
    ) -> Optional[LlmClassificationResult]:
        if self._requests is None:
            return None  # warmup failed — abstain

        prompt = _PROMPT_TEMPLATE.format(
            evidence=zone_evidence,
            candidates_block=_format_candidates_block(candidate_categories),
            kds_context=kds_context or "(없음)",
        )

        t0 = time.perf_counter()
        try:
            resp = self._requests.post(
                f"{self._host}/api/generate",
                json={
                    "model": self.model_name,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",  # Ollama JSON mode
                    "options": {
                        "temperature": 0.1,  # deterministic-ish
                        "top_p": 0.9,
                    },
                },
                timeout=timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ollama /api/generate failed: %s", exc)
            return None
        if resp.status_code != 200:
            logger.warning("Ollama /api/generate returned %d: %s",
                           resp.status_code, resp.text[:200])
            return None

        try:
            payload = resp.json()
        except json.JSONDecodeError:
            return None

        # Ollama wraps the model output in ``{"response": "...", ...}``
        response_text = payload.get("response") or ""
        result_obj = _extract_first_json(response_text)
        if result_obj is None:
            return None

        cat = _validate_response_category(
            result_obj.get("category"), candidate_categories,
        )
        if cat is None:
            return None

        try:
            confidence = float(result_obj.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))

        rationale = str(result_obj.get("rationale") or "").strip()
        if not rationale:
            rationale = "(LLM이 설명을 제공하지 않음)"

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return LlmClassificationResult(
            category=cat,
            confidence=confidence,
            rationale_ko=rationale,
            kds_references=[],  # KDS RAG integration in Phase K
            elapsed_ms=elapsed_ms,
        )


# ---------------------------------------------------------------------------
# Self-registration
# ---------------------------------------------------------------------------


def _factory(**kwargs) -> OllamaExaoneLlmBackend:
    return OllamaExaoneLlmBackend(**kwargs)


try:
    register_llm_backend(BACKEND_ID, _factory, replace=True)
except Exception:  # noqa: BLE001
    logger.debug(
        "Could not auto-register %s backend at import time",
        BACKEND_ID, exc_info=True,
    )


__all__ = [
    "BACKEND_ID",
    "DEFAULT_HOST",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_S",
    "OllamaExaoneLlmBackend",
]
