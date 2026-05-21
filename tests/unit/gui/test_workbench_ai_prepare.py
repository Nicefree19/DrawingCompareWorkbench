# -*- coding: utf-8 -*-
"""Phase I — Workbench AI prepare hook (kickoff + poll) tests.

These tests exercise the new workbench methods without spinning up
the full Workbench QObject:
  * ``_load_ai_config_v2`` — must return an AiClassifierConfig in
    auto mode (fallback chain: Qwen GGUF → mxbai ONNX).
  * ``_kickoff_ai_prepare_v2`` — must update lbl_status_v2 to
    "AI 분류기 준비 중…" and launch a daemon thread.
  * ``_poll_ai_prepare_v2`` — must update lbl_status_v2 to one of
    "✓ AI 준비 완료 (...)" or "⚠ AI 모델 미설치 — ...".
  * Defensive: stale callback after Workbench teardown must NOT
    crash (RuntimeError swallow on QObject access).

Pulling the methods off the Workbench class via __get__ binding lets
us run them against a tiny SimpleNamespace stub. We never need a
QApplication or a real QWidget — the methods only call setText()
and a getattr-guarded windowTitle() / hasattr() check.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# PySide6 mock setup — same pattern as test_drawing_compare_cache.py
# ---------------------------------------------------------------------------

_mocked_modules: list[str] = []
for _name in (
    "PySide6", "PySide6.QtWidgets", "PySide6.QtCore", "PySide6.QtGui",
    "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQml",
):
    if _name not in sys.modules:
        sys.modules[_name] = MagicMock()
        _mocked_modules.append(_name)


@pytest.fixture(scope="module", autouse=True)
def _restore_pyside6_after_module():
    """Pop the MagicMock entries this file installed."""
    yield
    for name in _mocked_modules:
        sys.modules.pop(name, None)


# ---------------------------------------------------------------------------
# Helpers — pull the bound method off the Workbench class directly
# ---------------------------------------------------------------------------


def _bind_workbench_method(method_name: str, instance):
    """Look up ``DrawingCompareWorkbenchV2.<method_name>`` and bind it
    to ``instance`` so we can call it without instantiating the full
    workbench QObject.

    V2 is the active workbench class — V1 (``DrawingCompareWorkbench``)
    is legacy and didn't get the Phase I AI prepare hooks.
    """

    # Import the class lazily so the PySide6 mocks take effect first
    from src.gui.drawing_compare_workbench import DrawingCompareWorkbenchV2
    return getattr(DrawingCompareWorkbenchV2, method_name).__get__(instance)


def _toy_workbench_stub(*, status_label_alive: bool = True):
    """SimpleNamespace mimicking the parts of Workbench the prepare
    methods touch."""

    label = MagicMock()
    if not status_label_alive:
        label.setText.side_effect = RuntimeError("widget deleted")
    obj = SimpleNamespace(
        windowTitle=MagicMock(return_value="DrawingCompare"),
        lbl_status_v2=label,
    )
    return obj


# Toy backend — registered into the real registry so the dispatcher
# can find it. Same pattern as test_ai_classifier_dual_backend.py.
def _make_toy(backend_id: str, *, available: bool = True):
    from src.services.comparison.ai_classifier.backends.base import (
        AbstractEmbeddingBackend,
    )
    from src.services.comparison.ai_classifier.backends import register_backend

    _avail = available

    class _Toy(AbstractEmbeddingBackend):
        native_dim = 4
        embedding_dim = 4
        model_sha256 = "0" * 64

        @classmethod
        def probe_available(cls) -> bool:
            return _avail

        def __init__(self) -> None:
            super().__init__()
            self.backend_id = backend_id

        def _load(self) -> None:
            return

        def _encode_impl(self, texts, *, normalize):
            return np.tile(np.array([1, 0, 0, 0], dtype=np.float32),
                           (len(texts), 1))

    register_backend(backend_id, lambda **kw: _Toy(), replace=True)
    return _Toy


@pytest.fixture(autouse=True)
def _reset_dispatcher_cache():
    from src.services.comparison.ai_classifier import clear_dispatcher_cache

    clear_dispatcher_cache()
    yield
    clear_dispatcher_cache()


# ---------------------------------------------------------------------------
# _load_ai_config_v2 — produces auto-mode config
# ---------------------------------------------------------------------------


def test_load_ai_config_returns_auto_mode() -> None:
    stub = _toy_workbench_stub()
    method = _bind_workbench_method("_load_ai_config_v2", stub)
    cfg = method()
    assert cfg.use_embedding is True
    assert cfg.embedding_backend_id == "auto"


# ---------------------------------------------------------------------------
# _kickoff_ai_prepare_v2 — happy path + skip when disabled
# ---------------------------------------------------------------------------


def test_kickoff_sets_status_text_and_starts_thread(tmp_path,
                                                      monkeypatch) -> None:
    """When AI is enabled and at least one backend is available, the
    kickoff must update lbl_status_v2 to "준비 중…" and launch a
    daemon thread that calls dispatcher.prepare()."""

    # Register a backend so auto-mode can resolve it
    _make_toy("kickoff_toy", available=True)

    # Override _load_ai_config_v2 to point at the toy backend + temp cache
    from src.services.comparison.ai_classifier import AiClassifierConfig

    def fake_cfg(self):
        return AiClassifierConfig(
            enabled=True, use_embedding=True,
            embedding_backend_id="auto",
            embedding_backend_fallbacks=["kickoff_toy"],
            cache_dir=str(tmp_path / "cache"),
        )

    stub = _toy_workbench_stub()
    stub._load_ai_config_v2 = fake_cfg.__get__(stub)
    # Bind _poll_ai_prepare_v2 too — kickoff schedules it via QTimer
    stub._poll_ai_prepare_v2 = _bind_workbench_method(
        "_poll_ai_prepare_v2", stub,
    )

    # Stub QTimer.singleShot so the poll doesn't actually fire
    captured_calls = []
    qt_core_mock = sys.modules["PySide6.QtCore"]
    qt_core_mock.QTimer = MagicMock()
    qt_core_mock.QTimer.singleShot = lambda ms, fn: captured_calls.append(
        (ms, fn)
    )

    # Patch QTimer reference in the workbench module (it imports at top)
    import src.gui.drawing_compare_workbench as wb
    monkeypatch.setattr(wb, "QTimer", qt_core_mock.QTimer)

    method = _bind_workbench_method("_kickoff_ai_prepare_v2", stub)
    method()

    # lbl_status_v2.setText was called with "준비 중…"
    stub.lbl_status_v2.setText.assert_called_with("AI 분류기 준비 중…")
    # And QTimer.singleShot was scheduled for the poll
    assert any(ms == 500 for ms, fn in captured_calls)
    # And a daemon thread was launched (best-effort — we wait briefly)
    thread = stub._ai_prepare_thread_v2
    thread.join(timeout=2.0)
    # Either the toy backend warmed up successfully (thread done) or
    # the prepare crashed (thread done with exception logged) — both
    # mean the thread terminated.
    assert not thread.is_alive()


def test_kickoff_skips_when_use_embedding_false(monkeypatch) -> None:
    """If AI is disabled in config, kickoff is a no-op — no thread,
    no setText call."""
    from src.services.comparison.ai_classifier import AiClassifierConfig

    def disabled_cfg(self):
        return AiClassifierConfig(use_embedding=False)

    stub = _toy_workbench_stub()
    stub._load_ai_config_v2 = disabled_cfg.__get__(stub)
    qt_core_mock = sys.modules["PySide6.QtCore"]
    qt_core_mock.QTimer = MagicMock()
    import src.gui.drawing_compare_workbench as wb
    monkeypatch.setattr(wb, "QTimer", qt_core_mock.QTimer)

    method = _bind_workbench_method("_kickoff_ai_prepare_v2", stub)
    method()

    stub.lbl_status_v2.setText.assert_not_called()


def test_kickoff_no_op_when_dispatcher_already_ready(tmp_path,
                                                       monkeypatch) -> None:
    """If the dispatcher is already prepared, kickoff doesn't repeat
    the warmup or change the status label."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, get_embedding_dispatcher,
    )

    _make_toy("kickoff_already", available=True)

    def cfg(self):
        return AiClassifierConfig(
            enabled=True, use_embedding=True,
            embedding_backend_id="auto",
            embedding_backend_fallbacks=["kickoff_already"],
            cache_dir=str(tmp_path / "cache"),
        )

    # Pre-warm the dispatcher synchronously
    d = get_embedding_dispatcher(cfg(None))
    d.prepare()
    assert d.is_ready()

    stub = _toy_workbench_stub()
    stub._load_ai_config_v2 = cfg.__get__(stub)
    qt_core_mock = sys.modules["PySide6.QtCore"]
    qt_core_mock.QTimer = MagicMock()
    import src.gui.drawing_compare_workbench as wb
    monkeypatch.setattr(wb, "QTimer", qt_core_mock.QTimer)

    method = _bind_workbench_method("_kickoff_ai_prepare_v2", stub)
    method()
    stub.lbl_status_v2.setText.assert_not_called()


# ---------------------------------------------------------------------------
# _poll_ai_prepare_v2 — ready → success label, last_error → failure label
# ---------------------------------------------------------------------------


def test_poll_shows_success_label_when_ready(tmp_path) -> None:
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, get_embedding_dispatcher,
    )

    _make_toy("poll_ready", available=True)

    def cfg(self):
        return AiClassifierConfig(
            enabled=True, use_embedding=True,
            embedding_backend_id="auto",
            embedding_backend_fallbacks=["poll_ready"],
            cache_dir=str(tmp_path / "cache"),
        )

    # Get the dispatcher cached + warm it up
    d = get_embedding_dispatcher(cfg(None))
    d.prepare()

    stub = _toy_workbench_stub()
    stub._load_ai_config_v2 = cfg.__get__(stub)
    method = _bind_workbench_method("_poll_ai_prepare_v2", stub)
    method()

    # The text starts with "✓ AI 준비 완료" and includes the backend ID
    args, _ = stub.lbl_status_v2.setText.call_args
    text = args[0]
    assert text.startswith("✓ AI 준비 완료")
    assert "poll_ready" in text


def test_poll_shows_failure_label_when_last_error(tmp_path) -> None:
    """When the dispatcher's last_error is set (warmup raised), the
    poll updates the label to the 미설치 message."""
    from src.services.comparison.ai_classifier import (
        AiClassifierConfig, get_embedding_dispatcher,
    )

    # No backend registered → auto fails
    def cfg(self):
        return AiClassifierConfig(
            enabled=True, use_embedding=True,
            embedding_backend_id="auto",
            embedding_backend_fallbacks=["nonexistent_backend"],
            cache_dir=str(tmp_path / "cache"),
        )

    # Trigger a failed prepare so last_error is set
    d = get_embedding_dispatcher(cfg(None))
    with pytest.raises(Exception):
        d.prepare()
    assert d.last_error() is not None

    stub = _toy_workbench_stub()
    stub._load_ai_config_v2 = cfg.__get__(stub)
    method = _bind_workbench_method("_poll_ai_prepare_v2", stub)
    method()

    args, _ = stub.lbl_status_v2.setText.call_args
    assert "미설치" in args[0]
    assert "휴리스틱" in args[0]


# ---------------------------------------------------------------------------
# Defensive — stale callbacks after teardown don't crash
# ---------------------------------------------------------------------------


def test_kickoff_swallows_runtime_error_on_dead_workbench(monkeypatch) -> None:
    """When the underlying QObject is gone, ``self.windowTitle()``
    raises RuntimeError. The kickoff must early-return cleanly."""
    stub = _toy_workbench_stub()
    stub.windowTitle = MagicMock(side_effect=RuntimeError("deleted"))

    method = _bind_workbench_method("_kickoff_ai_prepare_v2", stub)
    method()  # no crash
    stub.lbl_status_v2.setText.assert_not_called()


def test_poll_swallows_runtime_error_on_dead_workbench() -> None:
    stub = _toy_workbench_stub()
    stub.windowTitle = MagicMock(side_effect=RuntimeError("deleted"))

    method = _bind_workbench_method("_poll_ai_prepare_v2", stub)
    method()  # no crash
    stub.lbl_status_v2.setText.assert_not_called()


def test_kickoff_swallows_setText_runtime_error(monkeypatch, tmp_path) -> None:
    """If lbl_status_v2's underlying C++ object is dead, setText
    raises — the kickoff must catch and continue (not propagate)."""
    from src.services.comparison.ai_classifier import AiClassifierConfig

    _make_toy("kickoff_dead_label", available=True)

    def cfg(self):
        return AiClassifierConfig(
            enabled=True, use_embedding=True,
            embedding_backend_id="auto",
            embedding_backend_fallbacks=["kickoff_dead_label"],
            cache_dir=str(tmp_path / "cache"),
        )

    stub = _toy_workbench_stub(status_label_alive=False)  # setText raises
    stub._load_ai_config_v2 = cfg.__get__(stub)
    qt_core_mock = sys.modules["PySide6.QtCore"]
    qt_core_mock.QTimer = MagicMock()
    import src.gui.drawing_compare_workbench as wb
    monkeypatch.setattr(wb, "QTimer", qt_core_mock.QTimer)

    method = _bind_workbench_method("_kickoff_ai_prepare_v2", stub)
    method()  # no crash
    # No daemon thread was started since setText failed first
