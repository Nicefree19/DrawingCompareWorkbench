# -*- coding: utf-8 -*-
"""Phase J Step 5 (J2) — Stage-3 LLM cascade tests.

Pins the dispatcher + cascade behaviour we just shipped:

  Backend protocol contract:
  * StubLlmBackend conforms to LlmBackend protocol
  * Stub picks first candidate as answer, confidence=0.5
  * AbstractLlmBackend.classify catches all backend exceptions and
    returns None (abstain) — never raises into public_api

  Ollama HTTP contract (mock-only — no real Ollama needed):
  * probe_available returns False without ``requests``
  * probe_available returns False when /api/tags 404
  * Successful /api/generate → LlmClassificationResult parsed
  * /api/generate timeout → None (abstain)
  * /api/generate JSON parse failure → None
  * LLM picks category outside candidates → None (safety)

  Dispatcher (LlmClassifierDispatcher):
  * should_invoke True when Stage-2 abstained (classifier_used != "embedding")
  * should_invoke True when Stage-2 confidence < llm_invoke_below_confidence
  * should_invoke False when Stage-2 above threshold
  * classify_zone returns ChangeClassification(classifier_used="hybrid")
  * raw_evidence carries Stage-1 + Stage-2 + LLM metadata
  * Cache key separates by backend_id + host + model

  Public API cascade:
  * use_llm=False → no LLM dispatch, Stage-2 result preserved
  * use_llm=True + Stage-2 abstain → LLM tries; success → "hybrid"
  * use_llm=True + Stage-2 confident → LLM skipped (should_invoke=False)
  * Stage-3 abstain → keeps Stage-2 result
  * No exception propagates to public_api caller (all crashes
    swallowed, lower-tier result kept)
"""

from __future__ import annotations

import json
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from src.services.comparison.ai_classifier.schema import ChangeCategory


# ---------------------------------------------------------------------------
# Toy LLM backend used by dispatcher / cascade tests
# ---------------------------------------------------------------------------


def _register_toy_llm(
    backend_id: str = "toy_llm",
    *,
    available: bool = True,
    answer_idx: int = 0,
    confidence: float = 0.8,
    rationale: str = "(toy) deterministic",
    raise_on_classify: bool = False,
):
    """Build + register a toy LLM backend with controllable answer."""
    from src.services.comparison.ai_classifier.llm_backends import (
        register_llm_backend,
    )
    from src.services.comparison.ai_classifier.llm_backends.base import (
        AbstractLlmBackend, LlmClassificationResult,
    )

    _avail = available

    class _Toy(AbstractLlmBackend):
        backend_id_local = backend_id
        model_name = "toy-v1"

        def __init__(self):
            super().__init__()
            self.backend_id = backend_id

        @classmethod
        def probe_available(cls):
            return _avail

        def _warmup_impl(self):
            return  # no-op

        def _classify_impl(self, evidence, candidates, *, kds_context, timeout_s):
            if raise_on_classify:
                raise RuntimeError("forced for test")
            if not candidates:
                return None
            idx = min(answer_idx, len(candidates) - 1)
            return LlmClassificationResult(
                category=candidates[idx],
                confidence=confidence,
                rationale_ko=rationale,
                kds_references=[],
                elapsed_ms=1.0,
            )

    register_llm_backend(backend_id, lambda **kw: _Toy(), replace=True)


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
# StubLlmBackend protocol conformance
# ---------------------------------------------------------------------------


def test_stub_llm_satisfies_protocol() -> None:
    from src.services.comparison.ai_classifier import (
        LlmBackend, get_llm_backend,
    )

    stub = get_llm_backend("stub_llm")
    assert isinstance(stub, LlmBackend)
    assert stub.backend_id == "stub_llm"


def test_stub_llm_picks_first_candidate() -> None:
    from src.services.comparison.ai_classifier import get_llm_backend

    stub = get_llm_backend("stub_llm")
    stub.warmup()
    result = stub.classify(
        zone_evidence="보 단면 변경",
        candidate_categories=[
            ChangeCategory.STRUCTURAL_MEMBER,
            ChangeCategory.DIMENSION,
        ],
    )
    assert result is not None
    assert result.category == ChangeCategory.STRUCTURAL_MEMBER
    assert result.confidence == 0.5
    assert "(stub)" in result.rationale_ko


def test_stub_llm_abstains_on_empty_inputs() -> None:
    from src.services.comparison.ai_classifier import get_llm_backend

    stub = get_llm_backend("stub_llm")
    assert stub.classify("", [ChangeCategory.STRUCTURAL_MEMBER]) is None
    assert stub.classify("text", []) is None


def test_stub_llm_probe_always_true() -> None:
    from src.services.comparison.ai_classifier.llm_backends.stub_llm import (
        StubLlmBackend,
    )
    assert StubLlmBackend.probe_available() is True


# ---------------------------------------------------------------------------
# AbstractLlmBackend safety contract
# ---------------------------------------------------------------------------


def test_abstract_classify_swallows_subclass_exceptions() -> None:
    """If _classify_impl raises, the public classify must return None
    (NEVER propagate) so the cascade keeps Stage-2 result."""

    _register_toy_llm("crash_test", raise_on_classify=True)
    from src.services.comparison.ai_classifier import get_llm_backend

    crash = get_llm_backend("crash_test")
    crash.warmup()
    result = crash.classify(
        "보 단면",
        [ChangeCategory.STRUCTURAL_MEMBER],
    )
    assert result is None


# ---------------------------------------------------------------------------
# Ollama backend — mock HTTP
# ---------------------------------------------------------------------------


def test_ollama_probe_returns_false_without_requests(monkeypatch) -> None:
    """When ``requests`` package is missing, probe must return False
    without raising ImportError."""
    import importlib.util as _iu
    real_find = _iu.find_spec

    def _stub_find(name, *a, **kw):
        if name == "requests":
            return None
        return real_find(name, *a, **kw)

    monkeypatch.setattr(_iu, "find_spec", _stub_find)
    from src.services.comparison.ai_classifier.llm_backends.ollama_exaone import (
        OllamaExaoneLlmBackend,
    )
    assert OllamaExaoneLlmBackend.probe_available() is False


def test_ollama_warmup_raises_when_requests_missing(monkeypatch) -> None:
    """_warmup_impl must raise LlmBackendUnavailableError (not
    ImportError) when requests can't be imported."""
    from src.services.comparison.ai_classifier import LlmBackendUnavailableError
    from src.services.comparison.ai_classifier.llm_backends.ollama_exaone import (
        OllamaExaoneLlmBackend,
    )
    import builtins

    real_import = builtins.__import__

    def _stub_import(name, *args, **kwargs):
        if name == "requests":
            raise ImportError("forced for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _stub_import)
    backend = OllamaExaoneLlmBackend()
    with pytest.raises(LlmBackendUnavailableError, match="requests 미설치"):
        backend._warmup_impl()


def test_ollama_warmup_raises_when_model_not_pulled(monkeypatch) -> None:
    """When /api/tags returns 200 but model isn't in the list, warmup
    must raise with installation hint."""
    from src.services.comparison.ai_classifier import LlmBackendUnavailableError
    from src.services.comparison.ai_classifier.llm_backends.ollama_exaone import (
        OllamaExaoneLlmBackend,
    )

    fake_requests = MagicMock()
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"models": [{"name": "llama3.2:3b"}]}
    fake_requests.get.return_value = fake_resp

    monkeypatch.setattr(
        "src.services.comparison.ai_classifier.llm_backends.ollama_exaone.importlib.util.find_spec",
        lambda name: True if name == "requests" else None,
    )
    monkeypatch.setitem(
        __import__("sys").modules, "requests", fake_requests,
    )

    backend = OllamaExaoneLlmBackend()
    with pytest.raises(LlmBackendUnavailableError, match="ollama pull"):
        backend._warmup_impl()


def test_ollama_classify_parses_json_response(monkeypatch) -> None:
    """Successful /api/generate returns JSON-wrapped result; backend
    must parse + map back to ChangeCategory."""
    from src.services.comparison.ai_classifier.llm_backends.ollama_exaone import (
        OllamaExaoneLlmBackend,
    )

    fake_requests = MagicMock()
    # /api/tags during warmup
    tags_resp = MagicMock()
    tags_resp.status_code = 200
    tags_resp.json.return_value = {"models": [{"name": "exaone3.5:7.8b"}]}
    fake_requests.get.return_value = tags_resp
    # /api/generate during classify
    gen_resp = MagicMock()
    gen_resp.status_code = 200
    gen_resp.json.return_value = {
        "response": json.dumps({
            "category": "structural_member",
            "confidence": 0.9,
            "rationale": "보 단면 변경 — 구조부재",
        }),
    }
    fake_requests.post.return_value = gen_resp

    monkeypatch.setitem(
        __import__("sys").modules, "requests", fake_requests,
    )

    backend = OllamaExaoneLlmBackend()
    backend._warmup_impl()
    result = backend._classify_impl(
        "보 단면 변경",
        [ChangeCategory.STRUCTURAL_MEMBER, ChangeCategory.DIMENSION],
        kds_context="",
        timeout_s=10.0,
    )
    assert result is not None
    assert result.category == ChangeCategory.STRUCTURAL_MEMBER
    assert result.confidence == 0.9
    assert "구조부재" in result.rationale_ko


def test_ollama_classify_abstains_on_out_of_candidates(monkeypatch) -> None:
    """If the LLM picks a category that's NOT in the candidate list,
    abstain (safety boundary against hallucination)."""
    from src.services.comparison.ai_classifier.llm_backends.ollama_exaone import (
        OllamaExaoneLlmBackend,
    )

    fake_requests = MagicMock()
    tags_resp = MagicMock()
    tags_resp.status_code = 200
    tags_resp.json.return_value = {"models": [{"name": "exaone3.5:7.8b"}]}
    fake_requests.get.return_value = tags_resp
    # LLM picks UNKNOWN but candidates are STRUCTURAL_MEMBER + DIMENSION
    gen_resp = MagicMock()
    gen_resp.status_code = 200
    gen_resp.json.return_value = {
        "response": json.dumps({
            "category": "unknown",  # NOT in candidates
            "confidence": 0.7,
            "rationale": "잘 모르겠음",
        }),
    }
    fake_requests.post.return_value = gen_resp
    monkeypatch.setitem(
        __import__("sys").modules, "requests", fake_requests,
    )

    backend = OllamaExaoneLlmBackend()
    backend._warmup_impl()
    result = backend._classify_impl(
        "보 단면 변경",
        [ChangeCategory.STRUCTURAL_MEMBER, ChangeCategory.DIMENSION],
        kds_context="",
        timeout_s=10.0,
    )
    assert result is None  # abstain


def test_ollama_classify_abstains_on_garbage_json(monkeypatch) -> None:
    """LLM returns non-JSON gibberish → abstain (don't crash)."""
    from src.services.comparison.ai_classifier.llm_backends.ollama_exaone import (
        OllamaExaoneLlmBackend,
    )

    fake_requests = MagicMock()
    tags_resp = MagicMock()
    tags_resp.status_code = 200
    tags_resp.json.return_value = {"models": [{"name": "exaone3.5:7.8b"}]}
    fake_requests.get.return_value = tags_resp
    gen_resp = MagicMock()
    gen_resp.status_code = 200
    gen_resp.json.return_value = {"response": "this is not json at all"}
    fake_requests.post.return_value = gen_resp
    monkeypatch.setitem(
        __import__("sys").modules, "requests", fake_requests,
    )

    backend = OllamaExaoneLlmBackend()
    backend._warmup_impl()
    result = backend._classify_impl(
        "보", [ChangeCategory.STRUCTURAL_MEMBER],
        kds_context="", timeout_s=5.0,
    )
    assert result is None


def test_ollama_classify_handles_markdown_fenced_json(monkeypatch) -> None:
    """LLMs sometimes wrap JSON in ``` fences — extractor must cope."""
    from src.services.comparison.ai_classifier.llm_backends.ollama_exaone import (
        OllamaExaoneLlmBackend, _extract_first_json,
    )

    # Fenced
    text = '```json\n{"category": "grid", "confidence": 0.7}\n```'
    obj = _extract_first_json(text)
    assert obj == {"category": "grid", "confidence": 0.7}

    # Prose-wrapped (LLM explains before JSON)
    text2 = (
        '저는 다음과 같이 분류합니다:\n'
        '{"category": "dimension", "confidence": 0.85, "rationale": "치수 변경"}\n'
        '이상입니다.'
    )
    obj2 = _extract_first_json(text2)
    assert obj2["category"] == "dimension"


def test_extract_first_json_handles_nested_braces_in_string_values() -> None:
    """Phase L5 review fix (Issue #3): the previous brace-counting
    parser broke on strings containing `{` or `}` because the depth
    counter didn't track string context. Korean rationale fields
    naturally use braces for emphasis (e.g. {단면}).

    The new JSONDecoder.raw_decode-based parser delegates to the
    real JSON tokenizer, which handles string escaping + nested
    braces correctly.
    """
    from src.services.comparison.ai_classifier.llm_backends.ollama_exaone import (
        _extract_first_json,
    )

    # Korean rationale with literal {단면} braces — was broken before
    text1 = ('{"category": "structural_member", '
             '"rationale": "보의 {단면} 변경"}')
    obj1 = _extract_first_json(text1)
    assert obj1 is not None
    assert obj1["category"] == "structural_member"
    assert obj1["rationale"] == "보의 {단면} 변경"

    # Empty braces inside string
    text2 = '{"r": "before {} after"}'
    obj2 = _extract_first_json(text2)
    assert obj2 == {"r": "before {} after"}

    # Multiple nested + escaped quotes inside the string
    text3 = '{"r": "값: {a:1, b:{c:2}} \\"끝\\""}'
    obj3 = _extract_first_json(text3)
    assert obj3 is not None
    assert "값" in obj3["r"]


def test_extract_first_json_finds_first_object_among_garbage() -> None:
    """Multiple JSON objects in the text — the parser returns the
    FIRST valid one (not the last)."""
    from src.services.comparison.ai_classifier.llm_backends.ollama_exaone import (
        _extract_first_json,
    )

    text = 'noise {invalid garbage} more text {"first": 1} trailing {"second": 2}'
    obj = _extract_first_json(text)
    assert obj == {"first": 1}


def test_extract_first_json_returns_none_for_no_json() -> None:
    from src.services.comparison.ai_classifier.llm_backends.ollama_exaone import (
        _extract_first_json,
    )

    assert _extract_first_json("hello world") is None
    assert _extract_first_json("") is None
    assert _extract_first_json("{not actually json}") is None


def test_local_json_kds_thread_safe_lazy_load(tmp_path) -> None:
    """Phase L5 review fix (Issue #4): concurrent retrieve() calls
    from parallel threads must NOT trigger redundant file reads.
    Verified by counting how many times the on-disk file is opened
    during 8 concurrent retrieve calls — should be exactly 1."""
    import json
    import threading
    from src.services.comparison.ai_classifier.kds_rag.local_json import (
        LocalJsonKdsRagClient,
    )
    from src.services.comparison.ai_classifier.schema import ChangeCategory

    # Write a real clauses file
    p = tmp_path / "kds.json"
    payload = {
        "version": "v1",
        "clauses": [{
            "code": "TEST", "section": "1", "title": "t",
            "category_hints": ["structural_member"],
            "keywords": ["보"], "text": "보 단면강도",
        }],
    }
    p.write_text(json.dumps(payload), encoding="utf-8")

    client = LocalJsonKdsRagClient(path=p)

    # Track how many times read_text is actually called by patching
    # at the Path-instance level via subclass-style monkey
    read_count = {"n": 0}
    original_read_text = type(p).read_text

    def counting_read_text(self, *args, **kwargs):
        read_count["n"] += 1
        return original_read_text(self, *args, **kwargs)

    # Use unittest.mock to patch Path.read_text on the specific instance
    from unittest.mock import patch
    with patch.object(type(p), "read_text", counting_read_text):
        # Spawn N threads all calling retrieve() on the same client
        results = []
        errors = []

        def worker():
            try:
                results.append(client.retrieve(
                    "보 변경", [ChangeCategory.STRUCTURAL_MEMBER],
                    top_k=1,
                ))
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

    assert not errors, f"thread errors: {errors}"
    assert len(results) == 8
    # All 8 calls returned the same result (TEST clause)
    assert all("TEST" in r for r in results), (
        f"unexpected results: {results}"
    )
    # CRITICAL: file read happened exactly once despite 8 concurrent calls
    assert read_count["n"] == 1, (
        f"expected 1 file read, got {read_count['n']} — lock didn't work"
    )


def test_dispatcher_kds_rag_client_thread_safe_lazy_init() -> None:
    """Phase L5 review fix (Issue #4): _get_kds_rag_client lazy init
    is also thread-safe — concurrent first calls produce ONE client
    instance, not multiple."""
    import threading
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, get_llm_dispatcher, clear_llm_dispatcher_cache,
    )

    clear_llm_dispatcher_cache()
    cfg = AiClassifierConfig(
        use_llm=True, use_kds_rag=True, llm_backend_id="stub_llm",
        kds_rag_client_id="stub_kds",
    )
    d = get_llm_dispatcher(cfg)

    # Capture the client instance from each thread
    seen_instances = []
    errors = []

    def worker():
        try:
            seen_instances.append(d._get_kds_rag_client())
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors, f"thread errors: {errors}"
    assert len(seen_instances) == 8
    # CRITICAL: all 8 threads see the same singleton instance
    first = seen_instances[0]
    assert all(inst is first for inst in seen_instances), (
        "lazy init produced multiple client instances — lock didn't work"
    )


# ---------------------------------------------------------------------------
# Dispatcher should_invoke gating
# ---------------------------------------------------------------------------


def test_dispatcher_should_invoke_when_stage2_abstained() -> None:
    """Stage-2 abstain (classifier_used="heuristic") → LLM should fire."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, ChangeClassification, Severity,
        get_llm_dispatcher,
    )

    cfg = AiClassifierConfig(
        use_llm=True, llm_backend_id="stub_llm",
        llm_invoke_below_confidence=0.85,
    )
    d = get_llm_dispatcher(cfg)
    s1_only = ChangeClassification(
        zone_id="z1", category=ChangeCategory.STRUCTURAL_MEMBER,
        severity=Severity.CRITICAL, confidence=0.9,
        suggested_action="review", summary_ko="...",
        classifier_used="heuristic",  # Stage-1 only
    )
    assert d.should_invoke(s1_only) is True


def test_dispatcher_should_invoke_when_stage2_low_confidence() -> None:
    """Stage-2 below threshold → LLM fires for disambiguation."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, ChangeClassification, Severity,
        get_llm_dispatcher,
    )

    cfg = AiClassifierConfig(
        use_llm=True, llm_backend_id="stub_llm",
        llm_invoke_below_confidence=0.85,
    )
    d = get_llm_dispatcher(cfg)
    s2 = ChangeClassification(
        zone_id="z1", category=ChangeCategory.STRUCTURAL_MEMBER,
        severity=Severity.CRITICAL, confidence=0.7,  # < 0.85
        suggested_action="review", summary_ko="...",
        classifier_used="embedding",
    )
    assert d.should_invoke(s2) is True


def test_dispatcher_should_skip_when_stage2_confident() -> None:
    """Stage-2 above threshold → LLM skipped (no round-trip)."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, ChangeClassification, Severity,
        get_llm_dispatcher,
    )

    cfg = AiClassifierConfig(
        use_llm=True, llm_backend_id="stub_llm",
        llm_invoke_below_confidence=0.85,
    )
    d = get_llm_dispatcher(cfg)
    s2 = ChangeClassification(
        zone_id="z1", category=ChangeCategory.STRUCTURAL_MEMBER,
        severity=Severity.CRITICAL, confidence=0.95,  # >= 0.85
        suggested_action="review", summary_ko="...",
        classifier_used="embedding",
    )
    assert d.should_invoke(s2) is False


def test_dispatcher_should_skip_for_hybrid_result_no_recascade() -> None:
    """Phase L3 review fix (Issue #5): a result already through the
    cascade (classifier_used="hybrid") must NOT trigger Stage-3 again
    if accidentally fed back through public_api. Otherwise we'd get
    a re-classification loop."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, ChangeClassification, Severity,
        get_llm_dispatcher,
    )

    cfg = AiClassifierConfig(
        use_llm=True, llm_backend_id="stub_llm",
        llm_invoke_below_confidence=0.85,
    )
    d = get_llm_dispatcher(cfg)
    s_hybrid = ChangeClassification(
        zone_id="z1", category=ChangeCategory.STRUCTURAL_MEMBER,
        severity=Severity.CRITICAL, confidence=0.4,
        suggested_action="review", summary_ko="...",
        classifier_used="hybrid",  # already through Stage-3 once
    )
    assert d.should_invoke(s_hybrid) is False


def test_dispatcher_should_skip_for_disabled_or_error_results() -> None:
    """Phase L3 review fix (Issue #5): "disabled" / "error" results
    aren't useful as LLM input — skip to avoid wasted round-trips."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, ChangeClassification, Severity,
        get_llm_dispatcher,
    )

    cfg = AiClassifierConfig(
        use_llm=True, llm_backend_id="stub_llm",
    )
    d = get_llm_dispatcher(cfg)
    for used in ("disabled", "error", "future_unknown_value"):
        result = ChangeClassification(
            zone_id="z1", category=ChangeCategory.UNKNOWN,
            severity=Severity.NORMAL, confidence=0.0,
            suggested_action="review", summary_ko="...",
            classifier_used=used,
        )
        assert d.should_invoke(result) is False, (
            f"should NOT invoke LLM for classifier_used={used!r}"
        )


def test_ollama_probe_available_uses_passed_host_and_model(monkeypatch) -> None:
    """Phase L3 review fix (Bug #1): probe_available was hardcoded
    to DEFAULT_HOST regardless of caller — making it useless for
    remote-Ollama deployments. Now accepts host + model_name params
    so the GUI dialog can pass the user's configured values."""
    from src.services.comparison.ai_classifier.llm_backends.ollama_exaone import (
        OllamaExaoneLlmBackend,
    )
    from unittest.mock import MagicMock
    import importlib.util as _iu

    captured_urls = []

    fake_requests = MagicMock()

    def fake_get(url, **kwargs):
        captured_urls.append(url)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"models": [{"name": "custom-model:7b"}]}
        return resp

    fake_requests.get = fake_get
    monkeypatch.setitem(
        __import__("sys").modules, "requests", fake_requests,
    )
    # Stub find_spec so probe_available's importability check passes
    # (MagicMock'd modules don't have a real ModuleSpec).
    real_find = _iu.find_spec

    def _stub_find(name, *a, **kw):
        if name == "requests":
            return MagicMock()  # truthy spec
        return real_find(name, *a, **kw)

    monkeypatch.setattr(_iu, "find_spec", _stub_find)

    # Probe with custom host + custom model
    ok = OllamaExaoneLlmBackend.probe_available(
        host="http://10.0.0.5:11434",
        model_name="custom-model:7b",
    )
    assert ok is True
    # Verify the custom host was actually used
    assert any("10.0.0.5:11434" in u for u in captured_urls), (
        f"probe_available didn't honour custom host. URLs: {captured_urls}"
    )


# ---------------------------------------------------------------------------
# Dispatcher classify_zone — produces "hybrid" result
# ---------------------------------------------------------------------------


def test_dispatcher_classify_returns_hybrid_with_full_evidence() -> None:
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, ChangeClassification, Severity,
        get_llm_dispatcher,
    )

    cfg = AiClassifierConfig(
        use_llm=True, llm_backend_id="stub_llm",
        llm_invoke_below_confidence=0.85,
    )
    d = get_llm_dispatcher(cfg)
    s1 = ChangeClassification(
        zone_id="z1", category=ChangeCategory.STRUCTURAL_MEMBER,
        severity=Severity.CRITICAL, confidence=0.85,
        suggested_action="review", summary_ko="heuristic ans",
        classifier_used="heuristic",
    )
    s2 = ChangeClassification(
        zone_id="z1", category=ChangeCategory.DIMENSION,
        severity=Severity.NORMAL, confidence=0.6,
        suggested_action="review", summary_ko="embedding ans",
        classifier_used="embedding",
        raw_evidence={"top1_score": 0.6, "top2_score": 0.55},
    )
    zone = {
        "zone_id": "z1", "text_snippet": "보 단면 변경",
        "layer": "BEAM", "change_type": "modified",
    }
    result = d.classify_zone(
        zone, s1, s2,
        candidate_categories=[
            ChangeCategory.DIMENSION,
            ChangeCategory.STRUCTURAL_MEMBER,
        ],
    )
    assert result is not None
    assert result.classifier_used == "hybrid"
    # Stub LLM picks first candidate (DIMENSION here)
    assert result.category == ChangeCategory.DIMENSION
    # Evidence chain preserved
    raw = result.raw_evidence
    assert raw["stage1_category"] == "structural_member"
    assert raw["stage2_category"] == "dimension"
    assert raw["stage2_classifier"] == "embedding"
    assert raw["stage3_backend"] == "stub_llm"
    assert "(stub)" in raw["llm_rationale_ko"]


def test_dispatcher_classify_returns_none_when_backend_unavailable() -> None:
    """Unknown LLM backend ID → BackendUnavailableError on prepare →
    classify returns None (caller keeps Stage-2)."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, ChangeClassification, Severity,
        get_llm_dispatcher,
    )

    cfg = AiClassifierConfig(
        use_llm=True, llm_backend_id="definitely_not_a_backend",
    )
    d = get_llm_dispatcher(cfg)
    s1 = ChangeClassification(
        zone_id="z1", category=ChangeCategory.STRUCTURAL_MEMBER,
        severity=Severity.NORMAL, confidence=0.5,
        suggested_action="review", summary_ko="",
        classifier_used="heuristic",
    )
    result = d.classify_zone(
        {"zone_id": "z1", "text_snippet": "보"}, s1, s1,
        candidate_categories=[ChangeCategory.STRUCTURAL_MEMBER],
    )
    assert result is None


# ---------------------------------------------------------------------------
# Public API cascade — heuristic + LLM (no embedding)
# ---------------------------------------------------------------------------


def test_cascade_use_llm_false_skips_stage3() -> None:
    """When use_llm=False, the LLM dispatcher is never invoked."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, classify_zones,
    )

    cfg = AiClassifierConfig(
        use_llm=False, llm_backend_id="stub_llm",
    )
    out = classify_zones(
        [{"zone_id": "z1", "text_snippet": "보 단면 변경",
          "layer": "BEAM", "change_type": "modified"}],
        config=cfg,
    )
    assert len(out) == 1
    # Stage-3 NOT invoked → result stays as Stage-1 heuristic
    assert out[0].classifier_used == "heuristic"


def test_cascade_use_llm_true_with_stub_replaces_with_hybrid() -> None:
    """End-to-end: heuristic (Stage-1) + stub LLM (Stage-3) →
    classifier_used="hybrid" with rationale."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, classify_zones,
    )

    cfg = AiClassifierConfig(
        enabled=True, use_embedding=False, use_llm=True,
        llm_backend_id="stub_llm",
    )
    out = classify_zones(
        [{"zone_id": "z1", "text_snippet": "보 단면 변경",
          "layer": "BEAM", "change_type": "modified"}],
        config=cfg,
    )
    assert out[0].classifier_used == "hybrid"
    assert "(stub)" in out[0].raw_evidence["llm_rationale_ko"]


def test_cascade_keeps_stage2_when_llm_abstains() -> None:
    """LLM abstain → keep Stage-1 (or Stage-2) result unchanged."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, classify_zones,
    )

    _register_toy_llm("abstain_test", available=True, raise_on_classify=True)

    cfg = AiClassifierConfig(
        enabled=True, use_embedding=False, use_llm=True,
        llm_backend_id="abstain_test",
    )
    out = classify_zones(
        [{"zone_id": "z1", "text_snippet": "보 단면 변경",
          "layer": "BEAM", "change_type": "modified"}],
        config=cfg,
    )
    # LLM raised → abstained → Stage-1 heuristic stays
    assert out[0].classifier_used == "heuristic"


def test_cascade_skips_llm_when_stage2_confident(monkeypatch) -> None:
    """Confident Stage-2 → should_invoke=False → LLM dispatcher's
    classify_zone is NEVER called. Easier to verify by patching
    LlmClassifierDispatcher.classify_zone with a counter than to
    construct a perfect toy embedding chain."""

    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, ChangeClassification, Severity, classify_zones,
        clear_dispatcher_cache, clear_llm_dispatcher_cache,
    )
    from src.services.comparison.ai_classifier import (
        embedding_classifier as emb_mod,
    )
    from src.services.comparison.ai_classifier import llm_classifier as llm_mod

    clear_dispatcher_cache()
    clear_llm_dispatcher_cache()

    # Patch the embedding dispatcher's classify_zone so it deterministically
    # returns a high-confidence result without us needing a real backend
    # + corpus + manifest reconciliation.
    def fake_embedding_classify(self, zone):
        return ChangeClassification(
            zone_id=str(zone.get("zone_id") or ""),
            category=ChangeCategory.STRUCTURAL_MEMBER,
            severity=Severity.CRITICAL,
            confidence=0.95,  # ≥ llm_invoke_below_confidence
            suggested_action="review",
            summary_ko="(test) confident embedding answer",
            classifier_used="embedding",
            elapsed_ms=1.0,
            raw_evidence={
                "top1_score": 0.95, "top2_score": 0.4, "margin": 0.55,
                "top_categories": [
                    ("structural_member", 0.95),
                    ("dimension", 0.4),
                ],
            },
        )

    monkeypatch.setattr(
        emb_mod.EmbeddingClassifierDispatcher,
        "classify_zone", fake_embedding_classify,
    )
    # Force is_ready / prepare to no-ops so the patched classify_zone
    # path takes over completely.
    monkeypatch.setattr(
        emb_mod.EmbeddingClassifierDispatcher,
        "prepare", lambda self: setattr(self, "_prepared", True),
    )

    # Track LLM dispatcher invocations
    call_count = {"n": 0}
    real_classify = llm_mod.LlmClassifierDispatcher.classify_zone

    def counting_llm_classify(self, *args, **kwargs):
        call_count["n"] += 1
        return real_classify(self, *args, **kwargs)

    monkeypatch.setattr(
        llm_mod.LlmClassifierDispatcher,
        "classify_zone", counting_llm_classify,
    )

    cfg = AiClassifierConfig(
        enabled=True, use_embedding=True, use_llm=True,
        embedding_backend_id="auto",
        llm_backend_id="stub_llm",
        llm_invoke_below_confidence=0.85,
    )
    out = classify_zones(
        [{"zone_id": "z1", "text_snippet": "보 단면 변경",
          "layer": "BEAM", "change_type": "modified"}],
        config=cfg,
    )
    # Confident Stage-2 (0.95 ≥ 0.85) → LLM dispatcher.classify_zone
    # never called → no "hybrid" result
    assert call_count["n"] == 0
    assert out[0].classifier_used == "embedding"
    assert out[0].confidence == 0.95


# ---------------------------------------------------------------------------
# Singleton dispatcher cache
# ---------------------------------------------------------------------------


def test_get_llm_dispatcher_returns_same_instance_for_equal_config() -> None:
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, get_llm_dispatcher,
    )

    cfg = AiClassifierConfig(use_llm=True, llm_backend_id="stub_llm")
    d1 = get_llm_dispatcher(cfg)
    d2 = get_llm_dispatcher(cfg)
    assert d1 is d2


def test_get_llm_dispatcher_separates_by_backend_id() -> None:
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, get_llm_dispatcher,
    )

    cfg_a = AiClassifierConfig(use_llm=True, llm_backend_id="stub_llm")
    cfg_b = AiClassifierConfig(use_llm=True, llm_backend_id="ollama_exaone")
    assert get_llm_dispatcher(cfg_a) is not get_llm_dispatcher(cfg_b)


# ---------------------------------------------------------------------------
# Schema — hybrid_mode classmethod
# ---------------------------------------------------------------------------


def test_hybrid_mode_enables_all_three_tiers() -> None:
    from src.services.comparison.ai_classifier import AiClassifierConfig

    cfg = AiClassifierConfig.hybrid_mode()
    assert cfg.enabled is True
    assert cfg.use_embedding is True
    assert cfg.use_llm is True
    assert cfg.embedding_backend_id == "auto"
    # Default to stub for safety until Ollama is verified
    assert cfg.llm_backend_id == "stub_llm"
