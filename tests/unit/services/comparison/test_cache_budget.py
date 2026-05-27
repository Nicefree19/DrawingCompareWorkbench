# -*- coding: utf-8 -*-
from __future__ import annotations

from src.services.comparison.cache_budget import resolve_cache_byte_limit


def test_specific_cache_budget_env_takes_priority(monkeypatch) -> None:
    monkeypatch.setenv("DRAWING_COMPARE_RENDER_CACHE_MB", "32")
    monkeypatch.setenv("DRAWING_COMPARE_TEST_CACHE_MB", "4")

    assert resolve_cache_byte_limit(
        specific_env_var="DRAWING_COMPARE_TEST_CACHE_MB",
        default_mb=1,
    ) == 4 * 1024 * 1024


def test_shared_render_cache_budget_is_fallback(monkeypatch) -> None:
    monkeypatch.delenv("DRAWING_COMPARE_TEST_CACHE_MB", raising=False)
    monkeypatch.setenv("DRAWING_COMPARE_RENDER_CACHE_MB", "2.5")

    assert resolve_cache_byte_limit(
        specific_env_var="DRAWING_COMPARE_TEST_CACHE_MB",
        default_mb=1,
    ) == int(2.5 * 1024 * 1024)


def test_invalid_cache_budget_env_uses_default(monkeypatch) -> None:
    monkeypatch.setenv("DRAWING_COMPARE_TEST_CACHE_MB", "not-a-number")
    monkeypatch.setenv("DRAWING_COMPARE_RENDER_CACHE_MB", "0")

    assert resolve_cache_byte_limit(
        specific_env_var="DRAWING_COMPARE_TEST_CACHE_MB",
        default_mb=3,
    ) == 3 * 1024 * 1024
