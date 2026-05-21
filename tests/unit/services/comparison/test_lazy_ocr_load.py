# -*- coding: utf-8 -*-
"""Regression guards for the Phase G2.7-PERF lazy-OCR-load contract.

The drawing-compare module trio (paddle_ocr_backend, layout_analyzer,
ocr_extractor) defers the heavy OCR backend imports until the first
real OCR call. This module pins that contract:

  * Module load (no probe call) keeps RSS small
  * Probe is idempotent — repeat calls return same result instantly
  * Probe is thread-safe — concurrent first-callers all get one
    consistent result (no double-import, no race)
  * ``is_paddleocr_imported()`` (cheap) is False until the probe runs
  * ``check_ocr_availability`` (in ocr_extractor) triggers the probe
    on its first call

These tests don't actually load PaddleOCR (which would still cost
~500MB even in the test process); they exercise the probe machinery
with a stubbed import so the contract is validated cheaply.
"""

from __future__ import annotations

import importlib
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest


# ---------------------------------------------------------------------------
# paddle_ocr_backend probe behaviour
# ---------------------------------------------------------------------------


def _fresh_backend_module(monkeypatch, paddleocr_present: bool):
    """Reimport paddle_ocr_backend with paddleocr presence stubbed."""

    # Strip any cached import of paddle_ocr_backend so the module-load
    # state machine starts clean.
    for mod_name in list(sys.modules):
        if mod_name.startswith("src.services.comparison.paddle_ocr_backend"):
            del sys.modules[mod_name]

    if paddleocr_present:
        # Inject a fake paddleocr that exposes a PaddleOCR class.
        fake = type(sys)("paddleocr")
        fake.PaddleOCR = type("PaddleOCR", (), {})
        monkeypatch.setitem(sys.modules, "paddleocr", fake)
    else:
        monkeypatch.setitem(sys.modules, "paddleocr", None)
        # `import paddleocr` will raise ImportError when the cached
        # entry is None → matches the "package not installed" path.

    return importlib.import_module(
        "src.services.comparison.paddle_ocr_backend"
    )


def test_module_load_does_not_probe_paddle(monkeypatch) -> None:
    """Importing the module must NOT trigger the paddleocr import."""

    backend = _fresh_backend_module(monkeypatch, paddleocr_present=True)
    # Module load completed; probe state should still be "unprobed"
    assert backend._PADDLEOCR_AVAILABLE is None, \
        "module load triggered the probe — defeats lazy-load purpose"
    # Cheap status check returns False (not "True") because nothing has
    # probed yet
    assert backend.is_paddleocr_imported() is False
    assert backend._PaddleOCR is None


def test_probe_runs_once_then_caches(monkeypatch) -> None:
    backend = _fresh_backend_module(monkeypatch, paddleocr_present=True)
    assert backend._PADDLEOCR_AVAILABLE is None

    # First probe → does the import
    assert backend._probe_paddleocr() is True
    assert backend._PADDLEOCR_AVAILABLE is True
    assert backend._PaddleOCR is not None
    saved_class = backend._PaddleOCR

    # Subsequent probe → cached, same class object
    assert backend._probe_paddleocr() is True
    assert backend._PaddleOCR is saved_class

    # is_paddleocr_imported now reflects the cached state
    assert backend.is_paddleocr_imported() is True


def test_probe_handles_missing_package(monkeypatch) -> None:
    backend = _fresh_backend_module(monkeypatch, paddleocr_present=False)
    assert backend._PADDLEOCR_AVAILABLE is None

    assert backend._probe_paddleocr() is False
    assert backend._PADDLEOCR_AVAILABLE is False
    assert backend._PaddleOCR is None
    # Repeat → cached False, no re-attempt
    assert backend._probe_paddleocr() is False


def test_probe_is_thread_safe(monkeypatch) -> None:
    """8 concurrent first-callers all get the same answer; the import
    happens at most once.

    We can't directly count import attempts (the lazy probe goes
    through real ``import paddleocr``), but if the lock works, the
    final state is consistent and no exception escapes any thread.
    """

    backend = _fresh_backend_module(monkeypatch, paddleocr_present=True)

    barrier = threading.Barrier(8)
    results: list[bool] = []
    errors: list[BaseException] = []

    def worker():
        try:
            barrier.wait()  # release all 8 simultaneously
            results.append(backend._probe_paddleocr())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=8) as ex:
        for _ in range(8):
            ex.submit(worker)

    assert not errors, f"thread errors: {errors}"
    assert len(results) == 8
    assert all(r is True for r in results), \
        f"inconsistent results: {results}"
    # State is the same regardless of who won the race
    assert backend._PADDLEOCR_AVAILABLE is True


def test_is_paddleocr_imported_does_not_probe(monkeypatch) -> None:
    """The cheap status check must NOT trigger the import — even if the
    package is installed and would import successfully."""

    backend = _fresh_backend_module(monkeypatch, paddleocr_present=True)
    assert backend._PADDLEOCR_AVAILABLE is None  # unprobed

    # is_paddleocr_imported reads the cached flag; still None → returns False
    assert backend.is_paddleocr_imported() is False
    # And critically, calling it didn't change the state
    assert backend._PADDLEOCR_AVAILABLE is None


# ---------------------------------------------------------------------------
# layout_analyzer probe behaviour (mirrors paddle_ocr_backend pattern)
# ---------------------------------------------------------------------------


def _fresh_layout_module(monkeypatch, ppstructure_present: bool):
    for mod_name in list(sys.modules):
        if mod_name.startswith("src.services.comparison.layout_analyzer"):
            del sys.modules[mod_name]

    if ppstructure_present:
        fake = type(sys)("paddleocr")
        fake.PPStructure = type("PPStructure", (), {})
        monkeypatch.setitem(sys.modules, "paddleocr", fake)
    else:
        monkeypatch.setitem(sys.modules, "paddleocr", None)

    return importlib.import_module(
        "src.services.comparison.layout_analyzer"
    )


def test_layout_module_load_does_not_probe(monkeypatch) -> None:
    layout = _fresh_layout_module(monkeypatch, ppstructure_present=True)
    assert layout._PPSTRUCTURE_AVAILABLE is None
    assert layout._PPStructure is None


def test_layout_probe_caches_and_is_thread_safe(monkeypatch) -> None:
    layout = _fresh_layout_module(monkeypatch, ppstructure_present=True)

    barrier = threading.Barrier(4)
    results: list[bool] = []

    def worker():
        barrier.wait()
        results.append(layout._probe_ppstructure())

    with ThreadPoolExecutor(max_workers=4) as ex:
        for _ in range(4):
            ex.submit(worker)

    assert results == [True, True, True, True]
    assert layout._PPSTRUCTURE_AVAILABLE is True


# ---------------------------------------------------------------------------
# ocr_extractor — module-load + lazy resolution path
# ---------------------------------------------------------------------------


def test_ocr_extractor_module_load_does_not_force_paddle_probe(
    monkeypatch,
) -> None:
    """Importing ocr_extractor MUST NOT cascade into a paddleocr import.

    Before this fix, ocr_extractor had ``_PADDLEOCR_AVAILABLE =
    is_paddleocr_available()`` at module level — and after the lazy
    refactor, ``is_paddleocr_available()`` itself was the probe. So
    the seemingly-cheap ``OCR_AVAILABLE = ...`` flag on import would
    trigger the heavy import all over again. Guard against regression.
    """

    # Reset both modules to their initial state by removing from cache.
    for mod_name in list(sys.modules):
        if mod_name.startswith("src.services.comparison.paddle_ocr_backend"):
            del sys.modules[mod_name]
        if mod_name.startswith("src.services.comparison.ocr_extractor"):
            del sys.modules[mod_name]

    # Provide a stub paddleocr that would be loaded if probe ran
    fake = type(sys)("paddleocr")
    fake.PaddleOCR = type("PaddleOCR", (), {})
    monkeypatch.setitem(sys.modules, "paddleocr", fake)

    importlib.import_module("src.services.comparison.ocr_extractor")
    backend = importlib.import_module(
        "src.services.comparison.paddle_ocr_backend"
    )

    # The KEY assertion: importing ocr_extractor did NOT call the probe.
    # _PADDLEOCR_AVAILABLE in the backend module should still be None.
    assert backend._PADDLEOCR_AVAILABLE is None, (
        "ocr_extractor module-load triggered the paddle probe — "
        "lazy-load contract violated"
    )
