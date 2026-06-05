from __future__ import annotations

import subprocess

import pytest

from src.services.comparison import _process_cleanup as pc


def test_kill_process_tree_succeeds_on_taskkill_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pc.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
    )

    def _should_not_run(_pid: int) -> bool:
        raise AssertionError("terminate_process must not be called when taskkill succeeds")

    monkeypatch.setattr(pc, "terminate_process", _should_not_run)
    assert pc.kill_process_tree(123) is True


def test_kill_process_tree_falls_back_to_terminate_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pc.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="", stderr="denied"),
    )
    monkeypatch.setattr(pc, "terminate_process", lambda pid: pid == 200)
    assert pc.kill_process_tree(200) is True

    monkeypatch.setattr(pc, "terminate_process", lambda pid: False)
    assert pc.kill_process_tree(200) is False


def test_kill_process_tree_falls_back_when_taskkill_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(command, **kwargs):
        raise OSError("taskkill missing")

    monkeypatch.setattr(pc.subprocess, "run", boom)
    monkeypatch.setattr(pc, "terminate_process", lambda pid: True)
    assert pc.kill_process_tree(7) is True


def test_process_ids_for_image_tasklist_fallback_timeout_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pc.os, "name", "nt")
    monkeypatch.setattr(pc, "_process_ids_for_image_toolhelp", lambda image_name: None)

    def timeout_run(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(pc.subprocess, "run", timeout_run)
    assert pc.process_ids_for_image("ZWCAD.exe") == set()


def test_process_ids_for_image_parses_tasklist_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pc.os, "name", "nt")
    monkeypatch.setattr(pc, "_process_ids_for_image_toolhelp", lambda image_name: None)
    csv_out = '"ZWCAD.exe","4242","Console","1","100,000 K"\n"other.exe","99","Console","1","1 K"\n'
    monkeypatch.setattr(
        pc.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout=csv_out, stderr=""),
    )
    assert pc.process_ids_for_image("ZWCAD.exe") == {4242}


def test_process_ids_for_image_off_windows_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pc.os, "name", "posix")
    assert pc.process_ids_for_image("ZWCAD.exe") == set()
