# -*- coding: utf-8 -*-
"""Unit tests for S1.5 — once_per_session_logger.log_once helper."""

from __future__ import annotations

import logging
import threading

import pytest


@pytest.fixture(autouse=True)
def _reset_state():
    """Drop seen-key state so each test starts clean."""
    from src.utils.once_per_session_logger import reset_once_per_session_state

    reset_once_per_session_state()
    yield
    reset_once_per_session_state()


def test_first_call_logs_at_requested_level(caplog) -> None:
    """S1.5: the first call with a key emits at the requested level."""
    from src.utils.once_per_session_logger import log_once

    test_logger = logging.getLogger("test_s1_5_first")
    caplog.set_level(logging.INFO, logger="test_s1_5_first")

    result = log_once(test_logger, logging.INFO, "k1", "hello %s", "world")

    assert result is True
    matching = [r for r in caplog.records if r.message == "hello world"]
    assert len(matching) == 1
    assert matching[0].levelno == logging.INFO


def test_second_call_with_same_key_is_demoted_to_debug(caplog) -> None:
    """S1.5: same key → second call appears as DEBUG with throttled marker."""
    from src.utils.once_per_session_logger import log_once

    test_logger = logging.getLogger("test_s1_5_second")
    caplog.set_level(logging.DEBUG, logger="test_s1_5_second")

    log_once(test_logger, logging.INFO, "k1", "msg")
    result = log_once(test_logger, logging.INFO, "k1", "msg")

    assert result is False
    second = [r for r in caplog.records if "[throttled:k1]" in r.message]
    assert len(second) == 1
    assert second[0].levelno == logging.DEBUG


def test_different_keys_each_emit_at_requested_level(caplog) -> None:
    """S1.5: separate keys are independent — each gets the full level."""
    from src.utils.once_per_session_logger import log_once

    test_logger = logging.getLogger("test_s1_5_keys")
    caplog.set_level(logging.WARNING, logger="test_s1_5_keys")

    assert log_once(test_logger, logging.WARNING, "a", "first") is True
    assert log_once(test_logger, logging.WARNING, "b", "second") is True

    full_level = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(full_level) == 2


def test_reset_state_allows_first_call_again(caplog) -> None:
    """S1.5: reset_once_per_session_state drops accumulated keys."""
    from src.utils.once_per_session_logger import (
        log_once,
        reset_once_per_session_state,
    )

    test_logger = logging.getLogger("test_s1_5_reset")
    caplog.set_level(logging.INFO, logger="test_s1_5_reset")

    log_once(test_logger, logging.INFO, "k", "msg")  # first → INFO
    log_once(test_logger, logging.INFO, "k", "msg")  # throttled → DEBUG
    reset_once_per_session_state()
    result = log_once(test_logger, logging.INFO, "k", "msg")  # first again

    assert result is True


def test_empty_or_invalid_key_falls_through(caplog) -> None:
    """S1.5: defensive — degenerate key does not silently drop the record."""
    from src.utils.once_per_session_logger import log_once

    test_logger = logging.getLogger("test_s1_5_empty")
    caplog.set_level(logging.INFO, logger="test_s1_5_empty")

    assert log_once(test_logger, logging.INFO, "", "fallthrough") is True
    assert log_once(test_logger, logging.INFO, None, "also") is True  # type: ignore[arg-type]

    matching = [r for r in caplog.records if r.message in {"fallthrough", "also"}]
    assert len(matching) == 2


def test_log_once_is_thread_safe(caplog) -> None:
    """S1.5: concurrent first-call should still emit exactly once.

    20 threads race to call ``log_once`` with the same key. Exactly
    one must take the first-call branch (return True); the rest must
    be throttled (return False).
    """
    from src.utils.once_per_session_logger import log_once

    test_logger = logging.getLogger("test_s1_5_thread")
    caplog.set_level(logging.DEBUG, logger="test_s1_5_thread")

    results: list[bool] = []
    results_lock = threading.Lock()

    def worker():
        outcome = log_once(test_logger, logging.INFO, "shared", "racing")
        with results_lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for r in results if r is True) == 1
    assert sum(1 for r in results if r is False) == 19


def test_args_are_formatted_into_message(caplog) -> None:
    """S1.5: %-style positional args are passed through to logger.log."""
    from src.utils.once_per_session_logger import log_once

    test_logger = logging.getLogger("test_s1_5_args")
    caplog.set_level(logging.INFO, logger="test_s1_5_args")

    log_once(test_logger, logging.INFO, "fmt_key", "x=%s y=%d", "abc", 42)

    matching = [r for r in caplog.records if r.message == "x=abc y=42"]
    assert len(matching) == 1
