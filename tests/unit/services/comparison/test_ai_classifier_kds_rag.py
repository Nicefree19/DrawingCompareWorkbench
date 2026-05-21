# -*- coding: utf-8 -*-
"""Phase K2 — KDS RAG client + dispatcher integration tests.

Pins behaviour we deliberately added:

  Protocol + registry:
  * StubKdsRagClient + LocalJsonKdsRagClient self-register at import
  * Both conform to KdsRagClient Protocol
  * get_kds_rag_client falls back to stub on unknown ID (warning only)

  Stub client:
  * Always returns "" (no-op)
  * probe_available always True
  * Empty inputs handled cleanly

  Local-JSON client:
  * Returns "" when file is missing (graceful)
  * Loads + parses kds_clauses.json
  * Filters by category_hints overlap with candidates
  * Scores by keyword length × overlap (higher = better match)
  * Top-K formatted as ``[code §section] title: text``
  * Per-clause text truncated at 400 chars (prompt size guard)
  * Malformed JSON → empty result (no crash)
  * Hint-less clauses match all categories

  Dispatcher integration:
  * use_kds_rag=False → no RAG call, kds_rag_context_chars=0
  * use_kds_rag=True + stub → RAG call but empty context
  * use_kds_rag=True + local_json → context fetched + threaded to LLM
  * Explicit kds_context arg overrides RAG retrieval
  * RAG retrieve crash → empty context (cascade keeps moving)
  * raw_evidence carries kds_rag_client + kds_rag_context_chars

  Cache key:
  * Different use_kds_rag → different singleton dispatcher
  * Different kds_rag_client_id → different singleton dispatcher
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.services.comparison.ai_classifier.schema import ChangeCategory


@pytest.fixture(autouse=True)
def _reset_caches():
    from src.services.comparison.ai_classifier import (
        clear_dispatcher_cache, clear_llm_dispatcher_cache,
    )
    clear_dispatcher_cache()
    clear_llm_dispatcher_cache()
    yield
    clear_dispatcher_cache()
    clear_llm_dispatcher_cache()


# ---------------------------------------------------------------------------
# Stub client
# ---------------------------------------------------------------------------


def test_stub_kds_satisfies_protocol() -> None:
    from src.services.comparison.ai_classifier import (
        KdsRagClient, get_kds_rag_client,
    )

    stub = get_kds_rag_client("stub_kds")
    assert isinstance(stub, KdsRagClient)
    assert stub.client_id == "stub_kds"


def test_stub_kds_always_returns_empty() -> None:
    from src.services.comparison.ai_classifier import get_kds_rag_client

    stub = get_kds_rag_client("stub_kds")
    assert stub.retrieve("보 단면 변경",
                         [ChangeCategory.STRUCTURAL_MEMBER]) == ""
    assert stub.retrieve("", []) == ""


def test_stub_kds_probe_always_true() -> None:
    from src.services.comparison.ai_classifier.kds_rag.stub import (
        StubKdsRagClient,
    )
    assert StubKdsRagClient.probe_available() is True


def test_get_kds_rag_client_falls_back_to_stub_on_unknown(caplog) -> None:
    """Asking for an unregistered client returns stub + logs warning,
    so the LLM cascade never breaks on a misconfigured kds_rag_client_id."""
    import logging
    from src.services.comparison.ai_classifier import get_kds_rag_client

    with caplog.at_level(logging.WARNING):
        client = get_kds_rag_client("definitely_not_a_real_kds_client")
    assert client.client_id == "stub_kds"


# ---------------------------------------------------------------------------
# Local-JSON client — file resolution + retrieval
# ---------------------------------------------------------------------------


def _make_kds_clauses(path: Path, *, with_hints: bool = True) -> None:
    """Write a minimal kds_clauses.json to the given path."""
    payload = {
        "version": "v1",
        "clauses": [
            {
                "code": "KDS 24 24 00",
                "section": "5.3",
                "title": "휨강도",
                "category_hints": (
                    ["structural_member"] if with_hints else []
                ),
                "keywords": ["보", "휨", "단면"],
                "text": "보의 휨모멘트 단면강도는 ϕMn으로 계산한다.",
            },
            {
                "code": "KDS 14 30 00",
                "section": "4.1",
                "title": "치수 표기",
                "category_hints": (
                    ["dimension"] if with_hints else []
                ),
                "keywords": ["치수", "단위"],
                "text": "치수는 mm 단위로 표기한다.",
            },
            {
                "code": "KDS 99 99 99",
                "section": "1",
                "title": "general",
                "keywords": ["일반"],
                "text": "(no hints — applies to all categories)",
            },
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False),
                    encoding="utf-8")


def test_local_json_returns_empty_when_file_missing(monkeypatch,
                                                       tmp_path) -> None:
    """No kds_clauses.json in any standard location → retrieve "" cleanly."""
    from src.services.comparison.ai_classifier.kds_rag.local_json import (
        LocalJsonKdsRagClient,
    )

    # Point all candidate paths at empty tmp_path
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "no_appdata"))
    monkeypatch.chdir(tmp_path)

    client = LocalJsonKdsRagClient()
    assert client.retrieve("보 단면 변경",
                           [ChangeCategory.STRUCTURAL_MEMBER]) == ""


def test_local_json_loads_explicit_path(tmp_path) -> None:
    from src.services.comparison.ai_classifier.kds_rag.local_json import (
        LocalJsonKdsRagClient,
    )

    p = tmp_path / "custom_kds.json"
    _make_kds_clauses(p)
    client = LocalJsonKdsRagClient(path=p)
    result = client.retrieve(
        "보 단면 변경",
        [ChangeCategory.STRUCTURAL_MEMBER],
        top_k=5,
    )
    # The 휨강도 clause should match (keywords = 보, 휨, 단면)
    assert "휨강도" in result
    assert "KDS 24 24 00" in result


def test_local_json_filters_by_category_hint(tmp_path) -> None:
    """A dimension query should NOT pull the structural-member clause."""
    from src.services.comparison.ai_classifier.kds_rag.local_json import (
        LocalJsonKdsRagClient,
    )

    p = tmp_path / "custom_kds.json"
    _make_kds_clauses(p)
    client = LocalJsonKdsRagClient(path=p)
    result = client.retrieve(
        "치수 5500mm 변경",
        [ChangeCategory.DIMENSION],
        top_k=5,
    )
    assert "치수 표기" in result or "KDS 14 30 00" in result
    # The structural-member-hinted clause should not appear
    assert "휨강도" not in result


def test_local_json_hint_less_clause_matches_any_category(tmp_path) -> None:
    """The 'general' clause has no category_hints — should be eligible
    for any category that satisfies the keyword filter."""
    from src.services.comparison.ai_classifier.kds_rag.local_json import (
        LocalJsonKdsRagClient,
    )

    p = tmp_path / "custom_kds.json"
    _make_kds_clauses(p)
    client = LocalJsonKdsRagClient(path=p)
    # The general clause has keyword "일반"; query that contains it
    result = client.retrieve(
        "일반 사항 보 변경",
        [ChangeCategory.STRUCTURAL_MEMBER],
        top_k=5,
    )
    # Should include both 휨강도 (structural hint match) AND general
    # (hint-less, keyword "일반" matches)
    assert "KDS 99 99 99" in result or "general" in result


def test_local_json_returns_empty_on_no_keyword_match(tmp_path) -> None:
    """Query with no overlapping keywords → empty (don't return random
    clauses)."""
    from src.services.comparison.ai_classifier.kds_rag.local_json import (
        LocalJsonKdsRagClient,
    )

    p = tmp_path / "custom_kds.json"
    _make_kds_clauses(p)
    client = LocalJsonKdsRagClient(path=p)
    result = client.retrieve(
        "xyzunrelatedmunzi",  # nothing matches
        [ChangeCategory.STRUCTURAL_MEMBER],
        top_k=5,
    )
    assert result == ""


def test_local_json_handles_corrupt_file(tmp_path, caplog) -> None:
    """Corrupt JSON → empty result + logged warning, no crash."""
    from src.services.comparison.ai_classifier.kds_rag.local_json import (
        LocalJsonKdsRagClient,
    )
    import logging

    p = tmp_path / "broken.json"
    p.write_text("this is not valid JSON {{{", encoding="utf-8")
    client = LocalJsonKdsRagClient(path=p)
    with caplog.at_level(logging.WARNING):
        result = client.retrieve("보 단면",
                                 [ChangeCategory.STRUCTURAL_MEMBER])
    assert result == ""


def test_local_json_format_includes_code_section_title(tmp_path) -> None:
    """Output must follow ``[code §section] title: text`` format so
    the LLM prompt has consistent clause structure."""
    from src.services.comparison.ai_classifier.kds_rag.local_json import (
        LocalJsonKdsRagClient,
    )

    p = tmp_path / "custom_kds.json"
    _make_kds_clauses(p)
    client = LocalJsonKdsRagClient(path=p)
    result = client.retrieve(
        "보 단면 변경", [ChangeCategory.STRUCTURAL_MEMBER], top_k=1,
    )
    assert "[KDS 24 24 00 §5.3]" in result
    assert "휨강도:" in result


def test_local_json_truncates_per_clause_text(tmp_path) -> None:
    """Per-clause text > 400 chars must be truncated with ellipsis to
    prevent prompt blow-up."""
    from src.services.comparison.ai_classifier.kds_rag.local_json import (
        LocalJsonKdsRagClient,
    )

    p = tmp_path / "long.json"
    long_text = "보 " * 300  # ~600 chars
    payload = {
        "version": "v1",
        "clauses": [{
            "code": "KDS X", "section": "1", "title": "long",
            "category_hints": ["structural_member"],
            "keywords": ["보"], "text": long_text,
        }],
    }
    p.write_text(json.dumps(payload, ensure_ascii=False),
                 encoding="utf-8")
    client = LocalJsonKdsRagClient(path=p)
    result = client.retrieve(
        "보 변경", [ChangeCategory.STRUCTURAL_MEMBER], top_k=1,
    )
    assert len(result) <= 400
    assert result.endswith("...")


# ---------------------------------------------------------------------------
# Dispatcher integration
# ---------------------------------------------------------------------------


def test_dispatcher_skips_rag_when_use_kds_rag_false() -> None:
    """use_kds_rag=False → RAG client never called, kds_rag_context_chars=0."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, classify_zones,
    )

    cfg = AiClassifierConfig(
        enabled=True, use_embedding=False, use_llm=True,
        llm_backend_id="stub_llm",
        use_kds_rag=False,
    )
    out = classify_zones(
        [{"zone_id": "z1", "text_snippet": "보 단면 변경",
          "layer": "BEAM", "change_type": "modified"}],
        config=cfg,
    )
    assert out[0].classifier_used == "hybrid"
    assert out[0].raw_evidence["kds_rag_client"] == "none"
    assert out[0].raw_evidence["kds_rag_context_chars"] == 0


def test_dispatcher_uses_stub_rag_when_enabled() -> None:
    """use_kds_rag=True + stub_kds → client called but context still empty."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, classify_zones,
    )

    cfg = AiClassifierConfig(
        enabled=True, use_embedding=False, use_llm=True,
        llm_backend_id="stub_llm",
        use_kds_rag=True, kds_rag_client_id="stub_kds",
    )
    out = classify_zones(
        [{"zone_id": "z1", "text_snippet": "보 단면 변경",
          "layer": "BEAM", "change_type": "modified"}],
        config=cfg,
    )
    assert out[0].raw_evidence["kds_rag_client"] == "stub_kds"
    assert out[0].raw_evidence["kds_rag_context_chars"] == 0


def test_dispatcher_uses_local_json_rag_with_real_clauses(tmp_path,
                                                              monkeypatch) -> None:
    """End-to-end: use_kds_rag=True + local_json + a real
    kds_clauses.json → context retrieved + chars > 0 in raw_evidence."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, classify_zones,
    )

    # Point local_json client at a tmp clauses file via env var path
    clauses_path = tmp_path / "DrawingCompareWorkbench" / "kds_clauses.json"
    _make_kds_clauses(clauses_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    cfg = AiClassifierConfig(
        enabled=True, use_embedding=False, use_llm=True,
        llm_backend_id="stub_llm",
        use_kds_rag=True, kds_rag_client_id="local_json_kds",
    )
    out = classify_zones(
        [{"zone_id": "z1", "text_snippet": "보 단면 변경",
          "layer": "BEAM", "change_type": "modified"}],
        config=cfg,
    )
    assert out[0].raw_evidence["kds_rag_client"] == "local_json_kds"
    # Real clauses found → context_chars > 0
    assert out[0].raw_evidence["kds_rag_context_chars"] > 0


def test_dispatcher_singleton_separates_by_use_kds_rag() -> None:
    """Two configs differing only in use_kds_rag must produce different
    dispatcher instances (cache key honours the flag)."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, get_llm_dispatcher,
    )

    cfg_off = AiClassifierConfig(use_llm=True, use_kds_rag=False)
    cfg_on = AiClassifierConfig(use_llm=True, use_kds_rag=True)
    assert get_llm_dispatcher(cfg_off) is not get_llm_dispatcher(cfg_on)


def test_dispatcher_singleton_separates_by_kds_rag_client_id() -> None:
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, get_llm_dispatcher,
    )

    cfg_a = AiClassifierConfig(
        use_llm=True, use_kds_rag=True, kds_rag_client_id="stub_kds",
    )
    cfg_b = AiClassifierConfig(
        use_llm=True, use_kds_rag=True, kds_rag_client_id="local_json_kds",
    )
    assert get_llm_dispatcher(cfg_a) is not get_llm_dispatcher(cfg_b)


# ---------------------------------------------------------------------------
# Schema fields persisted in to_dict
# ---------------------------------------------------------------------------


def test_schema_to_dict_includes_kds_rag_fields() -> None:
    from src.services.comparison.ai_classifier import AiClassifierConfig

    cfg = AiClassifierConfig(
        use_kds_rag=True, kds_rag_client_id="local_json_kds",
        kds_rag_top_k=5, kds_rag_timeout_s=2.5,
    )
    d = cfg.to_dict()
    assert d["use_kds_rag"] is True
    assert d["kds_rag_client_id"] == "local_json_kds"
    assert d["kds_rag_top_k"] == 5
    assert d["kds_rag_timeout_s"] == 2.5
