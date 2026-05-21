# -*- coding: utf-8 -*-
"""Phase K2 — KDS RAG client protocol.

Stage-3 LLM (J2) accepts a ``kds_context: str`` parameter that the
dispatcher pre-fetches from a Korean Design Standards (KDS) /
Korean Construction Specifications (KCS) retrieval source. This
context is stuffed into the LLM prompt so the model can:

  1. Cite specific KDS clauses in its rationale
  2. Disambiguate categories using the standard's official wording
  3. Surface KDS references for the workbench reviewer

The actual KDS retrieval source varies per deployment:
  * Local JSON file with pre-parsed KDS clauses (offline, default)
  * HTTP endpoint to a self-hosted RAG service
  * MCP server (used by IDE/CLI agents but not production)

This module defines a single Protocol so the dispatcher doesn't
have to know which source is in use. New sources implement
``retrieve()`` and self-register, mirroring the embedding /
LLM backend registry pattern.
"""

from __future__ import annotations

import abc
import logging
from typing import Optional, Protocol, runtime_checkable


logger = logging.getLogger(__name__)


@runtime_checkable
class KdsRagClient(Protocol):
    """KDS RAG retrieval contract.

    Required attributes:
        client_id: Stable string ID matching the registry key
            (e.g. "stub_kds", "local_json_kds", "http_kds")

    Required methods:
        retrieve: Per-zone clause retrieval — returns Korean clause
            text ready to inject into the LLM prompt. Empty string
            on no match (LLM proceeds without RAG context).
        is_ready: True when the client is reachable / loaded.
        probe_available: classmethod — cheap availability check.

    Cost model: retrieve() is called once per zone that triggers
    Stage-3 LLM (~10-30% of zones). For a 100-zone batch with 20
    LLM-eligible zones, expect 20 retrieve() calls. Local file
    sources are sub-millisecond; HTTP sources should target < 200 ms
    each.
    """

    client_id: str

    def retrieve(
        self,
        zone_evidence: str,
        candidate_categories: list,
        *,
        top_k: int = 3,
        timeout_s: float = 5.0,
    ) -> str:
        """Search the KDS corpus for clauses relevant to this zone.

        Args:
            zone_evidence: Canonicalised zone description (same text
                Stage-2 fed to the embedding backend).
            candidate_categories: Stage-2 top-K candidates — narrows
                the KDS subset (e.g. STRUCTURAL_MEMBER → KDS 14
                concrete + KDS 24 steel).
            top_k: Maximum clauses to return.
            timeout_s: Wall-clock cap.

        Returns:
            Concatenated Korean clause text (KDS clause IDs +
            normative_text), formatted for direct LLM-prompt
            injection. Empty string when no match / on error
            (LLM proceeds without context).

        MUST NOT raise — abstain via empty string on any error so
        the cascade keeps moving.
        """
        ...

    def is_ready(self) -> bool:
        """Cheap status check. No I/O."""
        ...

    @classmethod
    def probe_available(cls) -> bool:
        """Cheap availability check (file existence / HTTP ping)."""
        ...


class AbstractKdsRagClient(abc.ABC):
    """Optional convenience base — provides default ``is_ready`` +
    error-swallow on retrieve.

    Subclasses override ``_retrieve_impl``. The base ``retrieve``
    catches exceptions and returns "" (abstain) so the cascade
    contract (never propagates) is enforced uniformly.
    """

    client_id: str = "abstract_kds"

    def __init__(self) -> None:
        self._ready: bool = True  # most KDS clients are stateless
        self._last_error: Optional[BaseException] = None

    @abc.abstractmethod
    def _retrieve_impl(
        self,
        zone_evidence: str,
        candidate_categories: list,
        *,
        top_k: int,
        timeout_s: float,
    ) -> str:
        """Subclass-specific retrieval. Returns Korean clause text."""

    def retrieve(
        self,
        zone_evidence: str,
        candidate_categories: list,
        *,
        top_k: int = 3,
        timeout_s: float = 5.0,
    ) -> str:
        """Public retrieve — catches all subclass exceptions and
        returns "" so the LLM dispatcher's cascade never sees a
        thrown error from the RAG layer."""

        if not zone_evidence:
            return ""
        try:
            result = self._retrieve_impl(
                zone_evidence,
                list(candidate_categories or []),
                top_k=int(top_k),
                timeout_s=float(timeout_s),
            )
        except BaseException as exc:  # noqa: BLE001
            self._last_error = exc
            logger.debug("KDS retrieve failed (abstaining): %s", exc)
            return ""
        return str(result or "")

    def is_ready(self) -> bool:
        return self._ready

    def last_error(self) -> Optional[BaseException]:
        return self._last_error

    @classmethod
    def probe_available(cls) -> bool:
        """Default: assume always available. Concrete clients
        override with file-exists / HTTP-ping checks."""
        return True


__all__ = [
    "KdsRagClient",
    "AbstractKdsRagClient",
]
