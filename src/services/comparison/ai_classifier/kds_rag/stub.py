# -*- coding: utf-8 -*-
"""Phase K2 — stub KDS RAG client (no-op).

Always returns "" — used in tests + as the default when KDS RAG
is disabled (cfg.use_kds_rag=False). Always available, no I/O.

The cascade contract (LLM dispatcher) treats empty kds_context the
same as "no RAG enabled" — the LLM still classifies but without
clause-level evidence. This is the safe default until the user
actually deploys a KDS RAG source.
"""

from __future__ import annotations

import logging
from typing import Any, List

from .base import AbstractKdsRagClient
from . import register_kds_rag_client


logger = logging.getLogger(__name__)


CLIENT_ID = "stub_kds"


class StubKdsRagClient(AbstractKdsRagClient):
    """No-op KDS RAG client — always returns empty context."""

    client_id = CLIENT_ID

    @classmethod
    def probe_available(cls) -> bool:
        return True

    def _retrieve_impl(
        self,
        zone_evidence: str,
        candidate_categories: List[Any],
        *,
        top_k: int,
        timeout_s: float,
    ) -> str:
        # Deterministic empty result — no I/O, no exceptions.
        return ""


def _factory(**kwargs) -> StubKdsRagClient:
    return StubKdsRagClient(**kwargs)


try:
    register_kds_rag_client(CLIENT_ID, _factory, replace=True)
except Exception:  # noqa: BLE001
    logger.debug(
        "Could not auto-register %s client at import time",
        CLIENT_ID, exc_info=True,
    )


__all__ = ["CLIENT_ID", "StubKdsRagClient"]
