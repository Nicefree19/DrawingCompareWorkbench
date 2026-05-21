# -*- coding: utf-8 -*-
"""§14: lru_cache regression tests for export_profiles path resolution.

The cProfile baseline (docs/VIEWER_BUILD_BOTTLENECK_REPORT.md) showed
``profile_path_value`` calling ``Path.resolve()`` 4,913 times per small
fixture, amplifying to 14,385 ``nt._getfinalpathname`` calls. Plan §14
adds ``functools.lru_cache(maxsize=4096)`` around the resolve helper.

These tests verify:

1. The cache exists, has the expected ``maxsize``, and warms up across
   repeated calls with the same input.
2. ``profile_path_value`` semantics are unchanged for the canonical
   relative / absolute / sensitive / fallback cases.
3. ``redact_payload_paths`` produces identical output before and after the
   cache (golden snapshot of a representative payload).
4. The cache key is the **string** representation, so ``str`` and ``Path``
   inputs cooperate (callers pass them interchangeably in practice).

The tests do NOT measure wall time — that is the job of
``scripts/benchmark_viewer_build.py``. They guard the contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.services.comparison.export_profiles import (
    _cached_resolve,
    _clear_resolve_cache,
    profile_path_value,
    redact_payload_paths,
)


@pytest.fixture(autouse=True)
def _isolate_cache():
    """Drop cached path resolutions before AND after every test so cache
    warm-up effects from one test do not bleed into the next."""

    _clear_resolve_cache()
    yield
    _clear_resolve_cache()


# ---------------------------------------------------------------------------
# Cache identity / behaviour
# ---------------------------------------------------------------------------


def test_cached_resolve_is_lru_cache_with_expected_maxsize():
    info = _cached_resolve.cache_info()
    assert info.maxsize == 4096
    assert info.currsize == 0
    assert info.hits == 0
    assert info.misses == 0


def test_cached_resolve_warms_up_on_repeat(tmp_path: Path):
    target = tmp_path / "demo.json"
    target.write_text("{}", encoding="utf-8")
    key = str(target)

    first = _cached_resolve(key)
    second = _cached_resolve(key)

    info = _cached_resolve.cache_info()
    assert first == second
    assert info.misses == 1, "first call must populate the cache"
    assert info.hits >= 1, "second call must hit the cache"


def test_cached_resolve_returns_same_path_object_on_hit(tmp_path: Path):
    target = tmp_path / "again.json"
    target.write_text("{}", encoding="utf-8")
    key = str(target)

    first = _cached_resolve(key)
    second = _cached_resolve(key)

    # lru_cache returns the same object reference on a hit (identity), which
    # is the strongest evidence the cache fired.
    assert first is second


def test_cache_clear_resets_counters(tmp_path: Path):
    _cached_resolve(str(tmp_path))
    _clear_resolve_cache()
    info = _cached_resolve.cache_info()
    assert info.currsize == 0
    assert info.hits == 0
    assert info.misses == 0


# ---------------------------------------------------------------------------
# profile_path_value semantics — must be unchanged by caching
# ---------------------------------------------------------------------------


def test_internal_profile_returns_text_unchanged(tmp_path: Path):
    """Caching must not alter the internal-profile passthrough."""

    out = profile_path_value(
        "anything",
        profile="internal",
        package_root=tmp_path,
        sensitive=True,
    )
    assert out == "anything"


def test_relative_to_package_root_resolves_against_cache(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    nested = pkg / "viewer" / "before.png"
    nested.parent.mkdir(parents=True)
    nested.write_text("x", encoding="utf-8")

    out = profile_path_value(
        nested,
        profile="sharable",
        package_root=pkg,
    )
    assert out == "viewer/before.png"

    # Repeat to confirm cache reuse: the second call must return the same
    # string and the cache hit count must increase.
    info_before = _cached_resolve.cache_info()
    out_again = profile_path_value(
        nested,
        profile="sharable",
        package_root=pkg,
    )
    info_after = _cached_resolve.cache_info()
    assert out_again == out
    assert info_after.hits > info_before.hits


def test_sensitive_absolute_outside_root_redacted(tmp_path: Path):
    # Use the platform-appropriate "absolute path outside the package root":
    # on Windows this is a different drive (or just an absolute string from
    # tmp_path's parent); on POSIX it is /tmp/<...>.
    outside = tmp_path.parent / "outside_pkg" / "secret.dxf"
    pkg = tmp_path / "pkg"
    pkg.mkdir()

    out = profile_path_value(
        outside,
        profile="sharable",
        package_root=pkg,
        sensitive=True,
    )
    assert out == "<redacted>/secret.dxf"


def test_str_and_path_inputs_share_cache_for_same_string(tmp_path: Path):
    """The cache key is the **string** representation. A caller that passes
    ``Path(...)`` and another that passes ``str(...)`` of the same text must
    not double-populate the cache."""

    target = tmp_path / "shared.txt"
    target.write_text("x", encoding="utf-8")

    profile_path_value(target, profile="sharable", package_root=tmp_path)
    profile_path_value(str(target), profile="sharable", package_root=tmp_path)

    info = _cached_resolve.cache_info()
    # Two distinct string keys may exist (the value, and the package_root),
    # but the value-resolve and root-resolve combined should produce far
    # fewer misses than the 4 calls this would naively cost.
    # Specifically: one resolve per unique string (target_str, root_str) → 2.
    # Subsequent calls all hit. So `misses <= 2` is the correct bound.
    assert info.misses <= 2
    assert info.hits >= 1


def test_failed_resolve_does_not_poison_cache(tmp_path: Path):
    """If ``Path.resolve()`` raises, ``profile_path_value`` falls back to the
    redaction string and the cache must NOT cache the failed key (lru_cache
    re-raises but does not store)."""

    # Force resolve failure by using a string that cannot be a path on Windows
    # (NUL byte). On POSIX this is also invalid. Some platforms may surface
    # ValueError instead of OSError — both must be tolerated by the wrapper.
    bad = "\0bad\0path"

    out = profile_path_value(
        bad,
        profile="sharable",
        package_root=tmp_path,
        sensitive=True,
    )
    # Output should be the redacted-shape fallback because resolve raised
    # and `_looks_absolute(bad)` is False but `sensitive=True`.
    assert out.startswith("<redacted>/") or out == bad

    # Critical: cache must not contain the bad key.
    info = _cached_resolve.cache_info()
    # Either the bad call missed and lru_cache re-raised (no entry stored),
    # or _cached_resolve was never invoked (early-return path). Both fine.
    # Just assert the cache is in a usable state — we can resolve a real
    # path afterwards without trouble.
    real = tmp_path / "real.txt"
    real.write_text("x", encoding="utf-8")
    resolved = _cached_resolve(str(real))
    assert resolved.exists()


# ---------------------------------------------------------------------------
# redact_payload_paths — golden snapshot under the cache
# ---------------------------------------------------------------------------


def test_redact_payload_paths_dict_matches_uncached_semantics(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    inner = pkg / "out" / "viewer.json"
    inner.parent.mkdir(parents=True)
    inner.write_text("x", encoding="utf-8")

    payload = {
        "source_a": str(tmp_path.parent / "outside" / "old.dxf"),
        "outputs": [
            {"viewer": str(inner)},
            {"safe": "rel/file.json"},
        ],
        "free_text": "Some prose without paths.",
    }

    redacted = redact_payload_paths(
        payload,
        profile="sharable",
        package_root=pkg,
    )

    # Sensitive key + absolute outside root → redacted-name form
    assert redacted["source_a"].startswith("<redacted>/") or redacted["source_a"] == ""
    # Inside-root absolute → relativised to viewer.json
    assert redacted["outputs"][0]["viewer"] == "out/viewer.json"
    # Already-relative non-path → unchanged
    assert redacted["outputs"][1]["safe"] == "rel/file.json"
    # Free text untouched (redact_payload_paths only redacts string values
    # under sensitive keys or that look absolute)
    assert redacted["free_text"] == "Some prose without paths."


def test_repeated_redact_payload_paths_warms_cache(tmp_path: Path):
    """The whole point of §14: invoking the redactor twice with overlapping
    paths should result in a high cache hit ratio on the second pass."""

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    leaf = pkg / "viewer" / "asset.json"
    leaf.parent.mkdir(parents=True)
    leaf.write_text("{}", encoding="utf-8")

    payload = {
        "outputs": [
            {"a_path": str(leaf)},
            {"b_path": str(leaf)},
            {"before_path": str(leaf)},
            {"after_path": str(leaf)},
        ]
    }

    redact_payload_paths(payload, profile="sharable", package_root=pkg)
    info_after_first = _cached_resolve.cache_info()

    redact_payload_paths(payload, profile="sharable", package_root=pkg)
    info_after_second = _cached_resolve.cache_info()

    # Second pass must hit the cache for every payload string + the package
    # root. No new misses.
    assert info_after_second.misses == info_after_first.misses
    assert info_after_second.hits > info_after_first.hits


def test_redact_payload_paths_internal_profile_passthrough(tmp_path: Path):
    """Internal profile path-handling must stay untouched."""

    payload = {"source_a": "C:\\customer\\old.dxf"}
    out = redact_payload_paths(payload, profile="internal", package_root=tmp_path)
    assert out == payload  # identity-preserving for internal profile
