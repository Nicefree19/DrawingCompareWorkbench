# -*- coding: utf-8 -*-
"""Stage hang watchdog: silent stalls must self-diagnose (2026-06-11)."""

from __future__ import annotations

import time
from pathlib import Path

from src.services.comparison.run_contract import RunManifestWriter
from src.services.comparison.stage_hang_watchdog import (
    StageHangWatchdog,
    resolve_hang_dump_timeout_s,
)


def _wait_for(predicate, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_watchdog_dumps_all_thread_stacks_on_stall(tmp_path: Path) -> None:
    with StageHangWatchdog(tmp_path, timeout_s=0.2) as dog:
        dog.pet("compare:running")
        assert _wait_for(lambda: dog.dump_path is not None), "dump never fired"
        dump = dog.dump_path.read_text(encoding="utf-8")
    assert "compare:running" in dump
    assert "Current thread" in dump or "Thread 0x" in dump  # faulthandler output
    # all_threads=True includes the watchdog's own _loop frame
    # (faulthandler prints file/function names, not thread names).
    assert "stage_hang_watchdog" in dump


def test_watchdog_stays_quiet_while_progress_flows(tmp_path: Path) -> None:
    with StageHangWatchdog(tmp_path, timeout_s=0.5) as dog:
        for i in range(6):
            dog.pet(f"stage{i}:running")
            time.sleep(0.1)
        assert dog.dump_path is None
    assert not list(tmp_path.glob("hang_stacks_*.log"))


def test_watchdog_rearms_after_pet_for_a_second_stall(tmp_path: Path) -> None:
    with StageHangWatchdog(tmp_path, timeout_s=0.2) as dog:
        dog.pet("first:running")
        assert _wait_for(lambda: dog.dump_path is not None)
        first = dog.dump_path
        dog.pet("second:running")  # re-arm
        assert _wait_for(lambda: dog.dump_path != first), "second stall not dumped"
    assert len(list(tmp_path.glob("hang_stacks_*.log"))) >= 1


def test_timeout_zero_disables_watchdog(tmp_path: Path) -> None:
    dog = StageHangWatchdog(tmp_path, timeout_s=0.0).start()
    time.sleep(0.15)
    dog.stop()
    assert dog.dump_path is None
    assert not list(tmp_path.glob("hang_stacks_*.log"))


def test_env_resolution(monkeypatch) -> None:
    monkeypatch.delenv("DRAWING_COMPARE_HANG_DUMP_S", raising=False)
    assert resolve_hang_dump_timeout_s() == 600.0
    monkeypatch.setenv("DRAWING_COMPARE_HANG_DUMP_S", "90")
    assert resolve_hang_dump_timeout_s() == 90.0
    monkeypatch.setenv("DRAWING_COMPARE_HANG_DUMP_S", "0")
    assert resolve_hang_dump_timeout_s() == 0.0
    monkeypatch.setenv("DRAWING_COMPARE_HANG_DUMP_S", "banana")
    assert resolve_hang_dump_timeout_s() == 600.0


def test_run_manifest_on_stage_hook_pets_watchdog(tmp_path: Path) -> None:
    manifest = RunManifestWriter(tmp_path)
    seen: list[tuple[str, str]] = []
    manifest.on_stage = lambda name, status: seen.append((name, status))
    manifest.stage("compare", "running")
    manifest.stage("compare", "completed")
    assert seen == [("compare", "running"), ("compare", "completed")]

    # A raising hook must never break manifest writes.
    manifest.on_stage = lambda *_: (_ for _ in ()).throw(RuntimeError("boom"))
    manifest.stage("artifact", "running")  # no exception propagates
    assert manifest.payload["stages"]["artifact"]["status"] == "running"
