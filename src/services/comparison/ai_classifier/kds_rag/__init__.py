# -*- coding: utf-8 -*-
"""Phase K2 — KDS RAG client registry.

Mirrors the embedding / LLM backend registry pattern. Concrete
clients self-register at import time; ``get_kds_rag_client(name)``
returns the registered constructor.

Built-in clients:
  * ``stub_kds`` — always returns "" (no-op). Default in J2 baseline,
    used for tests + when KDS RAG is disabled.
  * ``local_json_kds`` — reads from a local JSON file with pre-parsed
    KDS clauses. Offline-capable, deployment-friendly. The file's
    schema is documented in ``local_json.py``.

Future (not in K2):
  * ``http_kds`` — POST to a configurable HTTP endpoint
  * ``mcp_kds`` — wrapper for the kcsc-rag-mcp server (when running
    behind an MCP-aware client)

Auto-import is wrapped in try/except per backend so missing
dependencies (if any) don't break the package import.
"""

from __future__ import annotations

from typing import Callable

from .base import AbstractKdsRagClient, KdsRagClient


# Map: client_id → factory callable
KDS_RAG_REGISTRY: dict[str, Callable[..., KdsRagClient]] = {}


def register_kds_rag_client(
    client_id: str,
    factory: Callable[..., KdsRagClient],
    *,
    replace: bool = False,
) -> None:
    """Register a KDS RAG client factory under ``client_id``.

    Same fail-closed contract as the embedding / LLM registries.

    Raises:
        ValueError: ``client_id`` already registered and replace=False.
    """

    if client_id in KDS_RAG_REGISTRY and not replace:
        raise ValueError(
            f"KDS RAG client {client_id!r} already registered. "
            f"Pass replace=True to override explicitly."
        )
    KDS_RAG_REGISTRY[client_id] = factory


def get_kds_rag_client(client_id: str, **kwargs) -> KdsRagClient:
    """Resolve and instantiate the named KDS RAG client.

    Falls back to ``stub_kds`` (no-op) when the requested client
    isn't registered — KDS RAG is OPTIONAL enrichment so a missing
    client should not stop the cascade. Logs a warning so the user
    knows their config wasn't honoured.
    """

    factory = KDS_RAG_REGISTRY.get(client_id)
    if factory is None:
        import logging
        logging.getLogger(__name__).warning(
            "KDS RAG client %r not registered — falling back to stub. "
            "Available: %s",
            client_id, sorted(KDS_RAG_REGISTRY.keys()) or "(none)",
        )
        factory = KDS_RAG_REGISTRY.get("stub_kds")
        if factory is None:
            # Bootstrap order issue — stub itself failed to register.
            # Build an inline AbstractKdsRagClient that returns "".
            from .stub import StubKdsRagClient
            return StubKdsRagClient()
    return factory(**kwargs)


def available_kds_rag_clients() -> list[str]:
    """Snapshot of currently-registered KDS RAG client IDs."""

    return sorted(KDS_RAG_REGISTRY.keys())


__all__ = [
    "AbstractKdsRagClient",
    "KdsRagClient",
    "KDS_RAG_REGISTRY",
    "register_kds_rag_client",
    "get_kds_rag_client",
    "available_kds_rag_clients",
]


# ---------------------------------------------------------------------------
# Auto-import concrete clients (each block fail-silent on missing deps)
# ---------------------------------------------------------------------------

try:
    from . import stub  # noqa: F401  (registers "stub_kds")
except Exception:  # noqa: BLE001
    import logging as _log
    _log.getLogger(__name__).debug(
        "stub_kds client not auto-importable", exc_info=True,
    )

try:
    from . import local_json  # noqa: F401  (registers "local_json_kds")
except Exception:  # noqa: BLE001
    import logging as _log
    _log.getLogger(__name__).debug(
        "local_json_kds client not auto-importable", exc_info=True,
    )
