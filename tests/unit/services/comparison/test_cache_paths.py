# -*- coding: utf-8 -*-
"""Unit tests for the AppData-rooted cache path helper (Phase F P0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.services.comparison import cache_paths


def test_workbench_data_root_uses_localappdata_when_set(monkeypatch, tmp_path: Path) -> None:
    fake_local_app_data = tmp_path / "AppDataLocal"
    monkeypatch.setenv("LOCALAPPDATA", str(fake_local_app_data))
    root = cache_paths.workbench_data_root()
    assert root == fake_local_app_data / "DrawingCompareWorkbench"


def test_workbench_data_root_falls_back_to_home_when_no_localappdata(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    root = cache_paths.workbench_data_root()
    assert root == tmp_path / ".drawing_compare_workbench"


def test_workbench_data_root_does_not_create_directory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppDataLocal"))
    root = cache_paths.workbench_data_root()
    assert not root.exists(), "workbench_data_root() must be side-effect free"


def test_subdir_returns_relative_join(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    sub = cache_paths.subdir(cache_paths.SUBDIR_CACHE_VIEWER)
    assert sub == tmp_path / "DrawingCompareWorkbench" / "cache" / "viewer"


def test_subdir_rejects_absolute_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    with pytest.raises(ValueError, match="absolute"):
        cache_paths.subdir(str(tmp_path / "elsewhere"))


def test_subdir_rejects_empty_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    with pytest.raises(ValueError, match="non-empty"):
        cache_paths.subdir("")


def test_ensure_subdir_creates_directory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    sub = cache_paths.ensure_subdir(cache_paths.SUBDIR_CACHE_VIEWER)
    assert sub.exists() and sub.is_dir()


def test_ensure_subdir_is_idempotent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    p1 = cache_paths.ensure_subdir(cache_paths.SUBDIR_STATE)
    p2 = cache_paths.ensure_subdir(cache_paths.SUBDIR_STATE)
    assert p1 == p2 and p1.exists()


def test_convenience_helpers_all_inside_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    paths = [
        cache_paths.viewer_cache_dir(),
        cache_paths.normalize_cache_dir(),
        cache_paths.preview_cache_dir(),
        cache_paths.failure_cache_dir(),
        cache_paths.state_dir(),
        cache_paths.runs_dir(),
        cache_paths.temp_dir(),
    ]
    root = cache_paths.workbench_data_root()
    for p in paths:
        assert p.exists()
        # All must be under the workbench root — no leaks.
        assert root in p.parents or p == root


def test_is_inside_workbench_root_true_for_cache_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    cache_paths.viewer_cache_dir()  # materialise the root
    sub = cache_paths.subdir(cache_paths.SUBDIR_CACHE_VIEWER) / "x.png"
    assert cache_paths.is_inside_workbench_root(sub)


def test_is_inside_workbench_root_false_for_input_folder(
    monkeypatch, tmp_path: Path
) -> None:
    """The headline P0 invariant: caches MUST NOT leak into user input folders."""

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    cache_paths.viewer_cache_dir()
    foreign = tmp_path / "user_input_drawings" / "site_A.dxf"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text("dummy", encoding="utf-8")
    assert not cache_paths.is_inside_workbench_root(foreign)


def test_discover_legacy_flat_dirs_finds_existing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    root = cache_paths.workbench_data_root()
    (root / "dxf_cache").mkdir(parents=True, exist_ok=True)
    (root / "compare_state").mkdir(parents=True, exist_ok=True)
    legacy = cache_paths.discover_legacy_flat_dirs()
    legacy_names = {p.name for p in legacy}
    assert "dxf_cache" in legacy_names
    assert "compare_state" in legacy_names


def test_discover_legacy_flat_dirs_empty_when_no_root(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "absent"))
    assert cache_paths.discover_legacy_flat_dirs() == []
