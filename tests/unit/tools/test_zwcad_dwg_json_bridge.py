from __future__ import annotations

import json
import re
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from src.services.comparison.dwg_importer import _adapter_drawing_from_dict
from tools import zwcad_dwg_json_bridge as bridge


def test_zwcad_bridge_requires_existing_input(tmp_path: Path) -> None:
    args = Namespace(
        input=tmp_path / "missing.dwg",
        acadver="AC1032",
        prog_id=None,
        max_entities=10,
        visible=False,
        keep_open=False,
    )

    with pytest.raises(bridge.BridgeError, match="input DWG does not exist"):
        bridge.run_bridge(args)


def test_zwcad_bridge_wraps_com_output_with_native_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dwg = tmp_path / "sample.dwg"
    input_dwg.write_bytes(b"AC1032 zwcad")
    doc = _FakeDocument(
        entities=[
            _FakeLine(),
            _FakeCircle(),
            _FakePolyline(),
            _FakeText(),
            _FakeInsert(),
        ]
    )
    app = _FakeApp(doc)

    monkeypatch.setattr(
        bridge,
        "_dispatch_zwcad",
        lambda explicit=None: bridge.ZwcadSession(app, True, explicit or "ZWCAD.Application.2025"),
    )
    monkeypatch.setattr(bridge, "_zwcad_process_ids", lambda: set())
    monkeypatch.setattr(bridge, "_cleanup_spawned_zwcad", lambda existing_pids, **kwargs: [])

    payload = bridge.run_bridge(
        Namespace(
            input=input_dwg,
            acadver="ac1032",
            prog_id=None,
            max_entities=25,
            visible=False,
            keep_open=False,
        )
    )

    drawing = payload["drawing"]
    metadata = drawing["metadata"]
    provenance = metadata["commercial_dwg_json_bridge"]
    assert payload["schema_version"] == bridge.SCHEMA_VERSION
    assert provenance["evidence_scope"] == "native_dwg_bridge"
    assert provenance["uses_native_dwg"] is True
    assert provenance["uses_converted_dxf"] is False
    assert provenance["prog_id"] == "ZWCAD.Application.2025"
    assert provenance["entity_count"] == 5
    assert provenance["max_entities"] == 25
    assert provenance["possibly_truncated"] is False
    assert metadata["zwcad_dwg_json_bridge"]["bridge"] == bridge.BRIDGE_NAME
    assert metadata["zwcad_dwg_json_bridge"]["possibly_truncated"] is False
    assert drawing["entities"][0]["type"] == "LINE"
    assert drawing["entities"][0]["geometry"]["start"] == [0.0, 0.0, 0.0]
    assert drawing["entities"][1]["type"] == "CIRCLE"
    assert drawing["entities"][2]["geometry"]["vertices"][1]["point"] == [10.0, 0.0, 0.0]
    assert drawing["entities"][4]["type"] == "INSERT"
    assert drawing["entities"][4]["attributes"][0]["tag"] == "MARK"
    assert doc.closed is True
    assert doc.Saved is True
    assert app.Documents.opened_read_only is True
    assert app.quit_called is True


def test_zwcad_bridge_payload_matches_adapter_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dwg = tmp_path / "sample.dwg"
    input_dwg.write_bytes(b"AC1015 zwcad")
    doc = _FakeDocument(entities=[_FakeLine()])
    app = _FakeApp(doc)
    monkeypatch.setattr(
        bridge,
        "_dispatch_zwcad",
        lambda explicit=None: bridge.ZwcadSession(app, True, "ZWCAD.Application"),
    )
    monkeypatch.setattr(bridge, "_zwcad_process_ids", lambda: set())
    monkeypatch.setattr(bridge, "_cleanup_spawned_zwcad", lambda existing_pids, **kwargs: [])

    payload = bridge.run_bridge(
        Namespace(
            input=input_dwg,
            acadver="AC1015",
            prog_id=None,
            max_entities=10,
            visible=False,
            keep_open=False,
        )
    )

    drawing = _adapter_drawing_from_dict(payload["drawing"])
    assert drawing.header["$ACADVER"] == "AC1015"
    assert drawing.layers[0]["name"] == "0"
    assert drawing.model_space[0].raw_type == "LINE"


def test_zwcad_script_mode_wraps_lisp_output_with_native_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dwg = tmp_path / "sample.dwg"
    input_dwg.write_bytes(b"AC1032 zwcad")
    zwcad = tmp_path / "ZWCAD.exe"
    zwcad.write_text("stub", encoding="ascii")

    def fake_run(command, **kwargs):
        assert command[0] == str(zwcad.resolve())
        assert command[1] == str(input_dwg.resolve())
        assert command[2] == "/b"
        script_path = Path(command[3])
        script = script_path.read_text(encoding="ascii")
        assert "(setq DCW_ROI_ENABLED T)" in script
        assert "(setq DCW_ROI_MINX -5)" in script
        assert "(setq DCW_ROI_MAXX 15)" in script
        match = re.search(r'\(setq DCW_OUT "([^"]+)"\)', script)
        assert match is not None
        out = Path(match.group(1).replace("/", "\\"))
        out.write_text(
            json.dumps(
                {
                    "header": {"$ACADVER": "AC1032"},
                    "layers": [{"name": "0"}],
                    "entities": [
                        {
                            "type": "LINE",
                            "layer": "0",
                            "handle": "10",
                            "geometry": {"start": [0, 0, 0], "end": [1, 0, 0]},
                        }
                    ],
                    "metadata": {"zwcad_dwg_json_bridge": {"entity_count": 1}},
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(bridge, "resolve_zwcad_exe", lambda explicit=None: zwcad.resolve())
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)

    payload = bridge.run_bridge(
        Namespace(
            input=input_dwg,
            acadver="AC1032",
            mode="script",
            zwcad_exe=None,
            prog_id=None,
            timeout_seconds=1,
            max_entities=10,
            roi_json=json.dumps({"bbox": [0, 0, 10, 10], "margin": 5}),
            visible=False,
            keep_temp=False,
            keep_open=False,
        )
    )

    metadata = payload["drawing"]["metadata"]
    provenance = metadata["commercial_dwg_json_bridge"]
    assert payload["schema_version"] == bridge.SCHEMA_VERSION
    assert provenance["evidence_scope"] == "native_dwg_bridge"
    assert provenance["uses_native_dwg"] is True
    assert provenance["uses_converted_dxf"] is False
    assert provenance["script_mode"] is True
    assert provenance["zwcad_exe"] == str(zwcad.resolve())
    assert provenance["entity_count"] == 1
    assert provenance["max_entities"] == 10
    assert provenance["possibly_truncated"] is False
    assert provenance["roi"] == {"minx": -5.0, "miny": -5.0, "maxx": 15.0, "maxy": 15.0}
    assert metadata["zwcad_dwg_json_bridge"]["possibly_truncated"] is False
    assert payload["drawing"]["entities"][0]["type"] == "LINE"


def test_zwcad_lisp_com_mode_uses_send_command_and_wraps_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dwg = tmp_path / "sample.dwg"
    input_dwg.write_bytes(b"AC1018 zwcad")
    doc = _FakeLispDocument()
    app = _FakeApp(doc)
    monkeypatch.setattr(
        bridge,
        "_dispatch_zwcad",
        lambda explicit=None: bridge.ZwcadSession(app, True, "ZWCAD.Application.2025"),
    )
    monkeypatch.setattr(bridge, "_zwcad_process_ids", lambda: set())
    monkeypatch.setattr(bridge, "_cleanup_spawned_zwcad", lambda existing_pids, **kwargs: [])

    payload = bridge.run_bridge(
        Namespace(
            input=input_dwg,
            acadver="AC1018",
            mode="lisp-com",
            zwcad_exe=None,
            prog_id=None,
            timeout_seconds=2,
            max_entities=10,
            roi_json=json.dumps({"bbox": [0, 0, 10, 10], "margin": 2}),
            visible=False,
            keep_temp=False,
            keep_open=False,
        )
    )

    metadata = payload["drawing"]["metadata"]
    provenance = metadata["commercial_dwg_json_bridge"]
    assert doc.sent_commands
    assert "(setq DCW_ROI_ENABLED T)" in doc.sent_commands[0]
    assert "(setq DCW_ROI_MINX -2)" in doc.sent_commands[0]
    assert "(setq DCW_ROI_MAXX 12)" in doc.sent_commands[0]
    assert provenance["lisp_com_mode"] is True
    assert provenance["uses_native_dwg"] is True
    assert provenance["entity_count"] == 1
    assert provenance["max_entities"] == 10
    assert provenance["possibly_truncated"] is False
    assert provenance["roi"] == {"minx": -2.0, "miny": -2.0, "maxx": 12.0, "maxy": 12.0}
    assert metadata["zwcad_dwg_json_bridge"]["possibly_truncated"] is False
    assert payload["drawing"]["entities"][0]["type"] == "LINE"
    assert doc.closed is True
    assert doc.Saved is True
    assert app.Documents.opened_read_only is True
    assert app.quit_called is True


def test_zwcad_lisp_com_timeout_kills_spawned_process_without_graceful_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dwg = tmp_path / "sample.dwg"
    input_dwg.write_bytes(b"AC1014 zwcad")
    doc = _FakeLispDocument()
    app = _FakeApp(doc)
    cleanup_calls: list[tuple[set[int], float]] = []

    def fail_wait(*_args, **_kwargs) -> None:
        raise bridge.BridgeTimeoutError("timed out")

    def cleanup(existing_pids: set[int], *, grace_seconds: float = 5.0, only_pids=None) -> list[int]:
        cleanup_calls.append((set(existing_pids), grace_seconds))
        return [200]

    monkeypatch.setattr(
        bridge,
        "_dispatch_zwcad",
        lambda explicit=None: bridge.ZwcadSession(app, True, "ZWCAD.Application.2025"),
    )
    monkeypatch.setattr(bridge, "_zwcad_process_ids", lambda: {100})
    monkeypatch.setattr(bridge, "_wait_for_output", fail_wait)
    monkeypatch.setattr(bridge, "_cleanup_spawned_zwcad", cleanup)

    with pytest.raises(bridge.BridgeTimeoutError):
        bridge.run_bridge(
            Namespace(
                input=input_dwg,
                acadver="AC1014",
                mode="lisp-com",
                zwcad_exe=None,
                prog_id=None,
                timeout_seconds=1,
                max_entities=10,
                visible=False,
                keep_temp=False,
                keep_open=False,
            )
        )

    assert cleanup_calls == [({100}, 0.0)]
    assert doc.sent_commands
    assert doc.closed is False
    assert app.quit_called is False


def test_zwcad_lisp_com_watchdog_maps_blocked_send_command_to_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dwg = tmp_path / "sample.dwg"
    input_dwg.write_bytes(b"AC1027 zwcad")
    doc = _BlockingSendCommandDocument()
    app = _FakeApp(doc)
    cleanup_calls: list[tuple[set[int], float]] = []
    watchdog = _FakeWatchdog(fired=True, killed_pids=[200])

    def cleanup(existing_pids: set[int], *, grace_seconds: float = 5.0, only_pids=None) -> list[int]:
        cleanup_calls.append((set(existing_pids), grace_seconds))
        return []

    monkeypatch.setattr(
        bridge,
        "_dispatch_zwcad",
        lambda explicit=None: bridge.ZwcadSession(app, True, "ZWCAD.Application.2025"),
    )
    monkeypatch.setattr(bridge, "_zwcad_process_ids", lambda: {100})
    monkeypatch.setattr(bridge, "_zwcad_process_watchdog", lambda existing_pids, **kwargs: watchdog)
    monkeypatch.setattr(bridge, "_cleanup_spawned_zwcad", cleanup)

    with pytest.raises(bridge.BridgeTimeoutError, match="during send_command"):
        bridge.run_bridge(
            Namespace(
                input=input_dwg,
                acadver="AC1027",
                mode="lisp-com",
                zwcad_exe=None,
                prog_id=None,
                timeout_seconds=1,
                max_entities=10,
                visible=False,
                keep_temp=False,
                keep_open=False,
            )
        )

    assert watchdog.started is True
    assert watchdog.cancelled is True
    assert "send_command" in watchdog.stages
    assert cleanup_calls == [({100}, 0.0)]
    assert doc.closed is False
    assert app.quit_called is False


def test_candidate_prog_ids_prefers_explicit_then_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(bridge.PROG_ID_ENV, "ZWCAD.Application.2024")

    assert bridge._candidate_prog_ids("ZWCAD.Application.Custom")[:3] == (
        "ZWCAD.Application.Custom",
        "ZWCAD.Application.2024",
        "ZWCAD.Application.2025",
    )


def test_roi_json_expands_bbox_with_margin() -> None:
    assert bridge._roi_from_arg('{"bbox":[100,100,500,500],"margin":25}') == {
        "minx": 75.0,
        "miny": 75.0,
        "maxx": 525.0,
        "maxy": 525.0,
    }


def test_payload_in_roi_filters_insert_points_and_line_bboxes() -> None:
    roi = {"minx": 100.0, "miny": 100.0, "maxx": 500.0, "maxy": 500.0}

    assert bridge._payload_in_roi(
        {"type": "INSERT", "geometry": {"insert": [150, 150, 0]}},
        roi,
    )
    assert not bridge._payload_in_roi(
        {"type": "INSERT", "geometry": {"insert": [999, 999, 0]}},
        roi,
    )
    assert bridge._payload_in_roi(
        {"type": "LINE", "geometry": {"start": [0, 300, 0], "end": [1000, 300, 0]}},
        roi,
    )


def test_cleanup_spawned_zwcad_only_kills_new_pids(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[int] = []

    def fake_run(command, **kwargs):
        killed.append(int(command[2]))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(bridge.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(bridge, "_zwcad_process_ids", lambda: {100, 200, 300})
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)

    assert bridge._cleanup_spawned_zwcad({100}) == [200, 300]
    assert killed == [200, 300]


def test_zwcad_process_ids_fallback_timeout_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout_run(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(bridge, "_process_ids_for_image_toolhelp", lambda image_name: None)
    monkeypatch.setattr(bridge.subprocess, "run", timeout_run)

    assert bridge._zwcad_process_ids() == set()


def test_zwcad_kill_process_tree_falls_back_to_terminate_process(monkeypatch: pytest.MonkeyPatch) -> None:
    def failed_taskkill(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="denied")

    monkeypatch.setattr(bridge.subprocess, "run", failed_taskkill)
    monkeypatch.setattr(bridge, "_terminate_process", lambda pid: pid == 200)

    assert bridge._kill_process_tree(200) is True


class _FakeApp:
    def __init__(self, doc: "_FakeDocument") -> None:
        self.Documents = _FakeDocuments(doc)
        self.Visible = True
        self.quit_called = False

    def Quit(self) -> None:
        self.quit_called = True


class _FakeDocuments:
    def __init__(self, doc: "_FakeDocument") -> None:
        self.doc = doc
        self.opened_path = ""
        self.opened_read_only = None

    def Open(self, path: str, read_only=False) -> "_FakeDocument":
        self.opened_path = path
        self.opened_read_only = read_only
        return self.doc


class _FakeDocument:
    def __init__(self, *, entities: list[object]) -> None:
        self.Layers = [_FakeLayer()]
        self.ModelSpace = entities
        self.closed = False
        self.close_arg = None
        self.Saved = False

    def Close(self, save_changes=False) -> None:
        self.closed = True
        self.close_arg = save_changes


class _FakeLispDocument(_FakeDocument):
    def __init__(self) -> None:
        super().__init__(entities=[])
        self.sent_commands: list[str] = []

    def SendCommand(self, command: str) -> None:
        self.sent_commands.append(command)
        match = re.search(r'\(setq DCW_OUT "([^"]+)"\)', command)
        assert match is not None
        out = Path(match.group(1).replace("/", "\\"))
        out.write_text(
            json.dumps(
                {
                    "header": {"$ACADVER": "AC1018"},
                    "layers": [{"name": "0"}],
                    "entities": [
                        {
                            "type": "LINE",
                            "layer": "0",
                            "handle": "10",
                            "geometry": {"start": [0, 0, 0], "end": [1, 0, 0]},
                        }
                    ],
                    "metadata": {"zwcad_dwg_json_bridge": {"entity_count": 1}},
                }
            ),
            encoding="utf-8",
        )


class _BlockingSendCommandDocument(_FakeDocument):
    def __init__(self) -> None:
        super().__init__(entities=[])

    def SendCommand(self, command: str) -> None:
        raise RuntimeError("COM call unblocked after watchdog cleanup")


class _FakeWatchdog:
    def __init__(self, *, fired: bool, killed_pids: list[int]) -> None:
        self.fired = fired
        self.killed_pids = killed_pids
        self.timeout_seconds = 1.0
        self.stage = "initializing"
        self.stages: list[str] = []
        self.started = False
        self.cancelled = False

    def set_stage(self, stage: str) -> None:
        self.stage = stage
        self.stages.append(stage)

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


class _FakeLayer:
    Name = "0"
    Color = 7
    Linetype = "Continuous"
    Lineweight = 0


class _FakeLine:
    ObjectName = "ZcDbLine"
    Layer = "0"
    Handle = "10"
    OwnerID = "1"
    StartPoint = [0, 0, 0]
    EndPoint = [10, 0, 0]
    Color = 7
    Linetype = "Continuous"
    Lineweight = 0


class _FakeCircle:
    ObjectName = "ZcDbCircle"
    Layer = "A-COLS"
    Handle = "11"
    OwnerID = "1"
    Center = [5, 5, 0]
    Radius = 2.5
    Normal = [0, 0, 1]


class _FakePolyline:
    ObjectName = "ZcDbPolyline"
    Layer = "A-WALL"
    Handle = "12"
    OwnerID = "1"
    Coordinates = [0, 0, 10, 0, 10, 5]
    Closed = True


class _FakeText:
    ObjectName = "ZcDbText"
    Layer = "A-TEXT"
    Handle = "13"
    OwnerID = "1"
    TextString = "REV A"
    InsertionPoint = [1, 2, 0]
    Height = 2.5
    Rotation = 0.0
    HorizontalAlignment = 0
    VerticalAlignment = 0
    StyleName = "Standard"


class _FakeInsert:
    ObjectName = "ZcDbBlockReference"
    Layer = "A-BLOCK"
    Handle = "14"
    OwnerID = "1"
    EffectiveName = "GRID_BUBBLE"
    Name = "GRID_BUBBLE"
    InsertionPoint = [3, 4, 0]
    XScaleFactor = 1.0
    YScaleFactor = 1.0
    ZScaleFactor = 1.0
    Rotation = 0.0

    def GetAttributes(self):
        return [_FakeAttribute()]


class _FakeAttribute:
    TagString = "MARK"
    TextString = "A"
    InsertionPoint = [3, 4, 0]
    Handle = "15"


def test_zwcad_lisp_com_happy_path_cleanup_uses_zero_grace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a successful close the app is already Quit(), so cleanup must not sleep a
    grace window (finding 8: avoid the 5s happy-path stall)."""
    input_dwg = tmp_path / "sample.dwg"
    input_dwg.write_bytes(b"AC1018 zwcad")
    doc = _FakeLispDocument()
    app = _FakeApp(doc)
    grace_calls: list[float] = []

    def cleanup(existing_pids, *, grace_seconds=5.0, only_pids=None):
        grace_calls.append(grace_seconds)
        return []

    monkeypatch.setattr(
        bridge,
        "_dispatch_zwcad",
        lambda explicit=None: bridge.ZwcadSession(app, True, "ZWCAD.Application.2025"),
    )
    monkeypatch.setattr(bridge, "_zwcad_process_ids", lambda: {100})
    monkeypatch.setattr(bridge, "_cleanup_spawned_zwcad", cleanup)

    bridge.run_bridge(
        Namespace(
            input=input_dwg,
            acadver="AC1018",
            mode="lisp-com",
            zwcad_exe=None,
            prog_id=None,
            timeout_seconds=2,
            max_entities=10,
            visible=False,
            keep_temp=False,
            keep_open=False,
        )
    )

    assert grace_calls == [0.0]


def test_watchdog_cancel_joins_so_killed_pids_are_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cancel() must join a _fire() already in progress so killed_pids is fully
    written before the caller reads it (finding 11: no race)."""
    import time as _time

    def slow_cleanup(existing_pids, *, grace_seconds=5.0, only_pids=None):
        _time.sleep(0.3)
        return [7]

    monkeypatch.setattr(bridge, "_cleanup_spawned_zwcad", slow_cleanup)
    watchdog = bridge.ZwcadProcessWatchdog(set(), timeout_seconds=1.0)
    watchdog.start()
    # Wait until _fire begins (sets fired=True) but is still mid-cleanup (sleeping).
    deadline = _time.monotonic() + 5.0
    while not watchdog.fired and _time.monotonic() < deadline:
        _time.sleep(0.01)
    assert watchdog.fired is True
    watchdog.cancel()  # must block until _fire finishes writing killed_pids
    assert watchdog.killed_pids == [7]


def test_watchdog_cancel_before_start_does_not_raise() -> None:
    watchdog = bridge.ZwcadProcessWatchdog(set(), timeout_seconds=1.0)
    watchdog.cancel()  # never started -> must not attempt to join an unstarted timer


def test_cleanup_spawned_zwcad_only_kills_pinned_pids(monkeypatch: pytest.MonkeyPatch) -> None:
    """With only_pids set, cleanup terminates exactly the PID we spawned and leaves a
    ZWCAD the user launched independently after the snapshot alone (finding 7)."""
    killed: list[int] = []

    def fake_run(command, **kwargs):
        killed.append(int(command[2]))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    # Snapshot was {100}; now {100 existing, 200 ours, 300 user's-new}.
    monkeypatch.setattr(bridge, "_zwcad_process_ids", lambda: {100, 200, 300})
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)

    assert bridge._cleanup_spawned_zwcad({100}, only_pids={200}) == [200]
    assert killed == [200]  # 300 (the user's instance) is never touched


def test_cleanup_spawned_zwcad_empty_pin_kills_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Attaching to an existing instance (created_new False -> empty pin) kills nothing."""
    monkeypatch.setattr(bridge, "_zwcad_process_ids", lambda: {100, 200, 300})
    monkeypatch.setattr(bridge.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not kill")))
    assert bridge._cleanup_spawned_zwcad({100}, only_pids=set()) == []
