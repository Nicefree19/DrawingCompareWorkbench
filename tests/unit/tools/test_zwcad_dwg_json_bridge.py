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
    assert metadata["zwcad_dwg_json_bridge"]["bridge"] == bridge.BRIDGE_NAME
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

    payload = bridge.run_bridge(
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

    metadata = payload["drawing"]["metadata"]
    provenance = metadata["commercial_dwg_json_bridge"]
    assert doc.sent_commands
    assert provenance["lisp_com_mode"] is True
    assert provenance["uses_native_dwg"] is True
    assert payload["drawing"]["entities"][0]["type"] == "LINE"
    assert doc.closed is True
    assert doc.Saved is True
    assert app.Documents.opened_read_only is True
    assert app.quit_called is True


def test_candidate_prog_ids_prefers_explicit_then_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(bridge.PROG_ID_ENV, "ZWCAD.Application.2024")

    assert bridge._candidate_prog_ids("ZWCAD.Application.Custom")[:3] == (
        "ZWCAD.Application.Custom",
        "ZWCAD.Application.2024",
        "ZWCAD.Application.2025",
    )


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
